"""Smoke tests for the political_figures section.

These exercise the registry loader, the section's pull() (which runs against
the live lake and live network where present), and the contract artifacts.
Network-heavy parts are guarded so the test still asserts something useful
when the lake is empty or the network is offline.

Run::

    python -m unittest tests.test_political_figures -v
"""
from __future__ import annotations

import unittest

from worldscope.sections.political_figures import (
    PoliticalFiguresSection,
    load_registry,
)


class RegistryTest(unittest.TestCase):
    def test_registry_loads_cleanly(self):
        registry = load_registry()
        self.assertGreaterEqual(len(registry), 550)
        self.assertLessEqual(len(registry), 650)

    def test_every_entry_has_id_and_role(self):
        registry = load_registry()
        for entry in registry:
            self.assertIn("id", entry, msg=f"entry missing id: {entry}")
            self.assertIn("role", entry, msg=f"entry missing role: {entry}")

    def test_at_least_100_senators_and_400_house(self):
        registry = load_registry()
        senators = [e for e in registry if e.get("role") == "Senator"]
        house = [e for e in registry if e.get("role") == "Representative"]
        self.assertEqual(len(senators), 100)
        self.assertGreaterEqual(len(house), 435)

    def test_no_em_dashes_in_registry_names(self):
        registry = load_registry()
        for entry in registry:
            name = entry.get("name") or ""
            self.assertNotIn("—", name, msg=f"em-dash in name: {name}")
            self.assertNotIn("–", name, msg=f"en-dash in name: {name}")


class SmokePullTest(unittest.TestCase):
    """The full pull. Runs network (CourtListener) and lake-reads. We tolerate
    empty signal data (fresh checkout) but assert structure."""

    def test_political_figures_pull(self):
        section = PoliticalFiguresSection()
        items = section.pull()
        # The pull always returns at least one row per active figure plus stubs.
        # We assert >= 100 to cover registry-load + scoring-loop-completed.
        self.assertGreaterEqual(len(items), 100,
                                 msg=f"pull returned only {len(items)} items")

    def test_pull_items_have_anomaly_score(self):
        section = PoliticalFiguresSection()
        items = section.pull()
        # Every non-error, non-stub item carries a numeric anomaly_score.
        active = [it for it in items
                   if not it.get("_error") and not it.get("is_stub")]
        self.assertGreater(len(active), 50)
        for it in active[:25]:
            self.assertIn("anomaly_score", it)
            self.assertIsInstance(it["anomaly_score"], (int, float))
            self.assertGreaterEqual(it["anomaly_score"], 0.0)
            self.assertLessEqual(it["anomaly_score"], 1.0)

    def test_at_least_ten_figures_have_nonzero_score(self):
        """Per the section spec: at least 10 figures should register a
        non-zero composite score on a normal day's signal landscape.

        This is the cross-source liveness check. If it fails, either the
        Quiver lake is empty, GDELT is down, or the scorer's windows have
        drifted past the actual signal age. None of those should ever be
        true silently."""
        section = PoliticalFiguresSection()
        items = section.pull()
        scored = [it for it in items if it.get("anomaly_score", 0) > 0]
        self.assertGreaterEqual(
            len(scored), 10,
            msg=f"only {len(scored)} figures had anomaly_score > 0; "
                f"check that upstream lake artifacts are populated"
        )


class ContractArtifactsTest(unittest.TestCase):
    def test_extract_entities_shape(self):
        section = PoliticalFiguresSection()
        item = {
            "figure_id": "senator-warren-elizabeth-ma",
            "figure_name": "Elizabeth Warren",
            "figure_role": "Senator",
            "party": "Democratic",
            "jurisdiction": "MA",
            "bioguide_id": "W000817",
            "watchlist_tags": ["senate", "oversight"],
        }
        entities = section.extract_entities(item)
        self.assertEqual(len(entities), 1)
        ent = entities[0]
        self.assertEqual(ent["type"], "person")
        self.assertTrue(ent["id"].startswith("person:"))
        self.assertEqual(ent["canonical_name"], "Elizabeth Warren")

    def test_stub_items_skip_extract(self):
        section = PoliticalFiguresSection()
        item = {"is_stub": True, "figure_id": "x"}
        self.assertEqual(section.extract_entities(item), [])


if __name__ == "__main__":
    unittest.main()
