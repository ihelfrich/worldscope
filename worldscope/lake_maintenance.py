"""lake_maintenance.py — keep the committed lake under GitHub's hard 100 MB
file-size limit so the daily push never gets rejected again.

Background: the daily-brief push silently failed for ~13 days because
lake/db/worldscope.sqlite crossed 100 MB and GitHub's pre-receive hook
rejected it. The dominant bloat was the `quarantine` table (FK-rejected
records — pure diagnostic junk): ~22k rows / ~34 MB. Clearing it and
VACUUMing drops the DB from ~105 MB to ~64 MB.

This module, run before the daily commit, keeps the DB healthy:
  1. prune the quarantine table to a short retention window,
  2. prune records (and orphaned links) beyond a rolling window (insurance
     against unbounded future growth),
  3. VACUUM to reclaim freed pages,
  4. warn loudly if the file is still close to the limit.

    python -m worldscope.lake_maintenance            # default thresholds
    python -m worldscope.lake_maintenance --keep-days 120
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LAKE = REPO / "lake" / "db" / "worldscope.sqlite"

# GitHub rejects files > 100 MB and warns above 50 MB. Stay well clear.
SOFT_WARN_MB = 90.0


def _exec(con: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        cur = con.execute(sql, params)
        return cur.rowcount if cur.rowcount is not None else 0
    except sqlite3.Error as exc:
        print(f"[lake-maint] skip ({type(exc).__name__}: {exc}) :: {sql.split(chr(10))[0][:60]}")
        return 0


def maintain(db_path: Path = DEFAULT_LAKE, *, keep_days: int = 120,
             quarantine_keep_days: int = 2, vacuum: bool = True) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"[lake-maint] no lake at {db_path}")
        return {"ok": False}
    before = db_path.stat().st_size
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
        nr = _exec(con, "DELETE FROM records WHERE ingested_at < ?", (r_cut,))
        # clean up links/embeddings orphaned by the record prune
        _exec(con, "DELETE FROM record_entities WHERE record_id NOT IN (SELECT id FROM records)")
        _exec(con, "DELETE FROM record_embeddings WHERE record_id NOT IN (SELECT id FROM records)")
        _exec(con, "DELETE FROM claim_evidence WHERE record_id NOT IN (SELECT id FROM records)")
        con.commit()
        if vacuum:
            con.execute("VACUUM")
    finally:
        con.close()

    after = db_path.stat().st_size
    mb = after / 1e6
    print(f"[lake-maint] quarantine pruned ~{nq} rows, records pruned ~{nr} rows · "
          f"{before/1e6:.1f}MB -> {mb:.1f}MB")
    if mb > SOFT_WARN_MB:
        print(f"::warning::lake is {mb:.1f}MB, approaching GitHub's 100MB limit — "
              f"tighten --keep-days or move the lake out of git (LFS / external store)")
    return {"ok": True, "before": before, "after": after, "mb": mb,
            "quarantine_deleted": nq, "records_deleted": nr}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prune + VACUUM the lake under the 100MB git limit.")
    ap.add_argument("--db", default=str(DEFAULT_LAKE))
    ap.add_argument("--keep-days", type=int, default=120,
                    help="retain records ingested within this many days")
    ap.add_argument("--quarantine-keep-days", type=int, default=2)
    ap.add_argument("--no-vacuum", action="store_true")
    args = ap.parse_args(argv)
    res = maintain(Path(args.db), keep_days=args.keep_days,
                   quarantine_keep_days=args.quarantine_keep_days, vacuum=not args.no_vacuum)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
