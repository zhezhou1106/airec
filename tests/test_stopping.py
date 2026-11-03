"""Stopping and failing cleanly: SIGTERM, a busy lock, model errors, a broken config."""

from __future__ import annotations

import os
import signal
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from airec import cli, pipeline, web
from airec.config import Strict, load_settings
from airec.http import Http
from airec.llm import LlmError
from airec.sections import _REGISTRY, register
from airec.sections.base import Section, SectionResult, Window
from airec.sections.papers import Papers
from airec.sources import news
from airec.store import Store
from tests.conftest import NOW, ScriptedLlm, write_config

ORIGIN = {"Origin": "http://127.0.0.1:8000"}


class Stopper(Section):
    """A section during which the hub presses Stop."""

    key = "stopper"
    title = "Stopper"

    class Config(Strict):
        pass

    def run(self, window: Window) -> SectionResult:
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("SIGTERM should have interrupted the run")


@pytest.fixture
def stopper_settings(paths):
    register(Stopper)
    write_config(paths, stopper={})
    yield load_settings(paths)
    _REGISTRY.pop("stopper", None)


def test_sigterm_marks_the_run_cancelled_and_restores_the_handler(stopper_settings, web):
    previous = signal.getsignal(signal.SIGTERM)
    llm = ScriptedLlm(stopper_settings, lambda *_: {})
    with pytest.raises(pipeline.Terminated):
        pipeline.generate(stopper_settings, now=NOW, transport=web, llm=llm)
    assert signal.getsignal(signal.SIGTERM) is previous
    store = Store(stopper_settings.paths.db)
    runs = store.week_runs(pipeline.iso_week(NOW))
    store.close()
    assert [run["status"] for run in runs] == ["cancelled"]
    assert runs[0]["finished_at"]


def test_cli_run_exits_nonzero_when_already_running(paths, monkeypatch):
    monkeypatch.setenv("AIREC_HOME", str(paths.home))

    def busy(*_a, **_k):
        raise pipeline.AlreadyRunning("busy")

    monkeypatch.setattr(pipeline, "generate", busy)
    result = CliRunner().invoke(cli.app, ["run"])
    assert result.exit_code == 1
    assert "Another run is in progress" in result.output


def test_cli_direction_commands_report_model_errors_without_a_traceback(paths, monkeypatch):
    monkeypatch.setenv("AIREC_HOME", str(paths.home))
    (paths.directions / "ttt.md").write_text("Test-time training for LLMs.")

    def fail(*_a, **_k):
        raise LlmError("model unreachable")

    monkeypatch.setattr(cli, "compile_plan", fail)
    monkeypatch.setattr(Papers, "scan", fail)
    runner = CliRunner()
    for args in (["direction", "add", "Something to track"], ["direction", "test", "ttt"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 1, result.output
        assert "model unreachable" in result.output
        assert not isinstance(result.exception, LlmError)


@pytest.fixture
def client(settings):
    return TestClient(web.create_app(settings), base_url="http://127.0.0.1:8000")


def test_preview_model_error_is_a_502(client, settings, monkeypatch):
    (settings.paths.directions / "ttt.md").write_text("Test-time training for LLMs.")

    def fail(*_a, **_k):
        raise LlmError("model unreachable")

    monkeypatch.setattr(Papers, "scan", fail)
    reply = client.post("/api/directions/ttt/preview", headers=ORIGIN)
    assert reply.status_code == 502
    assert "model unreachable" in reply.json()["detail"]


@pytest.mark.parametrize(
    "broken", ["report:\n  max_window_days: lots\n", "report: [unclosed\n  title: x\n"]
)
def test_saving_over_an_already_invalid_config_is_a_422(client, settings, broken):
    report = client.get("/api/config").json()["report"]
    settings.paths.config.write_text(broken)
    reply = client.put(
        "/api/config/report", json={"values": {**report, "title": "New"}}, headers=ORIGIN
    )
    assert reply.status_code == 422
    assert settings.paths.config.read_text() == broken


def test_hacker_news_keeps_stories_at_exactly_min_points(web):
    web.route("hn.algolia.com", {"hits": []})
    news.hacker_news(Http(web), ["llm"], 50, NOW - timedelta(days=7), NOW)
    assert "points>=50" in web.requests[0].url.params["numericFilters"]


@pytest.mark.parametrize(("days", "span"), [(7, "week"), (14, "month")])
def test_reddit_top_span_covers_the_window(web, days, span):
    web.route("reddit.com", "<rss></rss>")
    news.reddit_top(Http(web), "LocalLLaMA", 10, NOW - timedelta(days=days), NOW)
    assert web.requests[0].url.params["t"] == span
