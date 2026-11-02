"""The app: pages, direction editing, and its local-only guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from airec import web
from airec.sections.papers.directions import load_directions
from airec.store import Store
from airec.web import create_app

ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture(autouse=True)
def no_launchctl(monkeypatch):
    monkeypatch.setattr("airec.schedule.is_installed", lambda: True)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1:8000")


def saved_week(settings, week="2026-W45"):
    (settings.paths.reports / f"{week}.md").write_text("# report\n")
    store = Store(settings.paths.db)
    store.set_state(
        "papers",
        f"output:{week}",
        {
            "since": "2026-10-26T12:00:00+00:00",
            "until": "2026-11-02T12:00:00+00:00",
            "markdown": "### Topic\n\n- **[A paper](https://arxiv.org/abs/1)** <script>x</script>",
            "highlights": ["Topic: [A paper](https://arxiv.org/abs/1)"],
            "shown": [],
            "stats": {},
            "warnings": ["a feed was down"],
        },
    )
    store.close()


def test_report_page_renders_sections_and_escapes_raw_html(client, settings):
    saved_week(settings)
    page = client.get("/")
    assert page.status_code == 200
    assert "Papers on your directions" in page.text
    assert 'target="_blank"' in page.text
    assert "<script>x" not in page.text
    assert "a feed was down" in page.text
    assert "Every Monday from 9:00" in page.text


def test_report_page_without_reports_explains_itself(client):
    assert "No report yet" in client.get("/").text


def test_older_week_can_be_selected(client, settings):
    saved_week(settings, "2026-W44")
    saved_week(settings, "2026-W45")
    assert ">2026-W44<" in client.get("/?week=2026-W44").text
    assert "<h1>2026-W45</h1>" in client.get("/").text
    assert "<h1>2026-W45</h1>" in client.get("/?week=../../etc/passwd").text


def test_direction_lifecycle(client, settings):
    folder = settings.paths.directions
    made = client.post(
        "/api/directions",
        json={"description": "Long horizon task post-training for language models."},
        headers=ORIGIN,
    )
    assert made.status_code == 200
    direction_id = made.json()["id"]
    assert direction_id in client.get("/directions").text

    edited = client.put(
        f"/api/directions/{direction_id}",
        json={"description": "A sharper description of the same direction."},
        headers=ORIGIN,
    )
    assert edited.status_code == 200
    assert "sharper" in load_directions(folder)[0].description

    client.post(f"/api/directions/{direction_id}/paused", json={"paused": True}, headers=ORIGIN)
    assert load_directions(folder) == []
    assert "Paused" in client.get("/directions").text
    client.post(f"/api/directions/{direction_id}/paused", json={"paused": False}, headers=ORIGIN)

    assert client.delete(f"/api/directions/{direction_id}", headers=ORIGIN).status_code == 200
    assert load_directions(folder, include_paused=True) == []


def test_too_short_description_is_refused(client):
    reply = client.post("/api/directions", json={"description": "short"}, headers=ORIGIN)
    assert reply.status_code == 422
    assert "sentence" in reply.json()["detail"]


def test_unknown_or_malicious_direction_is_404(client):
    assert client.delete("/api/directions/nothing", headers=ORIGIN).status_code == 404
    assert client.put(
        "/api/directions/..%2Fconfig", json={"description": "x" * 30}, headers=ORIGIN
    ).status_code in (404, 405)


def test_preview_returns_the_scan(client, settings, monkeypatch):
    (settings.paths.directions / "ttt.md").write_text("Test-time training for LLMs.")
    monkeypatch.setattr(web, "run_preview", lambda _s, direction: {"title": direction.id})
    reply = client.post("/api/directions/ttt/preview", headers=ORIGIN)
    assert reply.json() == {"title": "ttt"}


def test_writes_from_another_origin_are_refused(client):
    body = {"description": "A description that is long enough to pass."}
    assert client.post("/api/directions", json=body).status_code == 403
    hostile = {"Origin": "http://127.0.0.1:7777"}
    assert client.post("/api/directions", json=body, headers=hostile).status_code == 403


def test_other_hostnames_are_refused(settings):
    remote = TestClient(create_app(settings), base_url="http://evil.example")
    assert remote.get("/").status_code == 403


def test_page_can_be_framed_by_the_hub(client):
    assert "x-frame-options" not in client.get("/").headers
