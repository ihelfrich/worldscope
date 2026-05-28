"""backfill_lake_history — restore the lake's first-seen timestamps from
the snapshot store, after the upsert_record bug that re-stamped every
record's ingested_at to "today" on every conflict.

Background:
  The snapshot store (data/store.sqlite) writes one row per
  (section_id, snapshot_date) with the full items list as JSON. The
  lake (lake/db/worldscope.sqlite) holds the de-duplicated structured
  records table. The bug in worldscope/lake/__init__.py:upsert_record
  caused every record's ingested_at to be overwritten with NOW on every
  daily run. Result: even when a record first appeared days ago, the
  lake claimed it was ingested today, and trend queries grouped by
  date returned nothing useful.

  This tool walks the snapshot store oldest-first, and for each record
  in each day's snapshot, ensures the lake has the record with its
  ORIGINAL first-seen date as ingested_at. Existing lake records are
  updated only if the lake's current ingested_at is LATER than the
  earliest snapshot containing them (i.e. the lake was wrong, the
  snapshot store remembers the truth).

  Idempotent. Safe to re-run. Reports how many records had their
  ingested_at corrected.

Usage:
    python -m tools.backfill_lake_history
    python -m tools.backfill_lake_history --dry-run     # just report
    python -m tools.backfill_lake_history --since 2026-05-01
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT_DB = REPO / "data" / "store.sqlite"
LAKE_DB     = REPO / "lake" / "db" / "worldscope.sqlite"


def _item_id(item: dict) -> str:
    """Re-compute the same id the section adapter uses (see
    sections/__init__.py _item_id)."""
    if item.get("id"):
        return str(item["id"])
    import hashlib
    h = hashlib.sha1()
    h.update((item.get("url", "") + "|" + item.get("title", "")).encode("utf-8"))
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="don't write changes, just report what would happen")
    ap.add_argument("--since", default="2020-01-01",
                    help="ignore snapshots before this date (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    if not SNAPSHOT_DB.exists():
        print(f"snapshot store missing at {SNAPSHOT_DB}", file=sys.stderr)
        return 2
    if not LAKE_DB.exists():
        print(f"lake DB missing at {LAKE_DB}", file=sys.stderr)
        return 2

    snap = sqlite3.connect(f"file:{SNAPSHOT_DB}?mode=ro", uri=True)
    lake = sqlite3.connect(LAKE_DB)

    # Collect (record_id → earliest_snapshot_date) by walking the
    # snapshot store oldest-first.
    earliest: dict[str, str] = {}
    rows_scanned = 0
    rows = snap.execute(
        "SELECT section_id, snapshot_date, payload "
        "  FROM snapshots WHERE snapshot_date >= ? "
        "  ORDER BY snapshot_date ASC",
        (args.since,),
    ).fetchall()
    for sid, snap_date, payload in rows:
        try:
            items = json.loads(payload).get("items") or []
        except Exception:
            continue
        for it in items:
            rid = _item_id(it)
            if rid and rid not in earliest:
                earliest[rid] = snap_date
                rows_scanned += 1
    print(f"# scanned {len(rows)} snapshots, {rows_scanned} unique record ids")

    # Compare against the lake. For each record present in BOTH the
    # lake and our earliest map, if the lake's ingested_at (date part)
    # is LATER than the earliest snapshot date, correct it.
    lake_records = {
        rid: (ingested_at or "")[:10]
        for rid, ingested_at in lake.execute(
            "SELECT id, ingested_at FROM records"
        ).fetchall()
    }
    print(f"# lake currently has {len(lake_records):,} records")

    corrections: list[tuple[str, str, str]] = []
    for rid, lake_date in lake_records.items():
        first_seen = earliest.get(rid)
        if first_seen and first_seen < lake_date:
            corrections.append((rid, lake_date, first_seen))

    print(f"# {len(corrections):,} records need ingested_at correction")
    if corrections[:5]:
        print("  sample:")
        for rid, old, new in corrections[:5]:
            print(f"    {rid[:48]:48s}   {old} → {new}")

    if args.dry_run:
        print("# --dry-run: no writes")
        return 0

    # Apply corrections, preserving time-of-day at midnight UTC.
    print("# applying corrections...")
    t0 = time.monotonic()
    cur = lake.cursor()
    for rid, _old, new_date in corrections:
        cur.execute(
            "UPDATE records SET ingested_at = ? WHERE id = ?",
            (new_date + "T00:00:00Z", rid),
        )
    lake.commit()
    dur = time.monotonic() - t0
    print(f"# committed {len(corrections):,} updates in {dur:.2f}s")

    # Re-report the by-date distribution.
    print()
    print("# new lake distribution by ingested_at date:")
    for row in lake.execute(
        "SELECT substr(ingested_at,1,10) AS d, COUNT(*) FROM records GROUP BY d ORDER BY d"
    ).fetchall():
        print(f"  {row[0]}  {row[1]:>6,}")

    # ────────────────────────────────────────────────────────────────
    # SECOND PASS: retry quarantined records with the new auto-register
    # source logic. Sections that emit per-feed source_ids historically
    # failed FOREIGN KEY constraint and went to the quarantine table;
    # the upsert_record fix now auto-registers unknown sources so those
    # records can be safely retried.
    # ────────────────────────────────────────────────────────────────
    print()
    print("# retrying quarantined records with the new auto-register logic...")
    from worldscope.lake import Lake
    lake_obj = Lake.open(LAKE_DB)
    qrows = lake.execute(
        "SELECT id, source_id, section_id, raw_json FROM quarantine"
    ).fetchall()
    print(f"# {len(qrows):,} quarantined records to retry")
    recovered = 0
    still_failed = 0
    for q_id, source_id, section_id, raw_json in qrows:
        try:
            rec = json.loads(raw_json) if raw_json else {}
        except Exception:
            still_failed += 1
            continue
        try:
            lake_obj.upsert_record(
                record_id=rec.get("id") or q_id,
                source_id=rec.get("source_id") or source_id,
                section_id=rec.get("section_id") or section_id,
                original_url=rec.get("original_url"),
                original_text=rec.get("original_text"),
                original_lang=rec.get("original_lang", "en"),
                record_date=rec.get("record_date"),
                license=rec.get("license"),
                extra=rec.get("extra"),
            )
            recovered += 1
        except Exception as exc:
            still_failed += 1
    # Drop the now-recovered records from quarantine. Keep any that
    # still failed so the maintainer can inspect them.
    if recovered:
        lake.execute(
            "DELETE FROM quarantine WHERE id IN ("
            + ",".join(["?"] * len(qrows))
            + ")",
            [q[0] for q in qrows if q[0]],
        )
        lake.commit()
    print(f"# recovered {recovered:,} records, {still_failed:,} still failing")

    # Final report
    print()
    print("# final lake distribution by ingested_at date:")
    for row in lake.execute(
        "SELECT substr(ingested_at,1,10) AS d, COUNT(*) FROM records GROUP BY d ORDER BY d"
    ).fetchall():
        print(f"  {row[0]}  {row[1]:>6,}")
    print()
    print("# final per-section record counts:")
    for row in lake.execute(
        "SELECT section_id, COUNT(*) FROM records GROUP BY section_id ORDER BY COUNT(*) DESC"
    ).fetchall():
        print(f"  {row[0]:30s} {row[1]:>6,}")

    snap.close()
    lake.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
