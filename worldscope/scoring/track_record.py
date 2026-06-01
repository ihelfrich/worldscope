"""worldscope.scoring.track_record — objective self-evaluation of forecasts.

The system makes two kinds of probabilistic calls and stores them in the lake:

  1. `predictions` — explicit probabilistic forecasts ("X by <date>",
     confidence in [0,1]), later resolved with an actual_outcome.
  2. `paper_bets` — simulated trades where the model's credence differed
     enough from a prediction market's price to merit a position; later
     resolved YES/NO with a final P&L.

This module turns those resolved rows into honest skill metrics: Brier score,
the Brier *skill* score against a climatology baseline, log loss, a
calibration table (predicted probability vs. observed frequency) with its
expected calibration error, and — for bets — hit rate by confidence band and
the realized edge over the market's own implied probability (the only number
that says whether the system actually beat the crowd, not just guessed the
favorite).

Design, matching the rest of `worldscope.scoring`: deliberately offline and
dependency-free. Every function takes plain row-dicts (so it is trivial to
test without a database) and degrades to empty/None on missing data rather
than raising. Lake wiring lives in the thin `*_from_connection` helpers at
the bottom.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

_EPS = 1e-6  # log-loss clamp so a 0/1 probability can't blow up to infinity


def _norm(s: Any) -> str:
    """Normalize an outcome label for equality comparison."""
    return str(s or "").strip().lower()


def _clamp01(p: float) -> float:
    return 0.0 if p < 0 else 1.0 if p > 1 else p


# --------------------------------------------------------------------------
# predictions: explicit probabilistic forecasts
# --------------------------------------------------------------------------

@dataclass
class CalibrationBin:
    lo: float
    hi: float
    n: int
    mean_predicted: float     # average confidence of calls in this bin
    observed_freq: float      # fraction of those calls that came true

    def as_dict(self) -> dict:
        return {
            "lo": round(self.lo, 3), "hi": round(self.hi, 3), "n": self.n,
            "mean_predicted": round(self.mean_predicted, 4),
            "observed_freq": round(self.observed_freq, 4),
        }


@dataclass
class PredictionSkill:
    n_resolved: int = 0
    brier: Optional[float] = None             # lower is better; 0 = perfect
    baseline_brier: Optional[float] = None    # climatology (always predict base rate)
    brier_skill_score: Optional[float] = None # 1 - brier/baseline; >0 beats climatology
    log_loss: Optional[float] = None
    accuracy: Optional[float] = None          # fraction of calls that came true
    mean_confidence: Optional[float] = None
    ece: Optional[float] = None               # expected calibration error
    overconfidence: Optional[float] = None    # mean_confidence - accuracy (>0 = overconfident)
    bins: list[CalibrationBin] = field(default_factory=list)

    def as_dict(self) -> dict:
        def r(x):
            return round(x, 4) if isinstance(x, float) else x
        return {
            "n_resolved": self.n_resolved,
            "brier": r(self.brier),
            "baseline_brier": r(self.baseline_brier),
            "brier_skill_score": r(self.brier_skill_score),
            "log_loss": r(self.log_loss),
            "accuracy": r(self.accuracy),
            "mean_confidence": r(self.mean_confidence),
            "ece": r(self.ece),
            "overconfidence": r(self.overconfidence),
            "bins": [b.as_dict() for b in self.bins],
        }


def _resolved_predictions(rows: Iterable[dict]) -> list[tuple[float, float]]:
    """Return (predicted_prob, hit) pairs for resolved predictions.

    A prediction is "resolved" once it has an actual_outcome. `hit` is 1.0 if
    the predicted_outcome matches the actual_outcome, else 0.0. The predicted
    probability is the confidence assigned to the predicted outcome.
    """
    pairs: list[tuple[float, float]] = []
    for r in rows:
        actual = r.get("actual_outcome")
        if actual is None or _norm(actual) == "":
            continue
        try:
            p = _clamp01(float(r.get("confidence")))
        except (TypeError, ValueError):
            continue
        hit = 1.0 if _norm(r.get("predicted_outcome")) == _norm(actual) else 0.0
        pairs.append((p, hit))
    return pairs


def score_predictions(rows: Iterable[dict], *, bins: int = 10) -> PredictionSkill:
    """Compute calibration + skill metrics over resolved `predictions` rows."""
    pairs = _resolved_predictions(rows)
    skill = PredictionSkill(n_resolved=len(pairs))
    if not pairs:
        return skill

    n = len(pairs)
    base_rate = sum(h for _, h in pairs) / n

    skill.brier = sum((p - h) ** 2 for p, h in pairs) / n
    # Climatology baseline: always predict the observed base rate.
    skill.baseline_brier = sum((base_rate - h) ** 2 for _, h in pairs) / n
    if skill.baseline_brier and skill.baseline_brier > 0:
        skill.brier_skill_score = 1.0 - (skill.brier / skill.baseline_brier)
    skill.log_loss = -sum(
        h * math.log(max(p, _EPS)) + (1 - h) * math.log(max(1 - p, _EPS))
        for p, h in pairs
    ) / n
    skill.accuracy = base_rate
    skill.mean_confidence = sum(p for p, _ in pairs) / n
    skill.overconfidence = skill.mean_confidence - skill.accuracy

    # Calibration table + expected calibration error.
    edges = [i / bins for i in range(bins + 1)]
    table: list[CalibrationBin] = []
    ece = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        # last bin is inclusive of 1.0
        members = [(p, h) for p, h in pairs if (lo <= p < hi or (i == bins - 1 and p == hi))]
        if not members:
            continue
        bn = len(members)
        mean_pred = sum(p for p, _ in members) / bn
        obs = sum(h for _, h in members) / bn
        table.append(CalibrationBin(lo, hi, bn, mean_pred, obs))
        ece += (bn / n) * abs(obs - mean_pred)
    skill.bins = table
    skill.ece = ece
    return skill


# --------------------------------------------------------------------------
# paper bets: did the model beat the market?
# --------------------------------------------------------------------------

@dataclass
class BetSkill:
    n_resolved: int = 0
    hit_rate: Optional[float] = None
    hit_rate_ci95: Optional[tuple[float, float]] = None  # Wilson interval
    by_band: dict[str, dict] = field(default_factory=dict)  # band -> {n, hit_rate}
    mean_implied_prob: Optional[float] = None   # market's own implied prob for the side taken
    realized_edge: Optional[float] = None       # hit_rate - mean_implied_prob; >0 = beat market
    total_pnl: float = 0.0
    total_size: float = 0.0
    roi: Optional[float] = None
    avg_holding_days: Optional[float] = None

    def as_dict(self) -> dict:
        def r(x):
            return round(x, 4) if isinstance(x, float) else x
        return {
            "n_resolved": self.n_resolved,
            "hit_rate": r(self.hit_rate),
            "hit_rate_ci95": [r(self.hit_rate_ci95[0]), r(self.hit_rate_ci95[1])]
                              if self.hit_rate_ci95 else None,
            "by_band": {k: {"n": v["n"], "hit_rate": r(v["hit_rate"])}
                        for k, v in self.by_band.items()},
            "mean_implied_prob": r(self.mean_implied_prob),
            "realized_edge": r(self.realized_edge),
            "total_pnl": r(self.total_pnl),
            "total_size": r(self.total_size),
            "roi": r(self.roi),
            "avg_holding_days": r(self.avg_holding_days),
        }


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    phat = wins / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def score_paper_bets(rows: Iterable[dict]) -> BetSkill:
    """Score resolved paper bets.

    Each row should carry: side ('YES'|'NO'), price_at_bet (market YES price
    0-1 at entry), confidence_band ('low'|'medium'|'high'), final_outcome
    ('YES'|'NO'|'INVALIDATED'), final_pnl, size_usd, holding_period_days.
    INVALIDATED markets are excluded from hit/edge but not from P&L.
    """
    skill = BetSkill()
    scored: list[tuple[float, float, str]] = []  # (hit, implied_prob_of_side, band)
    pnl_total = 0.0
    size_total = 0.0
    holding: list[int] = []
    n_resolved = 0

    for r in rows:
        outcome = _norm(r.get("final_outcome"))
        if outcome == "":
            continue
        n_resolved += 1
        try:
            pnl_total += float(r.get("final_pnl") or 0.0)
            size_total += float(r.get("size_usd") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            holding.append(int(r.get("holding_period_days")))
        except (TypeError, ValueError):
            pass
        if outcome == "invalidated":
            continue
        side = _norm(r.get("side"))
        try:
            yes_price = _clamp01(float(r.get("price_at_bet")))
        except (TypeError, ValueError):
            continue
        implied = yes_price if side == "yes" else (1.0 - yes_price)
        hit = 1.0 if side == outcome else 0.0
        scored.append((hit, implied, _norm(r.get("confidence_band")) or "unbanded"))

    skill.n_resolved = n_resolved
    skill.total_pnl = pnl_total
    skill.total_size = size_total
    if size_total > 0:
        skill.roi = pnl_total / size_total
    if holding:
        skill.avg_holding_days = sum(holding) / len(holding)

    if scored:
        n = len(scored)
        wins = sum(h for h, _, _ in scored)
        skill.hit_rate = wins / n
        skill.hit_rate_ci95 = _wilson_ci(int(round(wins)), n)
        skill.mean_implied_prob = sum(ip for _, ip, _ in scored) / n
        skill.realized_edge = skill.hit_rate - skill.mean_implied_prob
        bands: dict[str, list[float]] = {}
        for h, _, band in scored:
            bands.setdefault(band, []).append(h)
        skill.by_band = {
            band: {"n": len(hs), "hit_rate": sum(hs) / len(hs)}
            for band, hs in sorted(bands.items())
        }
    return skill


# --------------------------------------------------------------------------
# thin lake adapters (the only part that touches a DB)
# --------------------------------------------------------------------------

def score_predictions_from_connection(conn, *, bins: int = 10) -> PredictionSkill:
    """Score all resolved predictions from a sqlite connection."""
    cur = conn.execute(
        "SELECT confidence, predicted_outcome, actual_outcome "
        "FROM predictions WHERE actual_outcome IS NOT NULL AND actual_outcome != ''"
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return score_predictions(rows, bins=bins)


def score_paper_bets_from_connection(conn) -> BetSkill:
    """Score all resolved paper bets from a sqlite connection."""
    cur = conn.execute(
        "SELECT b.side, b.price_at_bet, b.confidence_band, b.size_usd, "
        "       r.final_outcome, r.final_pnl, r.holding_period_days "
        "FROM paper_bets b JOIN paper_bet_resolutions r ON b.id = r.bet_id"
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return score_paper_bets(rows)
