"""History: every digest, every job's log and model calls, and single items."""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from airec.store import Store
from airec.web import jobs
from airec.web.app import cfg, render

router = APIRouter()


@router.get("/history", response_class=HTMLResponse)
def history(request: Request) -> HTMLResponse:
    c = cfg()
    store = Store(c.db_path)
    try:
        runs = []
        for r in store.runs("run", limit=200):
            digest = json.loads(r["digest"]) if r["digest"] else None
            job_name = (r["log_dir"] or "").rstrip("/").split("/")[-1]
            runs.append({"row": r, "digest": digest, "job": job_name})
    finally:
        store.close()
    others = [j for j in jobs.list_jobs(c, limit=80) if j["kind"] != "run"]
    return render(request, "history.html", cfg=c, nav="history", runs=runs, others=others)


def _load_run(run_id: int) -> tuple:
    c = cfg()
    store = Store(c.db_path)
    try:
        row = store.run(run_id)
        if row is None or row["kind"] != "run":
            raise HTTPException(404, "no such digest")
        digest = json.loads(row["digest"]) if row["digest"] else None
        ids = []
        if digest:
            ids = [r["id"] for s in digest["sections"] for r in s["notes"]]
        deep = {r["id"]: bool(r["deep_path"]) for r in store.items(ids)}
    finally:
        store.close()
    return c, row, digest, deep


@router.get("/history/{run_id}", response_class=HTMLResponse)
def digest_page(request: Request, run_id: int) -> HTMLResponse:
    c, row, digest, deep = _load_run(run_id)
    job_name = (row["log_dir"] or "").rstrip("/").split("/")[-1]
    if digest is None:
        return RedirectResponse(f"/jobs/{job_name}", status_code=303)
    cites = {r["n"]: {"title": r["title"], "topic": r["topic_name"], "kind": r["kind"],
                      "url": r["url"], "note": r["n"] <= digest["counts"]["notes"]}
             for s in digest["sections"] for r in s["notes"]}
    cites.update({r["n"]: {"title": r["title"], "topic": r["topic_name"], "kind": r["kind"],
                           "url": r["url"], "note": False} for r in digest["mentions"]})
    return render(request, "digest.html", cfg=c, nav="history", run=row, d=digest, deep=deep,
                  cites_json=json.dumps(cites, ensure_ascii=False), job_name=job_name)


@router.get("/history/{run_id}/markdown", response_class=PlainTextResponse)
def digest_markdown(run_id: int) -> PlainTextResponse:
    from airec.phases.write import render_markdown

    _c, _row, digest, _deep = _load_run(run_id)
    if digest is None:
        raise HTTPException(404, "this run has no digest")
    return PlainTextResponse(render_markdown(digest, run_id),
                             media_type="text/markdown; charset=utf-8")


@router.get("/jobs/{name}", response_class=HTMLResponse)
def job_page(request: Request, name: str) -> HTMLResponse:
    c = cfg()
    try:
        job = jobs.summary(c, name)
        calls = jobs.calls(c, name)
        full_log = jobs.tail(jobs.job_dir(c, name) / "run.log", 3000)
    except jobs.JobError as exc:
        raise HTTPException(404, str(exc)) from exc
    return render(request, "job.html", cfg=c, nav="history", job=job, calls=calls,
                  full_log=full_log)


@router.get("/item/{item_id:path}", response_class=HTMLResponse)
def item_page(request: Request, item_id: str, job: str = "", error: str = "") -> HTMLResponse:
    c = cfg()
    store = Store(c.db_path)
    try:
        row = store.item(item_id)
        if row is None:
            raise HTTPException(404, "not in the index")
        shown = store.q("SELECT s.run_id, s.tier, s.ts FROM shown s WHERE s.item_id=? "
                        "ORDER BY s.ts DESC", (item_id,))
    finally:
        store.close()
    deep = ""
    if row["deep_path"]:
        path = c.abs(row["deep_path"])
        if path.exists():
            deep = path.read_text(encoding="utf-8").split("\n", 4)[-1]
    running = None
    if job:
        try:
            running = jobs.summary(c, job)
        except jobs.JobError:
            running = None
    return render(request, "item.html", cfg=c, nav="history", item=row, shown=shown, deep=deep,
                  signals=json.loads(row["signals"] or "{}"), running=running, error=error,
                  deep_model=(c.models.get("phases") or {}).get("deep_read", {}).get("model", ""))


@router.post("/deep-read/{item_id:path}")
def start_deep_read(item_id: str) -> RedirectResponse:
    try:
        name = jobs.start(cfg(), "deepread", [item_id])
    except jobs.JobError as exc:
        return RedirectResponse(f"/item/{item_id}?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/item/{item_id}?job={name}", status_code=303)
