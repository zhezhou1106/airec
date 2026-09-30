"""One folder per job under data/logs/, and the lock that keeps jobs one at a time.

    run.log          the full log
    events.jsonl     phase progress, which the Run page draws
    <phase>.jsonl    one line per model call: prompt, reply, usage, latency
    meta.json        kind, args, status, timings, models
    config/          the config files as they were when the job started
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import CONFIG_FILES, Config


class LockBusy(RuntimeError):
    pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def read_lock(cfg: Config) -> dict[str, Any] | None:
    """The job holding the lock, or None. A lock whose process is gone is ignored."""
    try:
        data = json.loads(cfg.lock_path.read_text())
    except (OSError, ValueError):
        return None
    return data if pid_alive(int(data.get("pid") or 0)) else None


class Tracer:
    """Append-only JSONL, thread-safe."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        line = json.dumps({"ts": round(time.time(), 3), **record}, ensure_ascii=False,
                          default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def new_job_dir(cfg: Config, kind: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = cfg.logs_dir / f"{stamp}_{kind}"
    n = 2
    while path.exists():
        path = cfg.logs_dir / f"{stamp}_{kind}-{n}"
        n += 1
    path.mkdir(parents=True)
    return path


class RunContext:
    """Owns one job folder. Use as a context manager around the job body."""

    def __init__(self, cfg: Config, kind: str, *, args: dict[str, Any] | None = None,
                 log_dir: Path | None = None, echo: Callable[[str], None] | None = None,
                 take_lock: bool = True):
        self.cfg = cfg
        self.kind = kind
        self.dir = Path(log_dir) if log_dir else new_job_dir(cfg, kind)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.echo = echo
        self.take_lock = take_lock
        self._locked = False
        self.meta: dict[str, Any] = {
            "kind": kind, "args": args or {}, "status": "running", "pid": os.getpid(),
            "started_at": time.time(), "finished_at": None,
        }
        self._log = (self.dir / "run.log").open("a", encoding="utf-8", buffering=1)
        self._events = Tracer(self.dir / "events.jsonl")
        self._tracers: dict[str, Tracer] = {}
        self._prev_sigterm: Any = None

    @property
    def name(self) -> str:
        return self.dir.name

    def __enter__(self) -> "RunContext":
        if self.take_lock:
            self._acquire()
        snap = self.dir / "config"
        if not snap.exists():
            snap.mkdir()
            for name in CONFIG_FILES:
                if self.cfg.path(name).exists():
                    shutil.copy2(self.cfg.path(name), snap / f"{name}.yaml")
        self._write_meta()
        try:
            self._prev_sigterm = signal.signal(signal.SIGTERM, self._on_sigterm)
        except ValueError:  # not the main thread
            self._prev_sigterm = None
        self.event("start", kind=self.kind)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            status = "done"
        elif exc_type is KeyboardInterrupt:
            status = "cancelled"
            self.log("[warn] cancelled")
        else:
            status = "failed"
            self.log(f"[error] {exc_type.__name__}: {exc}")
            self.meta["error"] = f"{exc_type.__name__}: {exc}"
        self.meta.update(status=status, finished_at=time.time())
        self._write_meta()
        self.event("end", status=status)
        if self._prev_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, self._prev_sigterm)
            except ValueError:
                pass
        self._release()
        self._log.close()
        return False

    def _on_sigterm(self, signum, frame) -> None:  # noqa: ANN001
        raise KeyboardInterrupt

    # ---------- output ----------
    def log(self, msg: str) -> None:
        if self.echo:
            self.echo(msg)
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in msg.splitlines() or [""]:
            self._log.write(f"{stamp} {line}\n")

    def event(self, phase: str, **data: Any) -> None:
        self._events({"phase": phase, **data})

    def trace(self, phase: str) -> Tracer:
        if phase not in self._tracers:
            self._tracers[phase] = Tracer(self.dir / f"{phase}.jsonl")
        return self._tracers[phase]

    def update_meta(self, **fields: Any) -> None:
        self.meta.update(fields)
        self._write_meta()

    def _write_meta(self) -> None:
        tmp = self.dir / "meta.json.tmp"
        tmp.write_text(json.dumps(self.meta, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.dir / "meta.json")

    # ---------- lock ----------
    def _acquire(self) -> None:
        holder = read_lock(self.cfg)
        if holder and int(holder.get("pid") or 0) != os.getpid():
            raise LockBusy(f"another job is running ({holder.get('kind')}, pid "
                           f"{holder.get('pid')}); wait for it or cancel it on the Run page")
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.lock_path.write_text(json.dumps({
            "pid": os.getpid(), "kind": self.kind, "job": self.dir.name,
            "started_at": time.time()}))
        self._locked = True

    def _release(self) -> None:
        if not self._locked:
            return
        try:
            if int(json.loads(self.cfg.lock_path.read_text()).get("pid", 0)) == os.getpid():
                self.cfg.lock_path.unlink()
        except (OSError, ValueError):
            pass
        self._locked = False


def read_meta(job_dir: Path) -> dict[str, Any]:
    try:
        meta = json.loads((job_dir / "meta.json").read_text())
    except (OSError, ValueError):
        return {}
    if meta.get("status") in {"running", "starting"} and \
            not pid_alive(int(meta.get("pid") or 0)):
        meta["status"] = "died"
    return meta


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
