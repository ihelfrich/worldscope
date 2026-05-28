"""Tests for source health JSON and heatmap page."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.health_page import STATE_COLORS, render_health_page
from worldscope.source_health import build_source_health, write_source_health
from worldscope.store import SnapshotStore


AS_OF = date(2026, 5, 28)


class SourceHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store_path = self.root / "store.sqlite"
        self.store = SnapshotStore(self.store_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_history_shape_and_consecutive_failure_days(self) -> None:
        self.store.put("alpha", [{"id": "a"}], status="ok", when=date(2026, 5, 26))
        self.store.put("alpha", [], status="failed", error="timeout", when=date(2026, 5, 27))
        self.store.put("alpha", [], status="failed", error="timeout again", when=AS_OF)
        self.store.put("beta", [], status="empty_ok", when=date(2026, 5, 27))
        self.store.put("beta", [{"id": "b"}], status="ok", when=AS_OF)

        doc = build_source_health(
            self.store_path,
            as_of=AS_OF,
            days=3,
            source_tiers={"alpha": "primary_document", "beta": "mainstream_independent"},
        )
        self.assertEqual(set(doc), {"as_of", "sections"})
        self.assertEqual(doc["as_of"], "2026-05-28")
        by_id = {s["section_id"]: s for s in doc["sections"]}
        alpha = by_id["alpha"]
        self.assertEqual(set(alpha), {
            "section_id",
            "source_tier",
            "history",
            "consecutive_fresh_days",
            "consecutive_failure_days",
            "last_fresh_at",
        })
        self.assertEqual([h["state"] for h in alpha["history"]],
                         ["fresh", "stale_after_failure", "stale_after_failure"])
        self.assertEqual(alpha["history"][1]["carried_from"], "2026-05-26")
        self.assertEqual(alpha["history"][2]["error"], "timeout again")
        self.assertEqual(alpha["consecutive_failure_days"], 2)
        self.assertEqual(alpha["consecutive_fresh_days"], 0)
        self.assertTrue(alpha["last_fresh_at"])
        self.assertEqual(by_id["beta"]["consecutive_fresh_days"], 2)

    def test_write_source_health_json(self) -> None:
        doc = build_source_health(self.store_path, as_of=AS_OF, days=2, source_tiers={"empty": "unknown"})
        path = write_source_health(self.root / "dist", doc)
        self.assertEqual(path, self.root / "dist" / "data" / "source_health.json")
        self.assertIn('"as_of": "2026-05-28"', path.read_text(encoding="utf-8"))

    def test_page_renders_all_state_colors_and_sort_controls(self) -> None:
        doc = {
            "as_of": "2026-05-28",
            "sections": [{
                "section_id": "alpha",
                "source_tier": "primary_document",
                "history": [
                    {"date": "2026-05-24", "state": "fresh", "items": 1, "error": "", "carried_from": None},
                    {"date": "2026-05-25", "state": "empty_ok", "items": 0, "error": "", "carried_from": None},
                    {"date": "2026-05-26", "state": "carry_forward", "items": 1, "error": "", "carried_from": "2026-05-24"},
                    {"date": "2026-05-27", "state": "stale_after_failure", "items": 1, "error": "timeout", "carried_from": "2026-05-24"},
                    {"date": "2026-05-28", "state": "no_data", "items": 0, "error": "", "carried_from": None},
                ],
                "consecutive_fresh_days": 0,
                "consecutive_failure_days": 1,
                "last_fresh_at": "2026-05-24T03:00:00Z",
            }],
        }
        page = render_health_page(self.root / "dist", doc)
        html = page.read_text(encoding="utf-8")
        for color in STATE_COLORS.values():
            self.assertIn(color, html)
        self.assertIn('value="alpha"', html)
        self.assertIn('value="failing"', html)
        self.assertIn('value="stable"', html)
        self.assertIn("Thirty-day pull reliability", html)


if __name__ == "__main__":
    unittest.main()
