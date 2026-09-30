"""Embed: every item gets one embedding of its title and summary, once."""
from __future__ import annotations

from airec.phases.common import Job, batches


def item_text(row) -> str:  # noqa: ANN001
    return f"{row['title'] or ''}\n\n{(row['abstract'] or '')[:2000]}"


def embed_missing(job: Job, ids: list[str], phase: str = "embed") -> int:
    rows = job.store.missing_embeddings(ids)
    if not rows:
        return 0
    done = 0
    for chunk in batches(rows, 256):
        mat = job.embedder.embed([item_text(r) for r in chunk])
        job.store.set_embeddings([r["id"] for r in chunk], mat)
        job.store.commit()
        done += len(chunk)
        job.event(phase, done=done, total=len(rows))
    job.log(f"  embedded {done} items")
    return done
