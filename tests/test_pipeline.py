"""Whole runs: windows, failure isolation, scheduling, and adding a section."""

from __future__ import annotations

import json
import plistlib
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from airec import cli, pipeline, report, schedule
from airec.config import ConfigError, Strict, load_settings
from airec.sections import _REGISTRY, build_sections, register
from airec.sections.base import Section, SectionResult, Window
from airec.store import Store
from tests.conftest import NOW, ScriptedLlm, write_config


class Probe(Section):
    """A minimal third-party section, registered only inside these tests."""

    key = "probe"
    title = "Probe"
    windows: list[Window] = []
    outcomes: list[object] = []

    class Config(Strict):
        label: str = "item"

    def run(self, window: Window) -> SectionResult:
        Probe.windows.append(window)
        next_outcome = Probe.outcomes.pop(0) if Probe.outcomes else "ok"
        if isinstance(next_outcome, Exception):
            raise next_outcome
        return SectionResult(
            f"{self.config.label} body", highlights=["probe highlight"], shown=["x"], stats={"n": 1}
        )


class Steady(Probe):
    key = "steady"
    title = "Steady"


@pytest.fixture(autouse=True)
def registered():
    register(Probe)
    register(Steady)
    Probe.windows, Probe.outcomes = [], []
    yield
    _REGISTRY.pop("probe", None)
    _REGISTRY.pop("steady", None)


@pytest.fixture
def probe_settings(paths):
    write_config(paths, probe={"label": "hello"}, steady={})
    return load_settings(paths)


def generate(settings, web, now=NOW, **options):
    llm = ScriptedLlm(settings, lambda *_: {})
    return pipeline.generate(settings, now=now, transport=web, llm=llm, **options)


def test_a_new_section_needs_only_a_class_and_a_config_block(probe_settings, web):
    result = generate(probe_settings, web)
    text = result.path.read_text()
    assert result.status == "ok"
    assert result.path.name == "2026-W45.md"
    assert "## Probe\n\nhello body" in text
    assert "## At a glance" in text and "- probe highlight" in text


def test_section_without_options_needs_only_its_name(paths, web):
    paths.config.write_text(paths.config.read_text() + "\n")
    text = paths.config.read_text()
    head = text[: text.index("sections:")]
    paths.config.write_text(head + "sections:\n  probe:\n")
    assert generate(load_settings(paths), web).status == "ok"


def test_section_order_follows_config(paths, web):
    write_config(paths, steady={}, probe={})
    text = generate(load_settings(paths), web).path.read_text()
    assert text.index("## Steady") < text.index("## Probe")


def test_disabled_section_is_skipped(paths, web):
    write_config(paths, steady={}, probe={"enabled": False})
    assert "## Probe" not in generate(load_settings(paths), web).path.read_text()


def test_unknown_section_and_bad_options_are_config_errors(paths, web):
    write_config(paths, nonsense={})
    with pytest.raises(ConfigError, match="nonsense"):
        generate(load_settings(paths), web)
    write_config(paths, probe={"lable": "typo"})
    with pytest.raises(ConfigError, match="lable"):
        generate(load_settings(paths), web)


def test_one_failed_section_leaves_the_other_and_is_retried_next_week(probe_settings, web):
    week_ago = NOW - timedelta(days=7)
    generate(probe_settings, web, now=week_ago)

    Probe.outcomes = [RuntimeError("source exploded")]
    failed_run = generate(probe_settings, web)
    text = failed_run.path.read_text()
    assert failed_run.status == "partial"
    assert "failed and will be retried" in text and "Steady" in text

    Probe.windows.clear()
    generate(probe_settings, web, now=NOW + timedelta(days=7))
    # probe failed last week, so its next window starts where the last success ended
    assert Probe.windows[0].since == week_ago
    # steady succeeded, so it continues exactly where it left off
    assert Probe.windows[1].since == NOW


def test_catch_up_is_limited(probe_settings, web):
    generate(probe_settings, web)
    later = NOW + timedelta(days=60)
    generate(probe_settings, web, now=later)
    assert Probe.windows[-2].since == later - timedelta(days=14)


def test_window_starts_at_the_first_run_and_rerun_rebuilds_it(probe_settings, web):
    generate(probe_settings, web)
    generate(probe_settings, web, now=NOW + timedelta(hours=2))
    assert Probe.windows[2].since == NOW + timedelta(hours=2) - timedelta(days=7)


def test_custom_span_is_only_a_draft(probe_settings, web):
    result = generate(probe_settings, web, days=3)
    assert Probe.windows[0].since == NOW - timedelta(days=3)
    assert result.path.parent.name == "drafts"


def test_scheduled_retry_reruns_only_the_failed_section(probe_settings, web):
    Probe.outcomes = [RuntimeError("down")]
    first = generate(probe_settings, web)
    assert first.status == "partial"
    assert len(Probe.windows) == 2

    retry = generate(probe_settings, web, now=NOW + timedelta(hours=1), resume=True)
    assert retry.status == "ok"
    assert len(Probe.windows) == 3  # probe again; steady reused
    assert "item body" in retry.path.read_text().split("## Steady")[1]


def test_failed_rebuild_keeps_the_good_version(probe_settings, web):
    generate(probe_settings, web)
    Probe.outcomes = [RuntimeError("down")]
    rebuilt = generate(probe_settings, web, now=NOW + timedelta(hours=1))
    text = rebuilt.path.read_text()
    assert rebuilt.status == "ok"
    assert "hello body" in text
    assert "Rebuild failed" in text


def test_rebuild_forgets_the_previous_attempts_picks(probe_settings, web):
    generate(probe_settings, web)
    store = Store(probe_settings.paths.db)
    store.mark_shown("probe", ["dropped-by-rebuild"], "2026-W45")
    store.close()
    generate(probe_settings, web, now=NOW + timedelta(hours=1))
    store = Store(probe_settings.paths.db)
    assert "dropped-by-rebuild" not in store.shown_before(
        "probe", ["dropped-by-rebuild"], "2026-W46"
    )
    assert "x" in store.shown_before("probe", ["x"], "2026-W46")
    store.close()


def test_interrupted_run_marks_nothing_covered(probe_settings, web, monkeypatch):
    def crash(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("airec.report.deliver", crash)
    with pytest.raises(OSError):
        generate(probe_settings, web)
    store = Store(probe_settings.paths.db)
    assert store.last_covered("probe", "2026-W46") is None
    assert store.shown_before("probe", ["x"], "2026-W46") == {}
    store.close()


def test_unserialisable_stats_fail_only_that_section(probe_settings, web):
    original = Probe.run

    def bad(self, window):
        result = original(self, window)
        result.stats = {"when": NOW}
        return result

    Steady.run = original
    Probe.run = bad
    try:
        result = generate(probe_settings, web)
    finally:
        Probe.run = original
        del Steady.run
    assert result.status == "partial"
    assert [outcome.ok for outcome in result.outcomes] == [False, True]


def test_dry_run_leaves_no_trace(probe_settings, web):
    result = generate(probe_settings, web, dry_run=True)
    assert result.path.parent.name == "drafts"
    store = Store(probe_settings.paths.db) if probe_settings.paths.db.exists() else None
    assert store is None or store.recent_runs() == []


def test_single_section_run_never_replaces_the_report(probe_settings, web):
    result = generate(probe_settings, web, only="probe")
    assert result.path.parent.name == "drafts"
    assert "## Steady" not in result.path.read_text()


def test_report_is_copied_when_configured(paths, web, tmp_path):
    write_config(paths, probe={})
    text = paths.config.read_text().replace("copy_to: null", f"copy_to: {tmp_path / 'vault'}")
    paths.config.write_text(text)
    generate(load_settings(paths), web)
    assert (tmp_path / "vault" / "2026-W45.md").exists()


def test_concurrent_runs_are_refused(probe_settings, web):
    with (
        pipeline._exclusive(probe_settings.paths.lock),
        pytest.raises(pipeline.AlreadyRunning),
    ):
        generate(probe_settings, web)


def test_run_records_warnings_in_the_report(probe_settings, web):
    original = Probe.run

    def warned(self, window):
        self.warn("a feed was down")
        return original(self, window)

    Probe.run = warned
    try:
        text = generate(probe_settings, web).path.read_text()
    finally:
        Probe.run = original
    assert "a feed was down" in text


def test_build_sections_keeps_config_order(probe_settings, make_context):
    assert [s.key for s in build_sections(make_context_for(probe_settings, make_context))] == [
        "probe",
        "steady",
    ]


def make_context_for(settings, make_context):
    ctx = make_context()
    ctx.settings = settings
    return ctx


# ---- is_due ----


def due(settings, store, moment):
    return pipeline.is_due(settings, store, moment)


def local(day: int, hour: int):
    """A moment in local time in the week of Monday 2026-11-02."""
    return NOW.astimezone().replace(day=day, hour=hour, minute=30)


def test_due_only_after_the_weekly_slot(settings, store):
    assert not due(settings, store, local(2, 8))  # Monday, before nine
    assert due(settings, store, local(2, 9))
    assert due(settings, store, local(5, 3))  # the Mac slept until Thursday


def test_not_due_once_this_week_is_complete(settings, store):
    moment = local(3, 10)
    run = store.start_run(pipeline.iso_week(moment))
    store.finish_run(run, "ok", "report.md", 0)
    assert not due(settings, store, moment)


def test_partial_runs_retry_up_to_the_limit(settings, store):
    moment = local(3, 10)
    week = pipeline.iso_week(moment)
    for attempt in range(settings.app.schedule.max_attempts):
        assert due(settings, store, moment), attempt
        store.finish_run(store.start_run(week), "partial", "report.md", 0)
    assert not due(settings, store, moment)


@pytest.fixture
def new_york(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_week_and_slot_share_the_local_clock(settings, store, new_york):
    # Sunday 19:00 in New York is already Monday in UTC.
    sunday_evening = datetime(2026, 11, 8, 19, 0, tzinfo=ZoneInfo("America/New_York"))
    assert sunday_evening.astimezone(UTC).isoweekday() == 1
    store.finish_run(store.start_run("2026-W45"), "ok", "report.md", 0)
    assert pipeline.iso_week(sunday_evening) == "2026-W45"
    assert not due(settings, store, sunday_evening)
    monday_morning = datetime(2026, 11, 9, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert due(settings, store, monday_morning)


def test_online_is_false_on_network_errors():
    import httpx

    def down():
        raise httpx.ConnectError("no route")

    assert not pipeline.online(down)
    assert pipeline.online(lambda: None)


def test_link_uses_the_template_and_encodes_the_path(tmp_path):
    path = tmp_path / "reports" / "2026-W45.md"
    template = "http://127.0.0.1:7777/#/app/airec/outputs/{path}"
    assert (
        report.report_link(path, tmp_path, template)
        == "http://127.0.0.1:7777/#/app/airec/outputs/reports%2F2026-W45.md"
    )
    assert report.report_link(path, tmp_path, None) == path.as_uri()


def test_notification_carries_the_link_when_terminal_notifier_exists(monkeypatch):
    commands = []
    monkeypatch.setattr("airec.report.sys.platform", "darwin")
    monkeypatch.setattr("airec.report.shutil.which", lambda *_a, **_k: "/brew/terminal-notifier")
    monkeypatch.setattr(
        "airec.report.subprocess.run", lambda command, **_k: commands.append(command)
    )
    report.notify("AI Weekly", "ready", "http://x/y")
    assert commands[0][0] == "/brew/terminal-notifier"
    assert commands[0][commands[0].index("-open") + 1] == "http://x/y"


def test_notification_falls_back_to_applescript(monkeypatch):
    commands = []
    monkeypatch.setattr("airec.report.sys.platform", "darwin")
    monkeypatch.setattr("airec.report.shutil.which", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "airec.report.subprocess.run", lambda command, **_k: commands.append(command)
    )
    report.notify('AI "Weekly"', "ready", "http://x/y")
    assert commands[0][0] == "osascript"
    assert 'AI \\"Weekly\\"' in commands[0][2]


def test_cli_open_uses_the_newest_report(paths, monkeypatch):
    monkeypatch.setenv("AIREC_HOME", str(paths.home))
    opened = []
    monkeypatch.setattr("airec.report.open_link", opened.append)
    (paths.reports / "2026-W44.md").write_text("old")
    (paths.reports / "2026-W45.md").write_text("new")
    assert CliRunner().invoke(cli.app, ["open"]).exit_code == 0
    assert opened[0].endswith("2026-W45.md")


def test_home_card_has_plain_text_highlights(tmp_path):
    outcome = report.SectionOutcome(
        "papers",
        "Papers",
        Window(NOW, NOW),
        SectionResult("body", highlights=["Topic: [A paper](https://arxiv.org/abs/1)"]),
    )
    card = tmp_path / "card.json"
    report.write_home_card(card, "2026-W45", [outcome], "http://hub/link")
    assert json.loads(card.read_text())["items"] == [
        {"title": "Topic: A paper", "note": "2026-W45", "url": "http://hub/link"}
    ]


# ---- schedule and CLI ----


def test_plist_runs_the_if_due_check_hourly(settings):
    plist = schedule.build_plist(settings.paths, python="/venv/bin/python")
    assert plist["ProgramArguments"] == ["/venv/bin/python", "-m", "airec", "run", "--if-due"]
    assert plist["StartInterval"] == 3600 and plist["RunAtLoad"] is True
    assert plist["EnvironmentVariables"]["AIREC_HOME"] == str(settings.paths.home)
    assert plistlib.loads(plistlib.dumps(plist))["Label"] == schedule.LABEL


def test_cli_init_then_direction_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("AIREC_HOME", str(tmp_path / "cli-home"))
    runner = CliRunner()
    assert runner.invoke(cli.app, ["init"]).exit_code == 0
    listed = runner.invoke(cli.app, ["direction", "list"])
    assert listed.exit_code == 0
    assert "example" in listed.output


def test_cli_explains_a_missing_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIREC_HOME", str(tmp_path / "nowhere"))
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 1
    assert "rec init" in result.output
