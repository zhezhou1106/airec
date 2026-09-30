"""The web app: Run, History, Settings. Local only, no login."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup

from airec.config import Config
from airec.web import jobs

HERE = Path(__file__).parent
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def md(text: str | None) -> Markup:
    return Markup(_md.render(text or ""))


_CITE = re.compile(r"\[(\d{1,3})\]")


def cites(html: Markup) -> Markup:
    """[3] in rendered article text becomes a link to note 3 (and a margin note)."""
    return Markup(_CITE.sub(r'<a class="cite" href="#ref-\1" data-ref="\1">\1</a>', str(html)))


def when(epoch: float | None, fmt: str = "%b %-d, %H:%M") -> str:
    return datetime.fromtimestamp(epoch).strftime(fmt) if epoch else ""


def templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=str(HERE / "templates"))
    t.env.filters["md"] = md
    t.env.filters["when"] = when
    t.env.filters["cites"] = cites
    return t


def cfg() -> Config:
    """Fresh on every request, so edits made anywhere show up at once."""
    return Config()


def render(request: Request, name: str, **ctx):  # noqa: ANN201
    c = ctx.pop("cfg", None) or cfg()
    ctx.setdefault("active_job", jobs.active(c))
    ctx.setdefault("nav", name.split("/")[0].split(".")[0].split("_")[0])
    return request.app.state.templates.TemplateResponse(request, name, ctx)


def create_app() -> FastAPI:
    app = FastAPI(title="airec", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.templates = templates()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    from airec.web.routes import history, run, settings

    app.include_router(run.router)
    app.include_router(history.router)
    app.include_router(settings.router)

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/run", status_code=303)

    return app
