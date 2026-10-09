"""Tests for lake_maintenance.maintain's hard size ceiling.

Age-based retention alone let lake/db/worldscope.sqlite reach 105 MB in CI
on 2026-07-17 and every push that day was rejected. The ceiling drops the
oldest remaining day of records until the file fits, mirroring the
snapshot-store contract pinned in test_store_prune.py.
"""
import sqlite3

from worldscope import history
from worldscope.lake_archive import load_index, partition_files, read_partition
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
    arch = tmp_path / "archive"
    _mk_lake(lake, days=range(1, 11))
    res = maintain(lake, keep_days=365, max_mb=0.9, archive_dir=arch)
    assert res["ok"]
    kept = _days_left(lake)
    assert "2026-07-10" in kept          # newest day survives
    assert "2026-07-01" not in kept      # oldest goes first
    assert lake.stat().st_size / 1e6 <= 0.9 or kept == ["2026-07-10"]

    # Archive-before-evict: every evicted day is in the monthly partition and
    # recoverable through the cross-time read API, in order, payload intact.
    evicted = sorted({f"2026-07-{d:02d}" for d in range(1, 11)} - set(kept))
    assert evicted and res["records_archived"] == res["records_deleted"] == len(evicted)
    part = arch / "records" / "2026-07.jsonl.gz"
    assert part.exists()
    archived = list(read_partition(part))
    assert [r["ingested_at"][:10] for r in archived] == evicted
    assert all(r["payload"] == "x" * 200_000 for r in archived)
    idx = {e["file"]: e for e in load_index(arch)["partitions"]["records"]}
    assert idx["2026-07.jsonl.gz"]["rows"] == len(evicted)
    assert idx["2026-07.jsonl.gz"]["min_ingested_at"] == f"{evicted[0]}T12:00:00Z"
    recovered = list(history.iter_records("2026-07-01", "2026-07-31",
                                          db_path=lake, archive_dir=arch))
    assert [r["ingested_at"][:10] for r in recovered] == evicted + kept
    assert history.daily_counts("2026-07-01", "2026-07-31", db_path=lake, archive_dir=arch) \
        == {history._as_date(d): 1 for d in evicted + kept}


def test_ceiling_noop_when_under_limit(tmp_path):
    lake = tmp_path / "lake.sqlite"
    arch = tmp_path / "archive"
    _mk_lake(lake, days=[9, 10], fat_bytes=1_000)
    res = maintain(lake, keep_days=365, max_mb=50.0, archive_dir=arch)
    assert res["ok"]
    assert _days_left(lake) == ["2026-07-09", "2026-07-10"]
    assert res["records_archived"] == 0 and not arch.exists()   # nothing evicted, nothing written


def test_keep_days_eviction_archives_first(tmp_path):
    lake = tmp_path / "lake.sqlite"
    arch = tmp_path / "archive"
    _mk_lake(lake, days=[1, 2], fat_bytes=1_000)      # 2026-07: ancient vs today
    res = maintain(lake, keep_days=30, max_mb=50.0, archive_dir=arch)
    assert res["ok"] and res["records_deleted"] == 2 == res["records_archived"]
    assert _days_left(lake) == []
    assert [p.name for p in partition_files(arch, "records")] == ["2026-07.jsonl.gz"]
    ids = [r["ingested_at"] for r in history.iter_records("2026-07-01", "2026-07-02",
                                                          db_path=lake, archive_dir=arch)]
    assert ids == ["2026-07-01T12:00:00Z", "2026-07-02T12:00:00Z"]
    # a second run is a no-op for the archive (deterministic bytes)
    before = (arch / "records" / "2026-07.jsonl.gz").read_bytes()
    maintain(lake, keep_days=30, max_mb=50.0, archive_dir=arch)
    assert (arch / "records" / "2026-07.jsonl.gz").read_bytes() == before


def test_no_archive_opt_out(tmp_path):
    lake = tmp_path / "lake.sqlite"
    _mk_lake(lake, days=[1, 2], fat_bytes=1_000)
    res = maintain(lake, keep_days=30, max_mb=50.0, archive_dir=None)
    assert res["records_deleted"] == 2 and res["records_archived"] == 0
    assert res["archive_dir"] is None
