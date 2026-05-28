"""Tests for the story-threads detection + rendering."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.threads import (
    MIN_DAYS, MIN_ITEMS, build_threads, write_threads_json,
)
from worldscope.threads_page import (
    render_thread_detail, render_threads_index,
)


def _make_store(path: Path, items_per_day: dict[str, list[dict]]) -> None:
    """Build a minimal snapshot store. items_per_day maps date -> list of
    {section_id, title, summary, url}."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE snapshots(
        section_id TEXT, snapshot_date TEXT, payload TEXT, status TEXT,
        PRIMARY KEY(section_id, snapshot_date))""")
    by_sd: dict[tuple[str, str], list[dict]] = {}
    for d, items in items_per_day.items():
        for it in items:
            sid = it["section_id"]
            key = (sid, d)
            by_sd.setdefault(key, []).append(it)
    for (sid, d), items in by_sd.items():
        conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                     (sid, d, json.dumps({"items": items}), "ok"))
    conn.commit(); conn.close()


def _make_lake_for_today(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE records(id TEXT, section_id TEXT, ingested_at TEXT);
        CREATE TABLE entities(id TEXT, type TEXT, canonical_name TEXT);
        CREATE TABLE record_entities(record_id TEXT, entity_id TEXT,
            PRIMARY KEY(record_id, entity_id));
    """)
    today = "2026-05-28T03:00:00Z"
    conn.execute("INSERT INTO entities VALUES (?,?,?)", ("ent-china", "place", "China"))
    conn.execute("INSERT INTO records VALUES (?,?,?)",  ("r1", "macro", today))
    conn.execute("INSERT INTO record_entities VALUES (?,?)", ("r1", "ent-china"))
    conn.commit(); conn.close()


class TestThreadsDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp  = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store.sqlite"
        self.lake  = self.root / "lake.sqlite"
        _make_lake_for_today(self.lake)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_entity_with_3_days_becomes_thread(self) -> None:
        _make_store(self.store, {
            "2026-05-28": [{"section_id":"macro","title":"China cuts rates","summary":"PBOC moves","url":"x"},
                           {"section_id":"markets_global","title":"China bonds rally","summary":"","url":"y"}],
            "2026-05-27": [{"section_id":"foreign_news","title":"China trade deficit","summary":"","url":"a"},
                           {"section_id":"sanctions","title":"OFAC action vs China","summary":"","url":"b"}],
            "2026-05-26": [{"section_id":"macro","title":"China industrial output","summary":"","url":"c"},
                           {"section_id":"commentary","title":"China policy debate","summary":"","url":"d"}],
        })
        cs = {"by_confidence": {"high": [{"canonical_name": "China", "n_sections": 3}]}}
        threads = build_threads(store_db=self.store, lake_db=self.lake,
                                  cross_section=cs, today=date(2026, 5, 28))
        names = [t.title for t in threads]
        self.assertIn("China", names)
        china = next(t for t in threads if t.title == "China")
        self.assertEqual(china.days_active, 3)
        self.assertGreaterEqual(china.items_total, 6)
        self.assertTrue(china.is_active_today)

    def test_entity_below_min_days_skipped(self) -> None:
        _make_store(self.store, {
            "2026-05-28": [{"section_id":"macro","title":"Acme Corp earnings","summary":"","url":"x"}],
            "2026-05-27": [{"section_id":"macro","title":"Acme Corp insider sale","summary":"","url":"y"}],
        })
        cs = {"by_confidence": {"medium": [{"canonical_name": "Acme Corp", "n_sections": 1}]}}
        threads = build_threads(store_db=self.store, lake_db=self.lake,
                                  cross_section=cs, today=date(2026, 5, 28))
        names = [t.title for t in threads]
        self.assertNotIn("Acme Corp", names)

    def test_generic_short_names_skipped(self) -> None:
        _make_store(self.store, {
            "2026-05-28": [{"section_id":"x","title":"US position on AI policy","summary":"","url":"x"}],
            "2026-05-27": [{"section_id":"x","title":"US trade policy update","summary":"","url":"y"}],
            "2026-05-26": [{"section_id":"x","title":"US sanctions list","summary":"","url":"z"}],
        })
        cs = {"by_confidence": {"high": [
            {"canonical_name": "US", "n_sections": 5},
            {"canonical_name": "United States", "n_sections": 5},
        ]}}
        threads = build_threads(store_db=self.store, lake_db=self.lake,
                                  cross_section=cs, today=date(2026, 5, 28))
        names = [t.title for t in threads]
        self.assertNotIn("US", names)
        self.assertNotIn("United States", names)

    def test_writes_threads_json(self) -> None:
        _make_store(self.store, {
            "2026-05-28": [{"section_id":"a","title":"Polymarket bet on China","summary":"","url":"u1"},
                           {"section_id":"b","title":"China oil deal","summary":"","url":"u2"}],
            "2026-05-27": [{"section_id":"a","title":"China policy","summary":"","url":"u3"},
                           {"section_id":"b","title":"China consumer","summary":"","url":"u4"}],
            "2026-05-26": [{"section_id":"a","title":"China industrial","summary":"","url":"u5"},
                           {"section_id":"b","title":"China exports","summary":"","url":"u6"}],
        })
        cs = {"by_confidence": {"medium": [{"canonical_name": "China", "n_sections": 2}]}}
        threads = build_threads(store_db=self.store, lake_db=self.lake,
                                  cross_section=cs, today=date(2026, 5, 28))
        out = Path(self.tmp.name) / "dist"
        p = write_threads_json(threads, out, today=date(2026, 5, 28))
        self.assertTrue(p.exists())
        doc = json.loads(p.read_text())
        self.assertEqual(doc["date"], "2026-05-28")
        self.assertEqual(doc["lookback_days"], 14)
        self.assertEqual(doc["thread_count"], len(threads))


class TestThreadsPage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_index_renders_when_threads_exist(self) -> None:
        doc = {
            "date": "2026-05-28",
            "lookback_days": 14,
            "thread_count": 1,
            "threads": [{
                "id": "thread:china", "slug": "china", "title": "China",
                "entity_type": "place", "days_active": 4, "items_total": 12,
                "items_by_day": {
                    "2026-05-28": [{"section_id":"macro","title":"PBOC cuts","url":"u1","summary":"","date":"2026-05-28"}],
                    "2026-05-27": [{"section_id":"foreign_news","title":"Trade deficit","url":"u2","summary":"","date":"2026-05-27"}],
                },
                "sections_touched": ["macro", "foreign_news"],
                "heat_score": 124.4, "is_active_today": True,
                "synth": "China is active across multiple sections.",
            }],
        }
        path = render_threads_index(self.out, doc, today=date(2026, 5, 28))
        html = path.read_text()
        self.assertIn("STORY THREADS", html)
        self.assertIn("China", html)
        self.assertIn("./china/", html)
        # Heat pill renders for the thread
        self.assertIn("active", html.lower())

    def test_index_renders_empty_state(self) -> None:
        doc = {"date": "2026-05-28", "lookback_days": 14, "thread_count": 0, "threads": []}
        path = render_threads_index(self.out, doc, today=date(2026, 5, 28))
        html = path.read_text()
        self.assertIn("No multi-day arcs today", html)

    def test_detail_renders_per_day_timeline(self) -> None:
        thread = {
            "id": "thread:china", "slug": "china", "title": "China",
            "entity_type": "place", "days_active": 2, "items_total": 2,
            "items_by_day": {
                "2026-05-28": [{"section_id":"macro","title":"Today's China item","url":"u1","summary":"","date":"2026-05-28"}],
                "2026-05-27": [{"section_id":"foreign_news","title":"Yesterday's","url":"u2","summary":"","date":"2026-05-27"}],
            },
            "sections_touched": ["macro", "foreign_news"],
            "heat_score": 87.0, "is_active_today": True,
            "synth": "test synth",
        }
        path = render_thread_detail(self.out, thread, today=date(2026, 5, 28))
        html = path.read_text()
        # Today header
        self.assertIn("TODAY", html)
        # Yesterday's date
        self.assertIn("2026-05-27", html)
        # Items rendered as links
        self.assertIn('href="u1"', html)
        self.assertIn('href="u2"', html)
        # Back link to all threads
        self.assertIn("All threads", html)


if __name__ == "__main__":
    unittest.main()
