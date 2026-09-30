"""Select: which scored items get a note, which get a one-line mention.

Rules, in order:
  - An item shown before comes back only with a reason: something changed
    (code released, trending somewhere new) or it matches a topic that was
    not in the map when it was shown.
  - A share of the notes (digest.explore_share) is kept for items outside the
    map or under 'explore' topics, so the digest never only confirms the map.
  - At most digest.max_notes_per_topic notes per topic; near-duplicates skipped.
  - Notes need digest.min_score_note, mentions digest.min_score_mention.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from phases.common import Job

NOVELTY_ADJUST = {"new": 0.2, "advance": 0.0, "repeat": -0.6}


@dataclass
class Pick:
    id: str
    topic: str
    score: float
    novelty: str
    reason: str
    back: str = ""       # why a previously shown item is back, or "" when it is new
    resurfaced: bool = False


def select(job: Job, scored: dict[str, dict], resurfaced: set[str]) -> dict[str, list[Pick]]:
    d = job.cfg.settings["digest"]
    n_notes, n_mentions = int(d["notes"]), int(d["mentions"])
    min_note, min_mention = float(d["min_score_note"]), float(d["min_score_mention"])
    per_topic = int(d["max_notes_per_topic"])
    n_explore = round(n_notes * float(d["explore_share"]))
    explore_topics = {t["id"] for t in job.cfg.topics() if t.get("priority") == "explore"}
    explore_topics.add("other")

    ids = list(scored)
    rows = {r["id"]: r for r in job.store.items(ids)}
    last = job.store.last_shown(ids)
    vecs = job.store.embeddings(ids)

    pool: list[Pick] = []
    for item_id, s in scored.items():
        back = ""
        if item_id in last:
            back = _reason_to_return(rows[item_id], last[item_id], s["topic"], job)
            if not back:
                continue
        adj = s["score"] + NOVELTY_ADJUST.get(s["novelty"], 0.0)
        pool.append(Pick(item_id, s["topic"], round(adj, 2), s["novelty"], s["reason"] or "",
                         back, item_id in resurfaced))
    pool.sort(key=lambda p: -p.score)

    notes: list[Pick] = []
    counts: dict[str, int] = {}

    def fits(p: Pick) -> bool:
        if counts.get(p.topic, 0) >= per_topic:
            return False
        v = vecs.get(p.id)
        return v is None or all(
            float(v @ vecs[o.id]) <= 0.9 for o in notes if o.id in vecs)

    def take(p: Pick) -> None:
        notes.append(p)
        counts[p.topic] = counts.get(p.topic, 0) + 1

    for p in pool:  # the exploration share first, at a slightly lower bar
        if len(notes) >= n_explore:
            break
        if p.topic in explore_topics and p.score >= min_note - 0.5 and fits(p):
            take(p)
    chosen = {p.id for p in notes}
    for p in pool:
        if len(notes) >= n_notes:
            break
        if p.id not in chosen and p.score >= min_note and fits(p):
            take(p)
            chosen.add(p.id)
    mentions = [p for p in pool if p.id not in chosen and p.score >= min_mention][:n_mentions]
    job.log(f"  selected {len(notes)} notes and {len(mentions)} mentions "
            f"from {len(pool)} eligible items")
    names = {t["id"]: t.get("name", t["id"]) for t in job.cfg.topics()}
    names["other"] = "Outside your map"
    job.event("select", eligible=len(pool), mentions=len(mentions),
              repeats_held_back=len(scored) - len(pool),
              notes=[{"title": rows[p.id]["title"], "topic": names.get(p.topic, p.topic),
                      "score": p.score, "back": p.back or ("from your index" if p.resurfaced
                                                          else "")} for p in notes])
    return {"notes": notes, "mentions": mentions}


def _reason_to_return(row, shown, topic: str, job: Job) -> str:  # noqa: ANN001
    if row["changed_at"] and row["changed_at"] > shown["ts"]:
        return row["change_note"] or "updated since"
    old_topics = set(json.loads(shown["topic_ids"] or "[]"))
    if topic != "other" and old_topics and topic not in old_topics:
        name = next((t.get("name", topic) for t in job.cfg.topics() if t["id"] == topic), topic)
        return f"matches your new topic “{name}”"
    return ""


def to_json(sel: dict[str, list[Pick]]) -> dict[str, list[dict]]:
    return {k: [asdict(p) for p in v] for k, v in sel.items()}


def from_json(data: dict[str, list[dict]]) -> dict[str, list[Pick]]:
    return {k: [Pick(**p) for p in data.get(k, [])] for k in ("notes", "mentions")}
