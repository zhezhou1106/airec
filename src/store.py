"""The local index: everything ever collected, and the work already done on it.

The index saves work, it never blocks an item. A paper that was triaged under
the current interest map is not triaged again; a paper shown before can come
back when something changed (new code, trending again, a new topic in the map).

Tables
    items      one row per paper / post / repo / model / news link
    triage     keep-or-drop per item, per (map version, triage model)
    scores     0-10 score per item, per (map version, score model)
    runs       every job: window, status, log folder, the digest it wrote
    run_items  which items a run is working on (so a run can resume)
    shown      which items appeared in which digest
    kv         watermarks and small state
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    track TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    title TEXT,
    abstract TEXT,
    authors TEXT,
    published_at REAL,
    first_seen REAL,
    signals TEXT DEFAULT '{}',
    seen_in TEXT DEFAULT '[]',
    embedding BLOB,
    backfill INTEGER DEFAULT 0,
    changed_at REAL,
    change_note TEXT,
    text_path TEXT,
    note TEXT,
    note_model TEXT,
    oneliner TEXT,
    deep_path TEXT
);
CREATE INDEX IF NOT EXISTS items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS items_track ON items(track);
CREATE TABLE IF NOT EXISTS triage (
    item_id TEXT, key TEXT, keep INTEGER, topic TEXT, ts REAL,
    PRIMARY KEY (item_id, key)
);
CREATE TABLE IF NOT EXISTS scores (
    item_id TEXT, key TEXT, score REAL, novelty TEXT, reason TEXT, topic TEXT, ts REAL,
    PRIMARY KEY (item_id, key)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, status TEXT, started_at REAL, finished_at REAL, log_dir TEXT,
    map_hash TEXT, topic_ids TEXT DEFAULT '[]',
    k_since REAL, k_until REAL, n_since REAL, n_until REAL,
    news INTEGER DEFAULT 0,
    phases_done TEXT DEFAULT '[]', stats TEXT DEFAULT '{}',
    digest TEXT, digest_path TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
    run_id INTEGER, item_id TEXT, role TEXT, note TEXT,
    PRIMARY KEY (run_id, item_id)
);
CREATE TABLE IF NOT EXISTS shown (
    run_id INTEGER, item_id TEXT, tier TEXT, topic TEXT, ts REAL,
    PRIMARY KEY (run_id, item_id)
);
CREATE INDEX IF NOT EXISTS shown_item ON shown(item_id);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float16).tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(args)).fetchone()

    # ------------------------------------------------------------ items
    def upsert_item(self, item: dict[str, Any], track: str, *, backfill: bool = False,
                    now: float | None = None) -> bool:
        """Insert or merge one item. Returns True when it is new to the index.

        When a known item turns up somewhere new, or gains a code link, that is
        recorded as a change: a reason it may be shown again.
        """
        now = now or time.time()
        signals = dict(item.get("signals") or {})
        source = item["source"]
        row = self.one("SELECT * FROM items WHERE id=?", (item["id"],))
        if row is None:
            self.conn.execute(
                "INSERT INTO items (id, track, kind, source, url, title, abstract, authors,"
                " published_at, first_seen, signals, seen_in, backfill)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], track, item["kind"], source, item.get("url", ""),
                 item.get("title", ""), item.get("abstract", ""), item.get("authors", ""),
                 item.get("published_at") or now, now, json.dumps(signals),
                 json.dumps([source]), 1 if backfill else 0),
            )
            return True

        old_signals = json.loads(row["signals"] or "{}")
        seen_in = json.loads(row["seen_in"] or "[]")
        notes = []
        if not backfill and source not in seen_in:
            seen_in.append(source)
            if row["backfill"] or (now - (row["first_seen"] or now)) > 2 * 86400:
                notes.append(f"now on {source}")
        if signals.get("code_url") and not old_signals.get("code_url"):
            notes.append("code released")
        merged = {**old_signals, **{k: v for k, v in signals.items() if v not in ("", None)}}
        abstract = item.get("abstract") or ""
        fields: dict[str, Any] = {"signals": json.dumps(merged), "seen_in": json.dumps(seen_in)}
        if len(abstract) > len(row["abstract"] or ""):
            fields["abstract"] = abstract
        if not row["authors"] and item.get("authors"):
            fields["authors"] = item["authors"]
        if notes:
            fields["changed_at"] = now
            fields["change_note"] = "; ".join(notes)
        self.update_item(item["id"], **fields)
        return False

    def update_item(self, item_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))

    def item(self, item_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM items WHERE id=?", (item_id,))

    def items(self, ids: list[str]) -> list[sqlite3.Row]:
        out: list[sqlite3.Row] = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            out.extend(self.q(f"SELECT * FROM items WHERE id IN ({marks})", chunk))
        order = {k: n for n, k in enumerate(ids)}
        return sorted(out, key=lambda r: order.get(r["id"], 0))

    # ------------------------------------------------------------ embeddings
    def missing_embeddings(self, ids: list[str] | None = None, limit: int = 5000
                           ) -> list[sqlite3.Row]:
        if ids is not None:
            return [r for r in self.items(ids) if r["embedding"] is None]
        return self.q("SELECT id, title, abstract FROM items WHERE embedding IS NULL "
                      "ORDER BY published_at DESC LIMIT ?", (limit,))

    def set_embeddings(self, ids: list[str], mat: np.ndarray) -> None:
        self.conn.executemany("UPDATE items SET embedding=? WHERE id=?",
                              [(to_blob(v), i) for i, v in zip(ids, mat)])

    def embeddings(self, ids: list[str]) -> dict[str, np.ndarray]:
        out = {}
        for row in self.items(ids):
            vec = from_blob(row["embedding"])
            if vec is not None:
                out[row["id"]] = vec
        return out

    def iter_embeddings(self, *, track: str = "knowledge", since: float = 0,
                        chunk: int = 20000) -> Iterator[tuple[list[str], np.ndarray]]:
        """The whole index in chunks, for resurfacing."""
        last = ""
        while True:
            rows = self.q(
                "SELECT id, embedding FROM items WHERE track=? AND embedding IS NOT NULL "
                "AND published_at>=? AND id>? ORDER BY id LIMIT ?", (track, since, last, chunk))
            if not rows:
                return
            last = rows[-1]["id"]
            yield [r["id"] for r in rows], np.stack([from_blob(r["embedding"]) for r in rows])

    # ------------------------------------------------------------ judgements
    def triaged(self, key: str, ids: list[str]) -> dict[str, sqlite3.Row]:
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.q(f"SELECT * FROM triage WHERE key=? AND item_id IN ({marks})",
                            (key, *chunk)):
                out[r["item_id"]] = r
        return out

    def set_triage(self, key: str, item_id: str, keep: bool, topic: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO triage VALUES (?,?,?,?,?)",
                          (item_id, key, 1 if keep else 0, topic, time.time()))

    def scored(self, key: str, ids: list[str]) -> dict[str, sqlite3.Row]:
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.q(f"SELECT * FROM scores WHERE key=? AND item_id IN ({marks})",
                            (key, *chunk)):
                out[r["item_id"]] = r
        return out

    def set_score(self, key: str, item_id: str, score: float, novelty: str, reason: str,
                  topic: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?)",
                          (item_id, key, score, novelty, reason, topic, time.time()))

    def untriaged_recent(self, key: str, since: float, exclude: set[str],
                         limit: int = 20000) -> list[str]:
        """Knowledge items collected since `since` that were never triaged under `key`:
        what earlier runs had to skip because of the triage limit."""
        rows = self.q(
            "SELECT i.id FROM items i LEFT JOIN triage t ON t.item_id=i.id AND t.key=? "
            "WHERE i.track='knowledge' AND i.backfill=0 AND i.first_seen>=? AND t.item_id IS NULL "
            "ORDER BY i.first_seen DESC LIMIT ?", (key, since, limit))
        return [r["id"] for r in rows if r["id"] not in exclude]

    # ------------------------------------------------------------ shown
    def last_shown(self, ids: list[str]) -> dict[str, sqlite3.Row]:
        """For each id, its most recent appearance: ts, tier and the run's topic ids."""
        out: dict[str, sqlite3.Row] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.q(
                f"SELECT s.item_id, s.ts, s.tier, s.run_id, r.topic_ids FROM shown s "
                f"JOIN runs r ON r.id=s.run_id WHERE s.item_id IN ({marks}) ORDER BY s.ts",
                chunk,
            ):
                out[r["item_id"]] = r
        return out

    def mark_shown(self, run_id: int, item_id: str, tier: str, topic: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO shown VALUES (?,?,?,?,?)",
                          (run_id, item_id, tier, topic, time.time()))

    def shown_embeddings(self, limit: int = 2000) -> tuple[list[sqlite3.Row], np.ndarray | None]:
        rows = self.q(
            "SELECT i.id, i.title, i.embedding, MAX(s.ts) AS ts FROM shown s "
            "JOIN items i ON i.id=s.item_id WHERE i.embedding IS NOT NULL AND s.tier='note' "
            "GROUP BY i.id ORDER BY ts DESC LIMIT ?", (limit,))
        if not rows:
            return [], None
        return rows, np.stack([from_blob(r["embedding"]) for r in rows])

    # ------------------------------------------------------------ runs
    def create_run(self, kind: str, log_dir: str, **fields: Any) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (kind, status, started_at, log_dir) VALUES (?,?,?,?)",
            (kind, "running", time.time(), log_dir))
        run_id = int(cur.lastrowid)
        self.update_run(run_id, **fields)
        self.commit()
        return run_id

    def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        enc = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
        cols = ", ".join(f"{k}=?" for k in enc)
        self.conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*enc.values(), run_id))
        self.commit()

    def run(self, run_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM runs WHERE id=?", (run_id,))

    def runs(self, kind: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if kind:
            return self.q("SELECT * FROM runs WHERE kind=? ORDER BY id DESC LIMIT ?",
                          (kind, limit))
        return self.q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))

    def phase_done(self, run_id: int, phase: str) -> None:
        row = self.run(run_id)
        done = json.loads(row["phases_done"] or "[]") if row else []
        if phase not in done:
            done.append(phase)
            self.update_run(run_id, phases_done=done)

    def phases_done(self, run_id: int) -> list[str]:
        row = self.run(run_id)
        return json.loads(row["phases_done"] or "[]") if row else []

    def add_run_items(self, run_id: int, ids: Iterable[str], role: str, note: str = "") -> None:
        self.conn.executemany("INSERT OR IGNORE INTO run_items VALUES (?,?,?,?)",
                              [(run_id, i, role, note) for i in ids])
        self.commit()

    def run_items(self, run_id: int, role: str | None = None) -> list[sqlite3.Row]:
        if role:
            return self.q("SELECT * FROM run_items WHERE run_id=? AND role=?", (run_id, role))
        return self.q("SELECT * FROM run_items WHERE run_id=?", (run_id,))

    def abandon_stale_runs(self, alive: set[int]) -> int:
        """Mark 'running' rows whose process is gone; returns how many."""
        rows = self.q("SELECT id FROM runs WHERE status='running'")
        stale = [r["id"] for r in rows if r["id"] not in alive]
        for run_id in stale:
            self.update_run(run_id, status="abandoned")
        return len(stale)

    # ------------------------------------------------------------ kv
    def get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))
        self.commit()

    # ------------------------------------------------------------ stats
    def stats(self) -> dict[str, Any]:
        row = self.one(
            "SELECT COUNT(*) AS n, SUM(backfill) AS backfilled, "
            "SUM(embedding IS NOT NULL) AS embedded, SUM(note IS NOT NULL) AS noted, "
            "MIN(published_at) AS oldest FROM items WHERE track='knowledge'")
        news = self.one("SELECT COUNT(*) AS n FROM items WHERE track='news'")
        size = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_name(self.path.name + "-wal")
        if wal.exists():
            size += wal.stat().st_size
        return {
            "items": row["n"] or 0,
            "backfilled": row["backfilled"] or 0,
            "embedded": row["embedded"] or 0,
            "noted": row["noted"] or 0,
            "oldest": row["oldest"],
            "news": news["n"] or 0,
            "db_mb": round(size / 1e6, 1),
        }
