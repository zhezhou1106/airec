"""Reading the reports for the app: the gallery, one week, the directions.

Everything is read from the airec home (report files, directions, the database), so
the CLI and the app never disagree.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from airec import schedule
from airec.config import Settings
from airec.sections.papers import Papers
from airec.sections.papers.directions import Direction, load_directions
from airec.store import Store

ASSET = re.compile(r"^[\w.-]+\.(png|jpe?g|webp|gif)$")
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\((?:\./)?assets/([\w.-]+)\)")
_SUBTITLE = re.compile(r"^\s*# .+\n+\*([^*\n]+)\*[ \t]*$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ReportView:
    """Reports and run state for the app's lifetime.

    One database connection serves every request; FastAPI runs requests on worker
    threads, so database reads hold `lock`. Gallery rows are cached per report and
    re-read when the file or the assets folder changes.
    """

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.lock = threading.Lock()
        self._summaries: dict[str, tuple[tuple[int, ...], dict[str, Any]]] = {}

    def weeks(self) -> list[str]:
        return sorted((p.stem for p in self.settings.paths.reports.glob("*.md")), reverse=True)

    def section_warnings(self, week: str) -> list[str] | None:
        """The run notes the database kept for `week`, or None if it has no sections for it."""
        keys = self.settings.app.sections
        with self.lock:
            outputs = [self.store.get_state(key, f"output:{week}") for key in keys]
        found = [output for output in outputs if output]
        if not found:
            return None
        return [w for output in found for w in output["warnings"]]

    def report_path(self, week: str) -> Path:
        return self.settings.paths.reports / f"{week}.md"

    def text(self, week: str) -> str:
        path = self.report_path(week)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def cover(self, week: str, text: str) -> str | None:
        """The image the report opens with, else assets/<week>.<ext>, if it is on disk."""
        assets = self.settings.paths.reports / "assets"
        names = [m.group(1) for m in _IMAGE_REF.finditer(text)][:1]
        names += [f"{week}.{ext}" for ext in ("png", "jpg", "jpeg", "webp")]
        for name in names:
            if ASSET.match(name) and (assets / name).is_file():
                return f"/api/assets/{name}"
        return None

    def summary(self, week: str) -> dict[str, Any]:
        """One row of the weeks gallery; cached until the report or its assets change."""
        stamp = _stamp(self.report_path(week), self.settings.paths.reports / "assets")
        cached = self._summaries.get(week)
        if cached is None or cached[0] != stamp:
            cached = (stamp, self._summary(week))
            self._summaries[week] = cached
        return dict(cached[1])

    def _summary(self, week: str) -> dict[str, Any]:
        """One row of the weeks gallery, read from the report file alone (no database)."""
        text = self.text(week)
        _, body = _split_front_matter(text)
        window = _SUBTITLE.search(body)
        parts = _sections_of(body)
        glance = next((lines for title, lines in parts if "glance" in title.lower()), [])
        return {
            "week": week,
            "window": window.group(1).strip() if window else None,
            "highlights": [_plain(x[2:]) for x in glance if x.startswith("- ")][:3],
            "sections": [
                {"title": _plain(title), "count": sum(x.startswith(("- ", "* ")) for x in lines)}
                for title, lines in parts
                if "glance" not in title.lower()
            ],
            "cover": self.cover(week, text),
        }

    def report(self, week: str) -> dict[str, Any]:
        """What the reading view shows for one week: the file, and its run notes."""
        text = self.text(week)
        warnings = self.section_warnings(week)
        return {
            "week": week,
            "warnings": _run_notes(text) if warnings is None else warnings,
            "markdown": text,
            "file": str(self.report_path(week)),
        }

    def schedule(self) -> tuple[bool, str]:
        """Whether the weekly run is installed, and how to say when it runs."""
        plan = self.settings.app.schedule
        if not schedule.is_installed():
            return False, "Schedule is off"
        return True, f"Every {_WEEKDAYS[plan.weekday - 1]} from {plan.hour}:00"

    def directions(self) -> list[dict[str, Any]]:
        folder = self.settings.paths.directions
        return [self._direction(d) for d in load_directions(folder, include_paused=True)]

    def _direction(self, direction: Direction) -> dict[str, Any]:
        with self.lock:
            stored = self.store.get_state(Papers.key, f"plan:{direction.id}")
        current = bool(stored) and stored["fingerprint"] == direction.fingerprint
        plan = stored["plan"] if current else None
        return {
            "id": direction.id,
            "description": direction.description,
            "paused": direction.paused,
            "title": plan["title"] if plan else direction.id.replace("-", " ").capitalize(),
            "plan": plan,
        }


def _stamp(*paths: Path) -> tuple[int, ...]:
    """Changes when any of `paths` is written (a folder: when a file is added or removed)."""
    stamp: list[int] = []
    for path in paths:
        try:
            info = path.stat()
        except FileNotFoundError:
            stamp += [-1, -1]
        else:
            stamp += [info.st_mtime_ns, info.st_size]
    return tuple(stamp)


def _split_front_matter(text: str) -> tuple[str, str]:
    match = re.match(r"---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    return (match.group(1), text[match.end() :]) if match else ("", text)


def _sections_of(body: str) -> list[tuple[str, list[str]]]:
    """The "## " sections of a report body (outside code fences), each with its lines."""
    found: list[tuple[str, list[str]]] = []
    fenced = False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            found.append((line[3:].strip(), []))
        elif not fenced and line.strip() == "---" and found:
            break  # the footer (model calls, run notes) is not a section
        elif found:
            found[-1][1].append(line)
    return found


def _run_notes(text: str) -> list[str]:
    """The notes in the report's footer, for a week the database has no sections for."""
    match = re.search(r"<summary>Run notes</summary>(.*?)</details>", text, re.DOTALL)
    if not match:
        return []
    return [x[2:].strip() for x in match.group(1).splitlines() if x.startswith("- ")]


def _plain(markdown: str) -> str:
    """A markdown line as text: links keep their label, emphasis and code marks go."""
    return re.sub(r"[*_`]", "", _LINK.sub(r"\1", markdown)).strip()
