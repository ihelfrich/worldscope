"""Smoke + structural tests for the Ukraine theater section.

These run with live network. The Ukraine theater section is a multi-
source aggregate; the smoke test asserts only that at least three of
the configured sources produce records and that the ZSU-protection
rule blocks a known-bad synthetic record.

Run:
    python -m unittest tests.test_ukraine_theater -v
"""
from __future__ import annotations

import unittest


class TestZsuFilter(unittest.TestCase):
    """The ZSU-protection rule. No network needed; pure-logic tests."""

    def test_drops_explicit_active_position(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        bad = {
            "title": "ZSU 93rd Mechanised Brigade position near Bakhmut",
            "summary": "Ukrainian unit dug in at coordinates, fortified deployment",
            "latitude": 48.59,
            "longitude": 38.00,
        }
        self.assertTrue(_is_zsu_active_position(bad),
                        "active ZSU position record should be dropped")

    def test_keeps_strike_target(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        ok = {
            "title": "Russian strike on ZSU position near Kupiansk, 3 wounded",
            "summary": "Reported attack on Ukrainian brigade position",
            "latitude": 49.71,
            "longitude": 37.61,
        }
        self.assertFalse(_is_zsu_active_position(ok),
                         "strike-on-position record should be kept")

    def test_keeps_movement(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        ok = {
            "title": "Ukrainian brigade moved from Donetsk axis",
            "summary": "ZSU unit redeployed after withdrawal",
            "latitude": 48.0,
            "longitude": 37.8,
        }
        self.assertFalse(_is_zsu_active_position(ok),
                         "movement / withdrawal record should be kept")

    def test_keeps_record_outside_ukraine(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        outside = {
            "title": "ZSU brigade position",
            "summary": "Reported deployment",
            "latitude": 50.0,
            "longitude": 50.0,   # well outside Ukrainian claimed bbox
        }
        self.assertFalse(_is_zsu_active_position(outside),
                         "outside Ukrainian-claimed territory should be kept")

    def test_keeps_record_without_coords(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        no_geo = {
            "title": "ZSU brigade position",
            "summary": "Generic mention without coordinates",
        }
        self.assertFalse(_is_zsu_active_position(no_geo),
                         "records without lat/lon should not be filtered")

    def test_keeps_unrelated_record(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        unrelated = {
            "title": "Air alert in Kyiv oblast",
            "summary": "Civilian shelter advisory",
            "latitude": 50.4,
            "longitude": 30.5,
        }
        self.assertFalse(_is_zsu_active_position(unrelated),
                         "records without ZSU mention should be kept")


class TestUkrainePull(unittest.TestCase):
    """Live smoke test. Hits the network. Tolerant of some sources being
    down; only fails if zero records come back across all sources."""

    @classmethod
    def setUpClass(cls):
        from worldscope.sections.ukraine_theater import UkraineTheaterSection
        cls.section = UkraineTheaterSection()
        cls.items = cls.section.pull()

    def test_returns_records(self):
        self.assertGreater(len(self.items), 10,
                           f"expected >10 records, got {len(self.items)}")

    def test_multiple_sources(self):
        sources = {
            it.get("source_label")
            for it in self.items
            if not it.get("_error") and it.get("source_label")
        }
        self.assertGreaterEqual(
            len(sources), 3,
            f"expected >=3 distinct working sources, got {len(sources)}: {sources}"
        )

    def test_resolution_and_latency_present(self):
        for it in self.items:
            if it.get("_error") or it.get("_filter_summary"):
                continue
            self.assertIn("geo_resolution_m", it,
                          f"missing geo_resolution_m on record: {it}")
            self.assertIn("latency_hours", it,
                          f"missing latency_hours on record: {it}")

    def test_no_active_zsu_positions_present(self):
        from worldscope.sections.ukraine_theater import _is_zsu_active_position
        leaked = [it for it in self.items
                  if not it.get("_error")
                  and not it.get("_filter_summary")
                  and _is_zsu_active_position(it)]
        self.assertEqual(
            leaked, [],
            f"ZSU-protection rule must drop all active positions; leaked: {leaked}"
        )


if __name__ == "__main__":
    unittest.main()
