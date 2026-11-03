"""The airec app: read the weekly reports and manage paper directions.

A JSON API plus a React page (built from ../../../web into ./static), shown as a tab
inside my-hub or on its own. It binds to 127.0.0.1 and holds no state of its own:
reports, directions and the database in the airec home stay the source of truth, so
the CLI and the app never disagree.

Inside the hub the page looks like a hub page: it vendors the hub's design layer and
follows its style. HUB_API and HUB_TOKEN (set by the hub for services) let the server
read the hub's appearance for the first paint and open links through the hub.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from ruamel.yaml.error import YAMLError as RuamelYAMLError

from airec import __version__, pipeline, schedule
from airec.config import ConfigError, Settings, load_settings
from airec.llm import LlmError
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
from airec.web import config_edit

_STATIC = Path(__file__).parent / "static"
_WEEK = re.compile(r"^\d{4}-W\d{2}$")
_ASSET = re.compile(r"^[\w.-]+\.(png|jpe?g|webp|gif)$")
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\((?:\./)?assets/([\w.-]+)\)")
_SUBTITLE = re.compile(r"^\s*# .+\n+\*([^*\n]+)\*[ \t]*$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_DARK_STYLES = {"terminal", "mocha", "macchiato", "frappe"}
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_MAX_DESCRIPTION = 6000
_PREVIEW_DAYS = 14
_CONFIG_VALUES = Body(embed=True)
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ReportView:
    """Reports and run state, read fresh for each request."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def weeks(self) -> list[str]:
        return sorted((p.stem for p in self.settings.paths.reports.glob("*.md")), reverse=True)

    def section_warnings(self, week: str) -> list[str] | None:
        """The run notes the database kept for `week`, or None if it has no sections for it."""
        keys = self.settings.app.sections
        found = [o for o in (self.store.get_state(k, f"output:{week}") for k in keys) if o]
        if not found:
            return None
        return [w for output in found for w in output["warnings"]]

    def report_path(self, week: str) -> Path:
        return self.settings.paths.reports / f"{week}.md"

    def text(self, week: str) -> str:
        path = self.report_path(week)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def cover(self, week: str, text: str) -> str | None:
        """The image the report opens with, else assets/<week>.<ext>, if it is on disk."""
        assets = self.settings.paths.reports / "assets"
        names = [m.group(1) for m in _IMAGE_REF.finditer(text)][:1]
        names += [f"{week}.{ext}" for ext in ("png", "jpg", "jpeg", "webp")]
        for name in names:
            if _ASSET.match(name) and (assets / name).is_file():
                return f"/api/assets/{name}"
        return None

    def summary(self, week: str) -> dict[str, Any]:
        """One row of the weeks gallery, read from the report file alone (cheap, no database)."""
        text = self.text(week)
        _, body = _split_front_matter(text)
        window = _SUBTITLE.search(body)
        parts = _sections_of(body)
        glance = next((lines for title, lines in parts if "glance" in title.lower()), [])
        return {
            "week": week,
            "window": window.group(1).strip() if window else None,
            "highlights": [_plain(x[2:]) for x in glance if x.startswith("- ")][:3],
            "sections": [
                {"title": _plain(title), "count": sum(x.startswith(("- ", "* ")) for x in lines)}
                for title, lines in parts
                if "glance" not in title.lower()
            ],
            "cover": self.cover(week, text),
        }

    def report(self, week: str) -> dict[str, Any]:
        """What the reading view shows for one week: the file, and its run notes."""
        text = self.text(week)
        warnings = self.section_warnings(week)
        return {
            "week": week,
            "warnings": _run_notes(text) if warnings is None else warnings,
            "markdown": text,
            "file": str(self.report_path(week)),
        }

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

    @app.get("/api/overview")
    def overview():
        """What the page frame needs: the weeks on disk and the schedule."""
        reports = view()
        try:
            weeks, plan = reports.weeks(), reports.schedule_text()
        finally:
            reports.store.close()
        return {
            "title": settings.app.report.title,
            "weeks": weeks,
            "schedule": plan,
            "scheduled": plan != "Schedule is off",
        }

    @app.get("/api/weeks")
    def list_weeks():
        """The weeks gallery in one request: every report, newest first, with excerpt and cover."""
        reports = view()
        try:
            weeks = [reports.summary(week) for week in reports.weeks()]
        finally:
            reports.store.close()
        for i, week in enumerate(weeks):
            week["latest"] = i == 0
        return {"weeks": weeks}

    @app.get("/api/weeks/{week}")
    def week_report(week: str):
        reports = view()
        try:
            if not _WEEK.match(week) or week not in reports.weeks():
                raise HTTPException(404, "No report for that week.")
            return reports.report(week)
        finally:
            reports.store.close()

    @app.get("/api/assets/{name}")
    def report_asset(name: str):
        """Images next to the reports (the weekly cover), by bare file name only."""
        path = settings.paths.reports / "assets" / name
        if not _ASSET.match(name) or not path.is_file():
            raise HTTPException(404, "No such image.")
        return FileResponse(path)

    def config_view() -> dict[str, Any]:
        try:
            data = config_edit.snapshot(settings.paths)
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Could not read {settings.paths.config}: {exc}") from None
        reports = view()
        try:
            data["schedule_text"] = reports.schedule_text()
        finally:
            reports.store.close()
        data["scheduled"] = data["schedule_text"] != "Schedule is off"
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
                settings = load_settings(settings.paths)
            except ConfigError as exc:  # validated above, so this means the file moved under us
                raise HTTPException(500, str(exc)) from None
        return {"changed": changed, "config": config_view()}

    @app.get("/api/directions")
    def list_directions():
        reports = view()
        try:
            directions = reports.directions()
        finally:
            reports.store.close()
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
        return appearance()

    @app.post("/api/open")
    def open_url(url: str = Body(embed=True)):
        """Open a link in the browser: target=_blank does nothing inside the hub's window."""
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "Only http(s) links open.")
        if _hub_api():
            try:
                return _hub("POST", "/api/open-url", json={"url": url})
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
        look = appearance()
        attrs = f'data-style="{look["style"]}" data-theme="{look["theme"]}"'
        if look["term"]:
            attrs += " data-term"
        return page.read_text(encoding="utf-8").replace("<html", f"<html {attrs}", 1)

    return app


def _split_front_matter(text: str) -> tuple[str, str]:
    match = re.match(r"---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    return (match.group(1), text[match.end() :]) if match else ("", text)


def _sections_of(body: str) -> list[tuple[str, list[str]]]:
    """The "## " sections of a report body (outside code fences), each with its lines."""
    found: list[tuple[str, list[str]]] = []
    fenced = False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            found.append((line[3:].strip(), []))
        elif not fenced and line.strip() == "---" and found:
            break  # the footer (model calls, run notes) is not a section
        elif found:
            found[-1][1].append(line)
    return found


def _run_notes(text: str) -> list[str]:
    """The notes in the report's footer, for a week the database has no sections for."""
    match = re.search(r"<summary>Run notes</summary>(.*?)</details>", text, re.DOTALL)
    if not match:
        return []
    return [x[2:].strip() for x in match.group(1).splitlines() if x.startswith("- ")]


def _plain(markdown: str) -> str:
    """A markdown line as text: links keep their label, emphasis and code marks go."""
    return re.sub(r"[*_`]", "", _LINK.sub(r"\1", markdown)).strip()


def _hub_api() -> str:
    return os.environ.get("HUB_API", "").rstrip("/")


def _hub(method: str, path: str, **kwargs: Any) -> Any:
    token = os.environ.get("HUB_TOKEN", "")
    reply = httpx.request(
        method, f"{_hub_api()}{path}", headers={"X-Hub-Token": token}, timeout=10, **kwargs
    )
    reply.raise_for_status()
    return reply.json()


def appearance() -> dict[str, Any]:
    """The hub's current style; live changes arrive in the page by postMessage."""
    fallback = {"style": "terminal", "theme": "dark", "term": True}
    if not _hub_api():
        return fallback
    try:
        look = _hub("GET", "/api/settings")["settings"]["appearance"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return fallback
    style = str(look.get("style") or "terminal")
    if not re.fullmatch(r"[a-z]+", style):
        return fallback
    if style == "latte":
        theme = "light"
    elif style in _DARK_STYLES:
        theme = "dark"
    else:
        theme = "dark" if look.get("theme") == "dark" else "light"
    return {"style": style, "theme": theme, "term": style != "classic"}


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
