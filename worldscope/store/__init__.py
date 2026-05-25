"""SQLite-backed snapshot store. Each section's daily pull writes a JSON blob
keyed by (section_id, date) so the diff layer can compare today vs. yesterday.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".worldscope" / "store.sqlite"


class SnapshotStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                section_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,   -- YYYY-MM-DD
                pulled_at TEXT NOT NULL,        -- ISO UTC
                payload TEXT NOT NULL,          -- JSON
                PRIMARY KEY (section_id, snapshot_date)
            )
        """)
        self._conn.commit()

    def put(self, section_id: str, payload: Any, *, when: date | None = None) -> None:
        d = (when or date.today()).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
            (section_id, d, datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
        )
        self._conn.commit()

    def get(self, section_id: str, *, when: date | None = None) -> Any | None:
        d = (when or date.today()).isoformat()
        row = self._conn.execute(
            "SELECT payload FROM snapshots WHERE section_id = ? AND snapshot_date = ?",
            (section_id, d),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def previous(self, section_id: str, *, before: date | None = None) -> Any | None:
        """Most recent snapshot strictly before `before` (default: today)."""
        cutoff = (before or date.today()).isoformat()
        row = self._conn.execute(
            "SELECT payload FROM snapshots WHERE section_id = ? AND snapshot_date < ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (section_id, cutoff),
        ).fetchone()
        return json.loads(row[0]) if row else None
