"""Tests for reproducibility data and page rendering."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.reproducibility import build_from_repo, source_pull_rows
from worldscope.reproducibility_page import render_reproducibility_page
from worldscope.store import SnapshotStore


BRIEF_DAY = date(2026, 5, 28)


class ReproducibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "dist"
        self.out.mkdir()
        self.store_path = self.root / "store.sqlite"
        store = SnapshotStore(self.store_path)
        store.put("alpha", [{"id": "a1", "source_tier": "primary_document"}],
                  status="ok", when=BRIEF_DAY)
        store.put("beta", [], status="failed", error="upstream timeout", when=BRIEF_DAY)
        store.put("gamma", [{"id": "g1"}], status="ok", when=date(2026, 5, 27))

        data = self.out / "data"
        data.mkdir()
        (data / "claims.json").write_text(json.dumps({
            "brief_date": "2026-05-28",
            "generated_at": "2026-05-28T07:00:00Z",
            "generator_version": "abc1234",
            "summary": {"total": 3, "verified": 1, "divergent": 1, "unverified": 1, "skipped": 0},
            "claims": [],
        }), encoding="utf-8")
        (data / "today.json").write_text(json.dumps({"exported_records": 3}), encoding="utf-8")
        (data / "entities.json").write_text(json.dumps({"entities": [{"id": "e1"}, {"id": "e2"}]}), encoding="utf-8")
        (data / "signals.json").write_text(json.dumps({"entities": [{"id": "e1"}]}), encoding="utf-8")
        for name in ("figures.json", "graph.json", "threads.json"):
            (data / name).write_text("{}", encoding="utf-8")
        (self.out / "2026-05-28.html").write_text("<html>brief</html>", encoding="utf-8")
        (self.out / "index.html").write_text("<html>brief</html>", encoding="utf-8")
        (self.out / "zips").mkdir()
        (self.out / "zips" / "2026-05-28.zip").write_bytes(b"zip")
        (self.root / "briefings").mkdir()
        (self.root / "briefings" / "2026-05-28.md").write_text("# Brief", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_source_rows_include_every_section_in_store(self) -> None:
        rows = source_pull_rows(
            self.store_path,
            BRIEF_DAY,
            source_tiers={"alpha": "primary_document", "beta": "mainstream_independent"},
        )
        by_id = {r["section_id"]: r for r in rows}
        self.assertEqual(set(by_id), {"alpha", "beta", "gamma"})
        self.assertEqual(by_id["alpha"]["state"], "fresh")
        self.assertEqual(by_id["alpha"]["items_today"], 1)
        self.assertEqual(by_id["beta"]["state"], "failed")
        self.assertEqual(by_id["beta"]["error"], "upstream timeout")
        self.assertEqual(by_id["gamma"]["state"], "carried")
        self.assertEqual(by_id["gamma"]["items_today"], 1)

    def test_page_renders_sane_content_and_links(self) -> None:
        doc = build_from_repo(
            self.root,
            self.out,
            today=BRIEF_DAY,
            store_path=self.store_path,
            source_tiers={"alpha": "primary_document", "beta": "mainstream_independent"},
        )
        page = render_reproducibility_page(self.out, doc)
        html = page.read_text(encoding="utf-8")
        hub = (self.out / "reproducibility" / "index.html").read_text(encoding="utf-8")
        self.assertIn("How this brief was built", html)
        self.assertIn("Build proof sheets", hub)
        self.assertIn('href="./2026-05-28/"', hub)
        self.assertIn("alpha", html)
        self.assertIn("beta", html)
        self.assertIn("gamma", html)
        self.assertIn("upstream timeout", html)
        self.assertIn("abc1234", html)
        self.assertIn('href="../../data/claims.json"', html)
        self.assertIn('href="../../zips/2026-05-28.zip"', html)
        self.assertIn("briefings/2026-05-28.md", html)

    def test_artifact_contract_contains_expected_paths(self) -> None:
        doc = build_from_repo(self.root, self.out, today=BRIEF_DAY, store_path=self.store_path)
        artifacts = {row["path"]: row for row in doc["artifacts"]}
        self.assertIn("dist/2026-05-28.html", artifacts)
        self.assertIn("dist/zips/2026-05-28.zip", artifacts)
        self.assertIn("dist/data/claims.json", artifacts)
        self.assertIn("dist/data/today.json", artifacts)
        self.assertIn("dist/data/figures.json", artifacts)
        self.assertIn("dist/data/graph.json", artifacts)
        self.assertIn("dist/data/threads.json", artifacts)
        self.assertIn("briefings/2026-05-28.md", artifacts)
        self.assertEqual(artifacts["dist/zips/2026-05-28.zip"]["href"], "../../zips/2026-05-28.zip")
        self.assertTrue(artifacts["dist/data/claims.json"]["exists"])
        self.assertGreater(artifacts["dist/data/claims.json"]["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
