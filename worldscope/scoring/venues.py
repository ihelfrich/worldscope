"""worldscope.scoring.venues — which prices are evidence, and what they cost.

`realized_edge` in track_record is the one number in this system that claims
the model beat the crowd rather than merely picked favourites. Two things made
it not mean that.

**Venues were pooled.** The paper_bets section indexes Polymarket, Kalshi,
PredictIt and Manifold into a single edge calculation. Manifold is denominated
in mana — play money, no capital at risk. Its prices are systematically worse
calibrated than a real-money book precisely because being wrong is free, and
the section's own code concedes it (`if volume < 100: # Manifold markets are
in mana`). PredictIt is wound down. On 2026-08-24, of 140 indexed markets, 50
were PredictIt and 32 Manifold: **82 of 140 prices carried no capital**.
Beating those is not evidence of forecasting skill.

**Costs were absent.** paper_bet_placement fires at
|credence - price| >= 0.08 with `min(edge * 5, 1.0)` sizing and no spread,
fee, or depth term. Prediction-market round trips on thin books routinely cost
2-4 cents, and spreads widen toward 0 and 1 — exactly where a model's apparent
edges are largest. The omission therefore biases hardest in the direction the
strategy most wants to trade.

Neither of these is a modelling preference. They are the difference between a
number that can be reported and one that cannot.

The cost model here is deliberately a stated approximation rather than a
fitted one: there is no order-book history in the lake to fit against. It is
calibrated to published maker/taker economics and widens with thinness and
price extremity. Its purpose is to stop a raw edge being read as a net one,
not to price a trade to the basis point. Every constant is named and
overridable so it can be replaced when book data exists.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

REAL_MONEY = "real_money"
PLAY_MONEY = "play_money"
DEPRECATED = "deprecated"
UNKNOWN = "unknown"


class NotTradeable(ValueError):
    """A cost was requested for a venue where no capital is at risk."""


# Platform -> class. Unrecognised platforms are UNKNOWN, never REAL_MONEY:
# a new venue must be classified deliberately, not inherit scoreability by
# default. That default is the whole point.
PLATFORM_CLASS: dict[str, str] = {
    "polymarket": REAL_MONEY,
    "kalshi": REAL_MONEY,
    "manifold": PLAY_MONEY,
    "predictit": DEPRECATED,
}

# Published economics, as of 2026-08.
#   taker_fee   proportional fee on notional
#   base_spread half-spread at mid on a liquid book, in probability units
VENUE_ECONOMICS: dict[str, dict[str, float]] = {
    "polymarket": {"taker_fee": 0.0000, "base_spread": 0.010},
    "kalshi":     {"taker_fee": 0.0070, "base_spread": 0.010},
}

# Depth term. Cost scales with the fraction of recent volume the order
# represents; DEPTH_REF is the volume above which impact is negligible.
DEPTH_REF_USD = 250_000.0
MAX_IMPACT = 0.05

# Minimum resolved real-money bets before an edge is reportable. Below this a
# Wilson interval spans most of [0, 1] and the point estimate is noise.
MIN_REPORTABLE_N = 20


def classify(platform: Any) -> str:
    return PLATFORM_CLASS.get(str(platform or "").strip().lower(), UNKNOWN)


def is_scoreable(platform: Any) -> bool:
    """Only real-money venues count toward the headline edge."""
    return classify(platform) == REAL_MONEY


def partition(rows: Iterable[dict], *, key: str = "market_platform") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {
        REAL_MONEY: [], PLAY_MONEY: [], DEPRECATED: [], UNKNOWN: [],
    }
    for r in rows:
        out[classify(r.get(key))].append(r)
    return out


def round_trip_cost(platform: Any, *, price: float, size_usd: float,
                    volume_usd: Optional[float] = None) -> float:
    """Estimated round-trip cost in probability units (same units as edge).

    Three additive terms:

      spread   twice the half-spread, widened toward the tails. A book at
               p = 0.04 is far thinner in absolute terms than one at 0.50, and
               the widening factor 1 / (4 p (1-p)) is 1.0 at mid and rises
               symmetrically toward either end.
      fee      round-trip proportional fee, converted to probability units.
      impact   grows with the order's share of recent volume, capped.
    """
    cls = classify(platform)
    if cls != REAL_MONEY:
        raise NotTradeable(
            f"round_trip_cost: {platform!r} is {cls}; no capital is at risk "
            f"there, so a cost figure would imply a trade that cannot exist"
        )

    econ = VENUE_ECONOMICS[str(platform).strip().lower()]
    p = min(max(float(price), 0.005), 0.995)

    tail_widen = 1.0 / (4.0 * p * (1.0 - p))          # 1.0 at p=0.5
    spread = 2.0 * econ["base_spread"] * tail_widen

    fee = 2.0 * econ["taker_fee"]

    if volume_usd is None:
        impact = 0.0
    else:
        share = float(size_usd) / max(float(volume_usd), 1.0)
        impact = min(MAX_IMPACT, share * MAX_IMPACT * (DEPTH_REF_USD / max(float(volume_usd), 1.0)))
        impact = min(MAX_IMPACT, impact)

    return spread + fee + impact


def net_edge(raw_edge: float, platform: Any, *, price: float, size_usd: float,
             volume_usd: Optional[float] = None) -> float:
    """Raw edge less estimated round-trip cost."""
    return float(raw_edge) - round_trip_cost(
        platform, price=price, size_usd=size_usd, volume_usd=volume_usd)


def survives_costs(raw_edge: float, platform: Any, *, price: float,
                   size_usd: float, volume_usd: Optional[float] = None) -> bool:
    return net_edge(raw_edge, platform, price=price, size_usd=size_usd,
                    volume_usd=volume_usd) > 0.0
