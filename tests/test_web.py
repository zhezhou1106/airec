"""The app: pages, direction editing, and its local-only guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from airec import web
from airec.config import DEFAULTS
from airec.sections.papers.directions import load_directions
from airec.store import Store
from airec.web import create_app
from tests.conftest import write_config

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


def test_page_is_the_built_app_with_the_hub_style_on_html(client):
    page = client.get("/")
    assert page.status_code == 200
    assert '<div id="root">' in page.text
    assert '<html data-style="terminal" data-theme="dark" data-term' in page.text
    assert client.get("/directions").status_code == 200


def test_first_paint_follows_the_hub(client, monkeypatch):
    monkeypatch.setenv("HUB_API", "http://127.0.0.1:7777")
    replies = {"latte": "light", "mocha": "dark"}
    for style, theme in replies.items():
        look = {"settings": {"appearance": {"style": style, "theme": "dark"}}}
        monkeypatch.setattr(web, "_hub", lambda *_a, look=look, **_k: look)
        assert client.get("/api/appearance").json() == {
            "style": style,
            "theme": theme,
            "term": True,
        }
        assert f'data-style="{style}" data-theme="{theme}" data-term' in client.get("/").text
    classic = {"settings": {"appearance": {"style": "classic", "theme": "light"}}}
    monkeypatch.setattr(web, "_hub", lambda *_a, **_k: classic)
    assert client.get("/api/appearance").json() == {
        "style": "classic",
        "theme": "light",
        "term": False,
    }


def test_appearance_falls_back_when_the_hub_is_away(client, monkeypatch):
    monkeypatch.setenv("HUB_API", "http://127.0.0.1:9")
    assert client.get("/api/appearance").json()["style"] == "terminal"


def test_week_report_is_the_file_with_the_stored_run_notes(client, settings):
    saved_week(settings)
    overview = client.get("/api/overview").json()
    assert overview["weeks"] == ["2026-W45"]
    assert overview["schedule"] == "Every Monday from 9:00"
    report = client.get("/api/weeks/2026-W45").json()
    assert set(report) == {"week", "markdown", "warnings", "file"}
    assert report["markdown"] == "# report\n"
    assert report["warnings"] == ["a feed was down"]


def test_no_reports_yet(client):
    assert client.get("/api/overview").json()["weeks"] == []
    assert client.get("/api/weeks/2026-W45").status_code == 404


def test_weeks_are_newest_first_and_bad_weeks_are_404(client, settings):
    saved_week(settings, "2026-W44")
    saved_week(settings, "2026-W45")
    assert client.get("/api/overview").json()["weeks"] == ["2026-W45", "2026-W44"]
    assert client.get("/api/weeks/2026-W44").json()["week"] == "2026-W44"
    assert client.get("/api/weeks/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/api/weeks/2026-W01").status_code == 404


def test_cover_image_is_served_by_name_only(client, settings):
    saved_week(settings)
    (settings.paths.reports / "assets").mkdir()
    (settings.paths.reports / "assets" / "2026-W45.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    [week] = client.get("/api/weeks").json()["weeks"]
    assert week["cover"] == "/api/assets/2026-W45.png"
    assert client.get("/api/assets/2026-W45.png").status_code == 200
    assert client.get("/api/assets/..%2F2026-W45.md").status_code == 404


def test_links_open_through_the_hub(client, monkeypatch):
    monkeypatch.setenv("HUB_API", "http://127.0.0.1:7777")
    calls = []
    monkeypatch.setattr(web, "_hub", lambda *a, **k: calls.append((a, k)) or {"ok": True})
    url = "https://arxiv.org/abs/1"
    assert client.post("/api/open", json={"url": url}, headers=ORIGIN).json() == {"ok": True}
    assert calls == [(("POST", "/api/open-url"), {"json": {"url": url}})]
    bad = client.post("/api/open", json={"url": "file:///etc/passwd"}, headers=ORIGIN)
    assert bad.status_code == 400


def test_direction_lifecycle(client, settings):
    folder = settings.paths.directions
    made = client.post(
        "/api/directions",
        json={"description": "Long horizon task post-training for language models."},
        headers=ORIGIN,
    )
    assert made.status_code == 200
    direction_id = made.json()["id"]
    listed = client.get("/api/directions").json()
    assert [d["id"] for d in listed["directions"]] == [direction_id]
    assert listed["papers"]["quality_rules"]

    edited = client.put(
        f"/api/directions/{direction_id}",
        json={"description": "A sharper description of the same direction."},
        headers=ORIGIN,
    )
    assert edited.status_code == 200
    assert "sharper" in load_directions(folder)[0].description

    client.post(f"/api/directions/{direction_id}/paused", json={"paused": True}, headers=ORIGIN)
    assert load_directions(folder) == []
    assert client.get("/api/directions").json()["directions"][0]["paused"] is True
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


def test_reveal_shows_only_known_files(client, settings, monkeypatch):
    calls = []
    monkeypatch.setattr(web.subprocess, "run", lambda args, **_k: calls.append(args))
    saved_week(settings)

    def reveal(**body):
        return client.post("/api/reveal", json=body, headers=ORIGIN).status_code

    assert reveal(week="2026-W45") == 200
    assert calls[-1][-1].endswith("2026-W45.md")
    assert reveal(folder="directions") == 200
    assert calls[-1][-1] == str(settings.paths.directions)
    assert reveal(week="../x") == 404
    assert reveal(folder="/etc") == 400
    assert len(calls) == 2


DIGEST = """---
week: 2026-W44
tags: [airec, weekly]
---

# AI Weekly: 2026-W44

*Oct 19 to Oct 26, 2026*

![AI Weekly 2026-W44 overview](assets/2026-W44.png)

## At a glance

- Rising repo: [org/repo](https://github.com/org/repo): a **tool**
- Second highlight
- Third highlight
- Fourth highlight

## GitHub: rising repos

- **[org/repo](https://github.com/org/repo)** · ★ 1k
- **[org/other](https://github.com/org/other)** · ★ 2k

## AI news

### Model releases

- **A model.** Summary. [Source](https://example.com)

---

<details><summary>Run notes</summary>

- news: a feed was down

</details>
"""


def test_weeks_gallery_lists_every_week_with_excerpts(client, settings):
    (settings.paths.reports / "2026-W44.md").write_text(DIGEST)
    saved_week(settings, "2026-W45")
    weeks = client.get("/api/weeks").json()["weeks"]
    assert [w["week"] for w in weeks] == ["2026-W45", "2026-W44"]
    assert [w["latest"] for w in weeks] == [True, False]
    older = weeks[1]
    assert older["window"] == "Oct 19 to Oct 26, 2026"
    assert older["highlights"] == [
        "Rising repo: org/repo: a tool",
        "Second highlight",
        "Third highlight",
    ]
    assert older["sections"] == [
        {"title": "GitHub: rising repos", "count": 2},
        {"title": "AI news", "count": 1},
    ]
    assert older["cover"] is None
    assert weeks[0]["window"] is None and weeks[0]["highlights"] == []


def test_gallery_cover_follows_the_report_and_skips_missing_files(client, settings):
    (settings.paths.reports / "2026-W44.md").write_text(DIGEST)
    assets = settings.paths.reports / "assets"
    assets.mkdir()
    (assets / "2026-W44.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    [week] = client.get("/api/weeks").json()["weeks"]
    assert week["cover"] == "/api/assets/2026-W44.png"
    (assets / "2026-W44.png").unlink()
    (assets / "2026-W44.webp").write_bytes(b"RIFF")
    assert client.get("/api/weeks").json()["weeks"][0]["cover"] == "/api/assets/2026-W44.webp"


def test_week_exposes_the_report_file_for_the_shared_renderer(client, settings):
    (settings.paths.reports / "2026-W44.md").write_text(DIGEST)
    report = client.get("/api/weeks/2026-W44").json()
    assert report["markdown"] == DIGEST
    # No sections in the database: the run notes come from the file's footer.
    assert report["warnings"] == ["news: a feed was down"]


# ── Configuration ───────────────────────────────────────────────────────────


@pytest.fixture
def commented(settings):
    """The packaged config.yaml, comments and all, in the temporary home."""
    text = (DEFAULTS / "config.yaml").read_text()
    settings.paths.config.write_text(text)
    return text


def put_config(client, part, values):
    return client.put(f"/api/config/{part}", json={"values": values}, headers=ORIGIN)


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    a, b = before.splitlines(), after.splitlines()
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(a, b, strict=True) if x != y]


def test_config_save_changes_only_the_edited_values(client, settings, commented):
    report = client.get("/api/config").json()["report"]
    reply = put_config(client, "report", {**report, "title": "My Weekly", "max_window_days": 21})
    assert reply.status_code == 200 and reply.json()["changed"] is True
    after = settings.paths.config.read_text()
    diff = changed_lines(commented, after)
    assert [old.split(":")[0].strip() for old, _ in diff] == ["title", "max_window_days"]
    # The comment on the edited line survives.
    assert "max_window_days: 21" in after and "# after missed weeks" in diff[1][1]
    [backup] = settings.paths.home.glob("config.yaml.bak-*")
    assert backup.read_text() == commented
    assert client.get("/api/overview").json()["title"] == "My Weekly"


def test_config_sections_edit_lists_and_feed_rows_in_place(client, settings, commented):
    sections = {s["key"]: s["values"] for s in client.get("/api/config").json()["sections"]}
    assert list(sections)[:2] == ["github_trends", "ai_news"]
    news = sections["ai_news"]
    news["feeds"][0]["name"] = "OpenAI News"
    news["feeds"].append({"name": "New", "url": "https://example.com/feed", "primary": False})
    news["categories"] = news["categories"][:-1]
    reply = put_config(client, "sections", {"ai_news": news})
    assert reply.status_code == 200
    after = settings.paths.config.read_text()
    assert "{name: OpenAI News, url:" in after
    assert "      - {name: New, url: https://example.com/feed}\n" in after
    assert "Tools & infrastructure" not in after
    kept = [line for line in commented.splitlines() if "#" in line]
    assert all(line in after.splitlines() for line in kept)


def test_config_no_op_save_leaves_the_file_byte_identical(client, settings, commented):
    config = client.get("/api/config").json()
    for part in ("report", "schedule", "cover"):
        assert put_config(client, part, config[part]).json()["changed"] is False
    sections = {s["key"]: s["values"] for s in config["sections"]}
    assert put_config(client, "sections", sections).json()["changed"] is False
    models = {
        "providers": {p["name"]: p for p in config["models"]["providers"]},
        "roles": {r["name"]: r for r in config["models"]["roles"]},
    }
    assert put_config(client, "models", models).json()["changed"] is False
    assert settings.paths.config.read_text() == commented
    assert not list(settings.paths.home.glob("config.yaml.bak-*"))


def test_invalid_config_is_refused_with_field_errors_and_nothing_written(
    client, settings, commented
):
    schedule = client.get("/api/config").json()["schedule"]
    reply = put_config(client, "schedule", {**schedule, "hour": 30})
    assert reply.status_code == 422
    assert reply.json()["errors"][0]["loc"] == ["schedule", "hour"]
    bad_role = {"roles": {"fast": {"provider": "nowhere"}}}
    assert put_config(client, "models", bad_role).json()["errors"][0]["loc"] == [
        "llm",
        "roles",
        "fast",
        "provider",
    ]
    bad_section = put_config(client, "sections", {"github_trends": {"min_stars": "lots"}})
    assert bad_section.json()["errors"][0]["loc"] == ["sections", "github_trends", "min_stars"]
    assert put_config(client, "secrets", {}).status_code == 404
    assert settings.paths.config.read_text() == commented
    assert not list(settings.paths.home.glob("config.yaml.bak-*"))


def test_config_never_returns_secret_values(client, settings, commented, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-secret")
    settings.paths.env.write_text("OTHER_KEY=sk-file-secret\n")
    reply = client.get("/api/config")
    assert "sk-env-secret" not in reply.text and "sk-file-secret" not in reply.text
    [provider] = reply.json()["models"]["providers"]
    assert provider["api_key_env"] == "DEEPSEEK_API_KEY" and provider["key_set"] is True
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert client.get("/api/config").json()["models"]["providers"][0]["key_set"] is False
    saved = put_config(client, "models", {"providers": {"deepseek": {"api_key_env": "OTHER_KEY"}}})
    assert "sk-file-secret" not in saved.text
    assert saved.json()["config"]["models"]["providers"][0]["key_set"] is True
    assert "api_key_env: OTHER_KEY" in settings.paths.config.read_text()


def test_schedule_save_updates_the_header_text(client, commented):
    schedule = client.get("/api/config").json()["schedule"]
    reply = put_config(client, "schedule", {**schedule, "weekday": 5, "hour": 7})
    assert reply.json()["config"]["schedule_text"] == "Every Friday from 7:00"
    assert client.get("/api/overview").json()["schedule"] == "Every Friday from 7:00"


def test_preview_and_cli_test_use_the_run_path(settings, monkeypatch):
    """Both previews go through Papers.preview, which searches exactly like a run."""
    from typer.testing import CliRunner

    from airec import cli
    from airec.sections.papers import DirectionScan, Papers, SearchPlan

    (settings.paths.directions / "ttt.md").write_text("Test-time training for LLMs.")
    [direction] = load_directions(settings.paths.directions)
    calls = []

    def preview(self, d, window):
        calls.append((d.id, round(window.days)))
        plan = SearchPlan(title="T", phrases=["a b", "c d", "e f"])
        return DirectionScan(d, plan, 0, 0, [], [])

    monkeypatch.setattr(Papers, "preview", preview)
    assert web.run_preview(settings, direction)["title"] == "T"
    monkeypatch.setenv("AIREC_HOME", str(settings.paths.home))
    result = CliRunner().invoke(cli.app, ["direction", "test", "ttt", "--days", "5"])
    assert result.exit_code == 0, result.output
    assert calls == [("ttt", 14), ("ttt", 5)]


def test_config_sections_show_defaults_but_save_only_changes(client, settings, commented):
    sections = {s["key"]: s["values"] for s in client.get("/api/config").json()["sections"]}
    papers = sections["papers"]
    # Defaults the file leaves out are shown; the file's own values win.
    assert papers["max_shortlist"] == 15 and papers["judge_batch"] == 15
    assert papers["quality_rules"] and papers["enabled"] is True
    assert papers["max_papers"] == 5 and papers["min_score"] == 8
    assert next(iter(papers)) == "enabled"

    papers["judge_batch"] = 20
    reply = put_config(client, "sections", sections)
    assert reply.status_code == 200 and reply.json()["changed"] is True
    after = settings.paths.config.read_text()
    added = [line for line in after.splitlines() if line not in commented.splitlines()]
    assert added == ["    judge_batch: 20"]
    shown = {s["key"]: s["values"] for s in reply.json()["config"]["sections"]}
    assert shown["papers"]["judge_batch"] == 20


def test_config_section_without_settings_gets_a_block_only_when_changed(client, settings):
    write_config(settings.paths, papers=None)
    before = settings.paths.config.read_text()
    assert "  papers: null\n" in before
    sections = {s["key"]: s["values"] for s in client.get("/api/config").json()["sections"]}
    assert sections["papers"]["max_papers"] == 5
    assert put_config(client, "sections", sections).json()["changed"] is False
    assert settings.paths.config.read_text() == before
    sections["papers"]["max_papers"] = 3
    assert put_config(client, "sections", sections).json()["changed"] is True
    assert "max_papers: 3" in settings.paths.config.read_text()


def test_schedule_off_is_a_flag_not_a_sentence(client, monkeypatch):
    monkeypatch.setattr("airec.schedule.is_installed", lambda: False)
    overview = client.get("/api/overview").json()
    assert overview["scheduled"] is False and overview["schedule"] == "Schedule is off"
    assert client.get("/api/config").json()["scheduled"] is False
    monkeypatch.setattr("airec.schedule.is_installed", lambda: True)
    assert client.get("/api/overview").json()["scheduled"] is True


def test_gallery_rows_are_cached_until_the_report_changes(client, settings, monkeypatch):
    path = settings.paths.reports / "2026-W44.md"
    path.write_text(DIGEST)
    reads = []
    original = web.ReportView.text
    monkeypatch.setattr(
        web.ReportView, "text", lambda self, w: reads.append(w) or original(self, w)
    )
    first = client.get("/api/weeks").json()["weeks"]
    assert client.get("/api/weeks").json()["weeks"] == first
    assert reads == ["2026-W44"]
    path.write_text(DIGEST.replace("Oct 19 to Oct 26", "Oct 20 to Oct 27, plus"))
    assert client.get("/api/weeks").json()["weeks"][0]["window"].startswith("Oct 20")
    assert reads == ["2026-W44", "2026-W44"]
    (settings.paths.reports / "assets").mkdir()
    (settings.paths.reports / "assets" / "2026-W44.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert client.get("/api/weeks").json()["weeks"][0]["cover"] == "/api/assets/2026-W44.png"


def test_one_database_connection_serves_concurrent_requests(client, settings):
    from concurrent.futures import ThreadPoolExecutor

    saved_week(settings)
    (settings.paths.directions / "ttt.md").write_text("Test-time training for LLMs.")
    paths = ["/api/weeks/2026-W45", "/api/directions", "/api/overview", "/api/weeks"] * 20
    with ThreadPoolExecutor(8) as pool:
        replies = list(pool.map(client.get, paths))
    assert all(reply.status_code == 200 for reply in replies)
    assert replies[0].json()["warnings"] == ["a feed was down"]
