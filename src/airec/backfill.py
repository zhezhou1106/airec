"""Backfill: fill the index with the last N months of arXiv (metadata + embeddings).

Uses OAI-PMH month by month and remembers finished months, so it can be
stopped and started again. Backfilled papers never appear as "new"; they
reach a digest only through resurfacing.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from airec.config import Config
from airec.phases.common import Job
from airec.phases.embed import embed_missing
from airec.runlog import RunContext
from airec.sources.arxiv import harvest, month_ranges
from airec.sources.base import SourceContext
from airec.store import Store


def run_backfill(cfg: Config, rc: RunContext, months: int | None = None) -> None:
    months = int(months or cfg.s("index", "backfill_months"))
    store = Store(cfg.db_path)
    try:
        job = Job(cfg, store, rc)
        cats = (cfg.sources.get("arxiv") or {}).get("categories") or ["cs.LG", "cs.CL", "cs.AI"]
        ranges = month_ranges(months)
        start_epoch = datetime.combine(ranges[0][0], datetime.min.time(),
                                       tzinfo=timezone.utc).timestamp()
        done_months: list[str] = store.get("backfill_done", [])
        ctx = SourceContext(cfg, log=rc.log)
        rc.log(f"backfill: {months} months of {', '.join(cats)}")
        for n, (first, last) in enumerate(ranges, 1):
            label = first.strftime("%Y-%m")
            is_current = last >= datetime.now(timezone.utc).date().replace(day=1)
            if label in done_months and not is_current:
                rc.log(f"  {label}: already in the index")
                continue
            rc.event("harvest", month=label, done=n - 1, total=len(ranges))
            count = new = 0
            started = time.time()
            for item in harvest(ctx.http, cats, first, last, warn=rc.log,
                                first_version_after=start_epoch):
                new += store.upsert_item(item, "knowledge", backfill=True)
                count += 1
                if count % 1000 == 0:
                    store.commit()
                    rc.event("harvest", month=label, papers=count)
            store.commit()
            rc.log(f"  {label}: {count} papers ({new} new) in {time.time() - started:.0f}s")
            if not is_current and label not in done_months:
                done_months.append(label)
                store.set("backfill_done", done_months)
        rc.event("harvest", done=len(ranges), total=len(ranges))

        rc.log("embedding the index")
        while True:
            rows = store.missing_embeddings(limit=2000)
            if not rows:
                break
            embed_missing(job, [r["id"] for r in rows], phase="embed_index")
            left = store.one("SELECT COUNT(*) AS n FROM items WHERE embedding IS NULL")["n"]
            rc.event("embed_index", remaining=left)
            rc.log(f"  {left} papers left to embed")
        stats = store.stats()
        rc.log(f"index: {stats['items']} papers, {stats['embedded']} embedded, "
               f"{stats['db_mb']} MB")
    finally:
        store.close()
