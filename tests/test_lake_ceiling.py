"""Tests for lake_maintenance.maintain's hard size ceiling.

Age-based retention alone let lake/db/worldscope.sqlite reach 105 MB in CI
on 2026-07-17 and every push that day was rejected. The ceiling drops the
oldest remaining day of records until the file fits, mirroring the
snapshot-store contract pinned in test_store_prune.py.
"""
import sqlite3

from worldscope.lake_maintenance import maintain


def _mk_lake(path, days, fat_bytes=200_000):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, ingested_at TEXT, payload TEXT)")
    con.execute("CREATE TABLE quarantine (id INTEGER PRIMARY KEY, detected_at TEXT)")
    fat = "x" * fat_bytes
    for d in days:
        con.execute("INSERT INTO records (ingested_at, payload) VALUES (?, ?)",
                    (f"2026-07-{d:02d}T12:00:00Z", fat))
    con.commit()
    con.close()


def _days_left(path):
    con = sqlite3.connect(path)
    rows = [r[0] for r in con.execute(
        "SELECT DISTINCT date(ingested_at) FROM records ORDER BY 1")]
    con.close()
    return rows


def test_ceiling_drops_oldest_days_first(tmp_path):
    lake = tmp_path / "lake.sqlite"
    _mk_lake(lake, days=range(1, 11))
    res = maintain(lake, keep_days=365, max_mb=0.9)
    assert res["ok"]
    kept = _days_left(lake)
    assert "2026-07-10" in kept          # newest day survives
    assert "2026-07-01" not in kept      # oldest goes first
    assert lake.stat().st_size / 1e6 <= 0.9 or kept == ["2026-07-10"]


def test_ceiling_noop_when_under_limit(tmp_path):
    lake = tmp_path / "lake.sqlite"
    _mk_lake(lake, days=[9, 10], fat_bytes=1_000)
    res = maintain(lake, keep_days=365, max_mb=50.0)
    assert res["ok"]
    assert _days_left(lake) == ["2026-07-09", "2026-07-10"]
