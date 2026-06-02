"""Tests for the signal-driven paper-bet placement prompt construction.

The Claude call itself needs an API key and is not exercised here; we test the
pure prompt-building + signal-roster helpers that decide what the model sees."""
from datetime import date

from worldscope import signals as sg
from worldscope.sections import paper_bet_placement as p


def _signals():
    recs = [
        {"id": "1", "section_id": "forecasts", "original_text": "Iran talks stall",
         "record_date": "2026-05-31"},
        {"id": "2", "section_id": "foreign_news", "original_text": "Iran deadline passes",
         "record_date": "2026-05-31"},
        {"id": "3", "section_id": "conflict", "original_text": "Iran sanctions debated",
         "record_date": "2026-05-31"},
    ]
    return sg.fuse(recs, today=date(2026, 5, 31), min_sections=2)


def test_signal_roster_empty():
    assert p._format_signal_roster([]) == "(no cross-source signals surfaced today)"


def test_signal_roster_lists_key_sections_and_persistence():
    line = p._format_signal_roster(_signals())
    assert "key='iran'" in line
    assert "3 sections" in line
    assert "persist" in line


def test_build_prompts_inject_signals_and_schema():
    markets = [{"platform": "polymarket", "question": "Will X by June?",
                "market_id": "m1", "yes_price": 0.40, "volume": 12000,
                "end_date": "2026-06-30"}]
    system, user = p._build_decision_prompts(
        {"markets": "S&P up 1%"}, markets, _signals())
    # the model is told to use the cross-source signals as its primary lens
    assert "CROSS-SOURCE SIGNALS" in system
    # high confidence is gated on multi-section convergence
    assert "3+ independent sections" in system
    # the decision schema now asks which signals were cited
    assert '"signals_cited"' in system
    # the user prompt carries the ranked signal roster + the market roster
    assert "key='iran'" in user
    assert "Will X by June?" in user


def test_build_prompts_handle_no_signals_gracefully():
    markets = [{"platform": "kalshi", "question": "Q?", "market_id": "m9",
                "yes_price": 0.5, "volume": 100, "end_date": None}]
    system, user = p._build_decision_prompts({}, markets, [])
    assert "no cross-source signals" in user
    assert "Q?" in user


def test_kelly_lite_sizing_scales_with_edge_and_band():
    # higher edge and higher band -> larger size; capped sensibly
    low = p._kelly_lite_size(0.10, "low")
    med = p._kelly_lite_size(0.10, "medium")
    high = p._kelly_lite_size(0.10, "high")
    assert low < med < high
    # edge multiplier caps at 1.0 (edge*5 >= 1 when edge>=0.2)
    assert p._kelly_lite_size(0.5, "medium") == p._kelly_lite_size(0.2, "medium")
