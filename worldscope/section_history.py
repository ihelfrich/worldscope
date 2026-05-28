"""section_history — emit per-section daily item counts for sparklines.

Writes dist/data/section_history.json with the last N days of item
counts per section, sourced from the snapshot store. Read by the
homepage's inline section TOC to render 14-day sparklines without any
client-side IO beyond the single JSON load.

Shape:
  {
    "as_of": "2026-05-28",
    "lookback_days": 14,
    "sections": {
      "federal_register": {
        "history": [3, 5, 4, 7, 6, 12, 21, ...],   # newest LAST
        "dates":   ["2026-05-15", ..., "2026-05-28"],
        "today":   21,
        "delta_24h": 9,
        "delta_7d_pct": 233.3,
        "max":     21
      },
      ...
    }
  }
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

LOOKBACK_DAYS = 14


def build(store_db: Path, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    if not store_db.exists():
        return {"as_of": today.isoformat(), "lookback_days": LOOKBACK_DAYS,
                "sections": {}}
    conn = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        # Pull only the date range we care about.
        cutoff = (today - timedelta(days=LOOKBACK_DAYS - 1)).isoformat()
        rows = conn.execute(
            "SELECT section_id, snapshot_date, payload "
            "  FROM snapshots WHERE snapshot_date >= ? "
            "  ORDER BY section_id, snapshot_date",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    # Build dense per-section, per-day count matrix.
    by_section: dict[str, dict[str, int]] = {}
    for sid, d, payload in rows:
        try:
            n = len(json.loads(payload).get("items") or [])
        except Exception:
            n = 0
        by_section.setdefault(sid, {})[d] = n

    # Emit dense arrays, filling missing days with 0
    dates = [
        (today - timedelta(days=LOOKBACK_DAYS - 1 - i)).isoformat()
        for i in range(LOOKBACK_DAYS)
    ]
    out: dict[str, Any] = {}
    for sid, counts_by_day in by_section.items():
        history = [counts_by_day.get(d, 0) for d in dates]
        today_n = history[-1]
        yesterday_n = history[-2] if len(history) > 1 else 0
        seven_ago = history[-8] if len(history) > 7 else 0
        delta_24h = today_n - yesterday_n
        if seven_ago:
            delta_7d_pct = round((today_n - seven_ago) / seven_ago * 100, 1)
        else:
            delta_7d_pct = None
        out[sid] = {
            "history":      history,
            "dates":        dates,
            "today":        today_n,
            "delta_24h":    delta_24h,
            "delta_7d_pct": delta_7d_pct,
            "max":          max(history) if history else 0,
        }

    return {
        "as_of":         today.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "sections":      out,
    }


def build_from_repo(repo: Path, out_dir: Path,
                     today: date | None = None) -> Path:
    doc = build(repo / "data" / "store.sqlite", today=today)
    out_dir = Path(out_dir)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    path = out_dir / "data" / "section_history.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return path
