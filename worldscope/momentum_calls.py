"""Momentum calls: falsifiable forecasts that can actually go either way.

The signal engine's persistence calls ("X is still mentioned by 2+ sections
within 14 days") came true 1,390 times in 1,404 (99%). They are a salience
list, not forecasts. This adds a second kind with real uncertainty: for each
story the momentum engine marks SURGING or NEW, will it still be covered by
>= MIN_SECTIONS distinct sections on the day HORIZON_DAYS ahead? Surges often
fade within days, so the answer is genuinely open.

Each call is self-grading from the lake (no human input) and its stated
probability is calibrated against this method's own graded record per
momentum class, starting from an uninformative 50%.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

METHOD = "momentum-v1"
HORIZON_DAYS = 3
MIN_SECTIONS = 3
CLASSES = ("surging", "new")
PRIOR = 0.5
PRIOR_WEIGHT = 10        # pseudo-observations behind the 50% prior
CAP = (0.05, 0.95)
MAX_CALLS = 8


def _call_id(key: str, made: str) -> str:
    return hashlib.sha1(f"{METHOD}|{key}|{made}".encode()).hexdigest()


BACKTEST_PATH = Path(__file__).resolve().parent.parent / "data" / "momentum_calls_backtest.json"


def _backtest() -> dict[str, list[int]]:
    """Out-of-sample backtest tallies (tools/backtest_momentum_calls.py): each
    past day's calls made from data up to that day, graded on day + horizon."""
    import json
    try:
        bc = json.loads(BACKTEST_PATH.read_text(encoding="utf-8")).get("by_class", {})
    except (OSError, ValueError):
        return {}
    return {k: [int(v.get("yes", 0)), int(v.get("n", 0))] for k, v in bc.items()}


def track_record(conn, *, include_backtest: bool = True) -> dict[str, tuple[int, int]]:
    """{momentum class: (YES, total)}: live graded calls plus the backtest."""
    import json
    out: dict[str, list[int]] = _backtest() if include_backtest else {}
    try:
        rows = conn.execute(
            "SELECT indicators_used_json, actual_outcome FROM predictions "
            "WHERE method = ? AND actual_outcome IN ('YES', 'NO')", (METHOD,)).fetchall()
    except Exception:
        return {}
    for ind, outcome in rows:
        try:
            klass = (json.loads(ind or "[]") or ["?"])[0]
        except ValueError:
            continue
        b = out.setdefault(klass, [0, 0])
        b[0] += outcome == "YES"
        b[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def probability(klass: str, record: Optional[dict[str, tuple[int, int]]]) -> float:
    yes, n = (record or {}).get(klass, (0, 0))
    p = (yes + PRIOR_WEIGHT * PRIOR) / (n + PRIOR_WEIGHT)
    return round(min(max(p, CAP[0]), CAP[1]), 4)


def bucket(story: dict) -> str:
    """Calibration bucket: momentum class x whether the story has a long
    baseline. A surge in a perennial story (Putin: present 34 of 35 days) is
    nothing like a one-off spike (a ship sinking), and one rate for both made
    the perennial calls absurdly low."""
    window = len(story.get("records") or []) or 1
    base = "perennial" if story.get("days_present", 0) * 2 >= window else "episodic"
    return f"{story.get('klass')}:{base}"


def make_calls(stories: Iterable[dict], *, today: date,
               record: Optional[dict[str, tuple[int, int]]] = None) -> list[dict]:
    """Predictions (lake ``add_prediction`` shape) for surging/new stories."""
    made = today.isoformat()
    target = (today + timedelta(days=HORIZON_DAYS)).isoformat()
    out = []
    for st in stories:
        klass = st.get("klass")
        if klass not in CLASSES or not st.get("key"):
            continue
        b = bucket(st)
        out.append({
            "id": _call_id(st["key"], made),
            "target_date": target,
            "resolution_criteria": (
                f"Resolves YES if '{st['label']}' (key '{st['key']}') is referenced by "
                f">= {MIN_SECTIONS} distinct WORLDSCOPE sections on {target}; "
                f"otherwise NO."),
            "predicted_outcome": "YES",
            "confidence": probability(b, record),
            "training_window_days": len(st.get("records") or []),
            "indicators_used": [b],
            "method": METHOD,
            "evidence": [],
            "_key": st["key"], "_label": st["label"], "_klass": klass, "_bucket": b,
        })
        if len(out) >= MAX_CALLS:
            break
    return out


def sections_on_day(day: date, *, lake_path=None) -> dict[str, set[str]]:
    """{key: sections} for records collected on ``day``, read exactly as the
    momentum engine reads them (live DB + archive, keyed by ingestion day, the
    same key extraction), so a call is graded on the terms it was made on.
    The live DB keeps only a rolling week; the archive covers the rest."""
    from .momentum import DEFAULT_LAKE, _canon, _iter_window
    from .signals import record_key_pairs
    out: dict[str, set[str]] = {}
    for d, rec in _iter_window(day, 1, lake_path or DEFAULT_LAKE):
        if d != day.isoformat():
            continue
        for key, cased in record_key_pairs(rec):
            key, _ = _canon(key, cased)
            if key:
                out.setdefault(key, set()).add(rec["section_id"])
    return out


def grade_key_on_day(key: str, by_key: dict[str, set[str]],
                     min_sections: int = MIN_SECTIONS) -> str:
    return "YES" if len(by_key.get(key, ())) >= min_sections else "NO"


def grade_due(lake, conn, *, today: date, lake_path=None) -> int:
    """Settle calls whose target day is complete (target < today)."""
    from .signals import _recover_key
    due = [dict(r) for r in conn.execute(
        "SELECT id, target_date, resolution_criteria FROM predictions "
        "WHERE method = ? AND (actual_outcome IS NULL OR actual_outcome = '') "
        "AND target_date IS NOT NULL AND target_date < ?",
        (METHOD, today.isoformat())).fetchall()]
    by_day: dict[str, dict[str, set[str]]] = {}
    graded = 0
    for r in due:
        key = _recover_key(r["resolution_criteria"])
        if not key:
            continue
        day = r["target_date"][:10]
        if day not in by_day:
            by_day[day] = sections_on_day(date.fromisoformat(day), lake_path=lake_path)
        if not by_day[day]:
            continue    # nothing collected that day: ungradeable, not a NO
        lake.resolve_prediction(prediction_id=r["id"], resolved_at=today.isoformat(),
                                actual_outcome=grade_key_on_day(key, by_day[day]))
        graded += 1
    return graded
