"""cross_section.py: entity cross-section recurrence analyzer.

Purpose
-------
The desk-officer routine prompt asks for "cross-section entity recurrence
(same name in 3+ sections this week)" in its Weak Signals section and as
a follow-up-research priority. The model historically has had to derive
this from raw text it cannot fully hold in attention, so the signal was
unreliable.

This module computes the recurrence deterministically:

  1. Load the entities table (canonical_name + type + id).
  2. For each entity, scan today's records (title + summary +
     original_text) for substring mentions.
  3. Count distinct section_id values per entity.
  4. Emit any entity appearing in >= 3 sections, with:
       - the section list
       - up to 3 representative record IDs per section
       - a confidence band (high/medium/low) based on mention density
         and length of the canonical name (short names like "AI" are
         demoted to low to avoid false-positive matches)

The output JSON lives at lake/sections/_meta/<date>/cross_section.json
and the desk-officer routine prompt should be updated to read it.

Determinism + speed
-------------------
- Pure-Python sqlite3, no external deps.
- ~1,700 records today, ~1,200 entities = ~2M substring checks; runs
  in under 5 seconds on Ian's machine.
- Re-runnable; idempotent over its output file.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Common short tokens that produce false positives if matched naively.
# These get demoted to low confidence regardless of mention count.
SHORT_NAMES_DEMOTE = {
    "AI", "ML", "IT", "US", "EU", "UN", "G7", "G20", "OECD", "UK",
    "FBI", "CIA", "NSA", "DOJ", "SEC", "OFAC", "FERC", "FCC", "FTC",
    "Trump",  # too generic across non-political sections
}

# Entities of these types are usable as topical cross-section signals.
# Skip noise types like "kind:source-feed" etc.
ALLOWED_TYPES = {"person", "org", "place", "policy", "statute", "company",
                 "agency", "country", "city", "topic"}


@dataclass
class EntityHit:
    entity_id: str
    canonical_name: str
    entity_type: str
    section_counts: dict[str, int] = field(default_factory=dict)
    record_evidence: dict[str, list[str]] = field(default_factory=dict)

    @property
    def n_sections(self) -> int:
        return len(self.section_counts)

    @property
    def total_mentions(self) -> int:
        return sum(self.section_counts.values())

    def confidence(self) -> str:
        """High = 4+ sections + name length >= 7. Medium = 3 sections + length >= 5.
        Low = everything else (short names, edge cases)."""
        if self.canonical_name in SHORT_NAMES_DEMOTE:
            return "low"
        if len(self.canonical_name) < 4:
            return "low"
        if self.n_sections >= 4 and len(self.canonical_name) >= 7:
            return "high"
        if self.n_sections >= 3 and len(self.canonical_name) >= 5:
            return "medium"
        return "low"


def _load_entities(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return (entity_id, canonical_name, type) for entities we'll scan for."""
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, canonical_name, type FROM entities "
        "WHERE canonical_name IS NOT NULL AND LENGTH(canonical_name) >= 3"
    ).fetchall()
    out: list[tuple[str, str, str]] = []
    for eid, name, etype in rows:
        if etype not in ALLOWED_TYPES:
            continue
        out.append((eid, name.strip(), etype))
    return out


def _load_today_records(conn: sqlite3.Connection, day: str) -> list[tuple[str, str, str]]:
    """Return (record_id, section_id, search_blob) for records on `day`.

    search_blob concatenates original_text + extra_json so substring
    matching works against title, summary, and any extra-field strings.
    """
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, section_id, COALESCE(original_text,'') || ' ' || COALESCE(extra_json,'') "
        "FROM records WHERE record_date = ?",
        (day,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _scan(
    entities: list[tuple[str, str, str]],
    records: list[tuple[str, str, str]],
) -> dict[str, EntityHit]:
    """Substring-match each entity's canonical_name into each record's blob."""
    hits: dict[str, EntityHit] = {}
    # Pre-compile case-insensitive whole-word patterns. Whole-word avoids
    # matches like "Iran" inside "Iranian" or "AI" inside "PAID".
    name_patterns: list[tuple[str, str, str, re.Pattern]] = []
    for eid, name, etype in entities:
        # Use \b for word boundaries. Escape regex specials in name.
        pat = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        name_patterns.append((eid, name, etype, pat))

    for rid, sid, blob in records:
        if not blob:
            continue
        for eid, name, etype, pat in name_patterns:
            if pat.search(blob):
                hit = hits.get(eid)
                if hit is None:
                    hit = EntityHit(entity_id=eid, canonical_name=name, entity_type=etype)
                    hits[eid] = hit
                hit.section_counts[sid] = hit.section_counts.get(sid, 0) + 1
                evidence = hit.record_evidence.setdefault(sid, [])
                if len(evidence) < 3:
                    evidence.append(rid)
    return hits


def analyze(day: str, *, min_sections: int = 3, db_path: Path | None = None) -> dict:
    """Run the cross-section analysis for `day` and return the structured result."""
    db_path = db_path or (Path(__file__).resolve().parent.parent.parent
                          / "lake" / "db" / "worldscope.sqlite")
    if not db_path.exists():
        raise FileNotFoundError(f"lake sqlite not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        entities = _load_entities(conn)
        records = _load_today_records(conn, day)
        hits = _scan(entities, records)
    finally:
        conn.close()

    # Filter to recurrences >= min_sections, sort by (n_sections desc, mentions desc).
    recurrences = [h for h in hits.values() if h.n_sections >= min_sections]
    recurrences.sort(key=lambda h: (h.n_sections, h.total_mentions), reverse=True)

    result: dict = {
        "day": day,
        "min_sections_threshold": min_sections,
        "entities_scanned": len(entities),
        "records_scanned": len(records),
        "recurrences_found": len(recurrences),
        "by_confidence": {
            "high": [],
            "medium": [],
            "low": [],
        },
        "all": [],
    }
    for h in recurrences:
        item = {
            "entity_id": h.entity_id,
            "canonical_name": h.canonical_name,
            "entity_type": h.entity_type,
            "n_sections": h.n_sections,
            "total_mentions": h.total_mentions,
            "confidence": h.confidence(),
            "sections": sorted(h.section_counts.keys()),
            "section_counts": dict(sorted(h.section_counts.items())),
            "evidence_records": h.record_evidence,
        }
        result["by_confidence"][h.confidence()].append(item)
        result["all"].append(item)
    return result


def write(day: str, *, out_root: Path | None = None, **kwargs) -> Path:
    """Run analyze() and persist to lake/sections/_meta/<day>/cross_section.json."""
    out_root = out_root or (Path(__file__).resolve().parent.parent.parent / "lake")
    out_dir = out_root / "sections" / "_meta" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cross_section.json"
    result = analyze(day, **kwargs)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import argparse
    import datetime as _dt
    parser = argparse.ArgumentParser(description="Cross-section entity recurrence analyzer.")
    parser.add_argument("--day", default=_dt.date.today().isoformat(),
                        help="YYYY-MM-DD (default: today)")
    parser.add_argument("--min-sections", type=int, default=3,
                        help="Minimum section count for recurrence (default: 3)")
    args = parser.parse_args()
    out = write(args.day, min_sections=args.min_sections)
    print(f"wrote {out}")
    # Print top 15 to stdout as a quick sanity check
    data = json.loads(out.read_text())
    print(f"\n{data['recurrences_found']} recurrences "
          f"(scanned {data['entities_scanned']} entities × "
          f"{data['records_scanned']} records)")
    print("\nTop 15 by section count:")
    for item in data["all"][:15]:
        sects = ", ".join(item["sections"][:5])
        if len(item["sections"]) > 5:
            sects += f" +{len(item['sections']) - 5}"
        print(f"  [{item['confidence']:6}] {item['canonical_name'][:32]:32} "
              f"({item['entity_type']:8}) → {item['n_sections']} sections: {sects}")
