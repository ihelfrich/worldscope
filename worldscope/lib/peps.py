"""
peps.py — politically-exposed-persons lookup over the local OpenSanctions corpus.

The corpus at ~/Projects/econscope/data/opensanctions/entities.ftm.json contains
625,942 entries tagged as Wikidata-derived PEPs (`wd_peps` dataset). This module
builds an indexed view: streams the FtM file once, extracts the PEP entries with
country + position info, writes them to a compact SQLite table for fast lookup.

After the one-time index build (~3 minutes), queries are millisecond-fast.

Query API:
    by_country(iso_or_name, limit=50)     → list of PEPs from that country
    by_role_pattern(regex, limit=50)      → PEPs whose position matches a regex
    count_by_country()                    → {country: n} histogram
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CORPUS_PATH = Path.home() / "Projects" / "econscope" / "data" / "opensanctions" / "entities.ftm.json"
INDEX_PATH = Path.home() / ".worldscope" / "peps_index.sqlite"


def _index_conn() -> sqlite3.Connection:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(INDEX_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS peps (
            id TEXT PRIMARY KEY,
            caption TEXT NOT NULL,
            schema TEXT NOT NULL,
            countries TEXT NOT NULL,     -- comma-separated ISO codes
            country_names TEXT NOT NULL, -- denormalized human-readable
            position TEXT,
            datasets TEXT,
            topics TEXT,
            modified TEXT,
            built_at TEXT NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS peps_country_idx ON peps(countries)")
    c.execute("CREATE INDEX IF NOT EXISTS peps_modified_idx ON peps(modified DESC)")
    c.commit()
    return c


def is_index_built() -> bool:
    if not INDEX_PATH.exists():
        return False
    c = sqlite3.connect(INDEX_PATH)
    try:
        n = c.execute("SELECT COUNT(*) FROM peps").fetchone()[0]
        return n > 1000
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


def build_index(*, verbose: bool = True) -> int:
    """One-time scan of the FtM file → PEP index. Returns rows written.
    Takes ~2-3 minutes on a 2.6 GB corpus. Idempotent."""
    if not CORPUS_PATH.exists():
        if verbose:
            print(f"[peps] corpus missing at {CORPUS_PATH}; index not built")
        return 0
    c = _index_conn()
    c.execute("DELETE FROM peps")
    inserted = 0
    PERSON_SCHEMAS = {"Person"}
    with CORPUS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if '"wd_peps"' not in line and '"ru_rupep"' not in line and '"_peps"' not in line:
                continue
            try:
                ent = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ent.get("schema") not in PERSON_SCHEMAS:
                continue
            datasets = ent.get("datasets") or []
            if not any("pep" in d.lower() for d in datasets):
                continue
            props = ent.get("properties") or {}
            countries = props.get("country") or []
            country_names = props.get("nationality") or props.get("country") or []
            position = " · ".join((props.get("position") or [])[:3])
            modified_list = props.get("modifiedAt") or []
            modified = max(modified_list) if modified_list else ""
            topics = props.get("topics") or []
            try:
                c.execute(
                    "INSERT OR REPLACE INTO peps VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        ent.get("id", ""),
                        ent.get("caption") or (props.get("name") or ["(unnamed)"])[0],
                        ent.get("schema", ""),
                        ",".join(countries),
                        ",".join(country_names),
                        position,
                        ",".join(datasets),
                        ",".join(topics),
                        modified,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                continue
            inserted += 1
            if verbose and inserted % 50000 == 0:
                print(f"[peps] indexed {inserted:>7d} so far …")
    c.commit()
    if verbose:
        print(f"[peps] index built: {inserted} PEPs")
    return inserted


def by_country(country_iso_or_name: str, *, limit: int = 50) -> list[dict]:
    """Return PEPs for a country. Pass either ISO-2/ISO-3 lower-case (e.g. 'us',
    'usa') or a substring of the country name (case-insensitive)."""
    if not is_index_built():
        return []
    c = sqlite3.connect(INDEX_PATH)
    q = country_iso_or_name.lower()
    rows = c.execute(
        "SELECT id, caption, countries, country_names, position, modified, topics "
        "FROM peps WHERE LOWER(countries) LIKE ? OR LOWER(country_names) LIKE ? "
        "ORDER BY modified DESC LIMIT ?",
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()
    return [{
        "id": r[0], "name": r[1], "countries": r[2], "country_names": r[3],
        "position": r[4], "modified": r[5], "topics": r[6],
    } for r in rows]


def by_role_pattern(pattern: str, *, limit: int = 50) -> list[dict]:
    """Return PEPs whose position field matches a regex (e.g. 'minister of finance')."""
    if not is_index_built():
        return []
    c = sqlite3.connect(INDEX_PATH)
    rows = c.execute(
        "SELECT id, caption, countries, position, modified FROM peps "
        "WHERE position LIKE ? ORDER BY modified DESC LIMIT ?",
        (f"%{pattern}%", limit * 5),  # over-fetch then filter
    ).fetchall()
    pat = re.compile(pattern, re.IGNORECASE)
    out = []
    for r in rows:
        if pat.search(r[3] or ""):
            out.append({
                "id": r[0], "name": r[1], "countries": r[2],
                "position": r[3], "modified": r[4],
            })
            if len(out) >= limit:
                break
    return out


def count_by_country() -> dict[str, int]:
    if not is_index_built():
        return {}
    c = sqlite3.connect(INDEX_PATH)
    rows = c.execute(
        "SELECT countries, COUNT(*) FROM peps GROUP BY countries ORDER BY 2 DESC LIMIT 50"
    ).fetchall()
    return dict(rows)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Query the local PEP index.")
    p.add_argument("--build", action="store_true", help="(re)build the index from the FtM file")
    p.add_argument("--country", help="show PEPs from a country")
    p.add_argument("--role", help="show PEPs whose position matches this regex")
    p.add_argument("--top", action="store_true", help="show PEP counts by country")
    args = p.parse_args()
    if args.build or not is_index_built():
        build_index()
    if args.country:
        for r in by_country(args.country, limit=20):
            print(f"  [{r['modified'] or '?':10s}] {r['name'][:50]:50s}  {r['position'][:60]}")
    if args.role:
        for r in by_role_pattern(args.role, limit=20):
            print(f"  [{r['modified'] or '?':10s}] {r['name'][:40]:40s}  {r['countries'][:20]:20s}  {r['position'][:60]}")
    if args.top:
        for cc, n in count_by_country().items():
            print(f"  {cc[:20]:20s}  {n}")
