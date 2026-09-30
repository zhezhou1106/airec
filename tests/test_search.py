"""Web search falls back from one backend to the next."""
from __future__ import annotations

import pytest

from airec.config import Config
from airec.search import SearchError, WebSearch, _ddg_target


def backends(monkeypatch, calls, fail=()):
    for name in ("tavily", "brave", "duckduckgo"):
        def fn(self, q, n, news, days, name=name):
            calls.append(name)
            if name in fail:
                raise SearchError("quota used up")
            return [{"title": q, "url": f"https://{name}.example", "content": "", "published": ""}]
        monkeypatch.setattr(WebSearch, f"_{name}", fn)


def test_order_and_skipping_missing_keys(home, monkeypatch):
    calls: list[str] = []
    backends(monkeypatch, calls)
    s = WebSearch(Config())                       # no keys: only duckduckgo
    assert s.order == ["duckduckgo"]
    assert s.search("x")[0]["engine"] == "duckduckgo"
    (home / ".env").write_text("TAVILY_API_KEY=t\nBRAVE_API_KEY=b\n")
    assert WebSearch(Config()).search("x")[0]["engine"] == "tavily"


def test_a_failing_backend_is_skipped_for_the_rest_of_the_job(home, monkeypatch):
    (home / ".env").write_text("TAVILY_API_KEY=t\nBRAVE_API_KEY=b\n")
    calls: list[str] = []
    backends(monkeypatch, calls, fail={"tavily"})
    logs: list[str] = []
    s = WebSearch(Config(), log=logs.append)
    assert s.search("a")[0]["engine"] == "brave"
    assert s.search("b")[0]["engine"] == "brave"
    assert calls == ["tavily", "brave", "brave"]
    assert "falling back to brave" in logs[0]


def test_all_failing_raises(home, monkeypatch):
    backends(monkeypatch, [], fail={"duckduckgo"})
    s = WebSearch(Config())
    with pytest.raises(SearchError, match="every search backend failed"):
        s.search("x")
    assert not s.available


def test_order_setting_can_exclude_duckduckgo(home):
    import yaml
    path = home / "config/settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["search"] = {"order": ["tavily"]}
    path.write_text(yaml.safe_dump(data))
    s = WebSearch(Config())
    assert not s.available
    with pytest.raises(SearchError, match="no search backend"):
        s.search("x")


def test_ddg_link_unwrapping():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=abc"
    assert _ddg_target(href) == "https://example.com/a?b=1"
    assert _ddg_target("https://plain.example/") == "https://plain.example/"
