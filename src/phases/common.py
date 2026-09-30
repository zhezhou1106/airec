"""What every phase gets: the config, the index, the job folder, and model clients."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

import interests
from config import Config
from llm import ChatClient, EmbedClient
from runlog import RunContext
from store import Store

KIND_WORD = {"paper": "paper", "post": "blog post", "repo": "repository", "model": "model",
             "news": "article"}


class PhaseError(RuntimeError):
    pass


@dataclass
class Job:
    cfg: Config
    store: Store
    rc: RunContext
    run_id: int = 0
    _chats: dict[str, ChatClient] = field(default_factory=dict)
    _embed: EmbedClient | None = None
    _topics: tuple[list[str], np.ndarray] | None = None

    def log(self, msg: str) -> None:
        self.rc.log(msg)

    def event(self, phase: str, **data: Any) -> None:
        self.rc.event(phase, **data)

    def chat(self, phase: str) -> ChatClient:
        if phase not in self._chats:
            self._chats[phase] = ChatClient(self.cfg.endpoint(phase), trace=self.rc.trace(phase))
        return self._chats[phase]

    @property
    def embedder(self) -> EmbedClient:
        if self._embed is None:
            self._embed = EmbedClient(self.cfg, trace=self.rc.trace("embed"))
        return self._embed

    def topic_matrix(self) -> tuple[list[str], np.ndarray]:
        """Topic ids and their embeddings (from name, summary and 'counts' lines)."""
        if self._topics is None:
            topics = self.cfg.topics()
            ids = [t["id"] for t in topics]
            mat = self.embedder.embed([interests.topic_text(t) for t in topics])
            self._topics = (ids, mat)
        return self._topics

    def key(self, phase: str) -> str:
        """Cache key for judgements: the map version plus the model that judged."""
        return f"{self.cfg.map_hash()}:{self.cfg.endpoint(phase).model}"

    def map_block(self) -> str:
        return interests.prompt_block(self.cfg.interests)

    def topic_line(self, topic_id: str) -> str:
        for t in self.cfg.topics():
            if t["id"] == topic_id:
                return f"{t.get('name', topic_id)}: {' '.join(str(t.get('summary', '')).split())}"
        return "Outside the reader's listed topics, kept because it looked significant."


def short_ids(ids: list[str]) -> dict[str, str]:
    """Models copy 'i3' more reliably than 'arxiv:2609.12345'."""
    return {f"i{n}": item_id for n, item_id in enumerate(ids, 1)}


def signals_line(row: Any) -> str:
    s = json.loads(row["signals"] or "{}")
    parts = []
    if s.get("hf_upvotes"):
        parts.append(f"{s['hf_upvotes']} upvotes on HF papers")
    if s.get("stars"):
        parts.append(f"{s['stars']} stars ({s.get('stars_per_day', 0)}/day)")
    if s.get("citations"):
        parts.append(f"{s['citations']} citations ({s.get('influential_citations', 0)} influential)")
    if s.get("venue"):
        parts.append(f"venue: {s['venue']}")
    if s.get("code_url"):
        parts.append("code released")
    if s.get("hn_points"):
        parts.append(f"{s['hn_points']} points on HN")
    seen = json.loads(row["seen_in"] or "[]")
    if len(seen) > 1:
        parts.append("seen in " + ", ".join(seen))
    return "; ".join(parts)


LINE_ID = re.compile(r"^\W*(i\d+)\b\W*(.*)$")


def parse_lines(text: str) -> dict[str, str]:
    """{'i3': 'rest of the line'} from a line-per-item reply."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = LINE_ID.match(line.strip())
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def batches(seq: list[Any], size: int) -> list[list[Any]]:
    size = max(1, int(size))
    return [seq[i:i + size] for i in range(0, len(seq), size)]
