#!/usr/bin/env python3
"""backfill_lake_history.py

Generate honest empty placeholder snapshots in lake/sections/<id>/<YYYY-MM-DD>/
for a date range. Each placeholder is explicitly marked
`state: "backfill_no_data"` so the trends machinery can filter it out (or
weight it differently) when computing moving averages, medians, and
anomaly z-scores.

Purpose
-------
The lake currently has 2-3 days of real history per section. Moving averages
and z-scores computed over a denominator that small are meaningless. This
script fills in the directory scaffolding for the preceding N days so the
trends code has a stable shape to iterate over, without fabricating fake
records.

What gets written per (section, date)
-------------------------------------
  raw.jsonl        empty (0 bytes)
  summary.md       YAML front matter + short body noting backfill placeholder
  structured.json  {state: "backfill_no_data", counts: {}, ...}

What does NOT happen
--------------------
  * Real existing snapshots are never overwritten.
  * No fake records, no fake titles, no fake URLs.
  * No counts are estimated from neighboring days.

Idempotent: re-running the script does nothing if every target directory
already exists. If some sections have partial older history (e.g.
billionaires/2026-05-25), only the MISSING dates are created.

Usage
-----
  python tools/backfill_lake_history.py
  python tools/backfill_lake_history.py --start-date 2026-05-15 --end-date 2026-05-26
  python tools/backfill_lake_history.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LAKE_SECTIONS = REPO_ROOT / "lake" / "sections"

# Default range: 7 days ending the day before the earliest real snapshot
# (2026-05-27). Hard-coded per the backfill mission spec.
DEFAULT_START = date(2026, 5, 20)
DEFAULT_END   = date(2026, 5, 26)

BACKFILL_STATE = "backfill_no_data"


def _iter_dates(start: date, end: date):
    """Inclusive daterange iterator."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _discover_sections() -> list[str]:
    """Return every section id under lake/sections/ except _meta."""
    if not LAKE_SECTIONS.exists():
        return []
    out = []
    for p in sorted(LAKE_SECTIONS.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("_"):
            continue
        out.append(p.name)
    return out


def _section_title(section_id: str) -> str:
    """Human readable title used in the summary.md front matter. Section
    adapters carry the canonical title; we don't import them here to keep
    this script dependency-free, so we just titlecase the id."""
    return section_id.replace("_", " ").title()


def _summary_md(section_id: str, day: str) -> str:
    title = _section_title(section_id)
    return (
        "---\n"
        f"section: {section_id}\n"
        f"title: {title}\n"
        f"date: {day}\n"
        "record_count: 0\n"
        "new_today: 0\n"
        f"state: {BACKFILL_STATE}\n"
        "---\n"
        "\n"
        f"## {title}\n"
        "\n"
        "No historical pull for this date; this is a backfill placeholder "
        "so trends computation has scaffolding.\n"
    )


def _structured(section_id: str, day: str) -> dict:
    """Schema notes:
    - "day" + "section" satisfy the user's backfill spec.
    - "date" + "record_count" + "new_count" keep parity with the live
      structured.json schema in worldscope/sections/__init__.py so any
      downstream consumer that reads either key path continues to work.
    - "counts": {} is the explicit no-data signal per the spec.
    - "state": "backfill_no_data" is the canonical filter key.
    """
    return {
        "day": day,
        "date": day,
        "section": section_id,
        "state": BACKFILL_STATE,
        "record_count": 0,
        "new_count": 0,
        "counts": {},
        "anomalies": [],
        "entities_added": [],
        "entities_updated": [],
        "relationships": [],
        "predictions": [],
        "paper_bets": [],
    }


def _write_placeholder(section_id: str, day: str, *, dry_run: bool) -> bool:
    """Create the three placeholder files for (section_id, day). Returns
    True iff a new directory was created. Existing dirs are never
    overwritten."""
    folder = LAKE_SECTIONS / section_id / day
    if folder.exists():
        return False
    if dry_run:
        return True
    folder.mkdir(parents=True, exist_ok=False)
    # raw.jsonl: 0-byte file (no records)
    (folder / "raw.jsonl").touch()
    # summary.md: short YAML+body
    (folder / "summary.md").write_text(_summary_md(section_id, day), encoding="utf-8")
    # structured.json: explicit no-data state
    (folder / "structured.json").write_text(
        json.dumps(_structured(section_id, day), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def backfill(
    *,
    start: date,
    end: date,
    dry_run: bool = False,
) -> dict:
    sections = _discover_sections()
    if not sections:
        print("no sections discovered under lake/sections/", file=sys.stderr)
        return {"sections": 0, "created": 0, "skipped": 0, "per_section": {}}

    created = 0
    skipped = 0
    per_section: dict[str, dict] = {}
    for sid in sections:
        s_created = 0
        s_skipped = 0
        for d in _iter_dates(start, end):
            day = d.isoformat()
            if _write_placeholder(sid, day, dry_run=dry_run):
                s_created += 1
            else:
                s_skipped += 1
        created += s_created
        skipped += s_skipped
        per_section[sid] = {"created": s_created, "skipped": s_skipped}

    return {
        "sections": len(sections),
        "created": created,
        "skipped": skipped,
        "per_section": per_section,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--start-date", default=DEFAULT_START.isoformat(),
                        help=f"inclusive start (default: {DEFAULT_START.isoformat()})")
    parser.add_argument("--end-date", default=DEFAULT_END.isoformat(),
                        help=f"inclusive end (default: {DEFAULT_END.isoformat()})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written without touching disk")
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
    except ValueError as exc:
        parser.error(f"bad date: {exc}")

    if end < start:
        parser.error("end-date must be >= start-date")

    result = backfill(start=start, end=end, dry_run=args.dry_run)

    tag = "[dry-run] would create" if args.dry_run else "backfilled"
    print(
        f"{tag} {result['created']} section-days across {result['sections']} sections; "
        f"skipped {result['skipped']} (already present) "
        f"over window {start.isoformat()}..{end.isoformat()}"
    )
    # Surface sections that had partial pre-existing history (so we know
    # they were partially skipped, not fully backfilled).
    expected_per_section = (end - start).days + 1
    partial = [
        (sid, info["skipped"])
        for sid, info in sorted(result["per_section"].items())
        if 0 < info["skipped"] < expected_per_section
    ]
    if partial:
        print("\nsections with pre-existing history inside the backfill window:")
        for sid, n_skipped in partial:
            print(f"  {sid}: {n_skipped} day(s) already had snapshots, left untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
