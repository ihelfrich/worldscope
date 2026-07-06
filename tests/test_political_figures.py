"""Smoke tests for the political_figures section.

These exercise the registry loader, the section's pull() (which runs against
the live lake and live network where present), and the contract artifacts.
Network-heavy parts are guarded so the test still asserts something useful
when the lake is empty or the network is offline.

Run::

    python -m unittest tests.test_political_figures -v
"""
from __future__ import annotations

import os
import unittest
import warnings

from datetime import date

from worldscope.sections.political_figures import (
    PoliticalFiguresSection,
    load_registry,
    _max_signal_date,
)


# Healthy-lake bar: on a normal day's fresh signal landscape at least this many
# figures should register a non-zero composite score.
HEALTHY_NONZERO = 10

# When set (a scheduled run against freshly-pulled data), the liveness check is
# enforced strictly: a thin lake is a real regression there, not PR noise. On
# ordinary PR/push CI the check reads the *committed* lake, which legitimately
# thins when upstream connectors lapse, so a thin-but-live result warns instead
# of failing. A genuinely hollow lake (zero non-zero scores) fails either way.
DATA_QUALITY_ENV = "WORLDSCOPE_DATA_QUALITY"


def _data_quality_run() -> bool:
    return (os.environ.get(DATA_QUALITY_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


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


class AnchorDateTest(unittest.TestCase):
    """The scorer anchors recency to the freshest signal in the lake, so a
    slightly-stale committed lake still scores instead of decaying to zero."""

    def test_max_signal_date_picks_latest_valid(self):
        groups = [
            [{"date": "2026-06-08"}, {"date": "2026-06-10"}],
            [{"date": "2026-06-05T12:00:00Z"}, {"date": "bad"}, {"date": None}],
        ]
        self.assertEqual(_max_signal_date(*groups), date(2026, 6, 10))

    def test_max_signal_date_none_when_no_dates(self):
        self.assertIsNone(_max_signal_date([], [{"date": ""}, {"date": "nope"}]))


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

    def test_lake_is_not_hollow(self):
        """Cross-source liveness check: detect a *hollow* lake — one where no
        figure scores at all, meaning the Quiver/GDELT/Form-4 artifacts are
        empty, a sensor is down, or the scorer's windows drifted past the
        signal age. That must never pass silently, so it fails everywhere.

        The healthy-day bar is higher (HEALTHY_NONZERO). But this test reads
        the *committed* lake on ordinary CI, which legitimately thins when
        upstream connectors lapse — not a code regression the PR should carry.
        So a thin-but-live lake (1..HEALTHY_NONZERO-1 scored) only warns here,
        and is enforced strictly on a data-quality run against fresh data
        (WORLDSCOPE_DATA_QUALITY=1), preserving the strong liveness signal
        where the data is actually fresh."""
        section = PoliticalFiguresSection()
        items = section.pull()
        scored = [it for it in items if it.get("anomaly_score", 0) > 0]
        n = len(scored)

        # Hollow lake: zero signal. Always a failure.
        self.assertGreater(
            n, 0,
            msg="0 figures had anomaly_score > 0 — the lake is hollow "
                "(empty Quiver/GDELT/Form-4 artifacts, a dead sensor, or a "
                "scorer window drift). Check source_health / the daily-brief run."
        )

        if n >= HEALTHY_NONZERO:
            return

        # Thin but live. Strict on a fresh-data run; a warning on ordinary CI.
        detail = (f"only {n} figures had anomaly_score > 0 (healthy >= "
                  f"{HEALTHY_NONZERO}); the committed lake has thinned, likely "
                  f"because upstream connectors are stale.")
        if _data_quality_run():
            self.fail(detail + " Failing because this is a data-quality run "
                               "against fresh data.")
        warnings.warn(detail + " Not failing PR CI; set "
                      f"{DATA_QUALITY_ENV}=1 to enforce against fresh data.",
                      stacklevel=2)


class HollowLakeGateTest(unittest.TestCase):
    """Lock in the liveness gate's behavior without touching the network: a
    hollow lake always fails, a thin lake warns on PR CI but fails on a
    data-quality run, and a healthy lake passes."""

    @staticmethod
    def _items(nonzero: int, zero: int = 5):
        return ([{"anomaly_score": 0.5} for _ in range(nonzero)]
                + [{"anomaly_score": 0.0} for _ in range(zero)])

    def _run(self, items, *, data_quality: bool):
        from unittest import mock
        env = {DATA_QUALITY_ENV: "1"} if data_quality else {}
        case = SmokePullTest("test_lake_is_not_hollow")
        with mock.patch.object(PoliticalFiguresSection, "pull", return_value=items), \
             mock.patch.dict(os.environ, env, clear=False):
            if not data_quality:
                os.environ.pop(DATA_QUALITY_ENV, None)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                case.test_lake_is_not_hollow()
            return caught

    def test_hollow_lake_fails_on_pr_ci(self):
        with self.assertRaises(AssertionError):
            self._run(self._items(0), data_quality=False)

    def test_hollow_lake_fails_on_data_quality_run(self):
        with self.assertRaises(AssertionError):
            self._run(self._items(0), data_quality=True)

    def test_thin_lake_warns_but_passes_on_pr_ci(self):
        caught = self._run(self._items(HEALTHY_NONZERO - 3), data_quality=False)
        self.assertTrue(any("anomaly_score > 0" in str(w.message) for w in caught),
                        msg="thin lake should emit a data-quality warning")

    def test_thin_lake_fails_on_data_quality_run(self):
        with self.assertRaises(AssertionError):
            self._run(self._items(HEALTHY_NONZERO - 3), data_quality=True)

    def test_healthy_lake_passes_without_warning(self):
        caught = self._run(self._items(HEALTHY_NONZERO + 5), data_quality=False)
        self.assertFalse([w for w in caught if "anomaly_score > 0" in str(w.message)],
                         msg="a healthy lake should not warn")


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
