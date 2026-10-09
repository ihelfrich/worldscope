"""history.py — cross-time read API over the live lake DB + the archive.

The committed lake (lake/db/worldscope.sqlite) only ever holds the newest
week-or-so of `records`; lake_maintenance evicts older days to stay under
GitHub's file-size limit and (since lake_archive) copies them first into
monthly gzip JSONL partitions under lake/archive/. This module is the one
place that queries both as if they were one table:

    from worldscope import history
    history.iter_records("2026-06-01", "2026-10-08", section_id="firms")
    history.daily_counts("2026-06-01", "2026-10-08")         # {date: n}
    history.entity_timeline("Rosneft", "2026-06-01", "2026-10-08")

Semantics:
  * a date range is inclusive and keys on records.ingested_at (first-seen);
  * the same record id can exist in several places (archived once, then
    re-ingested into the live DB, or archived twice across months). Every
    query de-duplicates by id and the EARLIEST ingested_at copy wins, so a
    record is counted on the day it was first seen;
  * rows come back as plain dicts with the `records` columns (archive rows
    may carry a superset: whatever columns the table had when archived);
  * pure stdlib (sqlite3 / gzip / json). If `duckdb` happens to be
    importable, archive partitions are scanned with read_json instead
    (faster); any failure there falls back to the stdlib scan, and the
    results are verified against index.json row counts first.

CLI:
    python -m worldscope.history counts --since 2026-06-01 [--section firms]
    python -m worldscope.history search --q rosneft --since 2026-06-01
    python -m worldscope.history timeline --entity "Russia" --since 2026-06-01
    python -m worldscope.history backfill-from-sections --since 2026-05-20
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union

from . import lake_archive
from .lake_archive import DEFAULT_ARCHIVE, partition_files, read_partition

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "lake" / "db" / "worldscope.sqlite"
DEFAULT_SECTIONS = REPO / "lake" / "sections"

DateLike = Union[date, datetime, str]
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RECORD_COLUMNS = ("id", "source_id", "section_id", "ingested_at", "last_seen_at",
                  "original_url", "original_text", "original_lang", "record_date",
                  "license", "extra_json")


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

def _as_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def _bounds(start: DateLike, end: DateLike) -> tuple[str, str]:
    """Inclusive date range -> [start_ts, end_ts) on ISO-8601 Z strings."""
    s, e = _as_date(start), _as_date(end)
    return f"{s.isoformat()}T00:00:00Z", f"{(e + timedelta(days=1)).isoformat()}T00:00:00Z"


def _day(ts: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None


def _connect_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None


def _matches(row: dict, section_id, source_id, needle) -> bool:
    if section_id is not None and row.get("section_id") != section_id:
        return False
    if source_id is not None and row.get("source_id") != source_id:
        return False
    if needle is not None and needle not in (row.get("original_text") or "").lower():
        return False
    return True


def _record_key(row: dict) -> tuple[str, str]:
    return (str(row.get("ingested_at") or ""), str(row.get("id") or ""))


def _partitions_for(archive_dir: Path, table: str, start_ts: str, end_ts: str) -> list[Path]:
    """Partition files that can hold rows in [start_ts, end_ts). Uses the
    index's min/max when the file is indexed, else the month in the name."""
    idx = lake_archive.load_index(archive_dir)
    by_file = {e["file"]: e for e in idx["partitions"].get(table, [])}
    out = []
    for p in partition_files(archive_dir, table):
        e = by_file.get(p.name)
        if e and e.get("min_ingested_at") and e.get("max_ingested_at"):
            if e["max_ingested_at"] < start_ts or e["min_ingested_at"] >= end_ts:
                continue
        else:
            m = lake_archive._PART_RE.match(p.name)
            month = m.group(1) if m else ""
            if month and (month < start_ts[:7] or month > end_ts[:7]):
                continue
        out.append(p)
    return out


# --------------------------------------------------------------------- #
# Archive scan (stdlib, with optional DuckDB fast path)
# --------------------------------------------------------------------- #

def _duckdb_enabled() -> bool:
    if os.environ.get("WORLDSCOPE_HISTORY_DUCKDB", "1") in ("0", "false", "no"):
        return False
    try:
        import duckdb  # noqa: F401
        return True
    except Exception:
        return False


def _duckdb_scan(files: list[Path], start_ts: str, end_ts: str, section_id, source_id,
                 needle, archive_dir: Path) -> Optional[list[dict]]:
    """Read the archive rows in range with DuckDB. Returns None (caller falls
    back to the stdlib scan) on any error or if the row counts disagree with
    index.json — DuckDB's reader must see every gzip member we wrote."""
    try:
        import duckdb
        idx = lake_archive.load_index(archive_dir)
        expected = {e["file"]: e["rows"] for e in idx["partitions"].get("records", [])}
        if any(p.name not in expected for p in files):
            return None
        cols = ", ".join(f"{c}: 'VARCHAR'" for c in RECORD_COLUMNS)
        flist = "[" + ", ".join("'" + str(p).replace("'", "''") + "'" for p in files) + "]"
        src = f"read_json({flist}, format='newline_delimited', columns={{{cols}}})"
        con = duckdb.connect()
        total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        if total != sum(expected[p.name] for p in files):
            return None
        where = ["ingested_at >= ?", "ingested_at < ?"]
        params: list = [start_ts, end_ts]
        if section_id is not None:
            where.append("section_id = ?"); params.append(section_id)
        if source_id is not None:
            where.append("source_id = ?"); params.append(source_id)
        if needle is not None:
            where.append("contains(lower(coalesce(original_text, '')), ?)"); params.append(needle)
        cur = con.execute(f"SELECT {', '.join(RECORD_COLUMNS)} FROM {src} "
                          f"WHERE {' AND '.join(where)} ORDER BY ingested_at, id", params)
        out = [dict(zip(RECORD_COLUMNS, r)) for r in cur.fetchall()]
        con.close()
        return out
    except Exception:
        return None


def _archive_records(archive_dir: Path, start_ts: str, end_ts: str, section_id=None,
                     source_id=None, needle=None) -> Iterator[dict]:
    """Rows of the records archive in range, sorted by (ingested_at, id)."""
    files = _partitions_for(archive_dir, "records", start_ts, end_ts)
    if not files:
        return
    if _duckdb_enabled():
        rows = _duckdb_scan(files, start_ts, end_ts, section_id, source_id, needle, archive_dir)
        if rows is not None:
            yield from rows
            return

    def one(p: Path) -> Iterator[dict]:
        for r in read_partition(p):
            ts = str(r.get("ingested_at") or "")
            if ts < start_ts or ts >= end_ts:
                continue
            if _matches(r, section_id, source_id, needle):
                yield r

    # Files of one month are already sorted; merge the months (and parts)
    # so overlapping partitions still come out in global order.
    yield from heapq.merge(*(one(p) for p in files), key=_record_key)


def _live_records(db_path: Path, start_ts: str, end_ts: str, section_id=None,
                  source_id=None, needle=None) -> Iterator[dict]:
    con = _connect_ro(db_path)
    if con is None:
        return
    try:
        if not _has_table(con, "records"):
            return
        where = ["ingested_at >= ?", "ingested_at < ?"]
        params: list = [start_ts, end_ts]
        if section_id is not None:
            where.append("section_id = ?"); params.append(section_id)
        if source_id is not None:
            where.append("source_id = ?"); params.append(source_id)
        if needle is not None:
            where.append("instr(lower(coalesce(original_text, '')), ?) > 0"); params.append(needle)
        cur = con.execute(f"SELECT * FROM records WHERE {' AND '.join(where)} "
                          f"ORDER BY ingested_at, id", params)
        for r in cur:
            yield dict(r)
    finally:
        con.close()


# --------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------- #

def iter_records(start_date: DateLike, end_date: DateLike, section_id: Optional[str] = None,
                 source_id: Optional[str] = None, text_contains: Optional[str] = None, *,
                 db_path: Path = DEFAULT_DB, archive_dir: Path = DEFAULT_ARCHIVE
                 ) -> Iterator[dict]:
    """Union of live DB + archive, inclusive on ingested_at date, ordered by
    (ingested_at, id), de-duplicated by id (earliest copy wins).
    text_contains is a case-insensitive substring match on original_text."""
    start_ts, end_ts = _bounds(start_date, end_date)
    needle = text_contains.lower() if text_contains else None
    streams = [
        _archive_records(archive_dir, start_ts, end_ts, section_id, source_id, needle),
        _live_records(db_path, start_ts, end_ts, section_id, source_id, needle),
    ]
    seen: set[str] = set()
    for row in heapq.merge(*streams, key=_record_key):
        rid = str(row.get("id"))
        if rid in seen:
            continue
        seen.add(rid)
        yield row


def daily_counts(start: DateLike, end: DateLike, section_id: Optional[str] = None, *,
                 db_path: Path = DEFAULT_DB, archive_dir: Path = DEFAULT_ARCHIVE
                 ) -> dict[date, int]:
    """{date: number of records first seen that day}, live + archive."""
    counts: dict[date, int] = {}
    for row in iter_records(start, end, section_id=section_id,
                            db_path=db_path, archive_dir=archive_dir):
        d = _day(row.get("ingested_at"))
        if d is not None:
            counts[d] = counts.get(d, 0) + 1
    return dict(sorted(counts.items()))


def resolve_entity_ids(entity_name: str, db_path: Path = DEFAULT_DB) -> set[str]:
    """Entity ids whose id, canonical name or alias equals entity_name
    (case-insensitive) in the live entities table. The name itself is also
    matched against archived record_entities rows by the caller."""
    ids = {entity_name}
    con = _connect_ro(db_path)
    if con is None:
        return ids
    try:
        if not _has_table(con, "entities"):
            return ids
        needle = entity_name.lower()
        for r in con.execute("SELECT id, canonical_name, aliases_json FROM entities "
                             "WHERE lower(canonical_name) = ? OR id = ? "
                             "OR instr(lower(aliases_json), ?) > 0",
                             (needle, entity_name, json.dumps(needle)[1:-1])):
            if r["canonical_name"].lower() == needle or r["id"] == entity_name:
                ids.add(r["id"])
                continue
            try:
                aliases = [str(a).lower() for a in json.loads(r["aliases_json"] or "[]")]
            except ValueError:
                aliases = []
            if needle in aliases:
                ids.add(r["id"])
    finally:
        con.close()
    return ids


def entity_timeline(entity_name: str, start: DateLike, end: DateLike, *,
                    db_path: Path = DEFAULT_DB, archive_dir: Path = DEFAULT_ARCHIVE
                    ) -> dict[date, int]:
    """{date: records first seen that day that mention the entity}, from
    live record_entities + the record_entities archive."""
    start_ts, end_ts = _bounds(start, end)
    ids = resolve_entity_ids(entity_name, db_path)
    needle = entity_name.lower()
    first_seen: dict[str, str] = {}

    def note(rid: str, ts: Optional[str]) -> None:
        ts = str(ts or "")
        if not ts or ts < start_ts or ts >= end_ts:
            return
        if rid not in first_seen or ts < first_seen[rid]:
            first_seen[rid] = ts

    for p in _partitions_for(archive_dir, "record_entities", start_ts, end_ts):
        for r in read_partition(p):
            if r.get("entity_id") in ids or (r.get("entity_name") or "").lower() == needle:
                note(str(r.get("record_id")), r.get("ingested_at"))

    con = _connect_ro(db_path)
    if con is not None:
        try:
            if _has_table(con, "record_entities") and _has_table(con, "records"):
                marks = ",".join("?" * len(ids))
                for r in con.execute(
                        f"SELECT re.record_id, r.ingested_at FROM record_entities re "
                        f"JOIN records r ON r.id = re.record_id "
                        f"WHERE re.entity_id IN ({marks}) AND r.ingested_at >= ? "
                        f"AND r.ingested_at < ?", (*ids, start_ts, end_ts)):
                    note(str(r[0]), r[1])
        finally:
            con.close()

    counts: dict[date, int] = {}
    for ts in first_seen.values():
        d = _day(ts)
        if d is not None:
            counts[d] = counts.get(d, 0) + 1
    return dict(sorted(counts.items()))


# --------------------------------------------------------------------- #
# Backfill from lake/sections/<section>/<date>/raw.jsonl
# --------------------------------------------------------------------- #

def _item_id_fallback(item: dict) -> str:
    # Mirror of worldscope.sections.Section._item_id, used only if that import fails.
    if item.get("id"):
        return str(item["id"])
    h = hashlib.sha1()
    h.update((item.get("url", "") + "|" + item.get("title", "")).encode("utf-8"))
    return h.hexdigest()


def _item_id():
    try:
        from .sections import Section
        return Section._item_id
    except Exception:
        return _item_id_fallback


_RAW_TOP = {"id", "_id", "source_id", "section_id", "ingested_at_utc", "ingested_at",
            "original_url", "original_text", "original_lang", "record_date", "license",
            "entities", "extra", "url", "title", "summary", "date"}


def _raw_ingested_at(raw: dict, folder_day: str) -> str:
    """First-seen timestamp for a raw line. The folder day is authoritative:
    some day folders get regenerated later and carry that later run's
    ingested_at_utc stamp, which would mis-partition (and mis-date) the row,
    so a stamp that postdates its folder day is clamped to the folder day."""
    ts = raw.get("ingested_at_utc") or raw.get("ingested_at") or folder_day
    ts = str(ts)
    if _DAY_RE.match(ts):
        ts += "T00:00:00Z"
    if ts[:10] > folder_day:
        ts = f"{folder_day}T00:00:00Z"
    return ts


def raw_to_record(raw: dict, *, section_id: str, folder_day: str, item_id=None) -> dict:
    """Map one raw.jsonl line (Section.to_raw_record shape, or a bare item
    dict) onto the `records` columns. Best-effort: unknown top-level keys are
    folded into extra_json so nothing is dropped."""
    item_id = item_id or _item_id()
    rid = raw.get("id") or raw.get("_id")
    if not rid:
        rid = item_id({"url": raw.get("original_url") or raw.get("url") or "",
                       "title": raw.get("title") or ""})
    extra = dict(raw.get("extra") or {}) if isinstance(raw.get("extra"), dict) else {}
    for k, v in raw.items():
        if k not in _RAW_TOP and k not in extra:
            extra[k] = v
    text = raw.get("original_text")
    if text is None and ("title" in raw or "summary" in raw):
        text = (raw.get("title", "") + " — " + (raw.get("summary", "") or ""))
    sec = raw.get("section_id") or section_id
    return {
        "id": str(rid),
        "source_id": raw.get("source_id") or sec,
        "section_id": sec,
        "ingested_at": _raw_ingested_at(raw, folder_day),
        "last_seen_at": None,
        "original_url": raw.get("original_url") or raw.get("url"),
        "original_text": (text or "")[:500],
        "original_lang": raw.get("original_lang") or "en",
        "record_date": raw.get("record_date") or raw.get("date"),
        "license": raw.get("license"),
        "extra_json": json.dumps(extra, sort_keys=True, ensure_ascii=False),
    }


def _raw_days(sections_root: Path, since: date, until: date) -> list[tuple[str, str, Path]]:
    """[(day, section, raw.jsonl path)] sorted by day then section."""
    out = []
    for sec_dir in sorted(Path(sections_root).iterdir()):
        if not sec_dir.is_dir() or sec_dir.name.startswith("_"):
            continue
        for day_dir in sec_dir.iterdir():
            if not _DAY_RE.match(day_dir.name):
                continue
            d = date.fromisoformat(day_dir.name)
            if d < since or d > until:
                continue
            raw = day_dir / "raw.jsonl"
            if raw.exists():
                out.append((day_dir.name, sec_dir.name, raw))
    out.sort()
    return out


def _iter_raw(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r


def db_min_day(db_path: Path) -> Optional[date]:
    con = _connect_ro(db_path)
    if con is None:
        return None
    try:
        if not _has_table(con, "records"):
            return None
        v = con.execute("SELECT MIN(ingested_at) FROM records").fetchone()[0]
        return _day(v) if v else None
    finally:
        con.close()


def _entity_names(db_path: Path) -> dict[str, str]:
    con = _connect_ro(db_path)
    if con is None:
        return {}
    try:
        if not _has_table(con, "entities"):
            return {}
        return {r[0]: r[1] for r in con.execute("SELECT id, canonical_name FROM entities")}
    finally:
        con.close()


def backfill_from_sections(since: DateLike, until: Optional[DateLike] = None, *,
                           sections_root: Path = DEFAULT_SECTIONS, db_path: Path = DEFAULT_DB,
                           archive_dir: Path = DEFAULT_ARCHIVE, max_total_mb: float = 150.0,
                           log=print) -> dict:
    """Reconstruct archive partitions from lake/sections/*/<day>/raw.jsonl for
    days that are no longer in the live DB (older than its oldest record).
    Idempotent: merges into whatever the archive already holds.

    Two passes over the files: the first learns first-/last-seen per id (the
    same item is re-pulled day after day; only the first sighting becomes a
    row, its last sighting becomes last_seen_at), the second emits rows one
    month at a time. Stops — leaving the archive consistent — if the total
    archive size would exceed max_total_mb.
    """
    since_d = _as_date(since)
    until_d = _as_date(until) if until else date.today()
    oldest_live = db_min_day(db_path)
    if oldest_live is not None:
        until_d = min(until_d, oldest_live - timedelta(days=1))
    result = {"ok": True, "since": since_d.isoformat(), "until": until_d.isoformat(),
              "days": 0, "files": 0, "raw_lines": 0, "records": 0, "links": 0,
              "months": [], "partitions": 0, "bytes": 0, "stopped": False}
    if until_d < since_d:
        log(f"[history] nothing to backfill: live DB already covers {since_d}+")
        return result
    files = _raw_days(sections_root, since_d, until_d)
    result["files"] = len(files)
    result["days"] = len({d for d, _, _ in files})
    if not files:
        log(f"[history] no raw.jsonl under {sections_root} in {since_d}..{until_d}")
        return result
    item_id = _item_id()

    # pass 1: first/last sighting per id
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    for day, sec, path in files:
        for raw in _iter_raw(path):
            rid = raw.get("id") or raw.get("_id")
            if not rid:
                rid = item_id({"url": raw.get("original_url") or raw.get("url") or "",
                               "title": raw.get("title") or ""})
            rid = str(rid)
            ts = _raw_ingested_at(raw, day)
            result["raw_lines"] += 1
            if rid not in first or ts < first[rid]:
                first[rid] = ts
            if rid not in last or ts > last[rid]:
                last[rid] = ts
    log(f"[history] pass 1: {result['raw_lines']} raw lines, {len(first)} distinct ids "
        f"over {result['days']} days")

    # pass 2: emit, one month of day-folders at a time
    names = _entity_names(db_path)
    budget = int(max_total_mb * 1e6)
    emitted: set[str] = set()
    by_month: dict[str, list[tuple[str, str, Path]]] = {}
    for day, sec, path in files:
        by_month.setdefault(day[:7], []).append((day, sec, path))
    for month in sorted(by_month):
        recs: list[dict] = []
        links: list[dict] = []
        for day, sec, path in by_month[month]:
            for raw in _iter_raw(path):
                rec = raw_to_record(raw, section_id=sec, folder_day=day, item_id=item_id)
                rid = rec["id"]
                if rid in emitted or rec["ingested_at"] != first.get(rid):
                    continue
                emitted.add(rid)
                rec["last_seen_at"] = last.get(rid, rec["ingested_at"])
                recs.append(rec)
                for eid in raw.get("entities") or []:
                    if isinstance(eid, dict):
                        eid = eid.get("id")
                    if eid:
                        links.append({"record_id": rid, "entity_id": str(eid),
                                      "entity_name": names.get(str(eid)),
                                      "ingested_at": rec["ingested_at"]})
        before = lake_archive.archive_total_bytes(archive_dir)
        r1 = lake_archive.write_rows(archive_dir, "records", recs)
        r2 = lake_archive.write_rows(archive_dir, "record_entities", links)
        after = lake_archive.archive_total_bytes(archive_dir)
        if after > budget:
            # roll this month back: drop the files it touched, re-index
            for name in list(r1["files"]) + list(r2["files"]):
                for t in lake_archive.TABLES:
                    p = Path(archive_dir) / t / name
                    if p.exists():
                        p.unlink()
            lake_archive.refresh_index(archive_dir)
            result.update(stopped=True, ok=False, stop_month=month,
                          bytes=lake_archive.archive_total_bytes(archive_dir))
            log(f"::warning::[history] backfill stopped at {month}: archive would be "
                f"{after/1e6:.1f} MB > {max_total_mb} MB budget (rolled back)")
            return _finish(result, archive_dir, log)
        result["records"] += r1["written"]
        result["links"] += r2["written"]
        result["months"].append(month)
        log(f"[history] {month}: +{r1['written']} records, +{r2['written']} links "
            f"({(after-before)/1e6:.1f} MB, archive now {after/1e6:.1f} MB)")
    return _finish(result, archive_dir, log)


def _finish(result: dict, archive_dir: Path, log) -> dict:
    idx = lake_archive.load_index(archive_dir)
    parts = [e for t in lake_archive.TABLES for e in idx["partitions"].get(t, [])]
    result["partitions"] = len(parts)
    result["bytes"] = lake_archive.archive_total_bytes(archive_dir)
    log(f"[history] archive: {result['partitions']} partition files, "
        f"{result['bytes']/1e6:.1f} MB under {archive_dir}")
    return result


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #

def _table(rows: list[list[str]], header: list[str], out=None) -> None:
    out = out or sys.stdout
    widths = [len(h) for h in header]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*header), file=out)
    print("  ".join("-" * w for w in widths), file=out)
    for r in rows:
        print(fmt.format(*r).rstrip(), file=out)


def _clip(s: Optional[str], n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worldscope.history",
                                 description="Query the lake across time (live DB + archive).")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _range(p):
        p.add_argument("--since", required=True, help="YYYY-MM-DD (inclusive)")
        p.add_argument("--until", default=date.today().isoformat(), help="YYYY-MM-DD (inclusive)")

    p = sub.add_parser("counts", help="records first seen per day")
    _range(p); p.add_argument("--section")
    p = sub.add_parser("search", help="substring search on original_text")
    _range(p); p.add_argument("--q", required=True); p.add_argument("--section")
    p.add_argument("--source"); p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("timeline", help="records per day mentioning an entity")
    _range(p); p.add_argument("--entity", required=True)
    p = sub.add_parser("backfill-from-sections",
                       help="rebuild archive partitions from lake/sections/*/<day>/raw.jsonl")
    p.add_argument("--since", required=True); p.add_argument("--until")
    p.add_argument("--sections-root", default=str(DEFAULT_SECTIONS))
    p.add_argument("--max-total-mb", type=float, default=150.0)
    args = ap.parse_args(argv)
    db, arch = Path(args.db), Path(args.archive_dir)

    if args.cmd == "counts":
        counts = daily_counts(args.since, args.until, section_id=args.section,
                              db_path=db, archive_dir=arch)
        _table([[d.isoformat(), str(n)] for d, n in counts.items()], ["date", "records"])
        print(f"{sum(counts.values())} records over {len(counts)} days")
    elif args.cmd == "search":
        rows, n = [], 0
        for r in iter_records(args.since, args.until, section_id=args.section,
                              source_id=args.source, text_contains=args.q,
                              db_path=db, archive_dir=arch):
            n += 1
            if len(rows) < args.limit:
                rows.append([str(r.get("ingested_at") or "")[:10], _clip(r.get("section_id"), 18),
                             _clip(r.get("source_id"), 24), _clip(r.get("original_text"), 72)])
        _table(rows, ["first_seen", "section", "source", "text"])
        print(f"{n} matches" + (f" (showing {len(rows)})" if n > len(rows) else ""))
    elif args.cmd == "timeline":
        tl = entity_timeline(args.entity, args.since, args.until, db_path=db, archive_dir=arch)
        _table([[d.isoformat(), str(n)] for d, n in tl.items()], ["date", "records"])
        print(f"{sum(tl.values())} records mention {args.entity!r} over {len(tl)} days")
    elif args.cmd == "backfill-from-sections":
        res = backfill_from_sections(args.since, args.until, sections_root=Path(args.sections_root),
                                     db_path=db, archive_dir=arch, max_total_mb=args.max_total_mb)
        print(json.dumps({k: v for k, v in res.items() if k != "months"}, indent=1))
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
