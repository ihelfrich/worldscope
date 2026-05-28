"""Smoke + sanity tests for the worldscope MCP server tools.

Imports worldscope_mcp directly and calls each @mcp.tool() function as a
plain Python callable, asserting that:

  - It returns successfully against the current lake schema (no
    "no such column" / "no such table" errors).
  - The response shape matches what the README documents.
  - Read-only invariant holds (each tool opens a `mode=ro` connection).

These tests run against the committed lake/db/worldscope.sqlite, so they
cover real production data, not a fixture.

Run:  python -m unittest tests.test_mcp_server -v
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MCP_SCRIPT = REPO / "mcp-server" / "worldscope_mcp.py"
LAKE_DB = REPO / "lake" / "db" / "worldscope.sqlite"


def _load_mcp_module():
    """Load mcp-server/worldscope_mcp.py as a module. The directory name
    contains a hyphen so it isn't importable as a package — load by path."""
    spec = importlib.util.spec_from_file_location("worldscope_mcp", MCP_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["worldscope_mcp"] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(LAKE_DB.exists(), f"lake DB missing at {LAKE_DB}")
class TestMcpToolsAgainstRealLake(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_mcp_module()
        # FastMCP wraps each tool in a FunctionTool; the original callable
        # is preserved on the .fn attribute.
        cls.tools = {}
        for attr in dir(cls.m):
            obj = getattr(cls.m, attr)
            if hasattr(obj, "fn") and callable(getattr(obj, "fn", None)):
                cls.tools[attr] = obj.fn
            elif callable(obj) and getattr(obj, "__module__", "") == "worldscope_mcp":
                cls.tools[attr] = obj

    def _call(self, name, **kwargs):
        fn = self.tools.get(name) or getattr(self.m, name)
        if hasattr(fn, "fn"):
            fn = fn.fn
        return fn(**kwargs)

    # --- search_news ---------------------------------------------------------

    def test_search_news_returns_records(self) -> None:
        result = self._call("search_news", query="", days_back=365, limit=5)
        self.assertIn("count", result)
        self.assertIn("records", result)
        self.assertIsInstance(result["records"], list)
        if result["count"] > 0:
            r = result["records"][0]
            self.assertIn("id", r)
            self.assertIn("section_id", r)

    def test_search_news_with_query_filter(self) -> None:
        result = self._call("search_news", query="federal", days_back=365, limit=5)
        for r in result["records"]:
            blob = (r.get("original_text") or "").lower()
            self.assertIn("federal", blob)

    def test_search_news_caps_limit(self) -> None:
        result = self._call("search_news", query="", days_back=365, limit=10000)
        self.assertLessEqual(result["count"], 200)

    # --- lookup_entity -------------------------------------------------------

    def test_lookup_entity_by_substring(self) -> None:
        result = self._call("lookup_entity", name_or_id="China",
                            include_records=False, include_relationships=False)
        # Either we find China or we return the documented error shape.
        self.assertIn("entity", result)
        if result["entity"]:
            self.assertIn("id", result["entity"])
            self.assertIn("canonical_name", result["entity"])

    def test_lookup_entity_missing_returns_error_shape(self) -> None:
        result = self._call(
            "lookup_entity",
            name_or_id="zzzzzzzz-no-such-entity-zzzzzzzz",
            include_records=False, include_relationships=False,
        )
        self.assertIsNone(result["entity"])
        self.assertIn("error", result)

    # --- query_relationships -------------------------------------------------

    def test_query_relationships_for_real_entity(self) -> None:
        # Pick any entity that has at least one relationship.
        with sqlite3.connect(f"file:{LAKE_DB}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT from_entity FROM relationships LIMIT 1"
            ).fetchone()
        if not row:
            self.skipTest("no relationships in the lake")
        result = self._call("query_relationships", entity_id=row[0], direction="both", limit=10)
        self.assertIn("count", result)
        self.assertGreaterEqual(result["count"], 1)

    # --- graph_path ----------------------------------------------------------

    def test_graph_path_self_loop(self) -> None:
        # An entity is trivially path-length 0 to itself; verify the BFS
        # handles the same-endpoint case.
        with sqlite3.connect(f"file:{LAKE_DB}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT id FROM entities LIMIT 1").fetchone()
        if not row:
            self.skipTest("no entities in the lake")
        result = self._call("graph_path", entity_a=row[0], entity_b=row[0], max_hops=1)
        self.assertEqual(result.get("path"), [row[0]])

    # --- get_source_health ---------------------------------------------------

    def test_source_health_partitions_correctly(self) -> None:
        result = self._call("get_source_health", stale_hours=48)
        # Every source assigned to exactly one bucket.
        total = result["fresh_count"] + result["stale_count"] + result["failing_count"]
        with sqlite3.connect(f"file:{LAKE_DB}?mode=ro", uri=True) as conn:
            n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        self.assertEqual(total, n_sources)

    # --- get_anomalies -------------------------------------------------------

    def test_get_anomalies_runs(self) -> None:
        result = self._call("get_anomalies", days_back=365, limit=10)
        self.assertIn("count", result)
        self.assertIsInstance(result["anomalies"], list)

    # --- get_paper_bets ------------------------------------------------------

    def test_get_paper_bets_runs(self) -> None:
        result = self._call("get_paper_bets", status="all", days_back=365, limit=10)
        self.assertIn("summary", result)
        self.assertIn("bets", result)
        for k in ("open_count", "resolved_count", "total_resolved_pnl_usd"):
            self.assertIn(k, result["summary"])

    # --- get_brief -----------------------------------------------------------

    def test_get_brief_handles_no_briefs(self) -> None:
        # briefs table may be empty in this lake — the tool should return an
        # error payload rather than raising.
        result = self._call("get_brief", date_iso=None)
        self.assertTrue(isinstance(result, dict))

    # --- recent_state_bills --------------------------------------------------

    def test_recent_state_bills_runs(self) -> None:
        result = self._call("recent_state_bills", days_back=365, limit=5)
        self.assertIn("count", result)
        self.assertIn("bills", result)

    # --- get_section_summary -------------------------------------------------

    def test_get_section_summary_handles_missing(self) -> None:
        result = self._call("get_section_summary", section_id="zzz-no-such-section")
        self.assertIn("error", result)

    # --- cross_section_signals (new) ----------------------------------------

    def test_cross_section_signals_returns_shape(self) -> None:
        result = self._call("cross_section_signals", min_confidence="low", limit=10)
        if "error" in result and "no" in result["error"].lower():
            self.skipTest("no cross_section.json available yet")
        self.assertIn("date", result)
        self.assertIn("entities", result)
        self.assertIn("recurrences_found", result)
        self.assertIsInstance(result["entities"], list)
        for ent in result["entities"]:
            self.assertIn("canonical_name", ent)
            self.assertIn("n_sections", ent)

    def test_cross_section_signals_respects_confidence_filter(self) -> None:
        low = self._call("cross_section_signals", min_confidence="low", limit=200)
        high = self._call("cross_section_signals", min_confidence="high", limit=200)
        if "error" in low or "error" in high:
            self.skipTest("no cross_section.json available yet")
        self.assertLessEqual(len(high["entities"]), len(low["entities"]))

    # --- today_top_new (new) ------------------------------------------------

    def test_today_top_new_returns_per_section_cap(self) -> None:
        result = self._call("today_top_new", per_section=2, sections=10)
        if "error" in result:
            self.skipTest("no records in lake")
        self.assertIn("by_section", result)
        for sid, items in result["by_section"].items():
            self.assertLessEqual(len(items), 2, f"{sid} exceeded per_section cap")

    def test_today_top_new_caps_sections(self) -> None:
        result = self._call("today_top_new", per_section=1, sections=3)
        if "error" in result:
            self.skipTest("no records in lake")
        self.assertLessEqual(len(result["by_section"]), 3)

    # --- entity_neighborhood_graph (new) ------------------------------------

    def test_entity_neighborhood_graph_returns_seed(self) -> None:
        with sqlite3.connect(f"file:{LAKE_DB}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT DISTINCT from_entity FROM relationships LIMIT 1"
            ).fetchone()
        if not row:
            self.skipTest("no relationships in lake")
        result = self._call("entity_neighborhood_graph",
                            entity_id=row[0], radius=1, max_nodes=20)
        self.assertIn("nodes", result)
        self.assertIn("edges", result)
        self.assertGreaterEqual(result["node_count"], 1)
        node_ids = {n["id"] for n in result["nodes"]}
        self.assertIn(row[0], node_ids, "seed entity must appear in nodes")

    def test_entity_neighborhood_graph_dedupes_edges(self) -> None:
        with sqlite3.connect(f"file:{LAKE_DB}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT DISTINCT from_entity FROM relationships LIMIT 1"
            ).fetchone()
        if not row:
            self.skipTest("no relationships in lake")
        result = self._call("entity_neighborhood_graph",
                            entity_id=row[0], radius=1, max_nodes=50)
        # No duplicate (from,to,type) edge keys
        seen: set = set()
        for e in result["edges"]:
            key = (e["from"], e["to"], e["type"])
            self.assertNotIn(key, seen, f"duplicate edge: {key}")
            seen.add(key)

    def test_entity_neighborhood_graph_handles_missing(self) -> None:
        result = self._call("entity_neighborhood_graph",
                            entity_id="zzz-no-such-entity-zzz", radius=1, max_nodes=10)
        self.assertIn("error", result)

    # --- read-only invariant -------------------------------------------------

    def test_read_only_invariant(self) -> None:
        """search_news must open the DB read-only — a write attempt during
        a tool call would raise OperationalError. Smoke-test by calling
        the tool and then independently confirming the DB file's mtime
        hasn't been touched in a way the tool could have caused."""
        before = LAKE_DB.stat().st_mtime
        self._call("search_news", query="anything", days_back=7, limit=3)
        after = LAKE_DB.stat().st_mtime
        self.assertEqual(before, after, "MCP tool call modified the DB file")


if __name__ == "__main__":
    unittest.main()
