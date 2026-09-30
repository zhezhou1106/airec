"""Jobs started from the web app.

Each job is `python -m cli <command> ... --log-dir <folder>` in its own
process: it survives page reloads, can be cancelled, and takes the same lock
as the command line. The web app only reads the folder the job writes.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from config import Config
from runlog import new_job_dir, pid_alive, read_jsonl, read_lock, read_meta

JOB_NAME = re.compile(r"^[\w.\-]+$")
PHASES = {
    "run": ["check", "collect", "triage", "score", "select", "read", "news", "write"],
    "backfill": ["harvest", "embed_index"],
    "deepread": ["read", "write"],
    "revise": ["search", "revise"],
}
PHASE_LABELS = {
    "check": "Check models", "collect": "Collect", "triage": "Triage", "score": "Score",
    "select": "Select", "read": "Read and write notes", "news": "News", "write": "Write digest",
    "harvest": "Harvest arXiv", "embed_index": "Embed", "search": "Search the web",
    "revise": "Draft the revision",
}
COMMANDS = {"run": ["run"], "resume": ["resume"], "backfill": ["backfill"],
            "deepread": ["deep-read"], "revise": ["revise-map"]}


class JobError(RuntimeError):
    pass


def active(cfg: Config) -> dict[str, Any] | None:
    """The job running now, if any."""
    holder = read_lock(cfg)
    if holder:
        return {"kind": holder.get("kind"), "name": holder.get("job"), "pid": holder.get("pid")}
    if not cfg.logs_dir.exists():
        return None
    for d in sorted(cfg.logs_dir.iterdir(), reverse=True)[:5]:
        meta = read_meta(d)
        if meta.get("status") in {"starting", "running"}:
            return {"kind": meta.get("kind"), "name": d.name, "pid": meta.get("pid")}
    return None


def start(cfg: Config, command: str, argv: list[str]) -> str:
    """Launch a job; returns its folder name."""
    if command not in COMMANDS:
        raise JobError(f"unknown job {command}")
    busy = active(cfg)
    if busy:
        raise JobError(f"a {busy['kind']} job is already running")
    kind = "run" if command == "resume" else command
    log_dir = new_job_dir(cfg, kind)
    cmd = [sys.executable, "-m", "cli", *COMMANDS[command], *argv,
           "--log-dir", str(log_dir)]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "AIREC_HOME": str(cfg.root)}
    src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH", "")) if p)
    with (log_dir / "stdout.log").open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(cmd, cwd=str(cfg.root), env=env, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    # Reap the child: a zombie still passes os.kill(pid, 0) and would look alive.
    threading.Thread(target=proc.wait, daemon=True).start()
    (log_dir / "meta.json").write_text(json.dumps({
        "kind": kind, "status": "starting", "pid": proc.pid, "started_at": time.time(),
        "args": {"argv": argv}}))
    return log_dir.name


def job_dir(cfg: Config, name: str) -> Path:
    if not JOB_NAME.match(name or ""):
        raise JobError("bad job name")
    path = cfg.logs_dir / name
    if not path.is_dir():
        raise JobError(f"no job {name}")
    return path


def cancel(cfg: Config, name: str) -> bool:
    meta = read_meta(job_dir(cfg, name))
    pid = int(meta.get("pid") or 0)
    if meta.get("status") not in {"starting", "running"} or not pid_alive(pid):
        return False
    os.kill(pid, signal.SIGTERM)
    return True


# Which model-call files belong to which phase row.
CALL_FILES = {"triage": ["triage"], "score": ["score"], "read": ["note", "mention"],
              "news": ["news"], "write": ["article"]}
CALL_FILES_BY_KIND = {"deepread": {"write": ["deep_read"]}, "revise": {"revise": ["map_editor"],
                                                                        "search": ["map_editor"]}}
_CONTROL = {"phase", "ts", "status", "done", "total", "count"}


def summary(cfg: Config, name: str) -> dict[str, Any]:
    """Everything the job panel shows: windows, each phase in detail, model usage, log."""
    import interests

    d = job_dir(cfg, name)
    meta = read_meta(d)
    kind = meta.get("kind", "run")
    events = read_jsonl(d / "events.jsonl")
    now = time.time()
    phases: dict[str, dict[str, Any]] = {
        p: {"id": p, "label": PHASE_LABELS.get(p, p), "status": "waiting", "detail": "",
            "info": {}, "sources": [], "started": None, "ended": None}
        for p in PHASES.get(kind, [])}
    sources: dict[str, dict[str, Any]] = {}
    window: dict[str, Any] = {}
    for e in events:
        p, ts = e.get("phase", ""), e.get("ts")
        if p == "window":
            window = e
            continue
        if p.startswith("collect_"):
            track = p[8:]
            src = sources.setdefault(f"{track}:{e.get('source')}",
                                     {"name": e.get("source"), "track": track})
            src.update({k: e[k] for k in ("count", "error", "status") if k in e})
            owner = "collect" if track == "knowledge" else "news"
            if owner in phases and phases[owner]["started"] is None:
                phases[owner]["started"] = ts
                phases[owner]["status"] = "running"
            continue
        if p == "embed":
            if "collect" in phases and phases["collect"]["status"] != "done":
                phases["collect"]["info"]["embedded"] = f"{e.get('done')} / {e.get('total')}"
            continue
        if p == "news" and "done" in e and "candidates" not in e:
            phases.get("news", {}).get("info", {})["embedded"] = f"{e['done']} / {e['total']}"
            continue
        if p not in phases:
            continue
        ph = phases[p]
        if ph["started"] is None:
            ph["started"] = ts
        if e.get("status"):
            ph["status"] = e["status"]
            if e["status"] in ("done", "failed", "cancelled"):
                ph["ended"] = ts
        elif ph["status"] == "waiting":
            ph["status"] = "running"
        if "done" in e and "total" in e:
            ph["done"], ph["total"] = e["done"], e["total"]
            if e["done"] == 0:
                ph["progress_start"] = ts
        if p == "check" and e.get("model"):
            ph["info"].setdefault("models", []).append(
                {k: e.get(k) for k in ("model", "provider", "latency", "error")})
            continue
        for k, v in e.items():
            if k not in _CONTROL:
                ph["info"][k] = v
        if "count" in e:
            ph["info"]["count"] = e["count"]

    for src in sources.values():
        owner = "collect" if src["track"] == "knowledge" else "news"
        if owner in phases:
            phases[owner]["sources"].append(src)

    status = meta.get("status", "starting")
    names = interests.topic_names(cfg.interests)
    usage_files = {**CALL_FILES, **CALL_FILES_BY_KIND.get(kind, {})}
    totals = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0}
    for ph in phases.values():
        if status in ("failed", "cancelled", "died") and ph["status"] == "running":
            ph["status"] = status
        _describe(ph, names)
        start, end = ph["started"], ph["ended"] or (now if ph["status"] == "running" else None)
        ph["duration"] = _fmt_secs(end - start) if start and end else ""
        ph["pct"] = None
        if ph.get("total"):
            ph["pct"] = round(100 * ph["done"] / max(1, ph["total"]))
            began = ph.get("progress_start") or start
            if ph["status"] == "running" and ph["done"] and began:
                rate = (now - began) / ph["done"]
                ph["eta"] = _fmt_secs(rate * (ph["total"] - ph["done"]))
        usage = _usage(d, usage_files.get(ph["id"], []))
        if usage["calls"]:
            ph["usage"] = usage
            for k in totals:
                totals[k] += usage[k]
    return {
        "name": name, "kind": kind, "meta": meta, "status": status, "window": window,
        "phases": list(phases.values()), "usage": totals,
        "log": tail(d / "run.log", 80) or tail(d / "stdout.log", 80),
        "error": meta.get("error", ""),
        "live": status in ("starting", "running"),
        "elapsed": _elapsed(meta),
        "current": _current(phases),
    }


def _describe(ph: dict[str, Any], names: dict[str, str]) -> None:
    """The one-line summary shown next to each phase."""
    i, p, st = ph["info"], ph["id"], ph["status"]
    if "by_topic" in i:
        i["by_topic"] = {names.get(k, k): v for k, v in i["by_topic"].items()}
    if ph.get("total") and st == "running":
        ph["detail"] = f"{ph['done']:,} / {ph['total']:,}"
    if p == "check" and i.get("models"):
        bad = [m for m in i["models"] if m.get("error")]
        ph["detail"] = f"{len(bad)} not answering" if bad else f"{len(i['models'])} models answering"
    elif p == "collect" and "items" in i:
        ph["detail"] = f"{i['items']:,} items · {i.get('new', 0):,} new"
        if i.get("backlog"):
            ph["detail"] += f" · {i['backlog']:,} carried over"
        if i.get("resurfaced"):
            ph["detail"] += f" · {i['resurfaced']} from the index"
    elif p == "triage" and "kept" in i:
        ph["detail"] = f"kept {i['kept']:,} of {i['kept'] + i['dropped']:,}"
    elif p == "score" and "bands" in i:
        ph["detail"] = f"{i['bands'].get('8+', 0)} at 8+ · {i['bands'].get('7–8', 0)} at 7–8"
    elif p == "select" and "notes" in i:
        ph["detail"] = f"{len(i['notes'])} notes · {i.get('mentions', 0)} mentions"
    elif p == "read" and st == "done":
        ph["detail"] = f"{i.get('count', ph.get('total', 0))} notes written"
    elif p == "news" and "bullets" in i:
        ph["detail"] = f"{i['bullets']} bullets from {i['candidates']} stories"
    elif p == "write" and st == "done":
        ph["detail"] = "saved"
    elif not ph["detail"]:
        if "month" in i:
            ph["detail"] = str(i["month"]) + (f" · {i['papers']:,} papers" if "papers" in i else "")
        elif "remaining" in i:
            ph["detail"] = f"{i['remaining']:,} left"


def _current(phases: dict[str, dict[str, Any]]) -> str:
    for ph in phases.values():
        if ph["status"] == "running":
            now = ph["info"].get("current")
            return f"{ph['label']}" + (f": {now}" if now else "")
    return ""


_USAGE_CACHE: dict[str, tuple[float, int, dict[str, Any]]] = {}


def _usage(d: Path, stems: list[str]) -> dict[str, Any]:
    out = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0, "latency": 0.0,
           "models": []}
    for stem in stems:
        path = d / f"{stem}.jsonl"
        if not path.exists():
            continue
        st = path.stat()
        key = str(path)
        cached = _USAGE_CACHE.get(key)
        if not cached or cached[0] != st.st_mtime or cached[1] != st.st_size:
            u = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0, "latency": 0.0,
                 "models": []}
            for r in read_jsonl(path):
                u["calls"] += 1
                if r.get("error"):
                    u["errors"] += 1
                usage = r.get("usage") or {}
                u["tokens_in"] += int(usage.get("prompt_tokens") or 0)
                u["tokens_out"] += int(usage.get("completion_tokens") or 0)
                u["latency"] += float(r.get("latency_s") or 0)
                if r.get("model") and r["model"] not in u["models"]:
                    u["models"].append(r["model"])
            _USAGE_CACHE[key] = (st.st_mtime, st.st_size, u)
            cached = _USAGE_CACHE[key]
        u = cached[2]
        for k in ("calls", "tokens_in", "tokens_out", "errors", "latency"):
            out[k] += u[k]
        out["models"] += [m for m in u["models"] if m not in out["models"]]
    out["avg"] = round(out["latency"] / out["calls"], 1) if out["calls"] else 0
    return out


def _fmt_secs(secs: float) -> str:
    secs = int(max(0, secs))
    if secs >= 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d} min"
    return f"{secs // 60} min {secs % 60:02d} s" if secs >= 60 else f"{secs} s"


def tail(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 64000))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    return "\n".join(lines[-n:])


def _elapsed(meta: dict[str, Any]) -> str:
    start = meta.get("started_at")
    if not start:
        return ""
    return _fmt_secs((meta.get("finished_at") or time.time()) - start)


def list_jobs(cfg: Config, limit: int = 60) -> list[dict[str, Any]]:
    if not cfg.logs_dir.exists():
        return []
    out = []
    for d in sorted(cfg.logs_dir.iterdir(), reverse=True)[:limit]:
        if d.is_dir():
            meta = read_meta(d)
            out.append({"name": d.name, "kind": meta.get("kind", "?"),
                        "status": meta.get("status", "?"), "meta": meta,
                        "elapsed": _elapsed(meta)})
    return out


def calls(cfg: Config, name: str) -> dict[str, list[dict[str, Any]]]:
    """Model calls by phase, newest last."""
    d = job_dir(cfg, name)
    out = {}
    for path in sorted(d.glob("*.jsonl")):
        if path.stem in ("events", "embed"):
            continue
        out[path.stem] = read_jsonl(path)[-200:]
    return out
