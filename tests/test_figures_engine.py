"""Tests for figures_engine: deterministic spec generation + schema."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.figures_engine import (
    VEGA_CONFIG,
    build_figures,
)


def _stub_store(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE snapshots(
        section_id TEXT, snapshot_date TEXT, payload TEXT, status TEXT,
        PRIMARY KEY(section_id, snapshot_date))""")
    today_items = [{"_id": f"t{i}", "title": "t"} for i in range(10)]
    yest_items  = [{"_id": f"y{i}", "title": "y"} for i in range(6)]
    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                 ("federal_register", "2026-05-28",
                  json.dumps({"items": today_items}), "ok"))
    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)",
                 ("federal_register", "2026-05-27",
                  json.dumps({"items": yest_items}), "ok"))
    conn.commit(); conn.close()


def _stub_lake(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE records (id TEXT, section_id TEXT, ingested_at TEXT);
        CREATE TABLE entities (id TEXT, type TEXT, canonical_name TEXT);
    """)
    conn.commit(); conn.close()


class TestFiguresEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out  = self.root / "dist"
        self.store = self.root / "store.sqlite"
        self.lake  = self.root / "lake.sqlite"
        _stub_store(self.store)
        _stub_lake(self.lake)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self, **kwargs):
        return build_figures(
            out_dir=self.out, lake_db=self.lake, store_db=self.store,
            today=date(2026, 5, 28), use_llm=False, **kwargs,
        )

    def test_writes_figures_json(self) -> None:
        p = self._build()
        self.assertTrue(p.exists())
        doc = json.loads(p.read_text())
        self.assertEqual(doc["date"], "2026-05-28")
        self.assertEqual(doc["generator"], "deterministic")
        self.assertIsInstance(doc["figures"], list)

    def test_every_figure_has_required_fields(self) -> None:
        p = self._build(
            today_doc={
                "date": "2026-05-28",
                "section_counts": {"a": 12, "b": 5, "c": 3},
                "sections": {},
            },
            entities_doc={
                "entities": [
                    {"id": "ent-c", "name": "China", "type": "place",
                     "n_sections": 3, "n_mentions": 6, "sections": ["x","y","z"]},
                    {"id": "ent-j", "name": "Jackson", "type": "person",
                     "n_sections": 3, "n_mentions": 5, "sections": ["a","b","c"]},
                ],
            },
            cross_section={
                "recurrences_found": 2,
                "by_confidence": {
                    "medium": [
                        {"canonical_name": "China",   "n_sections": 3, "total_mentions": 6, "confidence": "medium"},
                        {"canonical_name": "Jackson", "n_sections": 3, "total_mentions": 5, "confidence": "medium"},
                    ],
                },
            },
        )
        doc = json.loads(p.read_text())
        for f in doc["figures"]:
            for k in ("id", "kicker", "title", "caption", "spec_type", "spec"):
                self.assertIn(k, f, f"figure {f.get('id')} missing {k}")
            self.assertEqual(f["spec_type"], "vega-lite")
            self.assertIsInstance(f["spec"], dict)
            self.assertIn("$schema", f["spec"])
            self.assertEqual(f["spec"]["config"], VEGA_CONFIG)

    def test_cross_section_figure_present_when_signals_exist(self) -> None:
        p = self._build(
            today_doc={"date": "2026-05-28", "section_counts": {"a": 1}, "sections": {}},
            entities_doc={"entities": []},
            cross_section={
                "recurrences_found": 1,
                "by_confidence": {"high": [
                    {"canonical_name": "Foo", "n_sections": 5, "total_mentions": 9, "confidence": "high"},
                ]},
            },
        )
        doc = json.loads(p.read_text())
        ids = [f["id"] for f in doc["figures"]]
        self.assertIn("cross-section", ids)

    def test_cross_section_absent_when_no_signals(self) -> None:
        p = self._build(
            today_doc={"date": "2026-05-28", "section_counts": {"a": 1}, "sections": {}},
            entities_doc={"entities": []},
            cross_section={"recurrences_found": 0, "by_confidence": {}},
        )
        doc = json.loads(p.read_text())
        ids = [f["id"] for f in doc["figures"]]
        self.assertNotIn("cross-section", ids)

    def test_section_deltas_uses_yesterday_snapshot(self) -> None:
        p = self._build(
            today_doc={"date": "2026-05-28",
                       "section_counts": {"federal_register": 10},
                       "sections": {}},
            entities_doc={"entities": []},
            cross_section={"recurrences_found": 0, "by_confidence": {}},
        )
        doc = json.loads(p.read_text())
        deltas = next((f for f in doc["figures"] if f["id"] == "section-deltas"), None)
        self.assertIsNotNone(deltas)
        # Should reflect today(10) - yesterday(6) = +4
        rows = deltas["spec"]["data"]["values"]
        fr_row = next(r for r in rows if r["section"] == "federal register")
        self.assertEqual(fr_row["today"], 10)
        self.assertEqual(fr_row["yesterday"], 6)
        self.assertEqual(fr_row["delta"], 4)

    def test_world_map_omitted_when_no_known_countries(self) -> None:
        p = self._build(
            today_doc={"date": "2026-05-28", "section_counts": {"a": 1}, "sections": {}},
            entities_doc={"entities": [
                {"id": "x", "name": "Some Tiny Town", "type": "place",
                 "n_sections": 1, "n_mentions": 1, "sections": ["a"]},
            ]},
            cross_section={"recurrences_found": 0, "by_confidence": {}},
        )
        doc = json.loads(p.read_text())
        ids = [f["id"] for f in doc["figures"]]
        self.assertNotIn("world-map", ids)

    def test_world_map_present_when_known_countries(self) -> None:
        p = self._build(
            today_doc={"date": "2026-05-28", "section_counts": {"a": 1}, "sections": {}},
            entities_doc={"entities": [
                {"id": "c", "name": "China",  "type": "place",
                 "n_sections": 3, "n_mentions": 8, "sections": ["a","b","c"]},
                {"id": "u", "name": "Ukraine", "type": "place",
                 "n_sections": 2, "n_mentions": 4, "sections": ["a","b"]},
            ]},
            cross_section={"recurrences_found": 0, "by_confidence": {}},
        )
        doc = json.loads(p.read_text())
        wm = next((f for f in doc["figures"] if f["id"] == "world-map"), None)
        self.assertIsNotNone(wm)
        # Inline values include both China and Ukraine
        # The geo layer is wm.spec.layer[1].data.values
        vals = wm["spec"]["layer"][1]["data"]["values"]
        names = {v["name"] for v in vals}
        self.assertIn("China",   names)
        self.assertIn("Ukraine", names)

    def test_llm_skipped_when_no_key(self) -> None:
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)
        p = self._build()
        doc = json.loads(p.read_text())
        self.assertEqual(doc["generator"], "deterministic")


if __name__ == "__main__":
    unittest.main()
