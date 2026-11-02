"""Config, HTTP, LLM parsing, the store and prompts."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import yaml
from pydantic import BaseModel

from airec import prompts
from airec.config import ConfigError, Paths, init_home, load_settings
from airec.http import Http
from airec.llm import Llm, LlmError, parse_json
from airec.sources.common import canonical_url
from airec.store import Store
from tests.conftest import NOW, ScriptedLlm


def test_init_home_creates_a_working_home_and_never_overwrites(tmp_path):
    paths = Paths(tmp_path / "fresh")
    created = init_home(paths)
    assert paths.config in created
    assert paths.env.stat().st_mode & 0o777 == 0o600
    assert load_settings(paths).app.llm.roles["fast"].provider == "deepseek"

    paths.config.write_text(paths.config.read_text() + "# mine\n")
    assert init_home(paths) == []
    assert "# mine" in paths.config.read_text()


def test_mistyped_key_is_rejected(paths):
    paths.config.write_text(paths.config.read_text().replace("report:", "reprt:\n  x: 1\nreport:"))
    with pytest.raises(ConfigError, match="reprt"):
        load_settings(paths)


def test_role_with_unknown_provider_is_rejected(paths):
    raw = yaml.safe_load(paths.config.read_text())
    raw["llm"]["roles"]["fast"]["provider"] = "nobody"
    paths.config.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="nobody"):
        load_settings(paths)


def test_http_honours_retry_after_then_succeeds():
    calls, waits = [], []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="done")

    http = Http(httpx.MockTransport(respond), sleep=waits.append)
    assert http.get("https://example.com").text == "done"
    assert waits == [7.0]


def test_http_gives_up_after_its_attempts():
    http = Http(httpx.MockTransport(lambda _r: httpx.Response(503)), sleep=lambda _s: None)
    with pytest.raises(httpx.HTTPStatusError):
        http.get("https://example.com", attempts=2)


@pytest.mark.parametrize(
    "reply",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Here you go: {"a": 1} hope that helps',
        '<think>{"not": "this"}</think>{"a": 1}',
    ],
)
def test_parse_json_tolerates_wrappers(reply):
    assert parse_json(reply) == {"a": 1}


def test_parse_json_rejects_prose():
    with pytest.raises(LlmError):
        parse_json("no json at all")


class _Shape(BaseModel):
    value: int


def test_llm_json_sends_the_error_back_once(settings):
    replies = iter(["not json", '{"value": 3}'])
    llm = ScriptedLlm(settings, lambda *_: next(replies))
    assert llm.json("fast", "system", "question", _Shape).value == 3
    assert "rejected" in llm.prompts[1][2]


def test_llm_json_fails_after_two_bad_replies(settings):
    llm = ScriptedLlm(settings, lambda *_: "still not json")
    with pytest.raises(LlmError):
        llm.json("fast", "system", "question", _Shape)


def test_llm_grows_the_token_budget_when_reasoning_ate_it(settings, monkeypatch):
    budgets = []

    def respond(request):
        body = httpx.Request("POST", "https://x", content=request.content).content
        budgets.append(__import__("json").loads(body)["max_tokens"])
        if len(budgets) == 1:
            choice = {"message": {"content": ""}, "finish_reason": "length"}
        else:
            choice = {"message": {"content": "answer"}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice], "usage": {"prompt_tokens": 2}})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr("airec.llm.time.sleep", lambda _s: None)
    llm = Llm(settings.app.llm, settings.secrets, transport=httpx.MockTransport(respond))
    assert llm.chat("fast", "s", "u") == "answer"
    assert budgets[1] == budgets[0] * 2
    assert llm.usage.prompt_tokens == 4


def test_llm_reports_a_missing_key_clearly(settings, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    llm = Llm(settings.app.llm, settings.secrets)
    with pytest.raises(LlmError, match="DEEPSEEK_API_KEY"):
        llm.chat("fast", "s", "u")


def test_store_rerun_in_the_same_week_is_not_suppressed(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.mark_shown("news", ["a"], "2026-W45")
    assert store.shown_before("news", ["a"], "2026-W45") == {}
    assert "a" in store.shown_before("news", ["a", "b"], "2026-W46")


def test_store_last_covered_ignores_failures_and_the_current_week(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    since, until = NOW - timedelta(days=7), NOW
    good = store.start_run("2026-W44")
    store.record_section(good, "news", "ok", since, until - timedelta(days=7), {})
    bad = store.start_run("2026-W45")
    store.record_section(bad, "news", "failed", since, until, {}, "boom")
    assert store.last_covered("news", "2026-W45") == until - timedelta(days=7)
    assert store.last_covered("news", "2026-W44") is None


def test_store_state_round_trip(store):
    assert store.get_state("s", "k", "fallback") == "fallback"
    store.set_state("s", "k", {"a": [1]})
    assert store.get_state("s", "k") == {"a": [1]}


def test_prompt_requires_every_placeholder():
    with pytest.raises(KeyError):
        prompts.render("paper_brief")
    assert "direction text" in prompts.render("paper_brief", description="direction text")


def test_canonical_url_drops_tracking_only():
    url = "https://www.Example.com/a/?utm_source=x&id=3&ref=hn#frag"
    assert canonical_url(url) == "https://example.com/a?id=3"
