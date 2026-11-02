"""One report, start to finish.

The pipeline owns the rules every section shares: which time window it covers, a
failure in one section never stops the others, and a failed section's window is
not marked as covered, so next week's run picks it up.
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from airec import report
from airec.config import ConfigError, Settings
from airec.http import Http
from airec.llm import Llm
from airec.sections import build_sections
from airec.sections.base import Context, Section, SectionResult, Window
from airec.store import Store

log = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    pass


@dataclass
class RunResult:
    path: Path
    status: str  # ok | partial | failed
    outcomes: list[report.SectionOutcome]


def iso_week(moment: datetime) -> str:
    """The ISO week on the local clock: the same clock as the schedule."""
    year, week, _ = moment.astimezone().isocalendar()
    return f"{year}-W{week:02d}"


def section_window(
    settings: Settings, store: Store, key: str, week: str, now: datetime, days: int | None
) -> Window:
    """From where this section last left off, within the catch-up limit."""
    config = settings.app.report
    if days is not None:
        return Window(now - timedelta(days=days), now)
    earliest = now - timedelta(days=config.max_window_days)
    covered = store.last_covered(key, week)
    since = covered or now - timedelta(days=config.first_window_days)
    return Window(max(since, earliest), now)


def build_context(
    settings: Settings,
    store: Store,
    week: str,
    *,
    transport: httpx.BaseTransport | None = None,
    llm: Llm | None = None,
    trace_path: Path | None = None,
) -> Context:
    llm = llm or Llm(settings.app.llm, settings.secrets, trace_path=trace_path)
    return Context(settings, store, Http(transport), llm, week)


def run_sections(
    sections: list[Section],
    settings: Settings,
    store: Store,
    week: str,
    now: datetime,
    days: int | None,
    *,
    resume: bool = False,
) -> list[report.SectionOutcome]:
    """Run each section. Nothing here records the outcome: see `_commit`.

    With `resume`, a section already done this week keeps its output instead of
    running again. A section that fails falls back to this week's earlier output,
    so a retry can never make the report worse.
    """
    outcomes = []
    for section in sections:
        saved = store.get_state(section.key, _output_key(week))
        if resume and saved:
            outcomes.append(_restored(section, saved))
            continue
        window = section_window(settings, store, section.key, week, now, days)
        outcome = report.SectionOutcome(section.key, section.title, window)
        log.info("%s: %s", section.key, window.label)
        try:
            result = section.run(window)
            _check_recordable(result)
            outcome.result = result
        except ConfigError:
            raise
        except Exception as exc:
            log.exception("%s failed", section.key)
            outcome.error = f"{type(exc).__name__}: {exc}"[:300]
        outcome.warnings = section.warnings
        if outcome.result is None and saved:
            error = outcome.error
            outcome = _restored(section, saved)
            outcome.warnings = [*outcome.warnings, f"Rebuild failed, kept earlier version: {error}"]
        log.info("%s: %s", section.key, outcome.result.stats if outcome.result else outcome.error)
        outcomes.append(outcome)
    return outcomes


def _output_key(week: str) -> str:
    return f"output:{week}"


def _check_recordable(result: SectionResult) -> None:
    """Catch a section's bad data while the failure can still be isolated to it."""
    json.dumps(result.stats)
    if not all(isinstance(item, str) for item in result.shown):
        raise TypeError("SectionResult.shown must contain strings")


def _restored(section: Section, saved: dict[str, Any]) -> report.SectionOutcome:
    return report.SectionOutcome(
        section.key,
        section.title,
        Window(datetime.fromisoformat(saved["since"]), datetime.fromisoformat(saved["until"])),
        SectionResult(saved["markdown"], saved["highlights"], saved["shown"], saved["stats"]),
        warnings=saved["warnings"],
        restored=True,
    )


def _commit(store: Store, run_id: int, week: str, outcomes: list[report.SectionOutcome]) -> None:
    """Record what the delivered report contains. A crash before this records nothing."""
    for outcome in outcomes:
        result, window = outcome.result, outcome.window
        if outcome.restored:
            continue
        store.record_section(
            run_id,
            outcome.key,
            "ok" if result else "failed",
            window.since,
            window.until,
            result.stats if result else {},
            outcome.error,
        )
        if result:
            store.replace_shown(outcome.key, result.shown, week)
            store.set_state(
                outcome.key,
                _output_key(week),
                {
                    "since": window.since.isoformat(),
                    "until": window.until.isoformat(),
                    "markdown": result.markdown,
                    "highlights": result.highlights,
                    "shown": result.shown,
                    "stats": result.stats,
                    "warnings": outcome.warnings,
                },
            )


def generate(
    settings: Settings,
    *,
    days: int | None = None,
    only: str | None = None,
    dry_run: bool = False,
    resume: bool = False,
    now: datetime | None = None,
    transport: httpx.BaseTransport | None = None,
    llm: Llm | None = None,
) -> RunResult:
    """Build this week's report and deliver it.

    A dry run works on a copy of the database and writes to reports/drafts, so it
    leaves no trace in what later runs consider covered or shown. Running one
    section or a custom span is always a dry run: either would replace the full
    report and leave part of the week uncovered.
    """
    now = now or datetime.now(UTC)
    week = iso_week(now)
    dry_run = dry_run or only is not None or days is not None
    paths = settings.paths
    paths.logs.mkdir(parents=True, exist_ok=True)
    trace = paths.logs / f"{week}.llm.jsonl"

    with _exclusive(paths.lock), database(paths.db, scratch=dry_run) as store:
        ctx = build_context(settings, store, week, transport=transport, llm=llm, trace_path=trace)
        sections = build_sections(ctx)
        if only:
            sections = [section for section in sections if section.key == only]
            if not sections:
                raise ConfigError(f"Section '{only}' is not enabled in config.yaml.")
        if not sections:
            raise ConfigError("No sections are enabled in config.yaml.")

        run_id = store.start_run(week)
        outcomes = run_sections(sections, settings, store, week, now, days, resume=resume)
        title = settings.app.report.title
        markdown = report.render(title, week, now.astimezone(), outcomes, ctx.llm.usage)
        folder = paths.drafts if dry_run else paths.reports
        config = settings.app.report
        if dry_run:
            config = config.model_copy(update={"copy_to": None})
        path = report.deliver(markdown, folder, week, config)
        _commit(store, run_id, week, outcomes)

        failures = sum(not outcome.ok for outcome in outcomes)
        status = "ok" if not failures else "failed" if failures == len(outcomes) else "partial"
        store.finish_run(run_id, status, str(path), ctx.llm.usage.total_tokens)

    if settings.app.report.notify and not dry_run:
        note = f"{week} is ready" if status == "ok" else f"{week}: {failures} section(s) failed"
        report.notify(title, note)
    return RunResult(path, status, outcomes)


@contextmanager
def _exclusive(lock_path: Path) -> Iterator[None]:
    """Only one run at a time: the hourly scheduler must not overlap a slow run."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning("Another run is in progress.") from None
        yield


@contextmanager
def database(path: Path, *, scratch: bool) -> Iterator[Store]:
    """The real database, or with `scratch` a throwaway copy of it."""
    if not scratch:
        store = Store(path)
        try:
            yield store
        finally:
            store.close()
        return
    with tempfile.TemporaryDirectory(prefix="airec-") as folder:
        copy = Path(folder) / path.name
        if path.exists():
            shutil.copyfile(path, copy)
        store = Store(copy)
        try:
            yield store
        finally:
            store.close()


def is_due(settings: Settings, store: Store, now: datetime) -> bool:
    """Has this week's slot passed without a complete report, attempts remaining?"""
    schedule = settings.app.schedule
    local = now.astimezone()
    slot = (local - timedelta(days=local.isoweekday() - schedule.weekday)).replace(
        hour=schedule.hour, minute=0, second=0, microsecond=0
    )
    if local < slot:
        return False
    runs = store.week_runs(iso_week(now))
    if any(run["status"] == "ok" for run in runs):
        return False
    return len(runs) < schedule.max_attempts


def online(probe: Callable[[], object] | None = None) -> bool:
    """Whether the network is up: a Mac just out of sleep often is not, yet."""
    try:
        (probe or (lambda: httpx.head("https://api.github.com", timeout=10)))()
    except httpx.HTTPError:
        return False
    return True
