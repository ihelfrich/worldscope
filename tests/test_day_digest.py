"""Tests for worldscope.analysis.day_digest — the day's settled facts.

In-memory SQLite; no network. Verifies date filtering, P&L/correctness
aggregation, |z|-ordering of anomalies, and graceful behaviour on an empty
lake.

Run:
    python -m unittest tests.test_day_digest -v
"""
from __future__ import annotations

import sqlite3
import unittest

from worldscope.analysis import day_digest as dd


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE paper_bets (id TEXT PRIMARY KEY, market_question TEXT,
            side TEXT, market_platform TEXT);
        CREATE TABLE paper_bet_resolutions (bet_id TEXT PRIMARY KEY,
            resolved_at TEXT, final_outcome TEXT, final_pnl REAL,
            holding_period_days INTEGER);
        CREATE TABLE predictions (id TEXT PRIMARY KEY, resolution_criteria TEXT,
            predicted_outcome TEXT, actual_outcome TEXT, confidence REAL,
            resolved_at TEXT);
        CREATE TABLE anomalies (id TEXT PRIMARY KEY, category TEXT, z_score REAL,
            description TEXT, section_id TEXT, detected_at TEXT);
    """)
    return conn


class TestDayDigest(unittest.TestCase):

    def test_empty_lake_is_safe(self):
        d = dd.build_day_digest(_db(), "2026-05-31")
        self.assertEqual(d["headline"]["bets_resolved"], 0)
        self.assertEqual(d["bets_resolved"]["items"], [])
        self.assertEqual(d["predictions_resolved"]["n"], 0)

    def test_missing_tables_is_safe(self):
        # Connection with no tables at all.
        d = dd.build_day_digest(sqlite3.connect(":memory:"), "2026-05-31")
        self.assertEqual(d["headline"]["anomalies"], 0)

    def test_bets_filtered_by_date_and_aggregated(self):
        conn = _db()
        conn.execute("INSERT INTO paper_bets VALUES('b1','Q1','YES','polymarket')")
        conn.execute("INSERT INTO paper_bets VALUES('b2','Q2','NO','kalshi')")
        conn.execute("INSERT INTO paper_bets VALUES('b3','Q3','YES','manifold')")
        conn.execute("INSERT INTO paper_bet_resolutions VALUES('b1','2026-05-31T10:00:00Z','YES',120,5)")
        conn.execute("INSERT INTO paper_bet_resolutions VALUES('b2','2026-05-31T11:00:00Z','YES',-80,9)")
        conn.execute("INSERT INTO paper_bet_resolutions VALUES('b3','2026-05-30T11:00:00Z','YES',50,2)")  # other day
        conn.commit()
        d = dd.build_day_digest(conn, "2026-05-31")
        b = d["bets_resolved"]
        self.assertEqual(b["n"], 2)
        self.assertEqual(b["wins"], 1)
        self.assertEqual(b["losses"], 1)
        self.assertAlmostEqual(b["net_pnl"], 40.0)
        # ordered by |pnl| desc -> 120 first
        self.assertEqual(b["items"][0]["pnl"], 120.0)

    def test_predictions_correctness(self):
        conn = _db()
        conn.execute("INSERT INTO predictions VALUES('p1','crit','YES','YES',0.8,'2026-05-31T09:00:00Z')")
        conn.execute("INSERT INTO predictions VALUES('p2','crit','YES','NO',0.7,'2026-05-31T09:00:00Z')")
        conn.execute("INSERT INTO predictions VALUES('p3','crit','YES',NULL,0.9,'2026-05-31T09:00:00Z')")  # unresolved
        conn.commit()
        d = dd.build_day_digest(conn, "2026-05-31")
        p = d["predictions_resolved"]
        self.assertEqual(p["n"], 2)            # unresolved excluded
        self.assertEqual(p["n_correct"], 1)

    def test_anomalies_ordered_by_abs_z(self):
        conn = _db()
        conn.execute("INSERT INTO anomalies VALUES('a1','fec',2.1,'d','fec','2026-05-31T08:00:00Z')")
        conn.execute("INSERT INTO anomalies VALUES('a2','court',-3.4,'d','courtlistener','2026-05-31T08:00:00Z')")
        conn.execute("INSERT INTO anomalies VALUES('a3','x',1.0,'d','x','2026-05-30T08:00:00Z')")  # other day
        conn.commit()
        d = dd.build_day_digest(conn, "2026-05-31")
        a = d["anomalies"]
        self.assertEqual(a["n"], 2)
        self.assertEqual(abs(a["items"][0]["z_score"]), 3.4)  # |z| desc


if __name__ == "__main__":
    unittest.main()
