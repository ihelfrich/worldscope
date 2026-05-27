"""Composite per-figure anomaly scoring.

Six components, weighted, summed to a value in [0, 1]:

    stock_activity      0.25   PTR volume vs 90d baseline + max |ExcessReturn| vs SPY in past 7d
    speech_volume       0.15   words spoken on floor / press vs 90d trailing
    speech_topic_drift  0.15   cosine distance from 90d topic centroid
    gdelt_tone          0.15   standardized 24h tone delta vs 30d baseline
    new_filings         0.10   count of new disclosures (PTR / Form 4 / OGE-278) past 7d
    enforcement_hits    0.20   DOJ + OIG + CourtListener mentions past 7d (binned 0/1/2+)

Each component returns a float in [0, 1]. Missing data degrades that
component to 0 (the figure is silently scored as "not anomalous on this
axis") rather than failing the whole pipeline.

Inputs:
    figure       a dict from the YAML registry (must have 'id', 'name')
    signals      a dict of pre-fetched signal arrays keyed by source:
                   {
                     "ptrs":         [ {"date": iso, "ticker": str, "amount_low_usd": float,
                                         "excess_return_pct": float|None, ...}, ... ],
                     "speeches":     [ {"date": iso, "word_count": int, "text": str}, ... ],
                     "speech_embed": np.ndarray shape (N, D) or None,
                     "gdelt_tone":   [ {"date": iso, "tone": float}, ... ],
                     "filings":      [ {"date": iso, "kind": str}, ... ],
                     "doj_hits":     [ {"date": iso, "url": str, "title": str}, ... ],
                     "oig_hits":     [ ... ],
                     "court_hits":   [ ... ],
                   }

The scorer is pure-Python with numpy; it does not touch the network. The
political_figures section is responsible for gathering signals; this module
only does the math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import numpy as np


COMPONENT_WEIGHTS = {
    "stock_activity":     0.25,
    "speech_volume":      0.15,
    "speech_topic_drift": 0.15,
    "gdelt_tone":         0.15,
    "new_filings":        0.10,
    "enforcement_hits":   0.20,
}
# Sanity: weights sum to 1.0
assert abs(sum(COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


@dataclass
class AnomalyComponents:
    """One row's six raw component scores, each in [0, 1]."""
    stock_activity: float = 0.0
    speech_volume: float = 0.0
    speech_topic_drift: float = 0.0
    gdelt_tone: float = 0.0
    new_filings: float = 0.0
    enforcement_hits: float = 0.0

    def composite(self) -> float:
        s = 0.0
        for name, w in COMPONENT_WEIGHTS.items():
            s += w * float(getattr(self, name, 0.0))
        # Clip to [0, 1] defensively. Individual components are already clipped
        # but mis-configured inputs could drift outside.
        return max(0.0, min(1.0, s))


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_iso(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    try:
        return date.fromisoformat(d[:10])
    except (ValueError, TypeError):
        return None


def _within(d: Optional[date], end: date, *, days: int) -> bool:
    if d is None:
        return False
    return (end - d).days <= days and d <= end


def _saturate(x: float, *, half: float) -> float:
    """Map [0, infinity) to [0, 1) with `half` mapping to 0.5.
    Uses x / (x + half), which is smooth, monotone, and has no clipping discontinuity."""
    if x <= 0 or not math.isfinite(x):
        return 0.0
    return x / (x + half)


# --------------------------------------------------------------------- #
# Component scorers
# --------------------------------------------------------------------- #


def stock_activity_score(ptrs: Iterable[dict], *, today: Optional[date] = None,
                          recent_days: int = 30) -> float:
    """Two sub-components: trailing recent-window PTR count vs 90d baseline,
    plus max |ExcessReturn vs SPY| over the same recent window.

    The recent window defaults to 30 days. The spec calls for 7d; in practice
    the STOCK Act allows up to 45 days between trade and disclosure and
    Quiver's `transaction_date` is the trade date, so most disclosed trades
    land 2 to 5 weeks back. A pure 7d window would always score zero in
    normal operation. We use 30d to align with the upstream Quiver lookback.

    Returns a value in [0, 1]. Empty input scores 0.
    """
    today = today or _today_utc()
    rows = list(ptrs or [])
    if not rows:
        return 0.0

    count_recent = sum(1 for r in rows
                        if _within(_parse_iso(r.get("date")), today, days=recent_days))
    count_90d = sum(1 for r in rows if _within(_parse_iso(r.get("date")), today, days=90))

    # Baseline rate over the full 90d window. Recent-window rate compared
    # against it. If 90d baseline is zero, fall back to raw recent count.
    daily_baseline = count_90d / 90.0 if count_90d else 0.0
    recent_rate = count_recent / float(recent_days)
    if daily_baseline <= 0:
        volume_signal = _saturate(float(count_recent), half=5.0)
    else:
        # Excess rate: how much above baseline did recent activity run?
        excess_rate = max(0.0, recent_rate - daily_baseline)
        volume_signal = _saturate(excess_rate, half=max(daily_baseline, 0.1))
        # If even the baseline-equal case is informative (the figure trades
        # at all), grant a floor proportional to count.
        if volume_signal < 0.1 and count_recent > 0:
            volume_signal = max(volume_signal,
                                 _saturate(float(count_recent), half=8.0))

    # Max |excess return| over recent window
    ers_recent = []
    for r in rows:
        if not _within(_parse_iso(r.get("date")), today, days=recent_days):
            continue
        er = r.get("excess_return_pct")
        if er is None:
            continue
        try:
            ers_recent.append(abs(float(er)))
        except (TypeError, ValueError):
            continue
    if ers_recent:
        peak = max(ers_recent)
        # 25% absolute excess vs SPY -> 0.5 component contribution
        er_signal = _saturate(peak, half=25.0)
    else:
        er_signal = 0.0

    # Equally weighted average of the two sub-components.
    return 0.5 * volume_signal + 0.5 * er_signal


def speech_volume_score(speeches: Iterable[dict], *, today: Optional[date] = None) -> float:
    """Words spoken in past 7d compared to mean weekly volume over 90d.
    Z-score-like; saturated. Empty input scores 0."""
    today = today or _today_utc()
    rows = list(speeches or [])
    if not rows:
        return 0.0

    weeks: dict[int, int] = {}
    last_7d_words = 0
    for r in rows:
        d = _parse_iso(r.get("date"))
        if d is None:
            continue
        wc = int(r.get("word_count") or 0)
        if (today - d).days <= 7:
            last_7d_words += wc
        if 0 <= (today - d).days <= 90:
            week_index = (today - d).days // 7
            weeks[week_index] = weeks.get(week_index, 0) + wc

    if not weeks:
        return 0.0
    weekly_volumes = list(weeks.values())
    baseline_mean = float(np.mean(weekly_volumes))
    baseline_std = float(np.std(weekly_volumes))

    if baseline_mean <= 0:
        return _saturate(float(last_7d_words), half=2000.0)

    z = (last_7d_words - baseline_mean) / (baseline_std if baseline_std > 1 else baseline_mean)
    return _saturate(max(0.0, z), half=2.0)


def speech_topic_drift_score(speech_embed: Optional[np.ndarray],
                             *, recent_n: int = 5) -> float:
    """Cosine distance from 90d topic centroid.

    `speech_embed` is an (N, D) matrix where rows are speech embeddings in
    chronological order, most-recent LAST. We compare the mean of the last
    `recent_n` rows to the mean of the preceding rows. The result is
    1 - cosine_similarity, mapped to [0, 1].

    Empty / single-row input scores 0.
    """
    if speech_embed is None:
        return 0.0
    if not isinstance(speech_embed, np.ndarray):
        return 0.0
    if speech_embed.ndim != 2 or speech_embed.shape[0] < (recent_n + 2):
        return 0.0

    recent = speech_embed[-recent_n:]
    baseline = speech_embed[:-recent_n]
    if recent.shape[0] == 0 or baseline.shape[0] == 0:
        return 0.0

    r_mean = recent.mean(axis=0)
    b_mean = baseline.mean(axis=0)
    # Cosine similarity in [-1, 1]; distance = (1 - sim) / 2 keeps it in [0, 1].
    rn = np.linalg.norm(r_mean) or 1.0
    bn = np.linalg.norm(b_mean) or 1.0
    sim = float(np.dot(r_mean, b_mean) / (rn * bn))
    distance = (1.0 - sim) / 2.0
    return max(0.0, min(1.0, distance))


def gdelt_tone_score(tones: Iterable[dict], *, today: Optional[date] = None) -> float:
    """Standardized 24h tone delta vs 30d baseline.

    Each tone row: {"date": iso, "tone": float}. GDELT tone is in [-100, 100];
    negative = more negative coverage. We compute the abs-value of (mean
    tone in last 24h minus 30d baseline mean) divided by the 30d std, then
    saturate.

    Empty input scores 0.
    """
    today = today or _today_utc()
    rows = list(tones or [])
    if not rows:
        return 0.0

    t24 = [float(r["tone"]) for r in rows
           if _within(_parse_iso(r.get("date")), today, days=1) and r.get("tone") is not None]
    t30 = [float(r["tone"]) for r in rows
           if _within(_parse_iso(r.get("date")), today, days=30) and r.get("tone") is not None]
    if not t24 or len(t30) < 5:
        return 0.0
    mean24 = float(np.mean(t24))
    mean30 = float(np.mean(t30))
    std30 = float(np.std(t30)) or 1.0
    z = abs(mean24 - mean30) / std30
    return _saturate(z, half=1.5)


def new_filings_score(filings: Iterable[dict], *, today: Optional[date] = None) -> float:
    """Count of new disclosures/Form 4/OGE-278 in past 7 days. Saturated.
    Empty input scores 0."""
    today = today or _today_utc()
    rows = list(filings or [])
    n = sum(1 for r in rows if _within(_parse_iso(r.get("date")), today, days=7))
    if n == 0:
        return 0.0
    return _saturate(float(n), half=3.0)


def enforcement_hits_score(doj_hits: Iterable[dict],
                            oig_hits: Iterable[dict],
                            court_hits: Iterable[dict],
                            *, today: Optional[date] = None) -> float:
    """DOJ + OIG + CourtListener mentions past 7d, binned 0 / 1 / 2+.

    bin 0 -> 0.0
    bin 1 -> 0.5
    bin 2+ -> 1.0

    Each list is counted independently; sum is the total bin count.
    """
    today = today or _today_utc()
    def _count(rows):
        return sum(1 for r in rows or []
                   if _within(_parse_iso(r.get("date")), today, days=7))
    total = _count(doj_hits) + _count(oig_hits) + _count(court_hits)
    if total <= 0:
        return 0.0
    if total == 1:
        return 0.5
    return 1.0


# --------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------- #


@dataclass
class FigureAnomalyScorer:
    """Stateless scorer. Wrap inputs with `score(figure, signals)`."""

    today: Optional[date] = None

    def score(self, figure: dict, signals: dict) -> dict:
        today = self.today or _today_utc()
        comps = AnomalyComponents(
            stock_activity=stock_activity_score(
                signals.get("ptrs") or [], today=today),
            speech_volume=speech_volume_score(
                signals.get("speeches") or [], today=today),
            speech_topic_drift=speech_topic_drift_score(
                signals.get("speech_embed")),
            gdelt_tone=gdelt_tone_score(
                signals.get("gdelt_tone") or [], today=today),
            new_filings=new_filings_score(
                signals.get("filings") or [], today=today),
            enforcement_hits=enforcement_hits_score(
                signals.get("doj_hits") or [],
                signals.get("oig_hits") or [],
                signals.get("court_hits") or [],
                today=today,
            ),
        )
        return {
            "id": figure.get("id"),
            "name": figure.get("name"),
            "role": figure.get("role"),
            "date": today.isoformat(),
            "components": asdict(comps),
            "anomaly_score": comps.composite(),
            "weights": dict(COMPONENT_WEIGHTS),
        }


def score_figure(figure: dict, signals: dict,
                 *, today: Optional[date] = None) -> dict:
    """Convenience: one figure, one score."""
    return FigureAnomalyScorer(today=today).score(figure, signals)


def score_all(figures: Iterable[dict],
              signal_lookup,
              *, today: Optional[date] = None) -> list[dict]:
    """Score every figure in the iterable.

    `signal_lookup` is a callable: figure -> signals dict. Called once per
    figure. This indirection lets the section pre-build signal indexes once
    and key lookups by figure_id without re-walking the lake per figure.
    """
    scorer = FigureAnomalyScorer(today=today)
    out = []
    for figure in figures:
        if figure.get("name") == "TODO":
            # Stub slots score 0 and carry a marker; they remain in the
            # registry but the brief filter can drop them.
            out.append({
                "id": figure.get("id"),
                "name": "TODO",
                "role": figure.get("role"),
                "date": (today or _today_utc()).isoformat(),
                "components": asdict(AnomalyComponents()),
                "anomaly_score": 0.0,
                "weights": dict(COMPONENT_WEIGHTS),
                "is_stub": True,
            })
            continue
        try:
            signals = signal_lookup(figure) or {}
        except Exception as exc:
            signals = {}
        row = scorer.score(figure, signals)
        out.append(row)
    return out
