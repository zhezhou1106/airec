"""Run: start a digest, watch it, cancel or resume it; keep the index filled."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from airec.store import Store
from airec.web import jobs
from airec.web.app import cfg, render

router = APIRouter()


def _last_job(c) -> str | None:  # noqa: ANN001
    act = jobs.active(c)
    if act:
        return act["name"]
    listed = jobs.list_jobs(c, limit=1)
    return listed[0]["name"] if listed else None


@router.get("/run", response_class=HTMLResponse)
def run_page(request: Request, error: str = "") -> HTMLResponse:
    c = cfg()
    store = Store(c.db_path)
    try:
        last_until = store.get("knowledge_until")
        news_until = store.get("news_until")
        stats = store.stats()
        runs = store.runs("run", limit=1)
        resumable = runs[0] if runs and runs[0]["status"] in (
            "failed", "cancelled", "abandoned", "running") else None
    finally:
        store.close()
    active = jobs.active(c)
    if resumable is not None and active:
        resumable = None
    name = _last_job(c)
    return render(request, "run.html", cfg=c, nav="run", error=error,
                  last_until=last_until, news_until=news_until, stats=stats,
                  resumable=resumable, job=jobs.summary(c, name) if name else None,
                  news_default=bool(c.s("news", "enabled")),
                  news_days=c.s("news", "max_days"),
                  first_days=c.s("run", "first_run_days"),
                  backfill_months=c.s("index", "backfill_months"))


def _start(command: str, argv: list[str]) -> RedirectResponse:
    try:
        jobs.start(cfg(), command, argv)
    except jobs.JobError as exc:
        return RedirectResponse(f"/run?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/run", status_code=303)


@router.post("/run/start")
def start_run(window: str = Form("since"), days: float = Form(7),
              news: str = Form("")) -> RedirectResponse:
    argv = ["--news" if news else "--no-news"]
    if window == "days":
        argv += ["--days", str(max(0.5, min(days, 365)))]
    return _start("run", argv)


@router.post("/run/resume/{run_id}")
def resume_run(run_id: int) -> RedirectResponse:
    return _start("resume", [str(run_id)])


@router.post("/run/backfill")
def backfill(months: int = Form(12)) -> RedirectResponse:
    return _start("backfill", ["--months", str(max(1, min(months, 36)))])


@router.post("/jobs/{name}/cancel")
def cancel(request: Request, name: str) -> RedirectResponse:
    try:
        jobs.cancel(cfg(), name)
    except jobs.JobError:
        pass
    back = request.headers.get("referer") or "/run"
    return RedirectResponse(back, status_code=303)


@router.get("/jobs/{name}/panel", response_class=HTMLResponse)
def panel(request: Request, name: str) -> HTMLResponse:
    c = cfg()
    try:
        job = jobs.summary(c, name)
    except jobs.JobError:
        return HTMLResponse("")
    return render(request, "_job_panel.html", cfg=c, job=job)


@router.get("/pill", response_class=HTMLResponse)
def pill(request: Request) -> HTMLResponse:
    return render(request, "_pill.html")
