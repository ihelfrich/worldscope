"""Tests for graph_export — entity co-occurrence graph for /graph/."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.graph_export import (
    MAX_EDGES,
    MAX_NODES,
    MIN_EDGE_WEIGHT,
    build_graph,
)


def _make_lake(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE records (id TEXT PRIMARY KEY, section_id TEXT, ingested_at TEXT);
        CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, canonical_name TEXT);
        CREATE TABLE record_entities (record_id TEXT, entity_id TEXT,
            PRIMARY KEY(record_id, entity_id));
    """)
    today = "2026-05-28T03:00:00Z"
    # 5 records, two sections
    for i in range(5):
        conn.execute("INSERT INTO records VALUES (?,?,?)",
                     (f"r{i}", "federal_register" if i < 3 else "sanctions", today))
    # 4 entities, including one pinnable "China"
    conn.execute("INSERT INTO entities VALUES (?,?,?)", ("ent-china",   "place",  "China"))
    conn.execute("INSERT INTO entities VALUES (?,?,?)", ("ent-warsh",   "person", "Kevin Warsh"))
    conn.execute("INSERT INTO entities VALUES (?,?,?)", ("ent-fed",     "org",    "Federal Reserve"))
    conn.execute("INSERT INTO entities VALUES (?,?,?)", ("ent-other",   "person", "Someone Else"))
    # China + Warsh co-occur in 3 records, China + Fed in 4 records
    co = [
        ("r0", "ent-china"), ("r0", "ent-warsh"),
        ("r1", "ent-china"), ("r1", "ent-warsh"),
        ("r2", "ent-china"), ("r2", "ent-warsh"),
        ("r0", "ent-fed"),   ("r1", "ent-fed"),
        ("r3", "ent-china"), ("r3", "ent-fed"),
        ("r4", "ent-china"), ("r4", "ent-fed"),
        ("r2", "ent-other"),  # singleton — should not create edges ≥2
    ]
    conn.executemany("INSERT INTO record_entities VALUES (?,?)", co)
    conn.commit(); conn.close()


class TestGraphExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lake = self.root / "lake.sqlite"
        self.meta = self.root / "_meta"
        self.out  = self.root / "dist"
        self.meta.mkdir()
        _make_lake(self.lake)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self):
        return build_graph(lake_db=self.lake, meta_dir=self.meta,
                           out_dir=self.out, today=date(2026, 5, 28))

    def test_writes_graph_json(self) -> None:
        p = self._build()
        self.assertTrue(p.exists())
        doc = json.loads(p.read_text())
        self.assertEqual(doc["date"], "2026-05-28")
        self.assertIn("nodes", doc)
        self.assertIn("edges", doc)

    def test_nodes_include_active_entities(self) -> None:
        doc = json.loads(self._build().read_text())
        names = {n["name"] for n in doc["nodes"]}
        self.assertIn("China",            names)
        self.assertIn("Kevin Warsh",      names)
        self.assertIn("Federal Reserve",  names)

    def test_edges_meet_minimum_weight(self) -> None:
        doc = json.loads(self._build().read_text())
        for e in doc["edges"]:
            self.assertGreaterEqual(e["weight"], MIN_EDGE_WEIGHT)

    def test_singleton_entity_has_no_edges(self) -> None:
        doc = json.loads(self._build().read_text())
        other_id = next((n["id"] for n in doc["nodes"] if n["name"] == "Someone Else"), None)
        if other_id is None:
            self.skipTest("singleton entity wasn't selected (expected)")
        for e in doc["edges"]:
            self.assertNotIn(other_id, (e["source"], e["target"]))

    def test_pinned_signal_marked(self) -> None:
        # Add cross_section.json that pins China
        cs_dir = self.meta / "2026-05-28"
        cs_dir.mkdir()
        (cs_dir / "cross_section.json").write_text(json.dumps({
            "by_confidence": {"high": [
                {"entity_id": "ent-china", "canonical_name": "China",
                 "n_sections": 2, "total_mentions": 5, "confidence": "high"},
            ]},
        }))
        doc = json.loads(self._build().read_text())
        china = next(n for n in doc["nodes"] if n["name"] == "China")
        self.assertTrue(china.get("pinned"), "pinned cross-section entity must carry pinned=True")

    def test_node_and_edge_caps(self) -> None:
        # Add many entities + records to overshoot the caps.
        conn = sqlite3.connect(self.lake)
        today = "2026-05-28T03:00:00Z"
        for i in range(MAX_NODES + 50):
            conn.execute("INSERT INTO entities VALUES (?,?,?)",
                         (f"ext-{i}", "person", f"Person {i:04d}"))
            rid = f"rext-{i}"
            conn.execute("INSERT INTO records VALUES (?,?,?)", (rid, "x", today))
            conn.execute("INSERT INTO record_entities VALUES (?,?)", (rid, f"ext-{i}"))
            conn.execute("INSERT INTO record_entities VALUES (?,?)", (rid, "ent-china"))
        conn.commit(); conn.close()
        doc = json.loads(self._build().read_text())
        self.assertLessEqual(doc["node_count"], MAX_NODES)
        self.assertLessEqual(doc["edge_count"], MAX_EDGES)

    def test_missing_lake_does_not_crash(self) -> None:
        # Point at a non-existent DB; should write an empty doc.
        empty = Path(self.tmp.name) / "nope.sqlite"
        p = build_graph(lake_db=empty, meta_dir=self.meta,
                        out_dir=self.out, today=date(2026, 5, 28))
        doc = json.loads(p.read_text())
        self.assertEqual(doc["nodes"], [])
        self.assertEqual(doc["edges"], [])


if __name__ == "__main__":
    unittest.main()
