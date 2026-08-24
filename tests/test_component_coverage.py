"""Component coverage: a weighted composite must declare its dead terms.

political_figures scores each figure as a weighted sum of six components. Two
of them (speech_volume, speech_topic_drift, together 30% of the weight) were
fed from a hardcoded empty list for the section's entire life. Because the
composite is a sum, a zero component is indistinguishable from a genuinely
calm one: every figure's score was simply compressed toward zero and the
top-10 ranking silently reordered.

Nothing in the output said so. These tests make the composite report which
components actually had input data, so a source that dies takes a visible
number with it.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from worldscope.scoring.figure_anomaly import (
    COMPONENT_WEIGHTS,
    CoverageTracker,
    FigureAnomalyScorer,
)

TODAY = date(2026, 8, 24)


def _signals(**over):
    base = {
        "ptrs": [], "speeches": [], "speech_embed": None,
        "gdelt_tone": [], "filings": [], "doj_hits": [],
        "oig_hits": [], "court_hits": [],
    }
    base.update(over)
    return base


def _figure(fid="f1"):
    return {"id": fid, "name": "Test Figure", "role": "senator"}


# --------------------------------------------------------------------------- #

def test_score_reports_which_components_had_input():
    scorer = FigureAnomalyScorer(today=TODAY)
    out = scorer.score(_figure(), _signals(
        speeches=[{"date": "2026-08-20", "word_count": 900}],
    ))
    cov = out["component_coverage"]
    assert cov["speech_volume"] is True
    assert cov["stock_activity"] is False
    assert cov["gdelt_tone"] is False


def test_empty_signals_report_no_coverage_anywhere():
    out = FigureAnomalyScorer(today=TODAY).score(_figure(), _signals())
    assert set(out["component_coverage"].values()) == {False}


def test_topic_drift_needs_two_vectors_to_count_as_covered():
    one = np.ones((1, 8), dtype=float)
    out = FigureAnomalyScorer(today=TODAY).score(
        _figure(), _signals(speech_embed=one))
    assert out["component_coverage"]["speech_topic_drift"] is False

    two = np.vstack([np.ones(8), np.arange(8, dtype=float)])
    out = FigureAnomalyScorer(today=TODAY).score(
        _figure(), _signals(speech_embed=two))
    assert out["component_coverage"]["speech_topic_drift"] is True


def test_effective_weight_reflects_only_covered_components():
    """The share of total weight that actually had data behind it."""
    out = FigureAnomalyScorer(today=TODAY).score(_figure(), _signals(
        speeches=[{"date": "2026-08-20", "word_count": 900}],
    ))
    assert out["effective_weight"] == pytest.approx(COMPONENT_WEIGHTS["speech_volume"])


def test_effective_weight_is_one_when_everything_has_data():
    two = np.vstack([np.ones(8), np.arange(8, dtype=float)])
    out = FigureAnomalyScorer(today=TODAY).score(_figure(), _signals(
        ptrs=[{"date": "2026-08-20", "ticker": "X", "amount": 1000}],
        speeches=[{"date": "2026-08-20", "word_count": 900}],
        speech_embed=two,
        gdelt_tone=[{"date": "2026-08-20", "tone": -3.0}],
        filings=[{"date": "2026-08-20", "kind": "form4"}],
        doj_hits=[{"date": "2026-08-20", "title": "x"}],
    ))
    assert out["effective_weight"] == pytest.approx(1.0)


def test_composite_is_unchanged_by_the_instrumentation():
    """Coverage is reporting, not rescaling. Changing the score silently would
    be the same class of error this fixes."""
    sig = _signals(speeches=[{"date": "2026-08-20", "word_count": 5000}])
    out = FigureAnomalyScorer(today=TODAY).score(_figure(), sig)
    comps = out["components"]
    expected = sum(COMPONENT_WEIGHTS[k] * comps[k] for k in COMPONENT_WEIGHTS)
    assert out["anomaly_score"] == pytest.approx(max(0.0, min(1.0, expected)))


# --------------------------------------------------------------------------- #
# Fleet-level tracker
# --------------------------------------------------------------------------- #

def test_tracker_counts_figures_with_data_per_component():
    t = CoverageTracker()
    t.observe({"speech_volume": True, "stock_activity": False})
    t.observe({"speech_volume": True, "stock_activity": True})
    t.observe({"speech_volume": False, "stock_activity": False})
    assert t.counts["speech_volume"] == 2
    assert t.counts["stock_activity"] == 1
    assert t.total == 3


def test_tracker_names_structurally_dead_components():
    t = CoverageTracker()
    for _ in range(50):
        t.observe({"speech_volume": False, "gdelt_tone": True})
    assert "speech_volume" in t.dead()
    assert "gdelt_tone" not in t.dead()


def test_tracker_dead_is_empty_when_nothing_observed():
    assert CoverageTracker().dead() == []


def test_tracker_reports_dead_weight_share():
    t = CoverageTracker()
    for _ in range(10):
        t.observe({k: (k != "speech_volume" and k != "speech_topic_drift")
                   for k in COMPONENT_WEIGHTS})
    assert t.dead_weight() == pytest.approx(0.30)


def test_tracker_renders_a_human_line():
    t = CoverageTracker()
    for _ in range(10):
        t.observe({k: k != "speech_volume" for k in COMPONENT_WEIGHTS})
    text = t.render()
    assert "speech_volume" in text
    assert "0/10" in text
