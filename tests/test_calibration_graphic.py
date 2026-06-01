"""Render-path tests for the forecast-calibration graphic.

Builds a temp lake DB with a predictions table and asserts DailyGraphics
produces a non-trivial PNG on both the data path (>= MIN_RESOLVED resolved
calls) and the placeholder path (too few). No network, no real lake.

Run:
    python -m unittest tests.test_calibration_graphic -v
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path


def _make_lake(db_path: Path, n_resolved: int):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE predictions (id TEXT PRIMARY KEY, confidence REAL, "
        "predicted_outcome TEXT, actual_outcome TEXT)"
    )
    # Alternate confident-correct and hedged-mixed so bins are non-degenerate.
    for i in range(n_resolved):
        conf = 0.9 if i % 2 == 0 else 0.6
        actual = "YES" if (i % 3 != 0) else "NO"
        conn.execute(
            "INSERT INTO predictions VALUES (?,?,?,?)",
            (f"p{i}", conf, "YES", actual),
        )
    conn.commit()
    conn.close()


class TestCalibrationGraphic(unittest.TestCase):

    def _render(self, n_resolved: int) -> Path:
        from worldscope.graphics import DailyGraphics
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "worldscope.sqlite"
        _make_lake(db, n_resolved)
        g = DailyGraphics(lake_db_path=db, output_root=tmp / "figures")
        return g.render_calibration("2026-05-31")

    def test_data_path_produces_png(self):
        path = self._render(20)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "calibration.png")
        self.assertGreater(path.stat().st_size, 3000, "PNG looks empty")

    def test_placeholder_path_when_too_few(self):
        path = self._render(2)  # below MIN_RESOLVED
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_no_predictions_table_is_safe(self):
        # Empty DB (no predictions table) must still render a placeholder.
        from worldscope.graphics import DailyGraphics
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "worldscope.sqlite"
        sqlite3.connect(db).close()
        g = DailyGraphics(lake_db_path=db, output_root=tmp / "figures")
        path = g.render_calibration("2026-05-31")
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
