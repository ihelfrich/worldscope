"""
trends.py: light-weight trend analysis over historical snapshots.

For each section we compute:
  - today's count
  - 7-day median count
  - 14-day median count
  - the new-vs-total share for the last 7 days
  - any text terms that appear today AND in >= 3 prior snapshots in the
    last 14 days (the "carrying narrative", terms recurring across days)

Cheap, deterministic, no LLM needed. Feeds the overview generator with
hard numbers that the synthesis can be grounded in.

Note on backfill placeholders
-----------------------------
The SQLite snapshot store this module reads from
(worldscope.store.SnapshotStore) is separate from the lake. Lake placeholders
written by tools/backfill_lake_history.py do not appear here. The
`_is_backfill_payload` helper is provided for any future lake-aware code
path that wants to filter records carrying state == "backfill_no_data";
the per-section output also surfaces a `real_days` count that downstream
consumers should prefer over `len(series)` when reporting a denominator.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from typing import Any

from .store import SnapshotStore


def _is_backfill_payload(payload: Any) -> bool:
    """True iff the snapshot payload was written as a backfill placeholder
    rather than a real pull. The lake layer marks these explicitly with
    state == "backfill_no_data"; the SnapshotStore path uses status ==
    "backfill_no_data" by analogy. Either form is honored here."""
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("status") == "backfill_no_data"
        or payload.get("state") == "backfill_no_data"
    )


def _tokens(text: str) -> set[str]:
    """Crude content-word tokens: words >= 5 chars, lowercased, deduped per item."""
    return {w for w in re.findall(r"[A-Za-z]{5,}", (text or "").lower())}


def _load_last_n_days(
    store: SnapshotStore, section_id: str, *, n: int = 14,
) -> list[tuple[date, list[dict], bool]]:
    """Return [(snapshot_date, items, is_backfill), ...] sorted ascending
    for the last n days.

    Payloads are SCHEMA_VERSION=2 dicts with shape:
        {schema_version, pulled_at, status, error, items}
    We extract `items` and flag whether the payload is a backfill
    placeholder (status/state == "backfill_no_data"). Old (v1) payloads
    that were bare lists get treated as empty (schema drift defense).
    """
    conn: sqlite3.Connection = store._conn
    rows = conn.execute(
        "SELECT snapshot_date, payload FROM snapshots "
        "WHERE section_id = ? AND snapshot_date >= ? ORDER BY snapshot_date",
        (section_id, (date.today() - timedelta(days=n)).isoformat()),
    ).fetchall()
    out: list[tuple[date, list[dict], bool]] = []
    for d, p in rows:
        try:
            payload = json.loads(p)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            out.append((
                date.fromisoformat(d),
                payload["items"],
                _is_backfill_payload(payload),
            ))
        # else: silently drop, schema drift or pre-v2 payload
    return out


def section_trend(store: SnapshotStore, section_id: str) -> dict[str, Any]:
    series = _load_last_n_days(store, section_id, n=14)
    # Counts include every day (real + backfill placeholders) so the
    # series rendered downstream has a stable shape. Medians and the
    # carrying-narrative token frequencies are computed over REAL days
    # only: backfill placeholders contribute zero items and would otherwise
    # collapse the median toward 0 artificially.
    counts = [(d, len(items)) for d, items, _ in series]
    real_counts = [(d, len(items)) for d, items, is_bf in series if not is_bf]
    today_count = counts[-1][1] if counts else 0

    last_7_real = [c for _, c in real_counts[-7:]]
    last_14_real = [c for _, c in real_counts]
    median = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0

    # Carrying narrative: tokens that appear today AND in >= 3 prior REAL
    # snapshots. Backfill placeholders have no items so they would not
    # contribute tokens anyway, but we exclude them explicitly for clarity.
    today_tokens: set[str] = set()
    if series:
        for item in series[-1][1]:
            today_tokens |= _tokens(item.get("title", "") + " " + item.get("summary", ""))

    prior_token_freq: Counter[str] = Counter()
    for _, items, is_bf in series[:-1]:
        if is_bf:
            continue
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
        "median_7d": median(last_7_real),
        "median_14d": median(last_14_real),
        "series": [(d.isoformat(), c) for d, c in counts],
        "real_days": len(real_counts),
        "backfill_days": len(series) - len(real_counts),
        "carrying_terms": carrying,
    }
