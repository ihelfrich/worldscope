"""Regression tests for the section-level cache state machine.

Covers the five states (fresh, fresh_empty, carry_forward, stale_after_failure,
no_data) plus schema-drift defense.

Run:  python -m unittest tests.test_cache_state_machine -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from worldscope.sections import (
    Section,
    SectionState,
    STATE_CARRY_FORWARD,
    STATE_FRESH,
    STATE_FRESH_EMPTY,
    STATE_NO_DATA,
    STATE_STALE,
)
from worldscope.store import SnapshotStore


# Test sections that surface deterministic behavior --------------------------

class _GoodSection(Section):
    id = "good"
    title = "Good"
    emoji = "✅"
    payload: list[dict] = [{"id": "a", "title": "alpha", "url": "u1", "date": "2026-05-25", "summary": ""}]

    def pull(self):
        return self.payload


class _EmptySection(Section):
    id = "empty"
    title = "Empty"
    emoji = "—"

    def pull(self):
        return []


class _FailingSection(Section):
    id = "failing"
    title = "Failing"
    emoji = "💥"

    def pull(self):
        raise RuntimeError("upstream is on fire")


# Test harness --------------------------------------------------------------

class CacheStateMachineTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "test.sqlite"
        self.store = SnapshotStore(self.store_path)
        os.environ.pop("WORLDSCOPE_SKIP", None)

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.pop("WORLDSCOPE_SKIP", None)

    # ---- the five states ----------------------------------------------

    def test_fresh_pull_with_items(self):
        sec = _GoodSection(store=self.store)
        state = sec.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_FRESH)
        self.assertEqual(len(state.items), 1)
        self.assertEqual(state.items[0]["title"], "alpha")
        # First run → everything is "new"
        self.assertEqual(len(state.new), 1)

    def test_fresh_empty(self):
        sec = _EmptySection(store=self.store)
        state = sec.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_FRESH_EMPTY)
        self.assertEqual(state.items, [])

    def test_carry_forward_when_skipped(self):
        # Day 1: pull cleanly to seed a snapshot
        sec = _GoodSection(store=self.store)
        sec.resolve(today=date(2026, 5, 24))
        # Day 2: set WORLDSCOPE_SKIP=good → carry forward
        os.environ["WORLDSCOPE_SKIP"] = "good"
        state = sec.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_CARRY_FORWARD)
        self.assertEqual(len(state.items), 1)
        self.assertEqual(state.source_date, "2026-05-24")

    def test_stale_after_failure_keeps_prior_data(self):
        # Day 1: a good section seeds the snapshot
        good = _GoodSection(store=self.store)
        good.resolve(today=date(2026, 5, 24))
        # Day 2: same id, but a failing pull
        class _Replaced(_FailingSection):
            id = "good"   # collide with prior snapshot
            title = "Good"
        sec = _Replaced(store=self.store)
        state = sec.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_STALE)
        self.assertEqual(len(state.items), 1)
        self.assertIn("upstream is on fire", state.error or "")
        self.assertEqual(state.source_date, "2026-05-24")

    def test_no_data_when_skipped_with_no_prior(self):
        os.environ["WORLDSCOPE_SKIP"] = "good"
        sec = _GoodSection(store=self.store)
        state = sec.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_NO_DATA)
        self.assertEqual(state.items, [])

    def test_no_data_when_failure_with_no_prior(self):
        sec = _FailingSection(store=self.store)
        state = sec.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_NO_DATA)
        self.assertIn("upstream is on fire", state.error or "")

    # ---- schema drift -------------------------------------------------

    def test_schema_drift_is_rejected(self):
        # Write a snapshot with a wrong schema_version directly
        bad = {"schema_version": 0, "items": []}
        self.store._conn.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?)",
            ("good", "2026-05-24", "2026-05-24T00:00:00+00:00", json.dumps(bad)),
        )
        self.store._conn.commit()
        # most_recent should return None because validation rejects it
        self.assertIsNone(self.store.most_recent("good"))

    def test_empty_pull_does_not_overwrite_good_snapshot_via_failure_path(self):
        # Critical: a CLEAN empty pull DOES overwrite the prior (legitimately quiet day).
        # But a FAILED pull does NOT overwrite — that was Gemini's "cache poisoning" catch.
        good = _GoodSection(store=self.store)
        good.resolve(today=date(2026, 5, 24))  # seeds 1 item
        # Now an empty pull on day 2 — should overwrite (empty_ok status)
        class _EmptyForGood(_EmptySection):
            id = "good"
            title = "Good"
        empty = _EmptyForGood(store=self.store)
        state = empty.resolve(today=date(2026, 5, 25))
        self.assertEqual(state.state, STATE_FRESH_EMPTY)
        # And a failed pull on day 3 should NOT overwrite — it falls back to day 2's empty.
        class _FailingForGood(_FailingSection):
            id = "good"
            title = "Good"
        fail = _FailingForGood(store=self.store)
        state = fail.resolve(today=date(2026, 5, 26))
        self.assertEqual(state.state, STATE_STALE)
        self.assertEqual(state.source_date, "2026-05-25")  # carries from the prior empty

    def test_empty_does_not_overwrite_same_day_non_empty(self) -> None:
        """Regression: the morning cron pulled 3 items at 07:00 UTC.
        An afternoon manual run that returns [] (rate-limited, source
        down, etc.) must NOT replace the morning's snapshot with empty,
        which would silently lose data. Invariant lives in
        SnapshotStore.put()."""
        sid = "test_no_clobber"
        today = date(2026, 5, 28)
        # Morning: 3 items
        self.store.put(sid,
                        [{"_id": "a", "title": "alpha"},
                         {"_id": "b", "title": "beta"},
                         {"_id": "c", "title": "gamma"}],
                        status="ok", when=today)
        # Afternoon: empty pull
        self.store.put(sid, [], status="empty_ok", when=today)
        # Morning's data must survive.
        snap = self.store.get(sid, when=today)
        self.assertIsNotNone(snap)
        self.assertEqual(len(snap["items"]), 3)

    def test_non_empty_overwrites_same_day_non_empty(self) -> None:
        """Counterpart: a same-day re-pull that returned MORE items should
        replace the prior. Only empty-replaces-non-empty is forbidden."""
        sid = "test_can_grow"
        today = date(2026, 5, 28)
        self.store.put(sid, [{"_id": "a"}], status="ok", when=today)
        self.store.put(sid, [{"_id": "a"}, {"_id": "b"}, {"_id": "c"}],
                        status="ok", when=today)
        snap = self.store.get(sid, when=today)
        self.assertEqual(len(snap["items"]), 3)


if __name__ == "__main__":
    unittest.main()
