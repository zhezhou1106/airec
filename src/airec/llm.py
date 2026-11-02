"""OpenAI-compatible chat, addressed by role rather than by model.

Sections ask for a role (`fast` for bulk judging, `writer` for what the reader
sees); config.yaml decides which provider and model fill it. Structured replies
are validated against a Pydantic schema, and a reply that fails validation is
sent back once with the error attached.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from airec.config import LlmConfig, Role, Secrets
from airec.http import USER_AGENT

log = logging.getLogger(__name__)

Schema = TypeVar("Schema", bound=BaseModel)
_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_ATTEMPTS = 3
_NO_RETRY_STATUS = {400, 401, 403, 404}


class LlmError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def parse_json(text: str) -> Any:
    """Parse JSON from a reply that may be fenced or carry a preamble."""
    text = _THINK.sub("", text).strip()
    candidates = [text, *_FENCE.findall(text)]
    for opener, closer in ("{}", "[]"):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise LlmError(f"No JSON in reply: {text[:200]!r}")


class Llm:
    def __init__(
        self,
        config: LlmConfig,
        secrets: Secrets,
        *,
        transport: httpx.BaseTransport | None = None,
        trace_path: Path | None = None,
    ):
        self.config = config
        self.secrets = secrets
        self.trace_path = trace_path
        self.usage = Usage()
        self._lock = threading.Lock()
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, transport=transport)

    def chat(self, role: str, system: str, user: str) -> str:
        return self._complete(role, system, user, json_mode=False)

    def json(self, role: str, system: str, user: str, schema: type[Schema]) -> Schema:
        """Ask for JSON matching `schema`; retry once, telling the model what was wrong."""
        prompt = user
        for attempt in range(2):
            reply = self._complete(role, system, prompt, json_mode=True)
            try:
                return schema.model_validate(parse_json(reply))
            except (LlmError, ValidationError) as exc:
                if attempt == 1:
                    raise LlmError(f"Invalid {schema.__name__} from '{role}': {exc}") from exc
                prompt = (
                    f"{user}\n\nYour previous reply was rejected: {exc}\n"
                    "Reply again with only valid JSON in the requested shape."
                )
        raise AssertionError("unreachable: the second attempt returns or raises")

    def ping(self, role: str) -> None:
        self._complete(role, "Reply with OK.", "OK?", json_mode=False)

    def _complete(self, role: str, system: str, user: str, *, json_mode: bool) -> str:
        spec = self._role(role)
        provider = self.config.providers[spec.provider]
        api_key = self.secrets.get(provider.api_key_env)
        if provider.api_key_env and not api_key:
            raise LlmError(f"{provider.api_key_env} is not set (see ~/.airec/.env).")
        payload: dict[str, Any] = {
            "model": spec.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": spec.temperature,
            "max_tokens": spec.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        url = f"{provider.base_url.rstrip('/')}/chat/completions"

        error: Exception | None = None
        for attempt in range(1, _ATTEMPTS + 1):
            started = time.monotonic()
            try:
                response = self._client.post(
                    url, json=payload, headers=headers, timeout=provider.timeout
                )
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                content = _THINK.sub("", choice["message"].get("content") or "").strip()
                self._record(role, spec, system, user, data, choice, started)
                if content:
                    return content
                # Reasoning models can spend the whole budget before answering.
                if choice.get("finish_reason") == "length":
                    payload["max_tokens"] = int(payload["max_tokens"]) * 2
                error = LlmError(f"empty reply from {spec.model}")
            except httpx.HTTPStatusError as exc:
                error = LlmError(f"{exc.response.status_code}: {exc.response.text[:200]}")
                if exc.response.status_code in _NO_RETRY_STATUS:
                    break
            except (httpx.TransportError, KeyError, IndexError, ValueError) as exc:
                error = exc
            if attempt < _ATTEMPTS:
                log.warning("LLM %s attempt %d failed: %s", role, attempt, error)
                time.sleep(2.0 * attempt)
        raise LlmError(f"'{role}' ({spec.model} via {spec.provider}) failed: {error}")

    def _role(self, role: str) -> Role:
        try:
            return self.config.roles[role]
        except KeyError:
            raise LlmError(f"No LLM role '{role}' in config.yaml.") from None

    def _record(
        self,
        role: str,
        spec: Role,
        system: str,
        user: str,
        data: dict[str, Any],
        choice: dict[str, Any],
        started: float,
    ) -> None:
        usage = data.get("usage") or {}
        with self._lock:
            self.usage.calls += 1
            self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)
            if self.trace_path:
                record = {
                    "role": role,
                    "model": spec.model,
                    "seconds": round(time.monotonic() - started, 2),
                    "usage": usage,
                    "finish_reason": choice.get("finish_reason"),
                    "system": system,
                    "user": user,
                    "reply": choice["message"].get("content"),
                }
                with self.trace_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
