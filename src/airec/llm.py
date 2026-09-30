"""OpenAI-compatible chat and embedding clients.

Each phase gets its own ChatClient from config/models.yaml, so any phase can
run on a local server (LM Studio, llama.cpp, vLLM) or an API (DeepSeek, ...).
Every HTTP attempt is handed to `trace`, which the run folder writes to
<phase>.jsonl: prompt, reply, usage, latency.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
import numpy as np

from airec.config import USER_AGENT, Config, EmbedEndpoint, Endpoint

Trace = Callable[[dict[str, Any]], None]
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    pass


def _no_trace(_record: dict[str, Any]) -> None:
    return None


def strip_think(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def extract_json(text: str) -> Any:
    """Parse JSON from a reply that may carry fences or a preamble."""
    text = strip_think(text)
    if not text:
        raise LLMError("empty completion")
    for candidate in (text, *(m.group(1) for m in _JSON_BLOCK.finditer(text))):
        try:
            return json.loads(candidate.strip())
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"could not parse JSON from: {text[:200]!r}")


class ChatClient:
    def __init__(self, ep: Endpoint, trace: Trace | None = None):
        self.ep = ep
        self.trace = trace or _no_trace
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if ep.api_key:
            headers["Authorization"] = f"Bearer {ep.api_key}"
        elif ep.local:
            headers["Authorization"] = "Bearer not-needed"
        self.client = httpx.Client(timeout=ep.timeout, headers=headers)

    @property
    def model(self) -> str:
        return self.ep.model

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 json_out: bool = False, retries: int = 2) -> str:
        payload: dict[str, Any] = {
            "model": self.ep.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.ep.temperature,
            "max_tokens": max_tokens or self.ep.max_tokens,
            "stream": False,
        }
        if self.ep.local:
            # Qwen-style local models otherwise spend the budget thinking.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            payload["reasoning_effort"] = "none"
        if json_out and self.ep.json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.ep.base_url}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            started = time.time()
            record: dict[str, Any] = {
                "phase": self.ep.phase, "provider": self.ep.provider, "model": self.ep.model,
                "attempt": attempt, "system": system, "user": user, "fallbacks": [],
            }
            try:
                resp = self.client.post(url, json=payload)
                for key in ("response_format", "reasoning_effort", "chat_template_kwargs"):
                    if resp.status_code == 400 and key in payload:
                        payload.pop(key)
                        record["fallbacks"].append(f"dropped {key}")
                        resp = self.client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                msg = choice.get("message") or {}
                content = strip_think(msg.get("content") or "")
                truncated = choice.get("finish_reason") == "length"
                if not content and not truncated:
                    content = strip_think(msg.get("reasoning_content") or "")
                    if content:
                        record["fallbacks"].append("answer taken from reasoning_content")
                record.update(response=content, finish_reason=choice.get("finish_reason"),
                              usage=data.get("usage"),
                              latency_s=round(time.time() - started, 2))
                if not content and truncated and attempt < retries:
                    payload["max_tokens"] = int(payload["max_tokens"]) * 2
                    record["error"] = "empty reply at the token limit; retrying with more room"
                    self.trace(record)
                    continue
                self.trace(record)
                if not content:
                    raise LLMError("the model returned an empty reply")
                return content
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                record.update(error=f"{type(exc).__name__}: {exc}",
                              latency_s=round(time.time() - started, 2))
                self.trace(record)
                if isinstance(exc, httpx.HTTPStatusError) and \
                        exc.response.status_code in (401, 403, 404):
                    break
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
        raise LLMError(f"{self.ep.phase} ({self.ep.model} via {self.ep.provider}) failed: "
                       f"{last_err}")

    def json(self, system: str, user: str, *, max_tokens: int | None = None) -> Any:
        raw = self.complete(system, user, max_tokens=max_tokens, json_out=True)
        try:
            return extract_json(raw)
        except LLMError:
            raw = self.complete(system + "\n\nReturn ONLY valid JSON. No prose, no fences.",
                                user, max_tokens=max_tokens, json_out=True)
            return extract_json(raw)

    def ping(self, timeout: float = 180) -> float:
        """One tiny request, so a phase fails in seconds rather than after timeouts."""
        started = time.time()
        payload: dict[str, Any] = {
            "model": self.ep.model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 8, "stream": False,
        }
        if self.ep.local:
            payload["reasoning_effort"] = "none"
        try:
            resp = self.client.post(f"{self.ep.base_url}/chat/completions", json=payload,
                                    timeout=timeout)
            if resp.status_code == 400 and "reasoning_effort" in payload:
                payload.pop("reasoning_effort")
                resp = self.client.post(f"{self.ep.base_url}/chat/completions", json=payload,
                                        timeout=timeout)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"{self.ep.phase}: {self.ep.model} at {self.ep.base_url} did not "
                           f"answer ({type(exc).__name__}: {exc})") from exc
        return round(time.time() - started, 2)


def list_models(base_url: str, api_key: str = "", timeout: float = 10) -> list[str]:
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
    resp.raise_for_status()
    return sorted(m.get("id", "") for m in resp.json().get("data", []))


# ---------------------------------------------------------------- embeddings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "embed-server.sh"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ready: set[str] = set()


def _health(base_url: str) -> int | None:
    parsed = urlparse(base_url)
    try:
        return httpx.get(f"{parsed.scheme}://{parsed.netloc}/health", timeout=2).status_code
    except httpx.HTTPError:
        return None


def ensure_embed_server(ep: EmbedEndpoint, data_dir: Path, log: Callable[[str], None] = print,
                        timeout: int = 600) -> None:
    """Start scripts/embed-server.sh when nothing answers on a localhost URL."""
    if not ep.autostart or ep.base_url in _ready:
        return
    parsed = urlparse(ep.base_url)
    if parsed.hostname not in _LOCAL_HOSTS:
        _ready.add(ep.base_url)
        return
    status = _health(ep.base_url)
    if status is not None and status != 503:
        _ready.add(ep.base_url)
        return
    if status is None and not SCRIPT.exists():
        return
    state = data_dir / "embed-server"
    state.mkdir(parents=True, exist_ok=True)
    proc = None
    with open(state / "start.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _health(ep.base_url) is None:
            log(f"  starting the embedding server on port {parsed.port}")
            env = {**os.environ, "EMBED_PORT": str(parsed.port or 80), "EMBED_MODEL": ep.model,
                   "EMBED_PID_FILE": str(state / "server.pid"), "DATA_DIR": str(data_dir)}
            with open(state / "server.log", "ab") as out:
                proc = subprocess.Popen([str(SCRIPT)], env=env, stdout=out,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True)
        deadline = time.time() + timeout
        while _health(ep.base_url) != 200:
            if proc is not None and proc.poll() is not None:
                raise LLMError(f"the embedding server exited; see {state / 'server.log'}")
            if time.time() > deadline:
                raise LLMError(f"the embedding server did not start within {timeout}s")
            time.sleep(1)
    _ready.add(ep.base_url)


class EmbedClient:
    def __init__(self, cfg: Config, trace: Trace | None = None):
        self.cfg = cfg
        self.ep = cfg.embedding()
        self.trace = trace or _no_trace
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
                   "Authorization": f"Bearer {self.ep.api_key or 'not-needed'}"}
        self.client = httpx.Client(timeout=self.ep.timeout, headers=headers)

    @property
    def model(self) -> str:
        return self.ep.model

    def embed(self, texts: list[str]) -> np.ndarray:
        """Unit-normalized float32 rows."""
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        ensure_embed_server(self.ep, self.cfg.data_dir)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.ep.batch):
            chunk = [(t or " ")[:6000] for t in texts[i:i + self.ep.batch]]
            for attempt in range(3):
                started = time.time()
                try:
                    resp = self.client.post(f"{self.ep.base_url}/embeddings",
                                            json={"model": self.ep.model, "input": chunk})
                    resp.raise_for_status()
                    data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
                    out.extend(d["embedding"] for d in data)
                    self.trace({"phase": "embed", "batch": len(chunk),
                                "latency_s": round(time.time() - started, 2)})
                    break
                except Exception as exc:  # noqa: BLE001
                    self.trace({"phase": "embed", "batch": len(chunk),
                                "error": f"{type(exc).__name__}: {exc}"})
                    if attempt == 2:
                        raise LLMError(f"embedding request failed: {exc}") from exc
                    time.sleep(2 * (attempt + 1))
        return normalize(np.asarray(out, dtype=np.float32))


def normalize(mat: np.ndarray) -> np.ndarray:
    if mat.ndim == 1:
        n = np.linalg.norm(mat)
        return mat / n if n else mat
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms
