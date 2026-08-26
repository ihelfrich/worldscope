"""Tests for signal-driven, provider-neutral paper-bet placement."""
import pytest
from datetime import date

from worldscope import signals as sg
from worldscope.sections import paper_bet_placement as p
from worldscope import model_gateway as mg


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


def test_placement_has_no_anthropic_requirement():
    assert p.PaperBetPlacementSection.requires_env == ()
    assert p.PaperBetPlacementSection.requires_packages == ()


def test_decision_call_uses_gateway_and_validates_json(monkeypatch):
    monkeypatch.setattr(
        p.model_gateway,
        "generate",
        lambda *a, **k: mg.ModelResult(
            text='{"decisions":[{"market_id":"m1","platform":"kalshi","side":"YES",'
                 '"credence":0.7,"confidence_band":"medium","rationale":"r",'
                 '"evidence_sections":["macro"],"signals_cited":[]}]}',
            provider="github-copilot-cli", model="auto",
        ),
    )
    decisions, result = p._call_model_for_decisions(
        {"macro": "evidence"},
        [{"market_id": "m1", "platform": "kalshi", "yes_price": 0.4}],
        [],
    )
    assert decisions[0]["market_id"] == "m1"
    assert result.provider == "github-copilot-cli"


def test_decision_call_accepts_copilot_json_fence(monkeypatch):
    monkeypatch.setattr(
        p.model_gateway, "generate",
        lambda *a, **k: mg.ModelResult(
            text='```json\n{"decisions": []}\n```',
            provider="github-copilot-cli", model="copilot-default",
        ),
    )
    decisions, _ = p._call_model_for_decisions({}, [], [])
    assert decisions == []


def test_decision_call_does_not_turn_model_outage_into_no_edge(monkeypatch):
    monkeypatch.setattr(
        p.model_gateway, "generate",
        lambda *a, **k: (_ for _ in ()).throw(mg.ModelUnavailable("offline")),
    )
    with pytest.raises(mg.ModelUnavailable):
        p._call_model_for_decisions({}, [], [])
