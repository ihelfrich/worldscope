"""Tests for worldscope.scoring.track_record — forecast self-evaluation.

Pure-logic over row-dicts (no network, no real lake) plus one in-memory
SQLite check of the connection adapters.

Run:
    python -m unittest tests.test_track_record -v
"""
from __future__ import annotations

import sqlite3
import unittest

from worldscope.scoring import track_record as tr


class TestPredictionSkill(unittest.TestCase):

    def test_empty_is_safe(self):
        s = tr.score_predictions([])
        self.assertEqual(s.n_resolved, 0)
        self.assertIsNone(s.brier)
        self.assertEqual(s.as_dict()["bins"], [])

    def test_unresolved_rows_ignored(self):
        rows = [
            {"confidence": 0.9, "predicted_outcome": "YES", "actual_outcome": None},
            {"confidence": 0.9, "predicted_outcome": "YES", "actual_outcome": ""},
        ]
        self.assertEqual(tr.score_predictions(rows).n_resolved, 0)

    def test_perfect_calls_zero_brier(self):
        rows = [
            {"confidence": 1.0, "predicted_outcome": "YES", "actual_outcome": "YES"},
            {"confidence": 1.0, "predicted_outcome": "NO", "actual_outcome": "NO"},
        ]
        s = tr.score_predictions(rows)
        self.assertEqual(s.n_resolved, 2)
        self.assertAlmostEqual(s.brier, 0.0)
        self.assertAlmostEqual(s.accuracy, 1.0)
        self.assertAlmostEqual(s.log_loss, 0.0, places=4)

    def test_brier_value_and_overconfidence(self):
        # Two calls at 0.8 confidence; one right, one wrong.
        rows = [
            {"confidence": 0.8, "predicted_outcome": "YES", "actual_outcome": "YES"},
            {"confidence": 0.8, "predicted_outcome": "YES", "actual_outcome": "NO"},
        ]
        s = tr.score_predictions(rows)
        # (0.8-1)^2 = .04 ; (0.8-0)^2 = .64 ; mean = .34
        self.assertAlmostEqual(s.brier, 0.34, places=6)
        self.assertAlmostEqual(s.accuracy, 0.5)
        self.assertAlmostEqual(s.mean_confidence, 0.8)
        self.assertAlmostEqual(s.overconfidence, 0.3)

    def test_brier_skill_score_beats_climatology(self):
        # Skill = discrimination: certain where right, hedged where unsure.
        # Climatology (predict the 0.75 base rate every time) is beaten only
        # if confidence tracks correctness.
        rows = [
            {"confidence": 1.0, "predicted_outcome": "YES", "actual_outcome": "YES"},
            {"confidence": 1.0, "predicted_outcome": "NO", "actual_outcome": "NO"},
            {"confidence": 0.5, "predicted_outcome": "YES", "actual_outcome": "YES"},
            {"confidence": 0.5, "predicted_outcome": "YES", "actual_outcome": "NO"},
        ]
        s = tr.score_predictions(rows)
        self.assertIsNotNone(s.brier_skill_score)
        self.assertGreater(s.brier_skill_score, 0.0)

    def test_skill_undefined_when_outcomes_degenerate(self):
        # All calls correct -> climatology is trivially perfect -> BSS undefined.
        rows = [
            {"confidence": 0.9, "predicted_outcome": "YES", "actual_outcome": "YES"},
            {"confidence": 0.9, "predicted_outcome": "NO", "actual_outcome": "NO"},
        ]
        self.assertIsNone(tr.score_predictions(rows).brier_skill_score)

    def test_calibration_bins_and_ece(self):
        rows = [
            {"confidence": 0.95, "predicted_outcome": "YES", "actual_outcome": "YES"},
            {"confidence": 0.95, "predicted_outcome": "YES", "actual_outcome": "NO"},
            {"confidence": 0.55, "predicted_outcome": "YES", "actual_outcome": "YES"},
        ]
        s = tr.score_predictions(rows, bins=10)
        self.assertTrue(len(s.bins) >= 1)
        self.assertTrue(0.0 <= s.ece <= 1.0)
        # bins should cover every resolved row exactly once
        self.assertEqual(sum(b.n for b in s.bins), s.n_resolved)


class TestBetSkill(unittest.TestCase):

    def test_empty_is_safe(self):
        s = tr.score_paper_bets([])
        self.assertEqual(s.n_resolved, 0)
        self.assertIsNone(s.hit_rate)
        self.assertEqual(s.total_pnl, 0.0)

    def test_hit_rate_edge_and_roi(self):
        rows = [
            # bet YES at implied 0.40, won -> beat the market
            {"side": "YES", "price_at_bet": 0.40, "confidence_band": "high",
             "size_usd": 100, "final_outcome": "YES", "final_pnl": 150,
             "holding_period_days": 10},
            # bet YES at implied 0.60, lost
            {"side": "YES", "price_at_bet": 0.60, "confidence_band": "low",
             "size_usd": 100, "final_outcome": "NO", "final_pnl": -100,
             "holding_period_days": 20},
        ]
        s = tr.score_paper_bets(rows)
        self.assertEqual(s.n_resolved, 2)
        self.assertAlmostEqual(s.hit_rate, 0.5)
        self.assertAlmostEqual(s.mean_implied_prob, 0.5)  # (0.40 + 0.60)/2
        self.assertAlmostEqual(s.realized_edge, 0.0)
        self.assertAlmostEqual(s.total_pnl, 50.0)
        self.assertAlmostEqual(s.roi, 50.0 / 200.0)
        self.assertAlmostEqual(s.avg_holding_days, 15.0)
        self.assertEqual(s.by_band["high"]["n"], 1)
        self.assertAlmostEqual(s.by_band["high"]["hit_rate"], 1.0)

    def test_no_side_implied_prob(self):
        # NO bet when YES price is 0.30 -> implied prob of the NO side is 0.70
        rows = [{"side": "NO", "price_at_bet": 0.30, "confidence_band": "medium",
                 "size_usd": 50, "final_outcome": "NO", "final_pnl": 20,
                 "holding_period_days": 5}]
        s = tr.score_paper_bets(rows)
        self.assertAlmostEqual(s.mean_implied_prob, 0.70)
        self.assertAlmostEqual(s.hit_rate, 1.0)

    def test_invalidated_excluded_from_hit_but_counted_in_pnl(self):
        rows = [
            {"side": "YES", "price_at_bet": 0.5, "confidence_band": "high",
             "size_usd": 100, "final_outcome": "INVALIDATED", "final_pnl": 0,
             "holding_period_days": 3},
            {"side": "YES", "price_at_bet": 0.5, "confidence_band": "high",
             "size_usd": 100, "final_outcome": "YES", "final_pnl": 100,
             "holding_period_days": 4},
        ]
        s = tr.score_paper_bets(rows)
        self.assertEqual(s.n_resolved, 2)        # both counted as resolved
        self.assertEqual(s.by_band["high"]["n"], 1)  # only the valid one scored
        self.assertAlmostEqual(s.hit_rate, 1.0)


class TestConnectionAdapters(unittest.TestCase):

    def _make_db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE predictions (
                id TEXT PRIMARY KEY, confidence REAL, predicted_outcome TEXT,
                actual_outcome TEXT);
            CREATE TABLE paper_bets (
                id TEXT PRIMARY KEY, side TEXT, price_at_bet REAL,
                confidence_band TEXT, size_usd REAL);
            CREATE TABLE paper_bet_resolutions (
                bet_id TEXT PRIMARY KEY, final_outcome TEXT, final_pnl REAL,
                holding_period_days INTEGER);
        """)
        conn.execute("INSERT INTO predictions VALUES ('p1',0.8,'YES','YES')")
        conn.execute("INSERT INTO predictions VALUES ('p2',0.8,'YES',NULL)")  # unresolved
        conn.execute("INSERT INTO paper_bets VALUES ('b1','YES',0.4,'high',100)")
        conn.execute("INSERT INTO paper_bet_resolutions VALUES ('b1','YES',150,10)")
        conn.commit()
        return conn

    def test_predictions_adapter_filters_unresolved(self):
        conn = self._make_db()
        s = tr.score_predictions_from_connection(conn)
        self.assertEqual(s.n_resolved, 1)
        self.assertAlmostEqual(s.accuracy, 1.0)

    def test_bets_adapter_joins_resolutions(self):
        conn = self._make_db()
        s = tr.score_paper_bets_from_connection(conn)
        self.assertEqual(s.n_resolved, 1)
        self.assertAlmostEqual(s.hit_rate, 1.0)
        self.assertAlmostEqual(s.realized_edge, 1.0 - 0.4)


if __name__ == "__main__":
    unittest.main()
