"""The deterministic overview fallback must be clean: HTML stripped, empty
sections dropped, busiest sections first."""
import sys
import types

# overview pulls in .calendar -> feedparser, an optional dep that may be absent
# in the test env. Stub it only if it's genuinely missing.
try:  # pragma: no cover
    import feedparser  # noqa: F401
except ImportError:  # pragma: no cover
    sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))

import os
from datetime import date

from worldscope.overview import build_overview


def _deltas():
    return {
        "fed": ("U.S. Federal Action", {"all": [1] * 30, "new": []}),
        "sl": ("State Legislative Action", {"all": [1] * 140, "new": []}),
        "state_news": ("State-Level News", {"all": [1] * 276, "new": [
            {"date": "2026-06-01",
             "title": "[California] Rallies for immigrants <figure><img src=x></figure>",
             "summary": "<p>Body text</p>"},
            {"date": "2026-06-01", "title": "[Texas] Budget passes", "summary": ""},
        ]}),
        "mk": ("Markets", {"all": [1] * 20, "new": [
            {"date": "2026-06-01", "title": "S&amp;P up 1%", "summary": ""}]}),
    }


def test_fallback_strips_html_and_decodes_entities():
    os.environ.pop("ANTHROPIC_API_KEY", None)
    md = build_overview(date(2026, 6, 1), _deltas(), {}, [])
    assert "<figure" not in md and "<img" not in md
    assert "S&P up 1%" in md  # &amp; decoded


def test_fallback_drops_empty_sections_and_counts_active():
    os.environ.pop("ANTHROPIC_API_KEY", None)
    md = build_overview(date(2026, 6, 1), _deltas(), {}, [])
    # 0-new sections are not dumped
    assert "U.S. Federal Action — 0 new" not in md
    assert "(no new items today)" not in md
    # headline counts active sections out of tracked
    assert "2 active section(s) (of 4 tracked)" in md


def test_fallback_lists_top_movers_busiest_first():
    os.environ.pop("ANTHROPIC_API_KEY", None)
    md = build_overview(date(2026, 6, 1), _deltas(), {}, [])
    movers_line = [L for L in md.splitlines() if "State-Level News (2)" in L][0]
    assert movers_line.index("State-Level News (2)") < movers_line.index("Markets (1)")
