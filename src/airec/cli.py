"""The `rec` command."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
import uvicorn

from airec import pipeline, report, schedule
from airec.config import ConfigError, Paths, Settings, init_home, load_settings
from airec.llm import LlmError
from airec.sections import build_sections
from airec.sections.base import Context, Section, SectionError, Window
from airec.sections.papers import DirectionScan, Papers, compile_plan
from airec.sections.papers.directions import load_directions, slugify
from airec.store import Store
from airec.web import create_app

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
direction_app = typer.Typer(no_args_is_help=True, help="Manage paper directions.")
schedule_app = typer.Typer(no_args_is_help=True, help="Manage the weekly schedule (macOS).")
app.add_typer(direction_app, name="direction")
app.add_typer(schedule_app, name="schedule")


def _settings() -> Settings:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return load_settings()
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None


def _papers(ctx: Context) -> Papers:
    for section in build_sections(ctx):
        if isinstance(section, Papers):
            return section
    raise ConfigError("The papers section is not enabled in config.yaml.")


@app.command()
def init() -> None:
    """Create ~/.airec (or $AIREC_HOME) with starter config. Never overwrites."""
    paths = Paths.resolve()
    created = init_home(paths)
    typer.echo(f"airec home: {paths.home}")
    for path in created:
        typer.echo(f"  created {path.relative_to(paths.home)}")
    typer.echo("Next: add keys to .env, then run `rec doctor`.")


@app.command()
def run(
    days: Annotated[int | None, typer.Option(help="Draft the last N days.")] = None,
    section: Annotated[str | None, typer.Option(help="Draft only this section.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Write a draft; remember nothing.")] = False,
    if_due: Annotated[bool, typer.Option(help="Run only if this week's report is owed.")] = False,
) -> None:
    """Generate this week's report."""
    settings = _settings()
    now = datetime.now(UTC)
    if if_due:
        store = Store(settings.paths.db)
        due = pipeline.is_due(settings, store, now)
        store.close()
        if not due or not pipeline.online():
            return
    try:
        result = pipeline.generate(
            settings, days=days, only=section, dry_run=dry_run, resume=if_due, now=now
        )
    except pipeline.AlreadyRunning:
        typer.echo("Another run is in progress.")
        raise typer.Exit(1) from None
    except pipeline.Terminated:
        typer.secho("Stopped (SIGTERM); the run is marked cancelled.", fg="yellow", err=True)
        raise typer.Exit(143) from None
    except KeyboardInterrupt:
        typer.secho("Interrupted; the run is marked cancelled.", fg="yellow", err=True)
        raise typer.Exit(130) from None
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None
    for outcome in result.outcomes:
        state = outcome.result.stats if outcome.result else f"FAILED: {outcome.error}"
        typer.echo(f"  {outcome.key}: {state}")
    if result.cover:
        typer.echo(f"  cover: {result.cover}")
    typer.echo(f"{result.status}: {result.path}")
    if result.status == "failed":
        raise typer.Exit(1)


# ---- directions ----


@direction_app.command("list")
def direction_list() -> None:
    """Show your directions and their search plans."""
    settings = _settings()
    store = Store(settings.paths.db)
    for direction in load_directions(settings.paths.directions):
        stored = store.get_state(Papers.key, f"plan:{direction.id}")
        current = stored and stored["fingerprint"] == direction.fingerprint
        title = stored["plan"]["title"] if current else "(plan made on next run)"
        typer.echo(f"{direction.id}: {title}")
    store.close()


@direction_app.command("add")
def direction_add(
    description: Annotated[str | None, typer.Argument(help="Omit to open your editor.")] = None,
    name: Annotated[str | None, typer.Option(help="File name; derived if omitted.")] = None,
) -> None:
    """Add a direction from a description; the rest is automatic."""
    settings = _settings()
    text = description if description is not None else typer.edit("")
    if not text or not text.strip():
        typer.secho("Empty description; nothing added.", fg="yellow")
        raise typer.Exit(1)
    store = Store(":memory:")
    try:
        ctx = pipeline.build_context(settings, store, pipeline.iso_week(datetime.now(UTC)))
        plan = compile_plan(ctx, text.strip())
    except (LlmError, SectionError, ConfigError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None
    finally:
        store.close()
    target = settings.paths.directions / f"{slugify(name or plan.title)}.md"
    if target.exists():
        typer.secho(f"{target} already exists; use --name.", fg="red", err=True)
        raise typer.Exit(1)
    settings.paths.directions.mkdir(parents=True, exist_ok=True)
    target.write_text(text.strip() + "\n", encoding="utf-8")
    typer.echo(f"Added {target}\n")
    _print_plan(plan.title, plan.phrases, plan.categories)
    typer.echo(f"\nTry it: rec direction test {target.stem}")


@direction_app.command("test")
def direction_test(
    direction_id: str,
    days: Annotated[int, typer.Option(help="How far back to look.")] = 14,
) -> None:
    """Show what a direction would catch, without recording anything."""
    settings = _settings()
    found = {d.id: d for d in load_directions(settings.paths.directions)}
    if direction_id not in found:
        typer.secho(f"No direction '{direction_id}'. Have: {', '.join(found)}", fg="red", err=True)
        raise typer.Exit(1)
    now = datetime.now(UTC)
    try:
        with pipeline.database(settings.paths.db, scratch=True) as store:
            ctx = pipeline.build_context(settings, store, pipeline.iso_week(now))
            window = Window(now - timedelta(days=days), now)
            scan = _papers(ctx).scan(found[direction_id], window)
    except (LlmError, SectionError, ConfigError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None
    _print_scan(scan)


def _print_plan(title: str, phrases: list[str], categories: list[str]) -> None:
    typer.echo(f"Plan: {title}")
    typer.echo(f"  phrases: {', '.join(phrases)}")
    typer.echo(f"  categories: {', '.join(categories) or 'all'}")


def _print_scan(scan: DirectionScan) -> None:
    _print_plan(scan.plan.title, scan.plan.phrases, scan.plan.categories)
    typer.echo(
        f"\n{scan.candidates} candidates, {scan.relevant} relevant, "
        f"{len(scan.accepted)} passed the quality gate:"
    )
    for item in scan.accepted:
        typer.echo(f"  {item.score:>4.1f}  {item.paper.title}\n        {item.reason}")


# ---- schedule ----


@schedule_app.command("install")
def schedule_install() -> None:
    """Run automatically from now on (hourly check; one report a week)."""
    settings = _settings()
    if sys.platform != "darwin":
        typer.secho(
            "This uses launchd, so macOS only. Elsewhere: cron `rec run --if-due`.", err=True
        )
        raise typer.Exit(1)
    target = schedule.install(settings.paths)
    plan = settings.app.schedule
    typer.echo(f"Installed {target}: weekly, ISO weekday {plan.weekday}, from {plan.hour}:00.")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    """Stop running automatically."""
    typer.echo("Removed." if schedule.uninstall() else "Not installed.")


# ---- inspection ----


@app.command("cover")
def cover_command(
    week: Annotated[
        str | None, typer.Option(help="e.g. 2026-W45; default the latest report.")
    ] = None,
) -> None:
    """(Re)draw the overview image for a report with Codex."""
    settings = _settings()
    reports = settings.paths.reports
    path = reports / f"{week}.md" if week else max(reports.glob("*.md"), default=None)
    if not path or not path.exists():
        typer.secho("No such report.", fg="red", err=True)
        raise typer.Exit(1)
    try:
        with pipeline._exclusive(settings.paths.lock):
            state = pipeline.add_cover(settings, path, path.stem)
    except pipeline.AlreadyRunning:
        typer.echo("Another run is in progress.")
        raise typer.Exit(1) from None
    typer.echo(f"cover: {state}")
    if state.startswith("failed"):
        raise typer.Exit(1)


@app.command()
def serve(port: Annotated[int, typer.Option(help="Port on 127.0.0.1.")] = 8765) -> None:
    """Open the app: read reports and manage directions."""
    settings = _settings()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")


@app.command("open")
def open_report() -> None:
    """Open the latest report."""
    settings = _settings()
    reports = sorted(settings.paths.reports.glob("*.md"))
    if not reports:
        typer.secho("No report yet. Run `rec run`.", fg="yellow", err=True)
        raise typer.Exit(1)
    home = settings.paths.home
    report.open_link(report.report_link(reports[-1], home, settings.app.report.link))


@app.command()
def status() -> None:
    """Recent runs and whether the schedule is active."""
    settings = _settings()
    typer.echo(f"Schedule: {'active' if schedule.is_installed() else 'not installed'}")
    store = Store(settings.paths.db)
    for row in store.recent_runs():
        typer.echo(
            f"{row['week']}  {row['status']:<8} {row['started_at']}  "
            f"{row['total_tokens']:>8} tokens  {row['report_path'] or ''}"
        )
    store.close()


@app.command()
def doctor() -> None:
    """Check config, keys, models and every data source."""
    settings = _settings()
    problems = 0

    def check(name: str, probe) -> None:
        nonlocal problems
        try:
            detail = probe()
        except Exception as exc:
            problems += 1
            typer.secho(f"  FAIL {name}: {str(exc)[:160]}", fg="red")
        else:
            typer.echo(f"  ok   {name}{f': {detail}' if detail else ''}")

    store = Store(":memory:")
    try:
        ctx = pipeline.build_context(settings, store, "doctor")
        sections: list[Section] = build_sections(ctx)
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1) from None
    for role, spec in settings.app.llm.roles.items():
        check(f"model '{role}' ({spec.model})", lambda r=role: ctx.llm.ping(r))
    for key in ("GITHUB_TOKEN", "TAVILY_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"):
        present = "set" if settings.secrets.get(key) else "not set (optional)"
        typer.echo(f"  key  {key}: {present}")
    for section in sections:
        for name, probe in section.checks():
            check(name, probe)
    store.close()
    if problems:
        raise typer.Exit(1)
