"""The SQLite memory between weekly runs.

The core owns four tables: runs, one row per section per run, what has been
shown, and a key/value `state` that sections use for small documents. A section
that needs tables of its own declares them in `Section.schema`, so a new section
needs no change here. ISO week strings ("2026-W45") sort chronologically.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    week TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',  -- running | ok | partial | failed
    report_path TEXT,
    total_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS section_runs (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    section TEXT NOT NULL,
    status TEXT NOT NULL,  -- ok | failed
    since TEXT NOT NULL,
    until TEXT NOT NULL,
    stats TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    PRIMARY KEY (run_id, section)
);
CREATE TABLE IF NOT EXISTS shown (
    section TEXT NOT NULL,
    item_id TEXT NOT NULL,
    week TEXT NOT NULL,
    shown_at TEXT NOT NULL,
    PRIMARY KEY (section, item_id)
);
CREATE TABLE IF NOT EXISTS state (
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (section, key)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def ensure_schema(self, schema: str) -> None:
        """Create a section's own tables. `schema` must be idempotent."""
        if schema:
            self.db.executescript(schema)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self.db:
            return self.db.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self.db:
            self.db.executemany(sql, rows)

    # ---- runs ----

    def start_run(self, week: str) -> int:
        # Callers hold the run lock, so a run still marked running was killed mid-way.
        self.execute(
            "UPDATE runs SET status = 'failed', finished_at = ? WHERE status = 'running'", (_now(),)
        )
        cursor = self.execute("INSERT INTO runs (week, started_at) VALUES (?, ?)", (week, _now()))
        return int(cursor.lastrowid or 0)

    def finish_run(self, run_id: int, status: str, report_path: str, total_tokens: int) -> None:
        self.execute(
            "UPDATE runs SET finished_at = ?, status = ?, report_path = ?, total_tokens = ? "
            "WHERE id = ?",
            (_now(), status, report_path, total_tokens, run_id),
        )

    def record_section(
        self,
        run_id: int,
        section: str,
        status: str,
        since: datetime,
        until: datetime,
        stats: dict[str, Any],
        error: str | None = None,
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO section_runs "
            "(run_id, section, status, since, until, stats, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                section,
                status,
                since.isoformat(),
                until.isoformat(),
                json.dumps(stats, ensure_ascii=False),
                error,
            ),
        )

    def last_covered(self, section: str, before_week: str) -> datetime | None:
        """End of the newest successful window from an earlier week.

        Earlier weeks only: re-running a report within its own week rebuilds the
        same window rather than finding it empty.
        """
        row = self.db.execute(
            "SELECT MAX(s.until) AS until FROM section_runs s JOIN runs r ON r.id = s.run_id "
            "WHERE s.section = ? AND s.status = 'ok' AND r.week < ?",
            (section, before_week),
        ).fetchone()
        return datetime.fromisoformat(row["until"]) if row["until"] else None

    def week_runs(self, week: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs WHERE week = ? ORDER BY id", (week,)).fetchall()

    def recent_runs(self, limit: int = 8) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---- shown items ----

    def shown_before(self, section: str, item_ids: Iterable[str], week: str) -> dict[str, str]:
        """{item id: when it was last shown} for items shown in an earlier week."""
        ids = list(dict.fromkeys(item_ids))
        found: dict[str, str] = {}
        for start in range(0, len(ids), 500):  # SQLite caps bound parameters
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.db.execute(
                f"SELECT item_id, shown_at FROM shown WHERE section = ? AND week < ? "
                f"AND item_id IN ({marks})",
                (section, week, *chunk),
            )
            found.update({row["item_id"]: row["shown_at"] for row in rows})
        return found

    def replace_shown(self, section: str, item_ids: Iterable[str], week: str) -> None:
        """Set what this week's report shows, forgetting an earlier attempt's picks."""
        self.execute("DELETE FROM shown WHERE section = ? AND week = ?", (section, week))
        self.mark_shown(section, item_ids, week)

    def mark_shown(self, section: str, item_ids: Iterable[str], week: str) -> None:
        now = _now()
        self.executemany(
            "INSERT INTO shown (section, item_id, week, shown_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (section, item_id) DO UPDATE SET week = excluded.week, "
            "shown_at = excluded.shown_at",
            [(section, item_id, week, now) for item_id in dict.fromkeys(item_ids)],
        )

    # ---- per-section state ----

    def get_state(self, section: str, key: str, default: Any = None) -> Any:
        row = self.db.execute(
            "SELECT value FROM state WHERE section = ? AND key = ?", (section, key)
        ).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, section: str, key: str, value: Any) -> None:
        self.execute(
            "INSERT OR REPLACE INTO state (section, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (section, key, json.dumps(value, ensure_ascii=False), _now()),
        )
