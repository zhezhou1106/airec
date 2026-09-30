"""Collect: ask every enabled source of a track for what appeared in the window."""
from __future__ import annotations

import sources
from phases.common import Job
from sources.base import SourceContext, merge


# arXiv lists a paper about a day after submission (longer over weekends), and
# HF daily papers lag the same way. Without an overlap, a paper submitted just
# before a run appears after it, dated before the next window: never collected.
# The index makes the overlap free: judged items are cached, shown ones not repeated.
KNOWLEDGE_OVERLAP_DAYS = 3


def collect(job: Job, track: str, since: float, until: float) -> list[str]:
    """Fetch, merge duplicates, write to the index. Returns the ids found."""
    if track == "knowledge":
        since -= KNOWLEDGE_OVERLAP_DAYS * 86400
    ctx = SourceContext(job.cfg, log=job.log)
    found = []
    for src in sources.build(ctx, track):
        job.event(f"collect_{track}", source=src.name, status="running")
        try:
            got = src.fetch(since, until)
            error = ""
        except Exception as exc:  # noqa: BLE001 - one broken source never stops a run
            got, error = [], f"{type(exc).__name__}: {exc}"
            job.log(f"[warn] {src.name} failed: {error}")
        found.extend(got)
        job.event(f"collect_{track}", source=src.name, count=len(got), error=error,
                  status="done")
    items = merge(found)
    new = sum(job.store.upsert_item(it, track) for it in items)
    job.store.commit()
    job.event("collect" if track == "knowledge" else "news", items=len(items), new=new)
    job.log(f"  {len(items)} {track} items ({new} new to the index)")
    return [it["id"] for it in items]
