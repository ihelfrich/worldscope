"""SQLite-backed snapshot store. Each section's daily pull writes a JSON
payload keyed by (section_id, date) with status metadata. The diff layer
and the orchestrator use the metadata to distinguish:

  - a fresh pull that returned items                    → status="ok"
  - a fresh pull that legitimately returned zero items  → status="empty_ok"
  - a failed pull (exception during fetch/parse)        → status="failed"
  - an explicit skip (WORLDSCOPE_SKIP)                  → never writes a new snapshot;
                                                          the previous snapshot is reused
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Resolution order for the snapshot store path:
#   1. WORLDSCOPE_STORE_PATH env var (explicit override; used by CI)
#   2. <repo>/data/store.sqlite if the file exists (preferred: snapshots
#      travel with the repo so CI can carry-forward locally-generated content)
#   3. ~/.worldscope/store.sqlite (legacy default for first-time local runs)
def _resolve_default_path() -> Path:
    env = os.environ.get("WORLDSCOPE_STORE_PATH")
    if env:
        return Path(env).expanduser()
    repo_local = Path(__file__).resolve().parents[2] / "data" / "store.sqlite"
    if repo_local.exists() or repo_local.parent.exists():
        return repo_local
    return Path.home() / ".worldscope" / "store.sqlite"

DEFAULT_PATH = _resolve_default_path()

# Bump when the in-snapshot payload shape changes (used by schema validation).
SCHEMA_VERSION = 2


class SnapshotStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                section_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                pulled_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (section_id, snapshot_date)
            )
        """)
        self._conn.commit()

    # ---- write ---------------------------------------------------------

    def put(self, section_id: str, items: list[dict], *,
            status: str = "ok",
            error: Optional[str] = None,
            when: Optional[date] = None) -> bool:
        """Write a snapshot. `status` is one of: ok, empty_ok, failed.

        Invariant: an empty same-day snapshot may NOT replace a non-empty
        one. A morning cron run that pulled 200 items must not be
        clobbered by an afternoon manual run that got rate-limited and
        returned []. Same-day non-empty replacing non-empty is allowed.

        Returns True when the write happened, False when it was refused.

        Concurrency: the SELECT-then-INSERT pair is wrapped in a
        BEGIN IMMEDIATE transaction so two concurrent put() callers
        across processes can't both read "non-empty exists" and one
        clobber the other. SQLite's BEGIN IMMEDIATE acquires a
        reserved lock immediately, serializing the critical section.
        """
        d = (when or date.today()).isoformat()
        # Single critical section: lock the DB, check prior state, decide.
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            # Already in a transaction (nested call). Proceed without
            # opening a new one — caller owns the lock.
            pass
        try:
            if not items:
                existing = self._conn.execute(
                    "SELECT payload FROM snapshots "
                    "WHERE section_id = ? AND snapshot_date = ?",
                    (section_id, d),
                ).fetchone()
                if existing:
                    try:
                        prior = json.loads(existing[0])
                        if (prior.get("items") or []):
                            self._conn.execute("COMMIT")
                            return False
                    except Exception:
                        pass
            payload = {
                "schema_version": SCHEMA_VERSION,
                "pulled_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "error": error,
                "items": items,
            }
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
                (section_id, d, payload["pulled_at"], json.dumps(payload)),
            )
            self._conn.execute("COMMIT")
            return True
        except Exception:
            # On any failure inside the critical section, release the lock
            # so we don't leave the DB wedged for subsequent writers.
            try: self._conn.execute("ROLLBACK")
            except sqlite3.OperationalError: pass
            raise

    # ---- read ----------------------------------------------------------

    def get(self, section_id: str, *, when: Optional[date] = None) -> Optional[dict]:
        d = (when or date.today()).isoformat()
        row = self._conn.execute(
            "SELECT payload FROM snapshots WHERE section_id = ? AND snapshot_date = ?",
            (section_id, d),
        ).fetchone()
        return _validate(json.loads(row[0])) if row else None

    def previous(self, section_id: str, *, before: Optional[date] = None) -> Optional[dict]:
        """Most recent snapshot strictly before `before` (default: today)."""
        cutoff = (before or date.today()).isoformat()
        row = self._conn.execute(
            "SELECT snapshot_date, payload FROM snapshots "
            "WHERE section_id = ? AND snapshot_date < ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (section_id, cutoff),
        ).fetchone()
        if not row:
            return None
        snapshot_date, payload_json = row
        payload = _validate(json.loads(payload_json))
        if payload is not None:
            payload["snapshot_date"] = snapshot_date
        return payload

    def most_recent(self, section_id: str) -> Optional[dict]:
        """Most recent snapshot, regardless of date (used for carry-forward
        when WORLDSCOPE_SKIP is set or when today's pull failed)."""
        row = self._conn.execute(
            "SELECT snapshot_date, payload FROM snapshots "
            "WHERE section_id = ? ORDER BY snapshot_date DESC LIMIT 1",
            (section_id,),
        ).fetchone()
        if not row:
            return None
        snapshot_date, payload_json = row
        payload = _validate(json.loads(payload_json))
        if payload is not None:
            payload["snapshot_date"] = snapshot_date
        return payload


# --- schema validation -----------------------------------------------------

def _validate(payload: Any) -> Optional[dict]:
    """Cheap schema check on a cached payload. Returns None if the payload
    doesn't conform — caller treats that as "no usable cache" rather than
    crashing during render. This is the schema-drift defense Gemini caught."""
    if not isinstance(payload, dict):
        return None
    required = {"schema_version", "pulled_at", "status", "items"}
    if not required.issubset(payload.keys()):
        return None
    if payload.get("schema_version") != SCHEMA_VERSION:
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    # Each item must be a dict; ignore the rest of the structure (loose)
    if not all(isinstance(it, dict) for it in items):
        return None
    return payload
