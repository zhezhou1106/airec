"""Resurface: bring older papers from the index back into consideration.

Each run takes the index papers closest to your topics that have not been
triaged under the current map. After a map change that is the best of the
whole index again; otherwise it slowly works down the list, run by run.
"""
from __future__ import annotations

import time

import numpy as np

from phases.common import Job


def resurface(job: Job, exclude: set[str]) -> list[str]:
    n = int(job.cfg.s("run", "resurface"))
    if n <= 0:
        return []
    topic_ids, topic_mat = job.topic_matrix()
    keep_topics = [i for i, t in enumerate(job.cfg.topics())
                   if t.get("priority", "core") in ("core", "side")]
    if not keep_topics:
        return []
    topic_mat = topic_mat[keep_topics]
    months = int(job.cfg.s("index", "backfill_months")) or 12
    since = time.time() - months * 31 * 86400

    pool_ids: list[str] = []
    pool_sims: list[np.ndarray] = []
    for ids, mat in job.store.iter_embeddings(since=since):
        sims = (mat @ topic_mat.T).max(axis=1)
        top = np.argsort(-sims)[: n * 10]
        pool_ids.extend(ids[i] for i in top)
        pool_sims.append(sims[top])
    if not pool_ids:
        return []
    sims = np.concatenate(pool_sims)
    order = [pool_ids[i] for i in np.argsort(-sims)]
    order = [i for i in order if i not in exclude]

    key = job.key("triage")
    picked: list[str] = []
    for start in range(0, len(order), 500):
        chunk = order[start:start + 500]
        triaged = job.store.triaged(key, chunk)
        shown = job.store.last_shown(chunk)
        for item_id in chunk:
            if item_id in triaged or item_id in shown:
                continue
            picked.append(item_id)
            if len(picked) >= n:
                break
        if len(picked) >= n:
            break
    job.log(f"  resurfacing {len(picked)} papers from the index")
    return picked
