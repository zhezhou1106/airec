"""Offline test kit: a temporary airec home, a fake web, and a scripted model."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
import yaml

from airec.config import DEFAULTS, Paths, Settings, load_settings
from airec.llm import Llm
from airec.pipeline import build_context, iso_week
from airec.sections.base import Context
from airec.store import Store

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)  # a Monday, ISO week 2026-W45
Handler = Callable[[httpx.Request], httpx.Response]


class FakeWeb(httpx.MockTransport):
    """Routes requests to handlers by URL substring; anything unrouted is a 404."""

    def __init__(self) -> None:
        self.routes: dict[str, Handler] = {}
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def route(self, fragment: str, handler: Handler | dict | list | str) -> None:
        if callable(handler):
            self.routes[fragment] = handler
        elif isinstance(handler, str):
            self.routes[fragment] = lambda _r: httpx.Response(200, text=handler)
        else:
            self.routes[fragment] = lambda _r: httpx.Response(200, json=handler)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for fragment, handler in self.routes.items():
            if fragment in url:
                return handler(request)
        return httpx.Response(404, json={"message": "not routed"})


class ScriptedLlm(Llm):
    """Answers by a function of (role, system, user), counting the calls."""

    def __init__(self, settings: Settings, answer: Callable[[str, str, str], object]):
        super().__init__(settings.app.llm, settings.secrets)
        self.answer = answer
        self.prompts: list[tuple[str, str, str]] = []

    def _complete(self, role: str, system: str, user: str, *, json_mode: bool) -> str:
        self.prompts.append((role, system, user))
        self.usage.calls += 1
        reply = self.answer(role, system, user)
        return reply if isinstance(reply, str) else json.dumps(reply)


def write_config(paths: Paths, **sections: object) -> None:
    raw = yaml.safe_load((DEFAULTS / "config.yaml").read_text())
    raw["report"]["notify"] = False
    if sections:
        raw["sections"] = sections
    paths.config.write_text(yaml.safe_dump(raw, sort_keys=False))


@pytest.fixture
def paths(tmp_path) -> Paths:
    home = Paths(tmp_path / "home")
    for folder in (home.home, home.directions, home.reports, home.logs):
        folder.mkdir(parents=True, exist_ok=True)
    write_config(home)
    return home


@pytest.fixture
def settings(paths) -> Settings:
    return load_settings(paths)


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def store():
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def make_context(settings, store, web) -> Callable[..., Context]:
    def make(answer: Callable[[str, str, str], object] = lambda *_: {}) -> Context:
        ctx = build_context(
            settings,
            store,
            iso_week(NOW),
            transport=web,
            llm=ScriptedLlm(settings, answer),
        )
        ctx.http.sleep = lambda _seconds: None
        return ctx

    return make
