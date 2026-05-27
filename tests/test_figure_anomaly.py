"""Unit tests for the figure-anomaly scorer.

Synthetic data only; no network or lake required. Run::

    python -m unittest tests.test_figure_anomaly -v
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

from worldscope.scoring.figure_anomaly import (
    AnomalyComponents,
    FigureAnomalyScorer,
    COMPONENT_WEIGHTS,
    enforcement_hits_score,
    gdelt_tone_score,
    new_filings_score,
    speech_topic_drift_score,
    speech_volume_score,
    stock_activity_score,
)


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else d


class WeightsTest(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(COMPONENT_WEIGHTS.values()), 1.0)

    def test_components_default_zero(self):
        c = AnomalyComponents()
        self.assertEqual(c.composite(), 0.0)

    def test_components_all_max(self):
        c = AnomalyComponents(
            stock_activity=1.0, speech_volume=1.0, speech_topic_drift=1.0,
            gdelt_tone=1.0, new_filings=1.0, enforcement_hits=1.0,
        )
        self.assertAlmostEqual(c.composite(), 1.0)


class StockActivityTest(unittest.TestCase):
    def test_empty_input_zero(self):
        self.assertEqual(stock_activity_score([]), 0.0)

    def test_recent_burst_scores_high(self):
        today = date(2026, 5, 27)
        # 8 PTRs in last 5 days, 0 over the prior 85 -> volume signal high.
        ptrs = [
            {"date": _iso(today - timedelta(days=i % 5)),
             "excess_return_pct": 30.0}
            for i in range(8)
        ]
        s = stock_activity_score(ptrs, today=today)
        self.assertGreater(s, 0.5)

    def test_huge_excess_return_alone_lifts_score(self):
        today = date(2026, 5, 27)
        ptrs = [{"date": _iso(today - timedelta(days=2)),
                  "excess_return_pct": 80.0}]
        s = stock_activity_score(ptrs, today=today)
        # Score is roughly 0.5 * 80/(80+25) = 0.38 from the excess-return half
        # plus a small volume contribution. Should be at least 0.2.
        self.assertGreater(s, 0.2)
        self.assertLess(s, 1.0)


class SpeechVolumeTest(unittest.TestCase):
    def test_empty_input_zero(self):
        self.assertEqual(speech_volume_score([]), 0.0)

    def test_huge_spike(self):
        today = date(2026, 5, 27)
        rows = []
        # Baseline: 100 words/week for 12 weeks.
        for w in range(2, 14):
            rows.append({"date": _iso(today - timedelta(days=7 * w)),
                          "word_count": 100})
        # This week: a 10x spike.
        rows.append({"date": _iso(today - timedelta(days=1)),
                      "word_count": 1000})
        s = speech_volume_score(rows, today=today)
        self.assertGreater(s, 0.3)


class TopicDriftTest(unittest.TestCase):
    def test_too_little_data_zero(self):
        self.assertEqual(speech_topic_drift_score(None), 0.0)
        self.assertEqual(speech_topic_drift_score(np.zeros((2, 8))), 0.0)

    def test_orthogonal_drift_is_half(self):
        # 10 rows in direction A, 5 rows in direction B (orthogonal).
        d = 16
        a = np.zeros((10, d), dtype=np.float32); a[:, 0] = 1.0
        b = np.zeros((5, d), dtype=np.float32);  b[:, 1] = 1.0
        embed = np.vstack([a, b])
        s = speech_topic_drift_score(embed, recent_n=5)
        # cosine sim of (orthogonal means) = 0, distance = 0.5
        self.assertAlmostEqual(s, 0.5, places=3)

    def test_no_drift_is_zero(self):
        d = 16
        same = np.zeros((20, d), dtype=np.float32)
        same[:, 0] = 1.0
        s = speech_topic_drift_score(same, recent_n=5)
        self.assertAlmostEqual(s, 0.0, places=3)


class GdeltToneTest(unittest.TestCase):
    def test_empty_zero(self):
        self.assertEqual(gdelt_tone_score([]), 0.0)

    def test_huge_24h_swing(self):
        today = date(2026, 5, 27)
        rows = []
        # 30 days of small-magnitude noise tone.
        for i in range(30):
            rows.append({"date": _iso(today - timedelta(days=i)),
                          "tone": 0.5 if i % 2 else -0.5})
        # Today: 5 strongly-negative items
        for _ in range(5):
            rows.append({"date": _iso(today), "tone": -8.0})
        s = gdelt_tone_score(rows, today=today)
        self.assertGreater(s, 0.5)


class NewFilingsTest(unittest.TestCase):
    def test_empty_zero(self):
        self.assertEqual(new_filings_score([]), 0.0)

    def test_three_recent(self):
        today = date(2026, 5, 27)
        rows = [{"date": _iso(today - timedelta(days=i))} for i in range(3)]
        s = new_filings_score(rows, today=today)
        self.assertAlmostEqual(s, 0.5, places=2)


class EnforcementHitsTest(unittest.TestCase):
    def test_bins(self):
        today = date(2026, 5, 27)
        # None
        self.assertEqual(enforcement_hits_score([], [], [], today=today), 0.0)
        # One DOJ
        self.assertEqual(
            enforcement_hits_score([{"date": _iso(today)}], [], [], today=today),
            0.5,
        )
        # One DOJ + one OIG -> 2 -> 1.0
        self.assertEqual(
            enforcement_hits_score(
                [{"date": _iso(today)}], [{"date": _iso(today)}], [],
                today=today,
            ),
            1.0,
        )


class CompositeIntegrationTest(unittest.TestCase):
    def test_full_pipeline_synthetic(self):
        today = date(2026, 5, 27)
        figure = {"id": "test-figure", "name": "Test Person"}
        signals = {
            "ptrs": [
                {"date": _iso(today - timedelta(days=i)),
                 "excess_return_pct": 40.0}
                for i in range(6)
            ],
            "speeches": [],
            "speech_embed": None,
            "gdelt_tone": [{"date": _iso(today), "tone": -10.0}] + [
                {"date": _iso(today - timedelta(days=i)), "tone": 0.0}
                for i in range(1, 20)
            ],
            "filings": [{"date": _iso(today - timedelta(days=2))}],
            "doj_hits": [{"date": _iso(today)}],
            "oig_hits": [],
            "court_hits": [{"date": _iso(today - timedelta(days=1))}],
        }
        scorer = FigureAnomalyScorer(today=today)
        row = scorer.score(figure, signals)
        self.assertIn("anomaly_score", row)
        self.assertGreaterEqual(row["anomaly_score"], 0.0)
        self.assertLessEqual(row["anomaly_score"], 1.0)
        # Stock + enforcement + filings all contributed something
        self.assertGreater(row["anomaly_score"], 0.3)

    def test_empty_signals_score_zero(self):
        figure = {"id": "test-figure", "name": "Quiet Person"}
        row = FigureAnomalyScorer().score(figure, {})
        self.assertEqual(row["anomaly_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
