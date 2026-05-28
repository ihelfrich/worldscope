"""Tests for the lake-history backfill placeholder filter.

The backfill script (tools/backfill_lake_history.py) writes
state=="backfill_no_data" placeholders under lake/sections/<id>/<date>/.
These tests verify that:

  1. The trends module correctly identifies backfill payloads via the
     `_is_backfill_payload` helper.
  2. `section_trend()` excludes backfill days from medians and exposes
     `real_days` / `backfill_days` counters.
  3. The graphics-layer `_is_backfill_placeholder` reads structured.json
     and returns True for backfill dirs, False for real-pull dirs.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# trends._is_backfill_payload
# ---------------------------------------------------------------------------

def test_is_backfill_payload_recognizes_state_key():
    from worldscope.trends import _is_backfill_payload
    assert _is_backfill_payload({"state": "backfill_no_data"}) is True


def test_is_backfill_payload_recognizes_status_key():
    from worldscope.trends import _is_backfill_payload
    assert _is_backfill_payload({"status": "backfill_no_data"}) is True


def test_is_backfill_payload_rejects_real_pulls():
    from worldscope.trends import _is_backfill_payload
    assert _is_backfill_payload({"status": "ok", "items": [1, 2, 3]}) is False
    assert _is_backfill_payload({"status": "empty_ok"}) is False
    assert _is_backfill_payload({"status": "failed"}) is False


def test_is_backfill_payload_handles_non_dict():
    from worldscope.trends import _is_backfill_payload
    assert _is_backfill_payload(None) is False
    assert _is_backfill_payload([1, 2, 3]) is False
    assert _is_backfill_payload("ok") is False


# ---------------------------------------------------------------------------
# section_trend filtering
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path):
    """Build a fresh SnapshotStore in tmp_path and return it."""
    from worldscope.store import SnapshotStore
    return SnapshotStore(tmp_path / "store.sqlite")


def _write_snapshot(store, section_id: str, when: date, *, items: list[dict],
                    status: str = "ok"):
    """Bypass put() so we can stamp arbitrary status, including backfill."""
    import json as _json
    from datetime import datetime, timezone
    payload = {
        "schema_version": 2,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error": None,
        "items": items,
    }
    store._conn.execute(
        "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
        (section_id, when.isoformat(), payload["pulled_at"], _json.dumps(payload)),
    )
    store._conn.commit()


def test_section_trend_excludes_backfill_from_medians(tmp_path):
    from worldscope.trends import section_trend
    store = _make_store(tmp_path)
    today = date.today()
    # 7 backfill placeholders + 2 real days with 50 items each.
    for offset in range(2, 9):
        _write_snapshot(
            store, "demo", today - timedelta(days=offset),
            items=[], status="backfill_no_data",
        )
    for offset in (0, 1):
        _write_snapshot(
            store, "demo", today - timedelta(days=offset),
            items=[{"title": f"item {i}", "summary": ""} for i in range(50)],
        )

    result = section_trend(store, "demo")
    # Median over real days (50, 50) is 50, not 0.
    assert result["median_7d"] == 50, result
    assert result["median_14d"] == 50, result
    assert result["real_days"] == 2
    assert result["backfill_days"] == 7
    # Series still shows all 9 days for downstream renderers.
    assert len(result["series"]) == 9


def test_section_trend_no_history(tmp_path):
    from worldscope.trends import section_trend
    store = _make_store(tmp_path)
    result = section_trend(store, "nonexistent_section")
    assert result["today_count"] == 0
    assert result["median_7d"] == 0
    assert result["median_14d"] == 0
    assert result["real_days"] == 0
    assert result["backfill_days"] == 0


# ---------------------------------------------------------------------------
# graphics._is_backfill_placeholder
# ---------------------------------------------------------------------------

def test_is_backfill_placeholder_reads_structured_json(tmp_path):
    from worldscope.graphics import _is_backfill_placeholder
    bf = tmp_path / "bf"
    bf.mkdir()
    (bf / "structured.json").write_text(
        json.dumps({"state": "backfill_no_data", "counts": {}}),
        encoding="utf-8",
    )
    assert _is_backfill_placeholder(bf) is True


def test_is_backfill_placeholder_returns_false_for_real_pull(tmp_path):
    from worldscope.graphics import _is_backfill_placeholder
    real = tmp_path / "real"
    real.mkdir()
    (real / "structured.json").write_text(
        json.dumps({"section": "x", "date": "2026-05-28", "record_count": 40}),
        encoding="utf-8",
    )
    assert _is_backfill_placeholder(real) is False


def test_is_backfill_placeholder_returns_false_for_missing_file(tmp_path):
    from worldscope.graphics import _is_backfill_placeholder
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _is_backfill_placeholder(empty) is False
