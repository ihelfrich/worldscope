"""
trends.py — light-weight trend analysis over historical snapshots.

For each section we compute:
  - today's count
  - 7-day median count
  - 14-day median count
  - the new-vs-total share for the last 7 days
  - any text terms that appear today AND in >= 3 prior snapshots in the
    last 14 days (the "carrying narrative" — terms recurring across days)

Cheap, deterministic, no LLM needed. Feeds the overview generator with
hard numbers that the synthesis can be grounded in.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from typing import Any

from .store import SnapshotStore


def _tokens(text: str) -> set[str]:
    """Crude content-word tokens: words >= 5 chars, lowercased, deduped per item."""
    return {w for w in re.findall(r"[A-Za-z]{5,}", (text or "").lower())}


def _load_last_n_days(store: SnapshotStore, section_id: str, *, n: int = 14) -> list[tuple[date, list[dict]]]:
    """Return [(snapshot_date, items), ...] sorted ascending for the last n days."""
    conn: sqlite3.Connection = store._conn
    rows = conn.execute(
        "SELECT snapshot_date, payload FROM snapshots "
        "WHERE section_id = ? AND snapshot_date >= ? ORDER BY snapshot_date",
        (section_id, (date.today() - timedelta(days=n)).isoformat()),
    ).fetchall()
    return [(date.fromisoformat(d), json.loads(p)) for d, p in rows]


def section_trend(store: SnapshotStore, section_id: str) -> dict[str, Any]:
    series = _load_last_n_days(store, section_id, n=14)
    counts = [(d, len(items)) for d, items in series]
    today_count = counts[-1][1] if counts else 0

    last_7 = [c for _, c in counts[-7:]]
    last_14 = [c for _, c in counts]
    median = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0

    # Carrying narrative: tokens that appear today AND in >= 3 prior snapshots
    today_tokens: set[str] = set()
    if series:
        for item in series[-1][1]:
            today_tokens |= _tokens(item.get("title", "") + " " + item.get("summary", ""))

    prior_token_freq: Counter[str] = Counter()
    for _, items in series[:-1]:
        day_tokens: set[str] = set()
        for item in items:
            day_tokens |= _tokens(item.get("title", "") + " " + item.get("summary", ""))
        for t in day_tokens:
            prior_token_freq[t] += 1

    STOPWORDS = {
        "their", "would", "could", "their", "which", "about", "these", "those",
        "where", "there", "while", "after", "other", "under", "first", "between",
        "should", "during", "without", "before", "through", "system", "states",
        "united", "federal", "register", "purpose", "section", "rules", "shall",
        "requirements", "department", "regulations", "regulation",
    }
    carrying = sorted(
        (t for t in today_tokens if prior_token_freq[t] >= 3 and t not in STOPWORDS),
        key=lambda t: -prior_token_freq[t],
    )[:12]

    return {
        "section_id": section_id,
        "today_count": today_count,
        "median_7d": median(last_7),
        "median_14d": median(last_14),
        "series": [(d.isoformat(), c) for d, c in counts],
        "carrying_terms": carrying,
    }
