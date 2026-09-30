"""Triage: a quick keep-or-drop on title and summary, in large batches.

Nothing is cut by keyword or similarity; the only limit is run.triage_max,
and when it applies the items least like any topic carry over to the next run
(for up to 21 days; see pipeline.BACKLOG_DAYS).
Verdicts are cached per (map version, model), so an item is triaged once
until the map changes.
"""
from __future__ import annotations

import numpy as np

from airec import prompts
from airec.phases.common import KIND_WORD, Job, PhaseError, batches, parse_lines, short_ids


def _order_by_affinity(job: Job, ids: list[str]) -> tuple[list[str], dict[str, str]]:
    """Most topic-like first, plus each item's nearest topic (the fallback label)."""
    topic_ids, topic_mat = job.topic_matrix()
    vecs = job.store.embeddings(ids)
    nearest: dict[str, str] = {}
    score: dict[str, float] = {}
    for item_id in ids:
        v = vecs.get(item_id)
        if v is None:
            score[item_id] = 0.0
            nearest[item_id] = topic_ids[0] if topic_ids else "other"
            continue
        sims = topic_mat @ v
        score[item_id] = float(sims.max())
        nearest[item_id] = topic_ids[int(np.argmax(sims))]
    return sorted(ids, key=lambda i: -score[i]), nearest


def triage(job: Job, ids: list[str]) -> dict[str, str]:
    """{item_id: topic} for every kept item, cached or new."""
    chat = job.chat("triage")
    key = job.key("triage")
    cached = job.store.triaged(key, ids)
    todo = [i for i in ids if i not in cached]
    ordered, nearest = _order_by_affinity(job, todo)
    cap = int(job.cfg.s("run", "triage_max"))
    if len(ordered) > cap:
        job.log(f"  triage limit {cap}: {len(ordered) - cap} least topic-like items carry over "
                "to the next run (raise run.triage_max to do them now)")
        ordered = ordered[:cap]
    job.log(f"  triage: {len(cached)} already done, {len(ordered)} to do")
    job.event("triage", cached=len(cached), todo=len(ordered), skipped=max(0, len(todo) - cap),
              model=chat.model, done=0, total=len(ordered))

    valid_topics = {t["id"] for t in job.cfg.topics()} | {"other"}
    system = prompts.render("triage", map=job.map_block())
    rows = {r["id"]: r for r in job.store.items(ordered)}
    failed_batches = 0
    missing: list[str] = []
    all_batches = batches(ordered, int(job.cfg.s("run", "triage_batch")))
    for n, batch in enumerate(all_batches, 1):
        got, error = _run_batch(chat, system, batch, rows)
        if error:
            failed_batches += 1
            job.log(f"[warn] triage batch {n} failed: {error}")
            if failed_batches >= 3 and failed_batches > n // 2:
                raise PhaseError(f"triage keeps failing ({error}); is the model running?")
            continue
        for item_id in batch:
            verdict = got.get(item_id)
            if verdict is None:
                missing.append(item_id)
                continue
            keep, topic = verdict
            job.store.set_triage(key, item_id, keep,
                                 topic if topic in valid_topics else nearest[item_id])
        job.store.commit()
        job.event("triage", done=min(n * len(batch), len(ordered)), total=len(ordered))

    if missing:
        # One retry in small batches; whatever the model still skips is kept
        # (a later step scores it) rather than silently lost.
        got_all: dict[str, tuple[bool, str]] = {}
        for batch in batches(missing, 8):
            got, _err = _run_batch(chat, system, batch, rows)
            got_all.update(got)
        for item_id in missing:
            keep, topic = got_all.get(item_id, (True, nearest[item_id]))
            job.store.set_triage(key, item_id, keep,
                                 topic if topic in valid_topics else nearest[item_id])
        job.store.commit()

    verdicts = job.store.triaged(key, ids)
    kept = {i: r["topic"] for i, r in verdicts.items() if r["keep"]}
    job.log(f"  triage: kept {len(kept)} of {len(verdicts)}")
    by_topic: dict[str, int] = {}
    for topic in kept.values():
        by_topic[topic] = by_topic.get(topic, 0) + 1
    job.event("triage", kept=len(kept), dropped=len(verdicts) - len(kept),
              by_topic=dict(sorted(by_topic.items(), key=lambda kv: -kv[1])))
    return kept


def _run_batch(chat, system: str, batch: list[str], rows: dict) -> tuple[dict, str]:  # noqa: ANN001
    sids = short_ids(batch)
    lines = []
    for sid, item_id in sids.items():
        r = rows[item_id]
        summary = " ".join((r["abstract"] or "").split())[:600]
        lines.append(f"{sid} [{KIND_WORD.get(r['kind'], r['kind'])}] {r['title']}\n    {summary}")
    try:
        reply = chat.complete(system, "Items:\n\n" + "\n".join(lines),
                              max_tokens=40 * len(batch) + 200)
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc)
    out: dict[str, tuple[bool, str]] = {}
    for sid, rest in parse_lines(reply).items():
        if sid not in sids:
            continue
        words = rest.replace("|", " ").split()
        if not words or words[0].lower() not in ("keep", "drop"):
            continue
        keep = words[0].lower() == "keep"
        topic = words[1].strip(".,;:").lower() if keep and len(words) > 1 else ""
        out[sids[sid]] = (keep, topic)
    return out, ""
