"""Structural tests for the CourtListener section (federal & state courts).

No live network: requests.get is monkeypatched with a fake that returns
canned CourtListener-shaped results per court. Verifies state courts are
covered, per-court volume caps and the overall limit hold, duplicate
clusters are collapsed, and a failing court degrades gracefully.

Run:
    python -m unittest tests.test_courtlistener -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from worldscope.sections import courtlistener as cl


class _FakeResp:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": self._results}


def _result(cluster_id, date, name, court):
    return {
        "cluster_id": cluster_id,
        "dateFiled": date,
        "caseName": name,
        "absolute_url": f"/opinion/{cluster_id}/{court}/",
        "snippet": f"snippet for {name}",
    }


class TestCourtListenerSection(unittest.TestCase):

    def _run_with_fake(self, fake_get):
        with mock.patch.object(cl.requests, "get", side_effect=fake_get):
            return cl.CourtListenerSection().pull()

    def test_state_courts_are_covered(self):
        """A state court id must produce state-tagged items with its label."""
        def fake_get(url, params=None, headers=None, timeout=None):
            court = params["court"]
            if court == "cal":
                return _FakeResp([_result(1, "2026-05-31", "People v. Doe", court)])
            if court == "scotus":
                return _FakeResp([_result(2, "2026-05-30", "United States v. X", court)])
            return _FakeResp([])

        items = self._run_with_fake(fake_get)
        by_juris = {it["jurisdiction"] for it in items}
        self.assertIn("state", by_juris, "state court opinions should be present")
        self.assertIn("federal", by_juris)
        cal = next(it for it in items if it["court"] == "cal")
        self.assertEqual(cal["court_label"], "Supreme Court of California")
        self.assertTrue(cal["title"].startswith("Supreme Court of California: "))

    def test_per_court_cap_and_overall_limit(self):
        """No court exceeds its cap; total respects LIMIT."""
        def fake_get(url, params=None, headers=None, timeout=None):
            court = params["court"]
            # 50 unique rows per court; caps should trim to PER_* per court.
            return _FakeResp([
                _result(f"{court}-{i}", "2026-05-31", f"{court} case {i}", court)
                for i in range(50)
            ])

        items = self._run_with_fake(fake_get)
        self.assertLessEqual(len(items), cl.CourtListenerSection.LIMIT)
        from collections import Counter
        per_court = Counter(it["court"] for it in items)
        for court, n in per_court.items():
            cap = (cl.CourtListenerSection.PER_FEDERAL
                   if court in cl.FEDERAL_COURTS
                   else cl.CourtListenerSection.PER_STATE)
            self.assertLessEqual(n, cap, f"{court} exceeded its per-court cap")

    def test_duplicate_clusters_collapsed(self):
        """The same cluster id surfacing under two courts is kept once."""
        def fake_get(url, params=None, headers=None, timeout=None):
            court = params["court"]
            if court in ("cal", "ca9"):
                return _FakeResp([_result(999, "2026-05-31", "Shared Case", court)])
            return _FakeResp([])

        items = self._run_with_fake(fake_get)
        shared = [it for it in items if it["id"] == "999"]
        self.assertEqual(len(shared), 1, "duplicate cluster should appear once")

    def test_failing_court_degrades_gracefully(self):
        """A court that errors must not sink the whole section."""
        def fake_get(url, params=None, headers=None, timeout=None):
            court = params["court"]
            if court == "scotus":
                raise RuntimeError("boom")
            if court == "cal":
                return _FakeResp([_result(7, "2026-05-31", "Survives", court)])
            return _FakeResp([])

        items = self._run_with_fake(fake_get)
        self.assertTrue(any(it["id"] == "7" for it in items),
                        "non-failing courts should still return results")

    def test_sorted_by_recency(self):
        def fake_get(url, params=None, headers=None, timeout=None):
            court = params["court"]
            if court == "cal":
                return _FakeResp([_result(1, "2026-05-10", "Older", court)])
            if court == "ny":
                return _FakeResp([_result(2, "2026-05-31", "Newer", court)])
            return _FakeResp([])

        items = self._run_with_fake(fake_get)
        dates = [it["date"] for it in items if it["id"] in ("1", "2")]
        self.assertEqual(dates, sorted(dates, reverse=True))


if __name__ == "__main__":
    unittest.main()
