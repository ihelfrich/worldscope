"""Tests for lake_export.export_today — the static JSON dump that powers
the in-browser chat widget on the homepage.

Run:  python -m unittest tests.test_lake_export -v
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.lake_export import (
    MAX_RECORDS_PER_SECTION,
    MAX_TOTAL_ENTITIES,
    export_today,
)


def _make_lake(db_path: Path) -> None:
    """Build a minimal lake DB with the records/entities/record_entities
    schema the exporter relies on."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE records (
          id TEXT PRIMARY KEY,
          section_id TEXT NOT NULL,
          source_id TEXT,
          original_text TEXT,
          original_url TEXT,
          record_date TEXT,
          ingested_at TEXT NOT NULL,
          original_lang TEXT
        );
        CREATE TABLE entities (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          canonical_name TEXT NOT NULL,
          aliases_json TEXT NOT NULL DEFAULT '[]',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          first_seen_at TEXT,
          last_seen_at TEXT
        );
        CREATE TABLE record_entities (
          record_id TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          PRIMARY KEY (record_id, entity_id)
        );
    """)
    # Records: 5 in 'federal_register' today, 2 in 'sanctions' today, 1 yesterday.
    today_iso = "2026-05-28T03:00:00Z"
    yesterday_iso = "2026-05-27T03:00:00Z"
    recs = [
        ("r1", "federal_register", "fr", "EO 14123 on AI", "https://x/1", "2026-05-28", today_iso, "en"),
        ("r2", "federal_register", "fr", "Proposed rule EPA", "https://x/2", "2026-05-28", today_iso, "en"),
        ("r3", "federal_register", "fr", "Notice DOL",       "https://x/3", "2026-05-28", today_iso, "en"),
        ("r4", "federal_register", "fr", "Order FERC",       "https://x/4", "2026-05-28", today_iso, "en"),
        ("r5", "federal_register", "fr", "Notice Treasury",  "https://x/5", "2026-05-28", today_iso, "en"),
        ("r6", "sanctions", "ofac", "Designation A", "https://o/1", "2026-05-28", today_iso, "en"),
        ("r7", "sanctions", "ofac", "Designation B", "https://o/2", "2026-05-28", today_iso, "en"),
        ("ry", "federal_register", "fr", "Old item", "https://x/y", "2026-05-27", yesterday_iso, "en"),
    ]
    conn.executemany(
        "INSERT INTO records VALUES (?,?,?,?,?,?,?,?)", recs,
    )
    conn.executemany(
        "INSERT INTO entities VALUES (?,?,?,?,?,?,?)",
        [
            ("ent-china",  "place",  "China",  "[]", "{}", today_iso, today_iso),
            ("ent-warsh",  "person", "Kevin Warsh", "[]", "{}", today_iso, today_iso),
            ("ent-old",    "person", "Yesterday Only", "[]", "{}", yesterday_iso, yesterday_iso),
        ],
    )
    conn.executemany(
        "INSERT INTO record_entities VALUES (?,?)",
        [
            ("r1", "ent-china"),
            ("r6", "ent-china"),
            ("r2", "ent-warsh"),
            ("ry", "ent-old"),  # only mentioned yesterday
        ],
    )
    conn.commit()
    conn.close()


class TestLakeExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lake_db  = self.root / "lake.sqlite"
        self.meta_dir = self.root / "_meta"
        self.out_dir  = self.root / "dist"
        self.meta_dir.mkdir()
        _make_lake(self.lake_db)
        # cross_section.json under _meta/<date>/
        cs_dir = self.meta_dir / "2026-05-28"
        cs_dir.mkdir()
        (cs_dir / "cross_section.json").write_text(json.dumps({
            "day": "2026-05-28",
            "recurrences_found": 1,
            "by_confidence": {"medium": [{
                "entity_id": "ent-china",
                "canonical_name": "China",
                "n_sections": 2,
                "total_mentions": 2,
                "sections": ["federal_register", "sanctions"],
                "confidence": "medium",
            }]},
        }))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_exports_three_files(self) -> None:
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        for k in ("today", "entities", "signals"):
            self.assertTrue(paths[k].exists(), f"{k}.json not written")

    def test_today_doc_shape(self) -> None:
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        doc = json.loads(paths["today"].read_text())
        self.assertEqual(doc["date"], "2026-05-28")
        self.assertIn("federal_register", doc["sections"])
        self.assertIn("sanctions", doc["sections"])
        # Yesterday's record should not appear
        for sid, recs in doc["sections"].items():
            for r in recs:
                self.assertTrue(r["ingested_at"].startswith("2026-05-28"))
        # section_counts should report 5 for federal_register (only today)
        self.assertEqual(doc["section_counts"]["federal_register"], 5)
        self.assertEqual(doc["section_counts"]["sanctions"], 2)

    def test_record_cap_per_section(self) -> None:
        """If a section has more than MAX_RECORDS_PER_SECTION today, the
        export should cap at the limit and reflect this in section_counts."""
        # Add 60 more records into one section to exceed the cap.
        conn = sqlite3.connect(self.lake_db)
        many = [(f"big{i}", "big_section", "src", f"text {i}",
                 f"https://b/{i}", "2026-05-28", "2026-05-28T03:00:00Z", "en")
                for i in range(60)]
        conn.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?,?)", many)
        conn.commit(); conn.close()

        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        doc = json.loads(paths["today"].read_text())
        self.assertEqual(len(doc["sections"]["big_section"]), MAX_RECORDS_PER_SECTION)
        # section_counts reflects the full total, not the cap
        self.assertEqual(doc["section_counts"]["big_section"], 60)
        self.assertEqual(doc["cap_per_section"], MAX_RECORDS_PER_SECTION)

    def test_entities_doc_excludes_yesterday_only(self) -> None:
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        doc = json.loads(paths["entities"].read_text())
        names = {e["name"] for e in doc["entities"]}
        self.assertIn("China", names)
        self.assertIn("Kevin Warsh", names)
        # 'Yesterday Only' wasn't mentioned in any record today
        self.assertNotIn("Yesterday Only", names)

    def test_entities_include_their_sections(self) -> None:
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        doc = json.loads(paths["entities"].read_text())
        china = next(e for e in doc["entities"] if e["name"] == "China")
        # China is mentioned by r1 (federal_register) + r6 (sanctions)
        self.assertEqual(sorted(china["sections"]), ["federal_register", "sanctions"])

    def test_cross_section_entities_pinned_to_top(self) -> None:
        """Regression: entities surfaced by the cross-section recurrence
        analyzer (cross_section.json) must always survive the export cap,
        because record_entities only links an entity to whichever section
        extracted it (usually one), making a SQL count-distinct severely
        understate cross-section reach. Bug originally manifested as
        'China' missing from entities.json despite being a top signal."""
        # Add many high-mention-count single-section entities so they would
        # otherwise crowd out cross-section signals from any pure SQL ranking.
        conn = sqlite3.connect(self.lake_db)
        today_iso = "2026-05-28T03:00:00Z"
        for i in range(50):
            ent_id = f"ent-bulk-{i}"
            conn.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?,?)",
                (ent_id, "person", f"Bulk Legislator {i:02d}", "[]", "{}", today_iso, today_iso),
            )
            # Link to many records to boost mention count.
            for rid in ("r1", "r2", "r3", "r4", "r5"):
                conn.execute("INSERT OR IGNORE INTO record_entities VALUES (?,?)", (rid, ent_id))
        conn.commit(); conn.close()

        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        doc = json.loads(paths["entities"].read_text())
        names = [e["name"] for e in doc["entities"]]
        self.assertIn("China", names,
            "China must be in the export — it's surfaced by cross_section.json")
        # China is pinned, so should be index 0 (or among the pinned block)
        china = next(e for e in doc["entities"] if e["name"] == "China")
        self.assertTrue(china.get("pinned"),
            "cross-section entities must be flagged pinned=True")
        china_idx = names.index("China")
        any_bulk_idx = min(i for i, n in enumerate(names) if n.startswith("Bulk Legislator"))
        self.assertLess(china_idx, any_bulk_idx,
            f"China (pinned cross-section) must rank above bulk legislators, "
            f"but got china_idx={china_idx}, first bulk={any_bulk_idx}")

    def test_entities_include_n_sections_and_n_mentions(self) -> None:
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        doc = json.loads(paths["entities"].read_text())
        china = next(e for e in doc["entities"] if e["name"] == "China")
        self.assertEqual(china["n_sections"], 2)
        self.assertEqual(china["n_mentions"], 2)

    def test_signals_passthrough(self) -> None:
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        sig = json.loads(paths["signals"].read_text())
        self.assertEqual(sig["recurrences_found"], 1)
        self.assertEqual(sig["by_confidence"]["medium"][0]["canonical_name"], "China")

    def test_missing_cross_section_does_not_crash(self) -> None:
        # Remove the cs file; export should still succeed with an empty signals doc.
        (self.meta_dir / "2026-05-28" / "cross_section.json").unlink()
        paths = export_today(
            lake_db=self.lake_db, meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        sig = json.loads(paths["signals"].read_text())
        self.assertEqual(sig["date"], "2026-05-28")
        self.assertEqual(sig["by_confidence"], {})

    def test_missing_lake_db_does_not_crash(self) -> None:
        export_today(
            lake_db=self.root / "no-such-file.sqlite",
            meta_dir=self.meta_dir,
            out_dir=self.out_dir, today=date(2026, 5, 28),
        )
        # No exception; both files exist (empty)
        today_doc = json.loads((self.out_dir / "data" / "today.json").read_text())
        self.assertEqual(today_doc["sections"], {})


if __name__ == "__main__":
    unittest.main()
