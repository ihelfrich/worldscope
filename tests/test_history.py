"""Tests for worldscope.history + worldscope.lake_archive: the cross-time
read API over live DB + monthly gzip archive partitions, the archive
writer's invariants (sorted, de-duplicated, deterministic, size-capped), and
the raw.jsonl backfill.
"""
import gzip
import hashlib
import json
import sqlite3
from datetime import date

import pytest

from worldscope import history, lake_archive
from worldscope.lake import Lake
from worldscope.lake_archive import (archive_records_where, load_index, partition_files,
                                     read_partition, write_rows)


# --------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------- #

def _rec(i, ts, section="firms", source="src", text="hello world", **kw):
    r = {"id": f"r{i:04d}", "source_id": source, "section_id": section,
         "ingested_at": ts, "last_seen_at": ts, "original_url": f"https://x/{i}",
         "original_text": text, "original_lang": "en", "record_date": ts[:10],
         "license": "pd", "extra_json": "{}"}
    r.update(kw)
    return r


def _mk_real_lake(path, rows, links=()):
    """A real schema lake (via Lake.open so migrations run) with rows."""
    lk = Lake.open(path)
    for sid in sorted({r["source_id"] for r in rows} | {"src"}):
        lk.register_source(source_id=sid, name=sid, url=None, license="pd",
                           tier="primary_document")
    for r in rows:
        lk._conn.execute(
            "INSERT INTO records (id, source_id, section_id, ingested_at, last_seen_at, "
            "original_url, original_text, original_lang, record_date, license, extra_json) "
            "VALUES (:id, :source_id, :section_id, :ingested_at, :last_seen_at, :original_url, "
            ":original_text, :original_lang, :record_date, :license, :extra_json)", r)
    for rid, eid, name in links:
        lk.upsert_entity(entity_id=eid, type="org", canonical_name=name, aliases=[name.upper()])
        lk.link_record_entity(rid, eid)
    lk.close()


def _rows(path):
    return list(read_partition(path))


# --------------------------------------------------------------------- #
# lake_archive: writer invariants
# --------------------------------------------------------------------- #

def test_write_rows_partitions_by_month_sorted_and_deduped(tmp_path):
    arch = tmp_path / "archive"
    rows = [_rec(3, "2026-07-02T00:00:00Z"), _rec(1, "2026-07-01T00:00:00Z"),
            _rec(2, "2026-08-01T00:00:00Z"), _rec(1, "2026-07-05T00:00:00Z")]  # dup id, later
    res = write_rows(arch, "records", rows)
    assert res["written"] == 3
    assert [p.name for p in partition_files(arch, "records")] == \
        ["2026-07.jsonl.gz", "2026-08.jsonl.gz"]
    july = _rows(arch / "records" / "2026-07.jsonl.gz")
    assert [r["id"] for r in july] == ["r0001", "r0003"]
    assert july[0]["ingested_at"] == "2026-07-01T00:00:00Z"   # first-seen wins
    idx = load_index(arch)
    e = {x["file"]: x for x in idx["partitions"]["records"]}
    assert e["2026-07.jsonl.gz"]["rows"] == 2
    assert e["2026-07.jsonl.gz"]["min_ingested_at"] == "2026-07-01T00:00:00Z"
    assert e["2026-07.jsonl.gz"]["max_ingested_at"] == "2026-07-02T00:00:00Z"
    assert e["2026-07.jsonl.gz"]["sha256"] == hashlib.sha256(
        (arch / "records" / "2026-07.jsonl.gz").read_bytes()).hexdigest()
    assert idx["partitions"]["record_entities"] == []


def test_write_rows_is_deterministic(tmp_path):
    rows = [_rec(i, f"2026-07-{(i % 28) + 1:02d}T01:00:00Z") for i in range(300)]
    write_rows(tmp_path / "a", "records", rows)
    write_rows(tmp_path / "b", "records", list(reversed(rows)))
    a = (tmp_path / "a" / "records" / "2026-07.jsonl.gz").read_bytes()
    b = (tmp_path / "b" / "records" / "2026-07.jsonl.gz").read_bytes()
    assert a == b
    assert load_index(tmp_path / "a") == load_index(tmp_path / "b")


def test_append_newer_rows_keeps_existing_bytes_and_order(tmp_path):
    arch = tmp_path / "archive"
    write_rows(arch, "records", [_rec(1, "2026-07-01T00:00:00Z"), _rec(2, "2026-07-02T00:00:00Z")])
    p = arch / "records" / "2026-07.jsonl.gz"
    before = p.read_bytes()
    res = write_rows(arch, "records", [_rec(3, "2026-07-03T00:00:00Z"),
                                       _rec(2, "2026-07-09T00:00:00Z")])  # r0002 is a dup
    assert res["written"] == 1
    after = p.read_bytes()
    assert after.startswith(before)                    # git-friendly append
    assert [r["id"] for r in _rows(p)] == ["r0001", "r0002", "r0003"]
    assert [r["ingested_at"] for r in _rows(p)] == sorted(r["ingested_at"] for r in _rows(p))
    assert {x["file"]: x["rows"] for x in load_index(arch)["partitions"]["records"]} == \
        {"2026-07.jsonl.gz": 3}


def test_out_of_order_rows_trigger_sorted_rewrite(tmp_path):
    arch = tmp_path / "archive"
    write_rows(arch, "records", [_rec(5, "2026-07-05T00:00:00Z")])
    write_rows(arch, "records", [_rec(1, "2026-07-01T00:00:00Z")])
    p = arch / "records" / "2026-07.jsonl.gz"
    assert [r["id"] for r in _rows(p)] == ["r0001", "r0005"]
    assert load_index(arch)["partitions"]["records"][0]["min_ingested_at"] == "2026-07-01T00:00:00Z"


def test_partition_splits_at_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(lake_archive, "MAX_PART_BYTES", 20_000)
    monkeypatch.setattr(lake_archive, "_FLUSH_EVERY", 10)
    arch = tmp_path / "archive"
    import random
    rnd = random.Random(1)
    rows = [_rec(i, f"2026-07-{(i % 28) + 1:02d}T00:00:00Z",
                 text="".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(400)))
            for i in range(600)]
    write_rows(arch, "records", rows)
    files = partition_files(arch, "records")
    assert len(files) > 1
    assert files[0].name == "2026-07.jsonl.gz" and files[1].name == "2026-07-part2.jsonl.gz"
    assert all(p.stat().st_size <= 20_000 + 8_000 for p in files)   # one flush window of slack
    got = [r["id"] for p in files for r in _rows(p)]
    assert len(got) == 600 and got == sorted(got, key=lambda i: (rows[int(i[1:])]["ingested_at"], i))
    # an append that would overflow the last part opens the next part
    write_rows(arch, "records", [_rec(900, "2026-07-29T00:00:00Z", text="z" * 30_000)])
    names = [p.name for p in partition_files(arch, "records")]
    assert names[-1] == f"2026-07-part{len(names)}.jsonl.gz"
    assert sum(x["rows"] for x in load_index(arch)["partitions"]["records"]) == 601


def test_archive_records_where_carries_links_and_names_not_quarantine(tmp_path):
    db = tmp_path / "lake.sqlite"
    _mk_real_lake(db, [_rec(1, "2026-07-01T00:00:00Z"), _rec(2, "2026-08-01T00:00:00Z")],
                  links=[("r0001", "org:rosneft", "Rosneft")])
    con = sqlite3.connect(db)
    con.execute("INSERT INTO quarantine VALUES ('q1', 'src', 'firms', '{}', 'bad', '2026-07-01T00:00:00Z')")
    con.commit()
    arch = tmp_path / "archive"
    res = archive_records_where(con, "ingested_at < ?", ("2026-07-15T00:00:00Z",), root=arch)
    con.close()
    assert res["records"] == 1 and res["record_entities"] == 1
    assert [p.name for p in partition_files(arch, "records")] == ["2026-07.jsonl.gz"]
    link = _rows(arch / "record_entities" / "2026-07.jsonl.gz")[0]
    assert link == {"record_id": "r0001", "entity_id": "org:rosneft",
                    "entity_name": "Rosneft", "ingested_at": "2026-07-01T00:00:00Z"}
    rec = _rows(arch / "records" / "2026-07.jsonl.gz")[0]
    assert rec["id"] == "r0001" and rec["last_seen_at"] == "2026-07-01T00:00:00Z"
    assert not (arch / "quarantine").exists()
    assert "q1" not in gzip.decompress((arch / "records" / "2026-07.jsonl.gz").read_bytes()).decode()


# --------------------------------------------------------------------- #
# history: read API over live + archive
# --------------------------------------------------------------------- #

@pytest.fixture
def world(tmp_path):
    """Archive holds June/July, live DB holds August; r0003 is in both."""
    db = tmp_path / "lake.sqlite"
    arch = tmp_path / "archive"
    write_rows(arch, "records", [
        _rec(1, "2026-06-10T08:00:00Z", text="Rosneft output rises"),
        _rec(2, "2026-07-01T08:00:00Z", section="markets", text="oil futures"),
        _rec(3, "2026-07-20T08:00:00Z", text="Rosneft sanctions"),
    ])
    write_rows(arch, "record_entities", [
        {"record_id": "r0001", "entity_id": "org:rosneft", "entity_name": "Rosneft",
         "ingested_at": "2026-06-10T08:00:00Z"},
        {"record_id": "r0003", "entity_id": "org:rosneft", "entity_name": "Rosneft",
         "ingested_at": "2026-07-20T08:00:00Z"},
    ])
    _mk_real_lake(db, [
        _rec(3, "2026-08-02T08:00:00Z", text="Rosneft sanctions (re-ingested)"),
        _rec(4, "2026-08-03T08:00:00Z", section="markets", source="other", text="brent"),
        _rec(5, "2026-08-03T09:00:00Z", text="ROSNEFT deal"),
    ], links=[("r0003", "org:rosneft", "Rosneft"), ("r0005", "org:rosneft", "Rosneft")])
    return db, arch


def test_iter_records_unions_and_dedups(world):
    db, arch = world
    got = list(history.iter_records("2026-06-01", "2026-08-31", db_path=db, archive_dir=arch))
    assert [r["id"] for r in got] == ["r0001", "r0002", "r0003", "r0004", "r0005"]
    r3 = next(r for r in got if r["id"] == "r0003")
    assert r3["ingested_at"] == "2026-07-20T08:00:00Z"        # earliest copy wins
    # inclusive end date, filters
    assert [r["id"] for r in history.iter_records("2026-07-20", "2026-07-20", db_path=db, archive_dir=arch)] == ["r0003"]
    assert [r["id"] for r in history.iter_records("2026-06-01", "2026-08-31", section_id="markets",
                                                  db_path=db, archive_dir=arch)] == ["r0002", "r0004"]
    assert [r["id"] for r in history.iter_records("2026-06-01", "2026-08-31", source_id="other",
                                                  db_path=db, archive_dir=arch)] == ["r0004"]
    assert [r["id"] for r in history.iter_records("2026-06-01", "2026-08-31", text_contains="rosneft",
                                                  db_path=db, archive_dir=arch)] == ["r0001", "r0003", "r0005"]
    assert list(history.iter_records("2025-01-01", "2025-12-31", db_path=db, archive_dir=arch)) == []


def test_daily_counts_and_entity_timeline(world):
    db, arch = world
    assert history.daily_counts("2026-06-01", "2026-08-31", db_path=db, archive_dir=arch) == {
        date(2026, 6, 10): 1, date(2026, 7, 1): 1, date(2026, 7, 20): 1, date(2026, 8, 3): 2}
    assert history.daily_counts("2026-06-01", "2026-08-31", section_id="markets",
                                db_path=db, archive_dir=arch) == {date(2026, 7, 1): 1, date(2026, 8, 3): 1}
    for name in ("Rosneft", "rosneft", "ROSNEFT", "org:rosneft"):
        assert history.entity_timeline(name, "2026-06-01", "2026-08-31", db_path=db, archive_dir=arch) == {
            date(2026, 6, 10): 1, date(2026, 7, 20): 1, date(2026, 8, 3): 1}, name


def test_history_works_without_db_or_archive(tmp_path):
    assert list(history.iter_records("2026-01-01", "2026-12-31", db_path=tmp_path / "none.sqlite",
                                     archive_dir=tmp_path / "none")) == []
    assert history.daily_counts("2026-01-01", "2026-12-31", db_path=tmp_path / "none.sqlite",
                                archive_dir=tmp_path / "none") == {}
    assert history.entity_timeline("x", "2026-01-01", "2026-12-31", db_path=tmp_path / "none.sqlite",
                                   archive_dir=tmp_path / "none") == {}


def test_cli_counts_and_search(world, capsys):
    db, arch = world
    base = ["--db", str(db), "--archive-dir", str(arch)]
    assert history.main(base + ["counts", "--since", "2026-06-01", "--until", "2026-08-31"]) == 0
    out = capsys.readouterr().out
    assert "2026-06-10  1" in out and "5 records over 4 days" in out
    assert history.main(base + ["search", "--q", "rosneft", "--since", "2026-06-01",
                                "--until", "2026-08-31", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "Rosneft output rises" in out and "3 matches (showing 2)" in out
    assert history.main(base + ["timeline", "--entity", "Rosneft", "--since", "2026-06-01",
                                "--until", "2026-08-31"]) == 0
    assert "3 records mention 'Rosneft' over 3 days" in capsys.readouterr().out


# --------------------------------------------------------------------- #
# upsert_record keeps first-seen ingested_at
# --------------------------------------------------------------------- #

def test_upsert_record_preserves_first_seen(tmp_path):
    lk = Lake.open(tmp_path / "lake.sqlite")
    assert lk.schema_version() == 2
    lk.register_source(source_id="src", name="src", url=None, license="pd", tier="primary_document")
    lk.upsert_record(record_id="a", source_id="src", section_id="firms",
                     original_url="u", original_text="v1")
    lk._conn.execute("UPDATE records SET ingested_at='2026-01-01T00:00:00Z', "
                     "last_seen_at='2026-01-01T00:00:00Z'")
    lk.upsert_record(record_id="a", source_id="src", section_id="firms",
                     original_url="u", original_text="v2")
    row = dict(lk._conn.execute("SELECT * FROM records").fetchone())
    lk.close()
    assert row["ingested_at"] == "2026-01-01T00:00:00Z"
    assert row["last_seen_at"] > "2026-01-01T00:00:00Z"
    assert row["original_text"] == "v2"


def test_v1_lake_migrates_to_v2(tmp_path):
    from worldscope.lake import SCHEMA_V1
    db = tmp_path / "v1.sqlite"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA_V1)
    con.execute("INSERT INTO sources (id, name, tier) VALUES ('s', 's', 't')")
    con.execute("INSERT INTO records (id, source_id, section_id, ingested_at) "
                "VALUES ('z', 's', 'sec', '2026-02-02T00:00:00Z')")
    con.commit(); con.close()
    lk = Lake.open(db)
    assert lk.schema_version() == 2
    row = dict(lk._conn.execute("SELECT ingested_at, last_seen_at FROM records").fetchone())
    assert row == {"ingested_at": "2026-02-02T00:00:00Z", "last_seen_at": "2026-02-02T00:00:00Z"}
    lk.close()
    assert Lake.open(db).schema_version() == 2      # idempotent re-open


# --------------------------------------------------------------------- #
# backfill from lake/sections/<section>/<day>/raw.jsonl
# --------------------------------------------------------------------- #

def _write_raw(root, section, day, rows):
    d = root / section / day
    d.mkdir(parents=True)
    with open(d / "raw.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_backfill_from_sections(tmp_path):
    sections = tmp_path / "sections"
    raw1 = {"id": "abc", "source_id": "firms", "section_id": "firms",
            "ingested_at_utc": "2026-06-01T10:00:00Z", "original_url": "https://x/1",
            "original_text": "Rosneft refinery fire", "original_lang": "en",
            "record_date": "2026-06-01", "license": "pd", "entities": ["org:rosneft"],
            "extra": {"k": 1}, "source_tier": "primary_document"}
    _write_raw(sections, "firms", "2026-06-01", [raw1])
    # same item again next day (re-pulled), plus a bare item without an id
    raw1b = dict(raw1, ingested_at_utc="2026-06-02T10:00:00Z", original_text="updated")
    bare = {"url": "https://x/2", "title": "T", "summary": "S", "date": "2026-06-02"}
    _write_raw(sections, "firms", "2026-06-02", [raw1b, bare])
    _write_raw(sections, "markets", "2026-07-03", [dict(raw1, id="m1", section_id="markets",
                                                        ingested_at_utc="2026-07-03T00:00:00Z")])
    _write_raw(sections, "_meta", "2026-07-03", [{"id": "ignored"}])
    (sections / "firms" / "2026-07-04").mkdir()
    _write_raw(sections, "firms", "2026-09-01", [dict(raw1, id="live-era")])   # not older than DB

    db = tmp_path / "lake.sqlite"
    _mk_real_lake(db, [_rec(9, "2026-08-15T00:00:00Z")], links=[("r0009", "org:rosneft", "Rosneft")])
    arch = tmp_path / "archive"
    res = history.backfill_from_sections("2026-05-20", sections_root=sections, db_path=db,
                                         archive_dir=arch, log=lambda *_: None)
    assert res["ok"] and not res["stopped"]
    assert res["until"] == "2026-08-14"                       # day before oldest live row
    assert res["records"] == 3 and res["links"] == 2
    assert res["months"] == ["2026-06", "2026-07"]
    assert sorted(p.name for p in partition_files(arch, "records")) == ["2026-06.jsonl.gz", "2026-07.jsonl.gz"]

    from worldscope.sections import Section
    bare_id = Section._item_id(bare)
    june = {r["id"]: r for r in _rows(arch / "records" / "2026-06.jsonl.gz")}
    assert set(june) == {"abc", bare_id}
    assert june["abc"]["ingested_at"] == "2026-06-01T10:00:00Z"      # first sighting
    assert june["abc"]["last_seen_at"] == "2026-06-02T10:00:00Z"     # last sighting
    assert june["abc"]["original_text"] == "Rosneft refinery fire"
    assert json.loads(june["abc"]["extra_json"]) == {"k": 1, "source_tier": "primary_document"}
    assert june[bare_id]["original_text"] == "T — S" and june[bare_id]["record_date"] == "2026-06-02"
    assert june[bare_id]["ingested_at"] == "2026-06-02T00:00:00Z"
    assert history._raw_ingested_at({"ingested_at_utc": "2026-10-08T14:00:00Z"}, "2026-09-21") \
        == "2026-09-21T00:00:00Z"                                     # regenerated folder: clamp
    assert history._raw_ingested_at({"ingested_at_utc": "2026-09-21T14:00:00Z"}, "2026-09-21") \
        == "2026-09-21T14:00:00Z"
    link = _rows(arch / "record_entities" / "2026-06.jsonl.gz")[0]
    assert link["entity_name"] == "Rosneft" and link["record_id"] == "abc"

    # recoverable through the read API together with the live DB
    ids = [r["id"] for r in history.iter_records("2026-05-01", "2026-09-30", db_path=db, archive_dir=arch)]
    assert ids == ["abc", bare_id, "m1", "r0009"]
    assert history.entity_timeline("Rosneft", "2026-05-01", "2026-09-30", db_path=db, archive_dir=arch) == {
        date(2026, 6, 1): 1, date(2026, 7, 3): 1, date(2026, 8, 15): 1}
    # idempotent: a second run changes nothing
    idx = load_index(arch)
    res2 = history.backfill_from_sections("2026-05-20", sections_root=sections, db_path=db,
                                          archive_dir=arch, log=lambda *_: None)
    assert res2["records"] == 0 and load_index(arch) == idx


def test_backfill_stops_within_budget(tmp_path):
    import random
    rnd = random.Random(7)
    sections = tmp_path / "sections"
    for i in range(3):   # ~40 KB compressed per month
        _write_raw(sections, "firms", f"2026-0{6+i}-01",
                   [{"id": f"id{i}{j}", "ingested_at_utc": f"2026-0{6+i}-01T00:00:00Z",
                     "original_text": "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz ")
                                              for _ in range(300))} for j in range(200)])
    arch = tmp_path / "archive"
    res = history.backfill_from_sections("2026-05-01", "2026-09-01", sections_root=sections,
                                         db_path=tmp_path / "none.sqlite", archive_dir=arch,
                                         max_total_mb=0.05, log=lambda *_: None)
    assert res["stopped"] and not res["ok"] and res["stop_month"] == "2026-07"
    assert res["bytes"] <= 50_000
    assert res["months"] == ["2026-06"]                     # June kept, July rolled back
    assert [p.name for p in partition_files(arch, "records")] == ["2026-06.jsonl.gz"]
    # what was kept is consistent with the index
    for e in load_index(arch)["partitions"]["records"]:
        assert (arch / "records" / e["file"]).exists()
