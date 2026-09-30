"""News: what happened since the last news run (at most news.max_days), as short bullets.

News is time-sensitive, so its window never overlaps the previous one and a
news item is never shown twice. Stories about the same event (five sites
covering one release) are grouped before the model sees them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import numpy as np

import interests, prompts
from phases.common import Job
from phases.embed import embed_missing

GROUP_SIM = 0.86
MAX_STORIES = 45


def select_stories(job: Job, ids: list[str]) -> list[dict[str, Any]]:
    """Group unseen news items into stories and keep the most relevant ones."""
    shown = job.store.last_shown(ids)
    rows = [r for r in job.store.items(ids) if r["id"] not in shown]
    if not rows:
        return []
    embed_missing(job, [r["id"] for r in rows], phase="news")
    vecs = job.store.embeddings([r["id"] for r in rows])
    rows = [r for r in rows if r["id"] in vecs]
    if not rows:
        return []
    _topic_ids, topic_mat = job.topic_matrix()
    mat = np.stack([vecs[r["id"]] for r in rows])
    relevance = (mat @ topic_mat.T).max(axis=1)

    def popularity(r: Any) -> float:
        s = json.loads(r["signals"] or "{}")
        raw = (s.get("hn_points") or 0) / 300 + (s.get("likes") or 0) / 500
        return min(raw, 0.15)

    order = np.argsort(-(relevance + np.array([popularity(r) for r in rows])))
    stories: list[dict[str, Any]] = []
    centers: list[np.ndarray] = []
    for i in order:
        r, v = rows[i], mat[i]
        for story, c in zip(stories, centers):
            if float(v @ c) >= GROUP_SIM:
                story["items"].append(r)
                break
        else:
            if len(stories) < MAX_STORIES:
                stories.append({"items": [r], "relevance": float(relevance[i])})
                centers.append(v)
    return stories


def write_news(job: Job, ids: list[str], since: float, until: float) -> dict[str, Any]:
    stories = select_stories(job, ids)
    window = f"{_day(since)} to {_day(until)}"
    if not stories:
        return {"markdown": "Quiet period: nothing new from the news sources.", "items": [],
                "window": window}
    blocks = []
    for n, story in enumerate(stories, 1):
        lead = story["items"][0]
        sources = ", ".join(dict.fromkeys(r["source"] for r in story["items"]))
        lines = [f"s{n}: {lead['title']}  ({_day(lead['published_at'])}; {sources})"]
        summary = " ".join((lead["abstract"] or "").split())[:500]
        if summary:
            lines.append(f"    {summary}")
        fit = json.loads(lead["signals"] or "{}").get("device_fit")
        if fit:
            lines.append(f"    fit: {fit}")
        blocks.append("\n".join(lines))
    system = prompts.render("news", window=window,
                            topics=interests.prompt_block(job.cfg.interests, detail=False),
                            stories=int(job.cfg.s("news", "stories")))
    reply = job.chat("news").complete(system, "Stories:\n\n" + "\n".join(blocks),
                                      max_tokens=2500)
    used: set[int] = set()

    def link(m: re.Match[str]) -> str:
        n = int(m.group(1))
        if not 1 <= n <= len(stories):
            return ""
        used.add(n)
        lead = stories[n - 1]["items"][0]
        return f" [{_site(lead)}]({lead['url']})"

    markdown = re.sub(r"\s*\[s(\d+)\]", link, reply).strip()
    shown_ids = [r["id"] for n in used for r in stories[n - 1]["items"]]
    bullets = sum(1 for line in markdown.splitlines() if line.lstrip().startswith(("-", "*")))
    job.log(f"  news: {bullets} bullets citing {len(used)} of {len(stories)} candidate stories")
    job.event("news", candidates=len(stories), cited=len(used), bullets=bullets)
    return {"markdown": markdown, "items": shown_ids, "window": window}


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%b %-d")


def _site(row: Any) -> str:
    src = row["source"]
    if src.startswith("feed:"):
        return src[5:]
    return {"hf_models": "Hugging Face", "hackernews": "HN", "github_releases": "GitHub",
            "web_news": re.sub(r"^www\.", "", (row["url"] or "").split("/")[2]
                               if "//" in (row["url"] or "") else "web")}.get(
        src, src.replace("reddit:", "r/"))
