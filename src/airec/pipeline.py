"""A digest run from start to finish.

    1. check    every model this run needs answers (fails in seconds, not hours)
    2. knowledge collect → embed → resurface → triage → score → select → read
    3. news     (when enabled) collect → group → write the news section
    4. write    article, markdown, History entry; mark items shown; move watermarks

Windows: papers and posts cover everything since the last finished run (the
first run looks back run.first_run_days). News covers the same span, capped at
news.max_days. Watermarks only move when a run finishes, so a failed run's
window is covered by the next one or by `rec resume`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from airec.config import Config
from airec.llm import LLMError
from airec.phases import collect as collect_phase
from airec.phases import embed as embed_phase
from airec.phases import news as news_phase
from airec.phases import read as read_phase
from airec.phases import resurface as resurface_phase
from airec.phases import score as score_phase
from airec.phases import select as select_phase
from airec.phases import triage as triage_phase
from airec.phases import write as write_phase
from airec.phases.common import Job, PhaseError
from airec.runlog import RunContext
from airec.store import Store

BACKLOG_DAYS = 21
PHASES = ["check", "collect", "triage", "score", "select", "read", "news", "write"]


@dataclass
class RunOptions:
    days: float | None = None       # override the knowledge window
    news: bool | None = None        # override settings.news.enabled
    resume: int | None = None       # run id to continue


def run_digest(cfg: Config, rc: RunContext, opts: RunOptions) -> int:
    store = Store(cfg.db_path)
    try:
        return _run(cfg, store, rc, opts)
    finally:
        store.close()


def _run(cfg: Config, store: Store, rc: RunContext, opts: RunOptions) -> int:
    now = time.time()
    if opts.resume:
        run = store.run(opts.resume)
        if run is None or run["kind"] != "run":
            raise PhaseError(f"no digest run {opts.resume}")
        if run["status"] == "done":
            raise PhaseError(f"run {opts.resume} already finished")
        run_id = run["id"]
        store.update_run(run_id, status="running", log_dir=cfg.rel(rc.dir))
        rc.log(f"resuming run {run_id}")
    else:
        stale = store.abandon_stale_runs(alive=set())
        if stale:
            rc.log(f"  marked {stale} unfinished earlier run(s) as abandoned")
        k_until = now
        last = store.get("knowledge_until")
        if opts.days:
            k_since = now - opts.days * 86400
        elif last:
            k_since = float(last)
        else:
            k_since = now - float(cfg.s("run", "first_run_days")) * 86400
        news_on = cfg.s("news", "enabled") if opts.news is None else opts.news
        n_since = n_until = None
        if news_on:
            cap = now - float(cfg.s("news", "max_days")) * 86400
            n_since = max(float(store.get("news_until") or cap), cap)
            n_until = now
        run_id = store.create_run(
            "run", cfg.rel(rc.dir), map_hash=cfg.map_hash(),
            topic_ids=[t["id"] for t in cfg.topics()], k_since=k_since, k_until=k_until,
            n_since=n_since, n_until=n_until, news=1 if news_on else 0)
    run = store.run(run_id)
    rc.update_meta(run_id=run_id)
    rc.event("window", run_id=run_id, resumed=bool(opts.resume), k_since=run["k_since"],
             k_until=run["k_until"], n_since=run["n_since"], n_until=run["n_until"],
             news=bool(run["news"]), map_hash=run["map_hash"],
             topics=len(json.loads(run["topic_ids"] or "[]")))
    job = Job(cfg, store, rc, run_id)
    done = set(store.phases_done(run_id))
    news_on = bool(run["news"])
    rc.log(f"run {run_id}: papers and posts from {_fmt(run['k_since'])} to "
           f"{_fmt(run['k_until'])}"
           + (f"; news from {_fmt(run['n_since'])}" if news_on else "; news off"))

    # 1. check ------------------------------------------------------------
    rc.event("check", status="running")
    needed = ["triage", "score", "note", "mention", "article"] + (["news"] if news_on else [])
    _check_models(job, needed)
    rc.event("check", status="done")

    # 2. knowledge ---------------------------------------------------------
    if "collect" not in done:
        rc.event("collect", status="running")
        ids = collect_phase.collect(job, "knowledge", run["k_since"], run["k_until"])
        store.add_run_items(run_id, ids, "candidate")
        embed_phase.embed_missing(job, ids)
        # Items earlier runs skipped at the triage limit compete again here,
        # until they are 21 days old.
        backlog = store.untriaged_recent(job.key("triage"), time.time() - BACKLOG_DAYS * 86400,
                                         exclude=set(ids))
        store.add_run_items(run_id, backlog, "backlog")
        if backlog:
            rc.log(f"  {len(backlog)} items carried over from earlier runs (not yet triaged)")
        back = resurface_phase.resurface(job, exclude=set(ids) | set(backlog))
        rc.event("collect", resurfaced=len(back), backlog=len(backlog))
        store.add_run_items(run_id, back, "resurfaced")
        embed_phase.embed_missing(job, back)
        store.phase_done(run_id, "collect")
        rc.event("collect", status="done", count=len(ids) + len(back))
    candidates = [r["item_id"] for r in store.run_items(run_id)
                  if r["role"] in ("candidate", "resurfaced", "backlog")]
    resurfaced = {r["item_id"] for r in store.run_items(run_id, "resurfaced")}
    embed_phase.embed_missing(job, candidates)

    rc.event("triage", status="running")
    kept = triage_phase.triage(job, candidates)
    rc.event("triage", status="done", count=len(kept))

    rc.event("score", status="running")
    scored = score_phase.score(job, kept)
    rc.event("score", status="done", count=len(scored))

    stats = _stats(run)
    if "select" in done and stats.get("selection"):
        sel = select_phase.from_json(stats["selection"])
    else:
        rc.event("select", status="running")
        sel = select_phase.select(job, scored, resurfaced)
        stats.update(selection=select_phase.to_json(sel), candidates=len(candidates),
                     kept=len(kept), scored=len(scored))
        store.update_run(run_id, stats=stats)
        store.phase_done(run_id, "select")
    rc.event("select", status="done", count=len(sel["notes"]) + len(sel["mentions"]))

    rc.event("read", status="running")
    read_phase.write_notes(job, sel["notes"])
    read_phase.write_mentions(job, sel["mentions"])
    rc.event("read", status="done", count=len(sel["notes"]))

    # 3. news ---------------------------------------------------------------
    news = None
    if news_on:
        rc.event("news", status="running")
        if "news_collect" not in done:
            ids = collect_phase.collect(job, "news", run["n_since"], run["n_until"])
            store.add_run_items(run_id, ids, "news")
            store.phase_done(run_id, "news_collect")
        news_ids = [r["item_id"] for r in store.run_items(run_id, "news")]
        news = news_phase.write_news(job, news_ids, run["n_since"], run["n_until"])
        rc.event("news", status="done", count=len(news["items"]))

    # 4. write --------------------------------------------------------------
    rc.event("write", status="running")
    notes, mentions = write_phase.build_refs(job, sel)
    article = write_phase.write_article(job, notes, mentions)
    data = write_phase.digest_data(job, run, notes, mentions, article, news)
    markdown = write_phase.render_markdown(data, run_id)
    paths = write_phase.save_markdown(job, markdown, data["date"])
    for r in notes:
        store.mark_shown(run_id, r["id"], "note", r["topic"])
    for r in mentions:
        store.mark_shown(run_id, r["id"], "mention", r["topic"])
    for item_id in (news or {}).get("items", []):
        store.mark_shown(run_id, item_id, "news", "")
    store.commit()
    store.update_run(run_id, digest=data, digest_path=cfg.rel(paths[0]) if paths else "",
                     status="done", finished_at=time.time())
    store.set("knowledge_until", run["k_until"])
    if news_on:
        store.set("news_until", run["n_until"])
    rc.event("write", status="done", paths=[str(p) for p in paths], notes=len(notes),
             mentions=len(mentions))
    for p in paths:
        rc.log(f"  → {p}")
    rc.log(f"done: {len(notes)} notes, {len(mentions)} mentions"
           + (f", {len(news['items'])} news items" if news else ""))
    return run_id


def _check_models(job: Job, phases: list[str]) -> None:
    missing = [f"{p} needs {job.cfg.missing_key(p)}" for p in phases if job.cfg.missing_key(p)]
    if missing:
        raise PhaseError("missing API keys (Settings → Keys): " + "; ".join(missing))
    seen: set[tuple[str, str]] = set()
    for phase in phases:
        ep = job.cfg.endpoint(phase)
        if (ep.base_url, ep.model) in seen:
            continue
        seen.add((ep.base_url, ep.model))
        try:
            latency = job.chat(phase).ping()
        except LLMError as exc:
            job.event("check", model=ep.model, provider=ep.provider, error=str(exc)[:200])
            raise PhaseError(str(exc)) from exc
        job.event("check", model=ep.model, provider=ep.provider, latency=latency)
        job.log(f"  {ep.model} ({ep.provider}) answers in {latency}s")
    job.topic_matrix()  # checks the embedding server too
    job.event("check", model=job.cfg.embedding().model, provider="embedding", latency=0)


def _stats(run: Any) -> dict[str, Any]:
    import json
    try:
        return json.loads(run["stats"] or "{}")
    except ValueError:
        return {}


def _fmt(epoch: float | None) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(epoch or 0).strftime("%Y-%m-%d %H:%M")
