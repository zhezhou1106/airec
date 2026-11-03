"""The airec app: read the weekly reports and manage paper directions.

A JSON API plus a React page (built from ../../../web into ./static), shown as a tab
inside my-hub or on its own. It binds to 127.0.0.1 and holds no state of its own:
reports, directions and the database in the airec home stay the source of truth, so
the CLI and the app never disagree. This module holds the routes; reading reports is
in `reports`, talking to the hub in `hub`.

Inside the hub the page looks like a hub page: it vendors the hub's design layer and
follows its style.
"""

from __future__ import annotations

import re
import subprocess
import sys
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from ruamel.yaml.error import YAMLError as RuamelYAMLError

from airec import __version__, pipeline
from airec.config import ConfigError, Settings, load_settings
from airec.llm import LlmError
from airec.sections import validate_block
from airec.sections.base import SectionError, Window
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
from airec.web import config_edit, hub
from airec.web.hub import appearance
from airec.web.reports import ASSET, ReportView

__all__ = ["ReportView", "appearance", "create_app", "run_preview"]

_STATIC = Path(__file__).parent / "static"
_WEEK = re.compile(r"^\d{4}-W\d{2}$")
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_MAX_DESCRIPTION = 6000
_PREVIEW_DAYS = 14
_CONFIG_VALUES = Body(embed=True)


def create_app(settings: Settings) -> FastAPI:
    reports = ReportView(settings, Store(settings.paths.db, check_same_thread=False))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        reports.store.close()

    app = FastAPI(
        title="airec", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )
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

    @app.get("/api/overview")
    def overview():
        """What the page frame needs: the weeks on disk and the schedule."""
        scheduled, plan = reports.schedule()
        return {
            "title": settings.app.report.title,
            "weeks": reports.weeks(),
            "schedule": plan,
            "scheduled": scheduled,
        }

    @app.get("/api/weeks")
    def list_weeks():
        """The weeks gallery in one request: every report, newest first, with excerpt and cover."""
        weeks = [reports.summary(week) for week in reports.weeks()]
        for i, week in enumerate(weeks):
            week["latest"] = i == 0
        return {"weeks": weeks}

    @app.get("/api/weeks/{week}")
    def week_report(week: str):
        if not _WEEK.match(week) or week not in reports.weeks():
            raise HTTPException(404, "No report for that week.")
        return reports.report(week)

    @app.get("/api/assets/{name}")
    def report_asset(name: str):
        """Images next to the reports (the weekly cover), by bare file name only."""
        path = settings.paths.reports / "assets" / name
        if not ASSET.match(name) or not path.is_file():
            raise HTTPException(404, "No such image.")
        return FileResponse(path)

    def config_view() -> dict[str, Any]:
        try:
            data = config_edit.snapshot(settings.paths)
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Could not read {settings.paths.config}: {exc}") from None
        data["scheduled"], data["schedule_text"] = reports.schedule()
        return data

    @app.get("/api/config")
    def get_config():
        """config.yaml as the Configuration tabs edit it. API keys are never included."""
        return config_view()

    @app.put("/api/config/{part}")
    def put_config(part: str, values: dict[str, Any] = _CONFIG_VALUES):
        """Save one tab: only changed values are written, comments and layout stay as they were.
        The schedule needs no reinstall: the launchd agent wakes hourly and reads it each time."""
        nonlocal settings
        if part not in {*config_edit.FIELDS, "sections", "models"}:
            raise HTTPException(404, "No such part of the configuration.")
        try:
            changed = config_edit.save(settings.paths, part, values)
        except config_edit.Invalid as exc:
            return JSONResponse({"detail": str(exc), "errors": exc.errors}, status_code=422)
        except (ValidationError, yaml.YAMLError, RuamelYAMLError) as exc:
            # The file on disk is already invalid, so the current values cannot be read.
            detail = f"{settings.paths.config} is invalid; fix it by hand first: {exc}"
            raise HTTPException(422, detail) from None
        if changed:
            try:
                settings = reports.settings = load_settings(settings.paths)
            except ConfigError as exc:  # validated above, so this means the file moved under us
                raise HTTPException(500, str(exc)) from None
        return {"changed": changed, "config": config_view()}

    @app.get("/api/directions")
    def list_directions():
        directions = reports.directions()
        papers = _papers_config(settings)
        return {
            "directions": directions,
            "papers": {
                "max_papers": papers.max_papers,
                "min_score": papers.min_score,
                "quality_rules": papers.quality_rules,
            },
        }

    @app.get("/api/appearance")
    def get_appearance():
        return hub.appearance()

    @app.post("/api/open")
    def open_url(url: str = Body(embed=True)):
        """Open a link in the browser: target=_blank does nothing inside the hub's window."""
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "Only http(s) links open.")
        if hub.api_url():
            try:
                return hub.request("POST", "/api/open-url", json={"url": url})
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"The hub did not open the link: {exc}") from None
        webbrowser.open(url)
        return {"ok": True}

    @app.post("/api/reveal")
    def reveal(
        week: str | None = Body(None, embed=True), folder: str = Body("reports", embed=True)
    ):
        """Show a week's report file, or the reports or directions folder, in Finder."""
        folders = {"reports": settings.paths.reports, "directions": settings.paths.directions}
        if folder not in folders:
            raise HTTPException(400, "Unknown folder.")
        target = folders[folder]
        if week is not None:
            target = settings.paths.reports
            if not _WEEK.match(week) or not (target / f"{week}.md").is_file():
                raise HTTPException(404, "No report for that week.")
            target = target / f"{week}.md"
        if sys.platform == "darwin":
            args = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
        else:
            args = ["xdg-open", str(target.parent if target.is_file() else target)]
        subprocess.run(args, check=False)
        return {"ok": True}

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

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

    if (_STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/directions", response_class=HTMLResponse, include_in_schema=False)
    def index():
        """The React page, with the hub's style already on <html> so the first paint matches."""
        page = _STATIC / "index.html"
        if not page.is_file():
            return HTMLResponse(
                "<p>The app is not built. Run <code>npm --prefix web ci && "
                "npm --prefix web run build</code> in the airec repo.</p>",
                status_code=503,
            )
        look = hub.appearance()
        attrs = f'data-style="{look["style"]}" data-theme="{look["theme"]}"'
        if look["term"]:
            attrs += " data-term"
        return page.read_text(encoding="utf-8").replace("<html", f"<html {attrs}", 1)

    return app


def run_preview(settings: Settings, direction: Direction) -> dict[str, Any]:
    now = datetime.now(UTC)
    window = Window(now - timedelta(days=_PREVIEW_DAYS), now)
    try:
        with pipeline.database(settings.paths.db, scratch=True) as store:
            ctx = pipeline.build_context(settings, store, pipeline.iso_week(now))
            config = _papers_config(settings)
            scan = Papers(ctx, config).preview(direction, window)
    except (LlmError, SectionError, ConfigError) as exc:
        raise HTTPException(502, str(exc)) from None
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
    """The papers settings, whether or not the section is enabled."""
    _, config = validate_block(Papers.key, settings.app.sections.get(Papers.key))
    return cast(Papers.Config, config)


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
