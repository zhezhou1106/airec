"""Score: a careful 0-10 judgement of each kept item, with a one-line reason.

The model sees the summary, the signals (stars, upvotes, code), and the most
similar items you were already shown, so it can tell a repeat from an advance.
Scores are cached per (map version, model).
"""
from __future__ import annotations

import re
from datetime import datetime

import numpy as np

from airec import prompts
from airec.phases.common import (KIND_WORD, Job, PhaseError, batches, parse_lines, short_ids,
                                 signals_line)

NOVELTY = {"new", "advance", "repeat"}
NUM = re.compile(r"\d+(\.\d+)?")


def score(job: Job, kept: dict[str, str]) -> dict[str, dict]:
    """{item_id: {score, novelty, reason, topic}} for every kept item."""
    chat = job.chat("score")
    key = job.key("score")
    ids = list(kept)
    cached = job.store.scored(key, ids)
    todo = [i for i in ids if i not in cached]
    job.log(f"  score: {len(cached)} already done, {len(todo)} to do")
    job.event("score", cached=len(cached), todo=len(todo), model=chat.model, done=0,
              total=len(todo))

    system = prompts.render("score", map=job.map_block())
    rows = {r["id"]: r for r in job.store.items(todo)}
    shown_rows, shown_mat = job.store.shown_embeddings()
    vecs = job.store.embeddings(todo)
    names = {t["id"]: t.get("name", t["id"]) for t in job.cfg.topics()}
    failed = 0
    all_batches = batches(todo, int(job.cfg.s("run", "score_batch")))
    for n, batch in enumerate(all_batches, 1):
        sids = short_ids(batch)
        blocks = []
        for sid, item_id in sids.items():
            r = rows[item_id]
            topic = names.get(kept[item_id], "outside the listed topics")
            block = [f"{sid} [{KIND_WORD.get(r['kind'], r['kind'])} · topic: {topic}] {r['title']}",
                     f"    Summary: {' '.join((r['abstract'] or '').split())[:1500]}"]
            sig = signals_line(r)
            if sig:
                block.append(f"    Signals: {sig}")
            related = _related(vecs.get(item_id), shown_rows, shown_mat)
            if related:
                block.append(f"    Already shown: {related}")
            blocks.append("\n".join(block))
        try:
            reply = chat.complete(system, "Items:\n\n" + "\n\n".join(blocks),
                                  max_tokens=80 * len(batch) + 200)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            job.log(f"[warn] score batch {n} failed: {exc}")
            if failed >= 3 and failed > n // 2:
                raise PhaseError(f"scoring keeps failing ({exc}); is the model running?") from exc
            continue
        for sid, rest in parse_lines(reply).items():
            if sid not in sids:
                continue
            parts = [p.strip() for p in rest.split("|")]
            m = NUM.search(parts[0]) if parts else None
            if not m:
                continue
            value = max(0.0, min(10.0, float(m.group(0))))
            novelty = parts[1].lower() if len(parts) > 1 else ""
            novelty = novelty if novelty in NOVELTY else "advance"
            reason = " | ".join(parts[2:])
            job.store.set_score(key, sids[sid], value, novelty, reason[:300], kept[sids[sid]])
        job.store.commit()
        job.event("score", done=min(n * len(batch), len(todo)), total=len(todo))

    out = {}
    for item_id, r in job.store.scored(key, ids).items():
        out[item_id] = {"score": r["score"], "novelty": r["novelty"], "reason": r["reason"],
                        "topic": kept[item_id]}
    job.log(f"  score: {len(out)} scored, {sum(v['score'] >= 7 for v in out.values())} at 7+")
    bands = {"8+": 0, "7–8": 0, "6–7": 0, "under 6": 0}
    for v in out.values():
        s = v["score"]
        bands["8+" if s >= 8 else "7–8" if s >= 7 else "6–7" if s >= 6 else "under 6"] += 1
    titles = {r["id"]: r["title"] for r in job.store.items(
        sorted(out, key=lambda i: -out[i]["score"])[:8])}
    top = [{"title": titles[i], "score": out[i]["score"], "reason": out[i]["reason"]}
           for i in titles]
    job.event("score", bands=bands, top=top)
    return out


def _related(vec, rows, mat) -> str:  # noqa: ANN001
    if vec is None or mat is None:
        return ""
    sims = mat @ vec
    out = []
    for i in np.argsort(-sims)[:2]:
        if sims[i] < 0.6:
            break
        when = datetime.fromtimestamp(rows[i]["ts"]).strftime("%Y-%m-%d")
        out.append(f"\"{rows[i]['title']}\" ({when})")
    return "; ".join(out)
