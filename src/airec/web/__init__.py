"""The airec app: read the weekly reports and manage paper directions.

A small server-rendered page, shown as a tab inside my-hub or on its own. It binds
to 127.0.0.1 and holds no state of its own: reports, directions and the database
in the airec home stay the source of truth, so the CLI and the app never disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from airec import __version__, pipeline, schedule
from airec.config import Settings
from airec.sections import available
from airec.sections.base import Window
from airec.sections.papers import Papers
from airec.sections.papers.directions import (
    Direction,
    is_valid_id,
    load_directions,
    remove_direction,
    save_direction,
    set_paused,
    unique_id,
)
from airec.store import Store

_HERE = Path(__file__).parent
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_MAX_DESCRIPTION = 6000
_PREVIEW_DAYS = 14
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _markdown() -> MarkdownIt:
    """Report text comes from models reading the web, so raw HTML stays off."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": False})

    def open_in_new_tab(renderer, tokens, index, options, env):
        tokens[index].attrSet("target", "_blank")
        tokens[index].attrSet("rel", "noopener")
        return renderer.renderToken(tokens, index, options, env)

    md.add_render_rule("link_open", open_in_new_tab)
    return md


class ReportView:
    """Reports and run state, read fresh for each request."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.md = _markdown()

    def weeks(self) -> list[str]:
        return sorted((p.stem for p in self.settings.paths.reports.glob("*.md")), reverse=True)

    def sections(self, week: str) -> list[dict[str, Any]]:
        registry = available()
        found = []
        for key in self.settings.app.sections:
            output = self.store.get_state(key, f"output:{week}")
            if not output:
                continue
            found.append(
                {
                    "key": key,
                    "title": registry[key].title if key in registry else key,
                    "html": self.md.render(output["markdown"]),
                    "highlights": [self.md.renderInline(h) for h in output["highlights"]],
                    "stats": output["stats"],
                    "warnings": output["warnings"],
                    "window": Window(
                        datetime.fromisoformat(output["since"]),
                        datetime.fromisoformat(output["until"]),
                    ).label,
                }
            )
        return found

    def raw_report(self, week: str) -> str:
        """A report the database has no sections for: show the file itself."""
        path = self.settings.paths.reports / f"{week}.md"
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[-1] if text.startswith("---") else text
        return self.md.render(body.split("\n---\n")[0])

    def schedule_text(self) -> str:
        plan = self.settings.app.schedule
        if not schedule.is_installed():
            return "Schedule is off"
        return f"Every {_WEEKDAYS[plan.weekday - 1]} from {plan.hour}:00"

    def directions(self) -> list[dict[str, Any]]:
        folder = self.settings.paths.directions
        return [self._direction(d) for d in load_directions(folder, include_paused=True)]

    def _direction(self, direction: Direction) -> dict[str, Any]:
        stored = self.store.get_state(Papers.key, f"plan:{direction.id}")
        current = bool(stored) and stored["fingerprint"] == direction.fingerprint
        plan = stored["plan"] if current else None
        return {
            "id": direction.id,
            "description": direction.description,
            "paused": direction.paused,
            "title": plan["title"] if plan else direction.id.replace("-", " ").capitalize(),
            "plan": plan,
        }


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="airec", version=__version__, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    templates = Jinja2Templates(directory=_HERE / "templates")
    folder = settings.paths.directions

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """Block DNS rebinding and cross-site writes; the hub shows this page in an iframe."""
        host = request.headers.get("host", "")
        if host.split(":")[0] not in _LOCAL_HOSTS:
            return JSONResponse({"detail": "local access only"}, status_code=403)
        if request.method != "GET":
            origin = request.headers.get("origin")
            if origin is None or urlparse(origin).netloc != host:
                return JSONResponse({"detail": "same origin only"}, status_code=403)
        return await call_next(request)

    def view() -> ReportView:
        return ReportView(settings, Store(settings.paths.db))

    @app.get("/", response_class=HTMLResponse)
    def report_page(request: Request, week: str | None = None):
        reports = view()
        try:
            weeks = reports.weeks()
            week = week if week in weeks else (weeks[0] if weeks else None)
            sections = reports.sections(week) if week else []
            context = {
                "page": "report",
                "title": settings.app.report.title,
                "week": week,
                "weeks": weeks,
                "sections": sections,
                "highlights": [h for section in sections for h in section["highlights"]],
                "warnings": [w for section in sections for w in section["warnings"]],
                "raw": "" if sections or not week else reports.raw_report(week),
                "schedule": reports.schedule_text(),
            }
        finally:
            reports.store.close()
        return templates.TemplateResponse(request, "report.html", context)

    @app.get("/directions", response_class=HTMLResponse)
    def directions_page(request: Request):
        reports = view()
        try:
            context = {
                "page": "directions",
                "title": settings.app.report.title,
                "directions": reports.directions(),
                "schedule": reports.schedule_text(),
                "papers": _papers_config(settings),
            }
        finally:
            reports.store.close()
        return templates.TemplateResponse(request, "directions.html", context)

    @app.post("/api/directions")
    def add_direction(description: str = Body(embed=True), name: str = Body("", embed=True)):
        text = _clean_description(description)
        direction_id = unique_id(folder, name.strip() or " ".join(text.split()[:6]))
        save_direction(folder, direction_id, text)
        return {"id": direction_id}

    @app.put("/api/directions/{direction_id}")
    def edit_direction(direction_id: str, description: str = Body(embed=True)):
        _require(direction_id, folder)
        save_direction(folder, direction_id, _clean_description(description))
        return {"id": direction_id}

    @app.post("/api/directions/{direction_id}/paused")
    def pause_direction(direction_id: str, paused: bool = Body(embed=True)):
        _require(direction_id, folder)
        set_paused(folder, direction_id, paused)
        return {"id": direction_id, "paused": paused}

    @app.delete("/api/directions/{direction_id}")
    def delete_direction(direction_id: str):
        _require(direction_id, folder)
        remove_direction(folder, direction_id)
        return {"id": direction_id}

    @app.post("/api/directions/{direction_id}/preview")
    def preview_direction(direction_id: str):
        """What this direction would catch right now, without recording anything."""
        direction = _require(direction_id, folder)
        return run_preview(settings, direction)

    return app


def run_preview(settings: Settings, direction: Direction) -> dict[str, Any]:
    now = datetime.now(UTC)
    window = Window(now - timedelta(days=_PREVIEW_DAYS), now)
    with pipeline.database(settings.paths.db, scratch=True) as store:
        ctx = pipeline.build_context(settings, store, pipeline.iso_week(now))
        config = _papers_config(settings)
        scan = Papers(ctx, config).scan(direction, window)
    return {
        "title": scan.plan.title,
        "phrases": scan.plan.phrases,
        "categories": scan.plan.categories,
        "candidates": scan.candidates,
        "relevant": scan.relevant,
        "days": _PREVIEW_DAYS,
        "papers": [
            {
                "title": item.paper.title,
                "url": item.paper.url,
                "score": item.score,
                "reason": item.reason,
            }
            for item in scan.accepted
        ],
    }


def _papers_config(settings: Settings) -> Papers.Config:
    block = dict(settings.app.sections.get(Papers.key) or {})
    block.pop("enabled", None)
    return Papers.Config.model_validate(block)


def _clean_description(description: str) -> str:
    text = description.strip()
    if len(text) < 20:
        raise HTTPException(422, "Describe the direction in at least a sentence.")
    if len(text) > _MAX_DESCRIPTION:
        raise HTTPException(422, f"Keep the description under {_MAX_DESCRIPTION} characters.")
    return text


def _require(direction_id: str, folder: Path) -> Direction:
    if is_valid_id(direction_id):
        for direction in load_directions(folder, include_paused=True):
            if direction.id == direction_id:
                return direction
    raise HTTPException(404, "No such direction.")
