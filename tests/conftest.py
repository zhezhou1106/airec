"""Offline fixtures: a temp project with the real config files, fake sources and fake models."""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import yaml

import llm, sources
from config import Config
from phases import read as read_phase

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400


def paper(n: int, title: str, days_ago: float = 1, **signals) -> dict:
    return {
        "id": f"arxiv:2609.{10000 + n}", "kind": "paper", "source": "arxiv",
        "url": f"https://arxiv.org/abs/2609.{10000 + n}", "title": title,
        "abstract": f"We study {title.lower()}. Results improve by {n}%.",
        "authors": "A. Author", "published_at": time.time() - days_ago * DAY,
        "signals": signals,
    }


def news_item(n: int, title: str, days_ago: float = 1) -> dict:
    return {
        "id": f"url:news{n}", "kind": "news", "source": "hackernews",
        "url": f"https://example.com/news/{n}", "title": title, "abstract": f"{title} today.",
        "authors": "", "published_at": time.time() - days_ago * DAY,
        "signals": {"hn_points": 200},
    }


class World:
    """What the fake sources return, and a record of the model calls made."""

    def __init__(self):
        self.knowledge: list[dict] = []
        self.news: list[dict] = []
        self.calls: dict[str, int] = {}
        self.fail_phase: str | None = None
        self.windows: dict[str, list[tuple[float, float]]] = {"knowledge": [], "news": []}


class FakeSource(sources.Source):
    def __init__(self, world: World, track: str):
        self.world, self.track, self.name = world, track, f"fake_{track}"

    def fetch(self, since, until):
        self.world.windows[self.track].append((since, until))
        items = self.world.knowledge if self.track == "knowledge" else self.world.news
        return [dict(it, signals=dict(it["signals"])) for it in items
                if since - DAY <= it["published_at"] <= until]


def fake_reply(world: World, phase: str, user: str) -> str:
    world.calls[phase] = world.calls.get(phase, 0) + 1
    if world.fail_phase == phase:
        raise llm.LLMError(f"{phase} is down")
    ids = re.findall(r"^(i\d+) ", user, re.M)
    if phase == "triage":
        return "\n".join(f"{i} keep {'continual-learning' if n % 2 else 'post-training-rl'}"
                         for n, i in enumerate(ids))
    if phase == "score":
        return "\n".join(f"{i} | {8.5 - 0.1 * n:.1f} | new | Clear mechanism, released code."
                         for n, i in enumerate(ids))
    if phase == "note":
        return ("**What it is.** A method.\n\n**How it works.** It works.\n\n"
                "**What they found.** Gains of 3%.\n\n**Open it if** you train models.")
    if phase == "mention":
        return "\n".join(f"{i}: A one-line summary." for i in re.findall(r"^(i\d+) ", user, re.M))
    if phase == "article":
        return "### A theme\n\nTwo results speak to each other [1][2], and one is odd [99].\n"
    if phase == "news":
        return "- **Something shipped.** It matters. [s1]"
    return "OK"


def fake_embed(texts: list[str]) -> np.ndarray:
    rows = []
    for t in texts:
        seed = int(hashlib.sha1(t.encode()).hexdigest()[:8], 16)
        rows.append(np.random.default_rng(seed).normal(size=32))
    return llm.normalize(np.asarray(rows, dtype=np.float32))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "config.example", tmp_path / "config")
    settings = yaml.safe_load((tmp_path / "config/settings.yaml").read_text())
    settings["output"]["obsidian_dir"] = str(tmp_path / "vault")
    settings["run"]["resurface"] = 0
    settings["news"]["enabled"] = False  # tests turn news on where they need it
    (tmp_path / "config/settings.yaml").write_text(yaml.safe_dump(settings))
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-test-123456789\n")
    monkeypatch.setenv("AIREC_HOME", str(tmp_path))
    monkeypatch.delenv("AIREC_CONFIG_DIR", raising=False)
    monkeypatch.delenv("AIREC_DATA_DIR", raising=False)
    monkeypatch.delenv("AIREC_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def world(home, monkeypatch):
    w = World()
    monkeypatch.setattr(sources, "build", lambda ctx, track: [FakeSource(w, track)])

    def complete(self, system, user, **_kw):
        reply = fake_reply(w, self.ep.phase, user)
        self.trace({"phase": self.ep.phase, "model": self.ep.model, "system": system,
                    "user": user, "response": reply, "latency_s": 0.01,
                    "usage": {"prompt_tokens": len(user) // 4,
                              "completion_tokens": len(reply) // 4}})
        return reply

    monkeypatch.setattr(llm.ChatClient, "complete", complete)
    monkeypatch.setattr(llm.ChatClient, "ping", lambda self, timeout=180: 0.01)
    monkeypatch.setattr(llm.EmbedClient, "embed", lambda self, texts: fake_embed(texts))
    monkeypatch.setattr(read_phase.Reader, "_fetch", lambda self, row: "## Method\nDetails.")
    return w


@pytest.fixture()
def cfg(home) -> Config:
    return Config()
