"""Fallback brief contract + the sentence truncator it (and the board) use."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import render_reasoned as rr  # noqa: E402
import fallback_brief as fb  # noqa: E402


@pytest.mark.parametrize("text,expected", [
    ("[St. Louis] Family files suit after shooting", "[St. Louis] Family files suit after shooting"),
    ("Dr. Smith testifies. The hearing resumes Friday.", "Dr. Smith testifies"),
    ("Gen. Milley warns of escalation. Pentagon responds.", "Gen. Milley warns of escalation"),
    ("Envoys arrive in Moscow. Talks resume Friday.", "Envoys arrive in Moscow"),
    ("No sentence break here", "No sentence break here"),
])
def test_oneline_keeps_abbreviations_whole(text, expected):
    assert rr._oneline(text, 200) == expected


def test_oneline_still_caps_length():
    out = rr._oneline("x" * 300, 50)
    assert out.endswith("…") and len(out) == 51


class _Claim:
    def __init__(self, text, status="multi_source", n=4, secs=("foreign_news", "ukraine_theater")):
        self.claim_text, self.status, self.confidence = text, status, 0.8
        self.n_sources, self.n_sections = n, len(secs)
        self.topics, self.contradiction_note = list(secs), ""


class _Report:
    def __init__(self, sid, status):
        self.section_id, self.status = sid, status


@pytest.fixture
def stub_engines(monkeypatch):
    claims = [_Claim("US envoys arrive in Moscow for talks", n=9),
              _Claim("[St. Louis] Family files wrongful death suit", n=3,
                     secs=("local_news", "state_news"))]
    reports = [_Report("foreign_news", "FRESH"), _Report("acled", "NO_KEY")]
    monkeypatch.setattr(rr, "_gather", lambda d: (claims, reports, 1, [], []))
    import worldscope.momentum as mom
    import worldscope.graph_view as gv
    monkeypatch.setattr(mom, "build_momentum", lambda d: {"days": [], "stories": []})
    monkeypatch.setattr(gv, "connections", lambda d: {"entities": []})


def test_fallback_follows_brief_conventions_and_is_marked(stub_engines):
    text = fb.build(date(2026, 11, 12))
    first = text.splitlines()[0]
    assert first == "# Morning Brief - Thursday 12 November 2026"  # picker reads H1
    assert fb.MARKER in text and "Automated edition" in text
    assert "## Headline" in text and "US envoys arrive in Moscow" in text
    assert "[St. Louis] Family files wrongful death suit" in text  # not "[St"
    assert "acled (no_key)" in text and "1/2 sections fresh" in text


def test_fallback_never_overwrites_a_composed_brief(tmp_path, stub_engines):
    out = tmp_path / "2026-11-12.md"
    out.write_text("# Morning Brief - composed by the desk officer\n", encoding="utf-8")
    assert fb.main(["--date", "2026-11-12", "--out", str(out)]) == 0
    assert "composed by the desk officer" in out.read_text(encoding="utf-8")


def test_fallback_refreshes_its_own_earlier_edition(tmp_path, stub_engines):
    out = tmp_path / "2026-11-12.md"
    out.write_text(f"# old\n\n{fb.MARKER}\nstale\n", encoding="utf-8")
    assert fb.is_fallback(out)
    assert fb.main(["--date", "2026-11-12", "--out", str(out)]) == 0
    assert "US envoys arrive in Moscow" in out.read_text(encoding="utf-8")
