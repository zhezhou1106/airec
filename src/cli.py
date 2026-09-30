"""The `rec` command. The web app starts the same commands in a child process."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import typer

from config import PHASES, load_config
from runlog import LockBusy, RunContext

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Personal research digest.")


def _echo(msg: str) -> None:
    typer.echo(msg)


@contextmanager
def job(kind: str, args: dict, log_dir: Optional[str]) -> Iterator[tuple]:
    cfg = load_config()
    try:
        with RunContext(cfg, kind, args=args, log_dir=Path(log_dir) if log_dir else None,
                        echo=_echo) as rc:
            yield cfg, rc
    except LockBusy as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
    except Exception as exc:  # noqa: BLE001 - logged by RunContext
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


LOG_DIR = typer.Option(None, "--log-dir", help="Job folder (the web app sets this).")


@app.command()
def run(days: Optional[float] = typer.Option(None, help="Look back this many days instead of "
                                              "'since the last run'."),
        news: Optional[bool] = typer.Option(None, "--news/--no-news",
                                            help="Override the news setting for this run."),
        log_dir: Optional[str] = LOG_DIR) -> None:
    """Make a digest."""
    from pipeline import RunOptions, run_digest

    with job("run", {"days": days, "news": news}, log_dir) as (cfg, rc):
        run_digest(cfg, rc, RunOptions(days=days, news=news))


@app.command()
def resume(run_id: Optional[int] = typer.Argument(None, help="Defaults to the latest run."),
           log_dir: Optional[str] = LOG_DIR) -> None:
    """Continue a run that failed or was cancelled."""
    from pipeline import RunOptions, run_digest
    from store import Store

    if run_id is None:
        store = Store(load_config().db_path)
        last = store.runs("run", limit=1)
        store.close()
        if not last:
            typer.echo("no runs yet")
            raise typer.Exit(1)
        run_id = last[0]["id"]
    with job("run", {"resume": run_id}, log_dir) as (cfg, rc):
        run_digest(cfg, rc, RunOptions(resume=run_id))


@app.command()
def backfill(months: Optional[int] = typer.Option(None, help="Default: index.backfill_months."),
             log_dir: Optional[str] = LOG_DIR) -> None:
    """Fill the index with past arXiv papers (safe to stop and restart)."""
    from backfill import run_backfill

    with job("backfill", {"months": months}, log_dir) as (cfg, rc):
        run_backfill(cfg, rc, months)


@app.command("deep-read")
def deep_read(item_id: str, log_dir: Optional[str] = LOG_DIR) -> None:
    """A long reading of one item (e.g. arxiv:2609.12345) with the deep_read model."""
    from deepread import run_deep_read

    with job("deepread", {"item_id": item_id}, log_dir) as (cfg, rc):
        run_deep_read(cfg, rc, item_id)


@app.command("revise-map")
def revise_map(request: str = typer.Argument("", help="What to change, in plain words."),
               request_file: Optional[str] = typer.Option(None, help="Read the request from a file."),
               web: bool = typer.Option(True, "--web/--no-web", help="Search the web first."),
               log_dir: Optional[str] = LOG_DIR) -> None:
    """Propose a revised interest map (nothing is saved until you accept it)."""
    from mapeditor import run_map_edit

    if request_file:
        request = Path(request_file).read_text(encoding="utf-8")
    if not request.strip():
        typer.echo("say what to change, e.g. rec revise-map \"add video generation as a side topic\"")
        raise typer.Exit(1)
    with job("revise", {"web": web}, log_dir) as (cfg, rc):
        run_map_edit(cfg, rc, request, web)
        typer.echo(f"proposal: {rc.dir / 'proposal.yaml'}")


@app.command()
def init() -> None:
    """Create config/ and .env from the examples (existing files are left alone)."""
    cfg = load_config()  # loading copies whatever is missing from the examples
    typer.echo(f"config: {cfg.config_dir}\nkeys:   {cfg.env_path}")
    typer.echo("next: rec serve, then Settings → Keys and Settings → Interests")


@app.command()
def check() -> None:
    """Ask every configured model for a one-word reply."""
    from llm import ChatClient, EmbedClient

    cfg = load_config()
    for phase in PHASES:
        if phase not in (cfg.models.get("phases") or {}):
            typer.echo(f"  {phase:<11} not set")
            continue
        missing = cfg.missing_key(phase)
        if missing:
            typer.echo(f"  {phase:<11} needs {missing}")
            continue
        ep = cfg.endpoint(phase)
        try:
            latency = ChatClient(ep).ping(timeout=60)
            typer.echo(f"  {phase:<11} {ep.model} ({ep.provider}) ok in {latency}s")
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  {phase:<11} {ep.model} ({ep.provider}) FAILED: {exc}")
    try:
        EmbedClient(cfg).embed(["ping"])
        typer.echo(f"  {'embedding':<11} {cfg.embedding().model} ok")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"  {'embedding':<11} FAILED: {exc}")


@app.command()
def status() -> None:
    """Index size and recent runs."""
    from store import Store

    cfg = load_config()
    store = Store(cfg.db_path)
    s = store.stats()
    typer.echo(f"index: {s['items']} papers/posts ({s['backfilled']} backfilled, "
               f"{s['embedded']} embedded), {s['news']} news items, {s['db_mb']} MB")
    for r in store.runs(limit=8):
        when = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M")
        typer.echo(f"  #{r['id']:<4} {r['kind']:<8} {r['status']:<10} {when}")
    store.close()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """The web app."""
    import uvicorn

    typer.echo(f"airec on http://{host}:{port}")
    uvicorn.run("web.app:create_app", factory=True, host=host, port=port,
                log_level="warning")


def main() -> None:  # python -m cli
    app()


if __name__ == "__main__":
    sys.exit(main())
