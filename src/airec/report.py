"""Assembling the weekly Markdown report, and delivering it."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from airec.config import ReportConfig
from airec.llm import Usage
from airec.sections.base import SectionResult, Window

log = logging.getLogger(__name__)

# launchd's PATH leaves out Homebrew.
_SEARCH_PATH = os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"])


@dataclass
class SectionOutcome:
    key: str
    title: str
    window: Window
    result: SectionResult | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    restored: bool = False  # reused from an earlier attempt this week

    @property
    def ok(self) -> bool:
        return self.result is not None


def render(
    title: str, week: str, generated: datetime, outcomes: list[SectionOutcome], usage: Usage
) -> str:
    spans = [outcome.window for outcome in outcomes]
    covered = Window(min(w.since for w in spans), max(w.until for w in spans)).label
    lines = [
        "---",
        f"week: {week}",
        f"generated: {generated:%Y-%m-%d %H:%M}",
        "tags: [airec, weekly]",
        "---",
        "",
        f"# {title}: {week}",
        "",
        f"*{covered}*",
        "",
    ]
    highlights = [h for o in outcomes if o.result for h in o.result.highlights]
    if highlights:
        lines += ["## At a glance", ""]
        lines += [f"- {highlight}" for highlight in highlights]
        lines.append("")
    for outcome in outcomes:
        lines += [f"## {outcome.title}", ""]
        if outcome.result:
            lines.append(outcome.result.markdown.strip())
        else:
            lines.append(f"*This section failed and will be retried: {outcome.error}*")
        lines.append("")
    lines += _run_notes(outcomes, usage)
    return "\n".join(lines).rstrip() + "\n"


def _run_notes(outcomes: list[SectionOutcome], usage: Usage) -> list[str]:
    warnings = [f"{o.key}: {warning}" for o in outcomes for warning in o.warnings]
    lines = ["---", "", f"<small>{usage.calls} model calls, {usage.total_tokens:,} tokens.</small>"]
    if warnings:
        lines += ["", "<details><summary>Run notes</summary>", ""]
        lines += [f"- {warning}" for warning in warnings]
        lines += ["", "</details>"]
    return lines


def deliver(markdown: str, folder: Path, week: str, config: ReportConfig) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{week}.md"
    path.write_text(markdown, encoding="utf-8")
    if config.copy_to:
        target = config.copy_to.expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target / path.name)
        except OSError as exc:
            log.warning("Could not copy the report to %s: %s", target, exc)
    return path


def report_link(path: Path, home: Path, template: str | None) -> str:
    """Where a report opens: `report.link` with {path} filled in, or the file itself."""
    if template:
        relative = path.relative_to(home) if path.is_relative_to(home) else path
        return template.replace("{path}", quote(str(relative), safe=""))
    return path.as_uri()


def notify(title: str, message: str, link: str) -> None:
    """A macOS notification that opens `link` when clicked, if terminal-notifier is installed.

    Plain AppleScript notifications cannot carry a click action, so without it the
    message arrives but clicking does nothing. Elsewhere this does nothing at all.
    """
    if sys.platform != "darwin":
        return
    notifier = shutil.which("terminal-notifier", path=_SEARCH_PATH)
    if notifier:
        command = [notifier, "-title", title, "-message", message, "-open", link]
        command += ["-group", "airec"]
    else:
        script = f"display notification {_quoted(message)} with title {_quoted(title)}"
        command = ["osascript", "-e", script]
    subprocess.run(command, check=False, capture_output=True, timeout=10)


def open_link(link: str) -> None:
    subprocess.run(["open", link], check=True)


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
