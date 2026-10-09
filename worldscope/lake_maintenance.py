"""lake_maintenance.py — keep the committed databases under GitHub's hard
100 MB file-size limit so the daily push never gets rejected again.

Background: the daily-brief push silently failed for ~13 days because
lake/db/worldscope.sqlite crossed 100 MB and GitHub's pre-receive hook
rejected it. The dominant bloat was the `quarantine` table (FK-rejected
records — pure diagnostic junk): ~22k rows / ~34 MB. Clearing it and
VACUUMing drops the DB from ~105 MB to ~64 MB.

The same failure mode then hit data/store.sqlite on 2026-07-05: the
snapshots table accrues one row per section per day, nothing ever deleted
them, and the store crossed 103 MB — every daily-brief push after that was
rejected. maintain_store() applies the same treatment to it.

This module, run before the daily commit, keeps both DBs healthy:
  1. prune the quarantine table to a short retention window,
  2. prune records (and orphaned links) beyond a rolling window (insurance
     against unbounded future growth) — every record row about to be
     evicted (by age or by the size ceiling below) is first written to
     lake/archive/<table>/YYYY-MM.jsonl.gz via worldscope.lake_archive, so
     cross-time history stays queryable through worldscope.history,
  3. prune snapshot-store history beyond a rolling window, always keeping
     the newest row per section (the render carry-forward reads it),
  4. VACUUM to reclaim freed pages,
  5. warn loudly if a file is still close to the limit.

    python -m worldscope.lake_maintenance            # default thresholds
    python -m worldscope.lake_maintenance --keep-days 120
    python -m worldscope.lake_maintenance --no-archive   # evict without archiving
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .lake_archive import DEFAULT_ARCHIVE, archive_records_where

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LAKE = REPO / "lake" / "db" / "worldscope.sqlite"
DEFAULT_STORE = REPO / "data" / "store.sqlite"

# GitHub rejects files > 100 MB and warns above 50 MB. Stay well clear.
SOFT_WARN_MB = 90.0


def _exec(con: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        cur = con.execute(sql, params)
        return cur.rowcount if cur.rowcount is not None else 0
    except sqlite3.Error as exc:
        print(f"[lake-maint] skip ({type(exc).__name__}: {exc}) :: {sql.split(chr(10))[0][:60]}")
        return 0


def _archive(con: sqlite3.Connection, archive_dir: Optional[Path],
             where_sql: str, params, totals: dict) -> None:
    """Archive-before-evict: copy the records (+ record_entities) that the
    following DELETE will remove into the monthly gzip partitions. A failure
    here must NOT be swallowed — losing history silently is the bug this
    exists to fix — so it propagates and the eviction does not happen."""
    if archive_dir is None:
        return
    res = archive_records_where(con, where_sql, params, root=archive_dir)
    totals["records_archived"] += res.get("records", 0)
    totals["links_archived"] += res.get("record_entities", 0)
    for m in res.get("months", []):
        totals.setdefault("months", [])
        if m not in totals["months"]:
            totals["months"].append(m)


def maintain(db_path: Path = DEFAULT_LAKE, *, keep_days: int = 120,
             quarantine_keep_days: int = 2, max_mb: float = 85.0,
             vacuum: bool = True,
             archive_dir: Optional[Path] = DEFAULT_ARCHIVE) -> dict:
    """Prune + VACUUM the lake. Every `records` row evicted here (by
    keep_days or by the max_mb ceiling) is archived first under
    `archive_dir` (lake/archive by default; pass None to disable).
    """
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"[lake-maint] no lake at {db_path}")
        return {"ok": False}
    before = db_path.stat().st_size
    archived = {"records_archived": 0, "links_archived": 0, "months": []}
    now = datetime.now(timezone.utc)
    q_cut = (now - timedelta(days=quarantine_keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r_cut = (now - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        nq = _exec(con, "DELETE FROM quarantine WHERE detected_at < ?", (q_cut,))
        # also hard-cap quarantine so a single bad day can't balloon it
        _exec(con, "DELETE FROM quarantine WHERE id IN "
                   "(SELECT id FROM quarantine ORDER BY detected_at DESC LIMIT -1 OFFSET 2000)")
        _archive(con, archive_dir, "ingested_at < ?", (r_cut,), archived)
        nr = _exec(con, "DELETE FROM records WHERE ingested_at < ?", (r_cut,))
        # clean up links/embeddings orphaned by the record prune
        _exec(con, "DELETE FROM record_entities WHERE record_id NOT IN (SELECT id FROM records)")
        _exec(con, "DELETE FROM record_embeddings WHERE record_id NOT IN (SELECT id FROM records)")
        _exec(con, "DELETE FROM claim_evidence WHERE record_id NOT IN (SELECT id FROM records)")
        con.commit()
        if vacuum:
            con.execute("VACUUM")

        # Age-based pruning alone let the lake hit GitHub's 100 MB limit on
        # 2026-07-17 (105 MB in CI, pushes rejected all day). Enforce a hard
        # ceiling the way maintain_store() does: drop the oldest remaining
        # day of records (plus orphans) and repeat until under max_mb.
        while vacuum and db_path.stat().st_size / 1e6 > max_mb:
            oldest = con.execute(
                "SELECT date(MIN(ingested_at)) FROM records").fetchone()[0]
            if oldest is None:
                print(f"::warning::lake still {db_path.stat().st_size/1e6:.1f}MB "
                      f"with no records left to prune — non-record tables are the bloat")
                break
            _archive(con, archive_dir, "date(ingested_at) = ?", (oldest,), archived)
            nr += _exec(con, "DELETE FROM records WHERE date(ingested_at) = ?", (oldest,))
            _exec(con, "DELETE FROM record_entities WHERE record_id NOT IN (SELECT id FROM records)")
            _exec(con, "DELETE FROM record_embeddings WHERE record_id NOT IN (SELECT id FROM records)")
            _exec(con, "DELETE FROM claim_evidence WHERE record_id NOT IN (SELECT id FROM records)")
            con.commit()
            con.execute("VACUUM")
    finally:
        con.close()

    after = db_path.stat().st_size
    mb = after / 1e6
    print(f"[lake-maint] quarantine pruned ~{nq} rows, records pruned ~{nr} rows · "
          f"{before/1e6:.1f}MB -> {mb:.1f}MB")
    if archive_dir is not None and archived["records_archived"]:
        print(f"[lake-maint] archived {archived['records_archived']} records + "
              f"{archived['links_archived']} entity links to {archive_dir} "
              f"(months: {', '.join(archived['months'])})")
    if mb > SOFT_WARN_MB:
        print(f"::warning::lake is {mb:.1f}MB, approaching GitHub's 100MB limit — "
              f"tighten --keep-days or move the lake out of git (LFS / external store)")
    return {"ok": True, "before": before, "after": after, "mb": mb,
            "quarantine_deleted": nq, "records_deleted": nr,
            "records_archived": archived["records_archived"],
            "links_archived": archived["links_archived"],
            "archive_dir": str(archive_dir) if archive_dir is not None else None}


# SQL fragment for the rows that must never be pruned: the newest snapshot
# per section, which the render carry-forward reads when a source fails.
_PROTECTED = ("(section_id, snapshot_date) NOT IN "
              "(SELECT section_id, MAX(snapshot_date) FROM snapshots GROUP BY section_id)")


def maintain_store(store_path: Path = DEFAULT_STORE, *, keep_days: int = 60,
                   max_mb: float = 80.0, vacuum: bool = True) -> dict:
    """Prune the snapshot store the same way maintain() prunes the lake.

    Retention: keep everything within keep_days of the newest snapshot_date
    (deterministic cutoff, independent of wall clock), plus the newest row
    per section unconditionally. The longest history consumer is threads.py
    at 14 days, so 60 leaves generous margin. If the VACUUMed file still
    exceeds max_mb, drop the oldest remaining unprotected day and repeat.
    """
    store_path = Path(store_path)
    if not store_path.exists():
        print(f"[store-maint] no store at {store_path}")
        return {"ok": True, "skipped": True}
    before = store_path.stat().st_size

    con = sqlite3.connect(str(store_path))
    try:
        newest = con.execute("SELECT MAX(snapshot_date) FROM snapshots").fetchone()[0]
        if newest is None:
            return {"ok": True, "before": before, "after": before, "deleted": 0}
        cutoff = con.execute("SELECT date(?, ?)", (newest, f"-{keep_days} days")).fetchone()[0]
        deleted = _exec(con, f"DELETE FROM snapshots WHERE snapshot_date < ? AND {_PROTECTED}",
                        (cutoff,))
        con.commit()
        if vacuum:
            con.execute("VACUUM")

        # Age-based pruning may not be enough if payloads ballooned recently;
        # enforce the size ceiling a day at a time, oldest first.
        while vacuum and store_path.stat().st_size / 1e6 > max_mb:
            oldest = con.execute(
                f"SELECT MIN(snapshot_date) FROM snapshots WHERE {_PROTECTED}"
            ).fetchone()[0]
            if oldest is None:
                print(f"::warning::store is {store_path.stat().st_size/1e6:.1f}MB with only "
                      f"newest-per-section rows left — payloads themselves are too large")
                break
            deleted += _exec(con, f"DELETE FROM snapshots WHERE snapshot_date = ? AND {_PROTECTED}",
                             (oldest,))
            con.commit()
            con.execute("VACUUM")
    finally:
        con.close()

    after = store_path.stat().st_size
    mb = after / 1e6
    print(f"[store-maint] pruned ~{deleted} snapshot rows · "
          f"{before/1e6:.1f}MB -> {mb:.1f}MB")
    if mb > SOFT_WARN_MB:
        print(f"::warning::snapshot store is {mb:.1f}MB, approaching GitHub's 100MB limit")
    return {"ok": True, "before": before, "after": after, "mb": mb, "deleted": deleted}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prune + VACUUM the lake + snapshot store under the 100MB git limit.")
    ap.add_argument("--db", default=str(DEFAULT_LAKE))
    ap.add_argument("--keep-days", type=int, default=120,
                    help="retain records ingested within this many days")
    ap.add_argument("--quarantine-keep-days", type=int, default=2)
    ap.add_argument("--lake-max-mb", type=float, default=85.0)
    ap.add_argument("--store", default=str(DEFAULT_STORE))
    ap.add_argument("--store-keep-days", type=int, default=60,
                    help="retain snapshots within this many days of the newest")
    ap.add_argument("--store-max-mb", type=float, default=80.0)
    ap.add_argument("--no-vacuum", action="store_true")
    ap.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE),
                    help="where evicted records are archived as monthly .jsonl.gz")
    ap.add_argument("--no-archive", action="store_true",
                    help="evict without archiving (history is lost; not recommended)")
    args = ap.parse_args(argv)
    res = maintain(Path(args.db), keep_days=args.keep_days,
                   quarantine_keep_days=args.quarantine_keep_days,
                   max_mb=args.lake_max_mb, vacuum=not args.no_vacuum,
                   archive_dir=None if args.no_archive else Path(args.archive_dir))
    store_res = maintain_store(Path(args.store), keep_days=args.store_keep_days,
                               max_mb=args.store_max_mb, vacuum=not args.no_vacuum)
    return 0 if (res.get("ok") and store_res.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
