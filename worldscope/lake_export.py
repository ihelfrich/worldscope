"""Lake export: write today's lake records + entities + cross-section signals
to static JSON under dist/data/ so the browser-side chat widget can query
them without a backend.

Static + small by design:

  dist/data/today.json        ~ 1-2 MB, sharded record snapshot for today
                                {date, sections: {sid: [records...]}}
  dist/data/entities.json     ~ 100-500 KB, entities mentioned today
                                [{id, name, type, sections: [...]}, ...]
  dist/data/signals.json      ~ tiny, copy of cross_section.json today
                                {date, entities: [...]}

These are loaded once on chat-panel-open and queried client-side by the
chat tools defined in dist/assets/worldscope-chat.js. The data is
intentionally pre-shaped (denormalized, per-section grouped) so the JS
side does zero joins.

Wired into worldscope/brief.py step 1f-bis, after site_builder runs.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any

# Cap per-section export so the static JSON stays small enough to load
# quickly on chat-panel-open. The full lake is queryable via the MCP server;
# the browser tier ships the top-N most recent records per section.
MAX_RECORDS_PER_SECTION = 50
MAX_TOTAL_ENTITIES = 800


def _today_iso(today: _date | None = None) -> str:
    return (today or _date.today()).isoformat()


def export_today(
    *,
    lake_db: Path,
    meta_dir: Path,
    out_dir: Path,
    today: _date | None = None,
) -> dict[str, Path]:
    """Export today's lake state to static JSON files.

    Args:
        lake_db: path to lake/db/worldscope.sqlite
        meta_dir: path to lake/sections/_meta (for cross-section signals)
        out_dir: dist/ root; this function writes under dist/data/
        today: target date (default: today UTC)

    Returns:
        {"today": Path, "entities": Path, "signals": Path} — paths written.
    """
    iso = _today_iso(today)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    today_path    = data_dir / "today.json"
    entities_path = data_dir / "entities.json"
    signals_path  = data_dir / "signals.json"

    # ---- records snapshot --------------------------------------------------
    sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    section_counts: dict[str, int] = {}
    total_records = 0
    if lake_db.exists():
        conn = sqlite3.connect(f"file:{lake_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, section_id, source_id, original_text, original_url,
                       record_date, ingested_at, original_lang
                  FROM records
                 WHERE substr(ingested_at, 1, 10) = ?
                 ORDER BY section_id, ingested_at DESC
                """,
                (iso,),
            ).fetchall()
            for r in rows:
                sid = r["section_id"]
                section_counts[sid] = section_counts.get(sid, 0) + 1
                if len(sections[sid]) >= MAX_RECORDS_PER_SECTION:
                    continue
                sections[sid].append({
                    "id":           r["id"],
                    "section_id":   sid,
                    "source_id":    r["source_id"],
                    "text":         (r["original_text"] or "")[:300],
                    "url":          r["original_url"],
                    "date":         r["record_date"],
                    "ingested_at":  r["ingested_at"],
                    "lang":         r["original_lang"],
                })
                total_records += 1
        finally:
            conn.close()

    today_doc = {
        "date":           iso,
        "generated_at":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "section_counts": section_counts,
        "exported_records": total_records,
        "cap_per_section":  MAX_RECORDS_PER_SECTION,
        "sections":       dict(sorted(sections.items())),
    }
    today_path.write_text(json.dumps(today_doc, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")

    # ---- entities mentioned today -----------------------------------------
    entities_doc: dict[str, Any] = {"date": iso, "entities": []}
    if lake_db.exists():
        conn = sqlite3.connect(f"file:{lake_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            ent_rows = conn.execute(
                """
                SELECT DISTINCT e.id, e.canonical_name, e.type
                  FROM entities e
                  JOIN record_entities re ON e.id = re.entity_id
                  JOIN records r ON re.record_id = r.id
                 WHERE substr(r.ingested_at, 1, 10) = ?
                 ORDER BY e.canonical_name
                 LIMIT ?
                """,
                (iso, MAX_TOTAL_ENTITIES),
            ).fetchall()
            # For each, fetch which sections mention it today
            for er in ent_rows:
                sec_rows = conn.execute(
                    """
                    SELECT DISTINCT r.section_id FROM records r
                      JOIN record_entities re ON r.id = re.record_id
                     WHERE re.entity_id = ?
                       AND substr(r.ingested_at, 1, 10) = ?
                    """,
                    (er["id"], iso),
                ).fetchall()
                entities_doc["entities"].append({
                    "id":   er["id"],
                    "name": er["canonical_name"],
                    "type": er["type"],
                    "sections": sorted({s["section_id"] for s in sec_rows}),
                })
        finally:
            conn.close()
    entities_path.write_text(json.dumps(entities_doc, ensure_ascii=False, separators=(",", ":")),
                             encoding="utf-8")

    # ---- cross-section signals (copy of analyzer output) -------------------
    cs_in = meta_dir / iso / "cross_section.json"
    if cs_in.exists():
        signals_path.write_text(cs_in.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        signals_path.write_text(json.dumps({"date": iso, "by_confidence": {}}), encoding="utf-8")

    return {"today": today_path, "entities": entities_path, "signals": signals_path}


# Convenience: invoked from the daily brief orchestrator.
def export_from_repo(repo_root: Path, out_dir: Path, today: _date | None = None) -> dict[str, Path]:
    return export_today(
        lake_db=repo_root / "lake" / "db" / "worldscope.sqlite",
        meta_dir=repo_root / "lake" / "sections" / "_meta",
        out_dir=Path(out_dir),
        today=today,
    )
