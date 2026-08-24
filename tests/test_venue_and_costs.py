"""Venue classification and transaction costs for the paper-bet scorecard.

Two defects made `realized_edge` — the one number in the whole system that
claims the model beat the crowd — not mean what it says.

1. Venues were pooled. paper_bets indexes Polymarket, Kalshi, PredictIt and
   Manifold together. Manifold is mana: play money, no capital at risk, and
   systematically worse-calibrated than a real-money book. PredictIt is wound
   down. On 2026-08-24, 82 of 140 indexed markets were one of those two.
   Beating them is not evidence of edge, and pooling inflates the headline.

2. Costs were absent. paper_bet_placement fires at |credence - price| >= 0.08
   with Kelly-lite sizing and no spread, fee, or depth model. Polymarket
   round-trips on thin markets routinely eat 2-4 cents, so a raw 8% edge can
   be a 4% edge net — and the bias is largest exactly where the model thinks
   the edge is biggest.

Neither is a modelling opinion. Both are the difference between a number that
can be reported and one that cannot.
"""
from __future__ import annotations

import pytest

from worldscope.scoring import venues


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("platform,expected", [
    ("polymarket", venues.REAL_MONEY),
    ("kalshi", venues.REAL_MONEY),
    ("Polymarket", venues.REAL_MONEY),
    ("manifold", venues.PLAY_MONEY),
    ("predictit", venues.DEPRECATED),
    ("something-new", venues.UNKNOWN),
    ("", venues.UNKNOWN),
    (None, venues.UNKNOWN),
])
def test_platform_classification(platform, expected):
    assert venues.classify(platform) == expected


def test_only_real_money_venues_are_scoreable():
    assert venues.is_scoreable("polymarket") is True
    assert venues.is_scoreable("kalshi") is True
    assert venues.is_scoreable("manifold") is False
    assert venues.is_scoreable("predictit") is False
    assert venues.is_scoreable("unknown-venue") is False, \
        "an unrecognised venue must not silently count as real money"


def test_partition_splits_rows_by_class():
    rows = [
        {"market_platform": "polymarket", "id": "a"},
        {"market_platform": "manifold", "id": "b"},
        {"market_platform": "kalshi", "id": "c"},
        {"market_platform": "predictit", "id": "d"},
    ]
    parts = venues.partition(rows)
    assert [r["id"] for r in parts[venues.REAL_MONEY]] == ["a", "c"]
    assert [r["id"] for r in parts[venues.PLAY_MONEY]] == ["b"]
    assert [r["id"] for r in parts[venues.DEPRECATED]] == ["d"]


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #

def test_round_trip_cost_is_positive_for_a_real_venue():
    c = venues.round_trip_cost("polymarket", price=0.50, size_usd=100.0)
    assert c > 0


def test_thin_markets_cost_more_than_liquid_ones():
    thin = venues.round_trip_cost("polymarket", price=0.50, size_usd=100.0,
                                  volume_usd=500.0)
    deep = venues.round_trip_cost("polymarket", price=0.50, size_usd=100.0,
                                  volume_usd=5_000_000.0)
    assert thin > deep


def test_extreme_prices_cost_more_than_mid_prices():
    """Spreads widen near 0 and 1, where the model's edges are largest."""
    mid = venues.round_trip_cost("polymarket", price=0.50, size_usd=100.0)
    tail = venues.round_trip_cost("polymarket", price=0.04, size_usd=100.0)
    assert tail > mid


def test_net_edge_subtracts_cost_from_raw_edge():
    raw = 0.08
    net = venues.net_edge(raw, "polymarket", price=0.50, size_usd=100.0)
    assert net < raw
    assert net == pytest.approx(
        raw - venues.round_trip_cost("polymarket", price=0.50, size_usd=100.0))


def test_net_edge_can_go_negative():
    """An 8% raw edge is not automatically a tradeable one."""
    net = venues.net_edge(0.02, "polymarket", price=0.02, size_usd=100.0,
                          volume_usd=200.0)
    assert net < 0


def test_play_money_has_no_meaningful_cost_and_is_flagged():
    with pytest.raises(venues.NotTradeable):
        venues.round_trip_cost("manifold", price=0.5, size_usd=100.0)


def test_survives_costs_uses_the_net_number():
    assert venues.survives_costs(0.20, "polymarket", price=0.50, size_usd=100.0)
    assert not venues.survives_costs(0.005, "polymarket", price=0.50, size_usd=100.0)


# --------------------------------------------------------------------------- #
# Scorecard integration
# --------------------------------------------------------------------------- #

def _bet(platform, side, outcome, price, band="medium"):
    return {
        "market_platform": platform, "side": side, "final_outcome": outcome,
        "price_at_bet": price, "confidence_band": band, "final_pnl": 10.0,
        "size_usd": 100.0, "holding_period_days": 7,
    }


def test_scorecard_reports_venues_separately():
    from worldscope.scoring.track_record import score_paper_bets_by_venue
    rows = [
        _bet("polymarket", "YES", "YES", 0.40),
        _bet("kalshi", "YES", "NO", 0.60),
        _bet("manifold", "YES", "YES", 0.10),
        _bet("manifold", "YES", "YES", 0.10),
    ]
    out = score_paper_bets_by_venue(rows)
    assert out[venues.REAL_MONEY].n_resolved == 2
    assert out[venues.PLAY_MONEY].n_resolved == 2


def test_headline_edge_comes_only_from_real_money():
    from worldscope.scoring.track_record import headline_edge
    rows = [
        # One real-money loss, two play-money wins at a generous price.
        _bet("polymarket", "YES", "NO", 0.50),
        _bet("manifold", "YES", "YES", 0.05),
        _bet("manifold", "YES", "YES", 0.05),
    ]
    edge = headline_edge(rows)
    assert edge["venue_class"] == venues.REAL_MONEY
    assert edge["n_resolved"] == 1
    assert edge["realized_edge"] is not None
    assert edge["realized_edge"] < 0, \
        "play-money wins must not rescue a real-money loss"


def test_headline_edge_declines_to_report_below_the_minimum_sample():
    from worldscope.scoring.track_record import headline_edge
    out = headline_edge([_bet("polymarket", "YES", "YES", 0.4)])
    assert out["reportable"] is False
    assert "insufficient" in out["note"].lower()


def test_headline_edge_is_not_reportable_with_zero_bets():
    from worldscope.scoring.track_record import headline_edge
    out = headline_edge([])
    assert out["reportable"] is False
    assert out["n_resolved"] == 0


# --------------------------------------------------------------------------- #
# Placement gating
# --------------------------------------------------------------------------- #

def test_placement_declares_a_net_edge_floor():
    from worldscope.sections import paper_bet_placement as pbp
    assert pbp.MIN_NET_EDGE > 0
    assert pbp.EDGE_THRESHOLD > pbp.MIN_NET_EDGE, \
        "the raw threshold must be the looser of the two; the net floor binds"


def test_a_raw_edge_at_the_threshold_can_fail_the_net_floor():
    """The exact bias the cost model exists to remove: an 8% raw edge on a
    thin tail market is not a tradeable 8%."""
    from worldscope.sections.paper_bet_placement import EDGE_THRESHOLD, MIN_NET_EDGE
    net = venues.net_edge(EDGE_THRESHOLD, "polymarket", price=0.03,
                          size_usd=100.0, volume_usd=1_000.0)
    assert net <= MIN_NET_EDGE


def test_a_wide_edge_at_mid_price_still_passes():
    from worldscope.sections.paper_bet_placement import MIN_NET_EDGE
    net = venues.net_edge(0.25, "polymarket", price=0.50, size_usd=100.0,
                          volume_usd=1_000_000.0)
    assert net > MIN_NET_EDGE


# --------------------------------------------------------------------------- #
# Self-graded vs externally-resolved forecasts
# --------------------------------------------------------------------------- #

def _pred(method, conf, outcome, predicted="YES"):
    return {"method": method, "confidence": conf,
            "predicted_outcome": predicted, "actual_outcome": outcome}


def test_signal_fusion_predictions_are_marked_self_graded():
    from worldscope.scoring.track_record import is_self_graded
    assert is_self_graded({"method": "signal-fusion-v1"}) is True
    assert is_self_graded({"method": "research-radar-v1"}) is True
    assert is_self_graded({"method": "analyst-call"}) is False


def test_split_scores_the_two_populations_separately():
    from worldscope.scoring.track_record import score_predictions_split
    rows = [
        _pred("signal-fusion-v1", 0.95, "YES"),
        _pred("signal-fusion-v1", 0.95, "YES"),
        _pred("analyst-call", 0.60, "NO"),
    ]
    out = score_predictions_split(rows)
    assert out["self_graded"]["skill"].n_resolved == 2
    assert out["externally_resolved"]["skill"].n_resolved == 1


def test_the_self_graded_caveat_refuses_the_edge_reading():
    from worldscope.scoring.track_record import score_predictions_split
    out = score_predictions_split([_pred("signal-fusion-v1", 0.9, "YES")])
    caveat = out["self_graded"]["caveat"].lower()
    assert "own attention" in caveat
    assert "must not be cited" in caveat
