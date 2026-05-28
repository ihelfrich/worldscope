"""Tests for the XSS / injection defenses in the render layer.

Each test exercises an attacker-shaped input through the corresponding
render path and asserts that the dangerous markup did not survive
verbatim.
"""
from __future__ import annotations

import unittest
from datetime import date

from worldscope.lib.page_chrome import _json_script_safe
from worldscope.sections import (
    STATE_FRESH, Section, SectionState,
)


class TestJsonScriptSafe(unittest.TestCase):
    def test_escapes_close_script_tag(self) -> None:
        s = '{"name":"</script><img src=x onerror=alert(1)>"}'
        out = _json_script_safe(s)
        self.assertNotIn("</script>", out)
        self.assertIn("<\\/script>", out)

    def test_escapes_html_comment_opener(self) -> None:
        s = '{"x":"<!--break"}'
        out = _json_script_safe(s)
        self.assertNotIn("<!--", out)

    def test_passthrough_safe_json(self) -> None:
        s = '{"a":1,"b":"normal text"}'
        out = _json_script_safe(s)
        self.assertEqual(out, s)


class TestLegacySectionRender(unittest.TestCase):
    """Section.render_html() is the legacy weekly path. The audit caught
    it accepting unescaped item fields. These tests verify the fix."""

    class _Sec(Section):
        id = "test_xss"
        title = "Test"
        emoji = "🧪"

        def pull(self):  # pragma: no cover - state injected directly
            return []

    def _state(self, items: list[dict]) -> SectionState:
        return SectionState(
            section_id=self._Sec.id,
            title=self._Sec.title,
            emoji=self._Sec.emoji,
            state=STATE_FRESH,
            items=items,
            new=items,
            comparison_date=None,
            source_date="2026-05-28",
        )

    def test_title_with_script_tag_is_escaped(self) -> None:
        sec = self._Sec()
        items = [{
            "_id": "x1",
            "title": "<script>alert('xss')</script>Reuters",
            "url": "https://example.com/a",
            "date": "2026-05-28",
            "summary": "ok",
        }]
        html_out = sec.render_html(self._state(items))
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_javascript_url_is_neutralized(self) -> None:
        sec = self._Sec()
        items = [{
            "_id": "x2",
            "title": "Click me",
            "url": "javascript:alert(1)",
            "date": "2026-05-28",
            "summary": "ok",
        }]
        html_out = sec.render_html(self._state(items))
        # The href should not start with javascript: anymore
        self.assertNotIn("href='javascript:", html_out)
        self.assertNotIn('href="javascript:', html_out)

    def test_summary_html_is_escaped(self) -> None:
        sec = self._Sec()
        items = [{
            "_id": "x3",
            "title": "Headline",
            "url": "https://example.com",
            "date": "2026-05-28",
            "summary": '<img src=x onerror="alert(1)">',
        }]
        html_out = sec.render_html(self._state(items))
        self.assertNotIn('onerror="alert', html_out)
        self.assertIn("&lt;img", html_out)


class TestMcpPathValidation(unittest.TestCase):
    """The MCP server's get_section_summary and cross_section_signals
    used to build filesystem paths from user-supplied strings without
    validation. The fix rejects path-traversal segments and invalid
    formats outright."""

    def _load(self):
        # Probe for the mcp dep; skip cleanly if absent.
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp package not installed")
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_wmcp",
            Path(__file__).resolve().parent.parent / "mcp-server" / "worldscope_mcp.py",
        )
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_rejects_path_traversal_section_id(self) -> None:
        m = self._load()
        fn = m.get_section_summary
        fn = fn.fn if hasattr(fn, "fn") else fn
        result = fn(section_id="../../etc")
        self.assertIn("error", result)

    def test_rejects_bad_date_format(self) -> None:
        m = self._load()
        fn = m.get_section_summary
        fn = fn.fn if hasattr(fn, "fn") else fn
        result = fn(section_id="federal_register", date_iso="../../passwd")
        self.assertIn("error", result)

    def test_rejects_uppercase_section_id(self) -> None:
        m = self._load()
        fn = m.get_section_summary
        fn = fn.fn if hasattr(fn, "fn") else fn
        result = fn(section_id="FederalRegister")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
