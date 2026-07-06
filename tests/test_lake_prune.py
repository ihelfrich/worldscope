"""Tests for lake_maintenance.maintain — lake DB retention + hard size ceiling.

The lake `records` table grows ~1 MB/day and its data is younger than the
age-based retention window, so age pruning reclaims nothing and the file
crept back toward GitHub's 100 MB push limit. These tests pin the ceiling:
the oldest ingested day goes first, the newest day always survives, and
orphaned link rows are cascaded out with their records.
"""
import sqlite3

from worldscope.lake_maintenance import maintain


def _mk_lake(path, records, *, entities=None):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE records (
            id TEXT PRIMARY KEY, source_id TEXT, section_id TEXT,
            ingested_at TEXT NOT NULL, original_url TEXT, original_text TEXT,
            original_lang TEXT DEFAULT 'en', record_date TEXT, license TEXT,
            extra_json TEXT);
        CREATE TABLE record_entities (record_id TEXT, entity_id TEXT);
        CREATE TABLE record_embeddings (record_id TEXT, vec TEXT);
        CREATE TABLE claim_evidence (claim_id TEXT, record_id TEXT);
        CREATE TABLE quarantine (id TEXT PRIMARY KEY, detected_at TEXT);
        """
    )
    con.executemany(
        "INSERT INTO records (id, source_id, section_id, ingested_at, original_text) "
        "VALUES (?, ?, ?, ?, ?)", records)
    for rid, eid in (entities or []):
        con.execute("INSERT INTO record_entities VALUES (?, ?)", (rid, eid))
    con.commit()
    con.close()


def _ingested_days(path):
    con = sqlite3.connect(path)
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT date(ingested_at) FROM records ORDER BY 1").fetchall()]
    con.close()
    return days


def _fat_records(day, n, size=60_000):
    blob = "x" * size
    return [(f"{day}-{i}", "src", "sec", f"{day}T00:00:00Z", blob) for i in range(n)]


def test_size_ceiling_drops_oldest_ingested_day_first(tmp_path):
    lake = tmp_path / "worldscope.sqlite"
    rows = []
    for day in ("2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"):
        rows += _fat_records(day, 40)
    _mk_lake(lake, rows)
    assert lake.stat().st_size / 1e6 > 2.0  # starts fat

    res = maintain(lake, keep_days=3650, max_mb=2.0)  # age prune inert; ceiling bites
    assert res["ok"]
    kept = _ingested_days(lake)
    assert "2026-06-04" in kept                      # newest always survives
    assert "2026-06-01" not in kept                  # oldest dropped first
    assert lake.stat().st_size / 1e6 <= 2.0 or kept == ["2026-06-04"]


def test_newest_day_survives_even_if_over_ceiling(tmp_path):
    lake = tmp_path / "worldscope.sqlite"
    _mk_lake(lake, _fat_records("2026-06-10", 60))   # single fat day
    res = maintain(lake, keep_days=3650, max_mb=0.01)  # impossible ceiling
    assert res["ok"]
    # It must not empty the lake to nothing; the newest day is protected.
    assert _ingested_days(lake) == ["2026-06-10"]


def test_ceiling_cascades_orphan_cleanup(tmp_path):
    lake = tmp_path / "worldscope.sqlite"
    rows = _fat_records("2026-06-01", 40) + _fat_records("2026-06-05", 40)
    ents = [("2026-06-01-0", "person:x"), ("2026-06-05-0", "person:y")]
    _mk_lake(lake, rows, entities=ents)

    maintain(lake, keep_days=3650, max_mb=2.0)
    con = sqlite3.connect(lake)
    orphaned = con.execute(
        "SELECT COUNT(*) FROM record_entities WHERE record_id NOT IN "
        "(SELECT id FROM records)").fetchone()[0]
    con.close()
    assert orphaned == 0  # link rows for dropped records were cleaned up


def test_under_ceiling_is_a_noop(tmp_path):
    lake = tmp_path / "worldscope.sqlite"
    _mk_lake(lake, _fat_records("2026-06-01", 5) + _fat_records("2026-06-02", 5))
    res = maintain(lake, keep_days=3650, max_mb=500.0)
    assert res["ok"]
    assert _ingested_days(lake) == ["2026-06-01", "2026-06-02"]  # nothing dropped
