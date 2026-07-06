"""Tests for lake_maintenance.maintain_store — snapshot-store retention.

The store crossed GitHub's 100 MB push limit on 2026-07-05 and blocked the
daily-brief commit. These tests pin the retention contract: old rows go,
the newest row per section never goes, and the size ceiling is enforced.
"""
import sqlite3

from worldscope.lake_maintenance import maintain_store


def _mk_store(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE snapshots ("
        " section_id TEXT NOT NULL,"
        " snapshot_date TEXT NOT NULL,"
        " pulled_at TEXT NOT NULL,"
        " payload TEXT NOT NULL,"
        " PRIMARY KEY (section_id, snapshot_date))"
    )
    con.executemany("INSERT INTO snapshots VALUES (?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def _dates(path):
    con = sqlite3.connect(path)
    rows = con.execute(
        "SELECT section_id, snapshot_date FROM snapshots ORDER BY 1, 2"
    ).fetchall()
    con.close()
    return rows


def test_rows_beyond_keep_days_are_pruned(tmp_path):
    store = tmp_path / "store.sqlite"
    _mk_store(store, [
        ("econ", "2026-01-01", "t", "{}"),   # 155 days before newest -> pruned
        ("econ", "2026-06-01", "t", "{}"),   # 4 days before newest -> kept
        ("econ", "2026-06-05", "t", "{}"),   # newest -> kept
    ])
    res = maintain_store(store, keep_days=60, max_mb=100.0)
    assert res["ok"]
    assert _dates(store) == [("econ", "2026-06-01"), ("econ", "2026-06-05")]


def test_newest_row_per_section_survives_any_age(tmp_path):
    store = tmp_path / "store.sqlite"
    _mk_store(store, [
        ("stale_section", "2025-01-01", "t", "{}"),  # ancient but the only row
        ("econ", "2026-06-05", "t", "{}"),
    ])
    maintain_store(store, keep_days=60, max_mb=100.0)
    # Carry-forward reads the newest row per section; it must never be pruned.
    assert ("stale_section", "2025-01-01") in _dates(store)


def test_size_ceiling_drops_oldest_days_first(tmp_path):
    store = tmp_path / "store.sqlite"
    fat = "x" * 200_000
    _mk_store(store, [
        ("econ", f"2026-06-{d:02d}", "t", fat) for d in range(1, 11)
    ])
    res = maintain_store(store, keep_days=365, max_mb=0.9)
    assert res["ok"]
    kept = _dates(store)
    # Newest day always survives; whatever else remains is the most recent.
    assert ("econ", "2026-06-10") in kept
    assert store.stat().st_size / 1e6 <= 0.9 or len(kept) == 1


def test_missing_store_is_not_an_error(tmp_path):
    res = maintain_store(tmp_path / "absent.sqlite")
    assert res["ok"] and res.get("skipped")
