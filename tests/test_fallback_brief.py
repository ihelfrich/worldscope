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
                     secs=("foreign_news", "tech_news"))]
    reports = [_Report("foreign_news", "FRESH"), _Report("acled", "NO_KEY")]
    monkeypatch.setattr(rr, "_gather", lambda d: (claims, reports, 1, [], []))
    import worldscope.momentum as mom
    import worldscope.graph_view as gv
    monkeypatch.setattr(mom, "build_momentum", lambda d: {"days": [], "stories": []})
    monkeypatch.setattr(gv, "connections", lambda d: {"entities": []})
    monkeypatch.setattr(fb, "data_day", lambda d: d)
    monkeypatch.setattr(fb, "_gap_labels", lambda: {})
    monkeypatch.setattr(fb, "cross_domain", lambda d: [])


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


# --- selection rules ---------------------------------------------------------

class _Ev:
    def __init__(self, section, title="t", source_id=None):
        self.section, self.title, self.source_id = section, title, source_id or section


def _claim(text, evidence, status="multi_source"):
    c = _Claim(text, status=status, n=len(evidence),
               secs=tuple(sorted({e.section for e in evidence})))
    c.evidence = evidence
    return c


def test_local_only_and_roundups_and_disputed_are_not_news():
    local = _claim("Gas station suit filed", [_Ev("local_news", source_id=f"l{i}") for i in range(5)])
    roundup = _claim("HT morning brief Nov 10: Trump says X", [_Ev("foreign_news"), _Ev("tech_news")])
    disputed = _claim("EU-Mercosur deal", [_Ev("foreign_news"), _Ev("ukraine_theater")],
                      status="contradicted")
    assert fb.newsworthiness(local) is None
    assert fb.newsworthiness(disputed) is None
    assert fb.display_text(roundup) == "HT morning brief Nov 10: Trump says X"  # no better title
    assert fb.newsworthiness(roundup) is None


def test_structured_feed_clusters_are_not_key_developments():
    quakes = _claim("M5.2 near Popondetta", [_Ev("usgs_quakes"), _Ev("usgs_quakes"), _Ev("weather")])
    assert fb.newsworthiness(quakes) is None


def test_independence_beats_raw_source_count():
    syndicated = _claim("Wire story", [_Ev("local_news", source_id=f"st{i}") for i in range(6)]
                        + [_Ev("foreign_news")])
    independent = _claim("Cross-checked story", [_Ev("foreign_news"), _Ev("tech_news"),
                                                 _Ev("ukraine_theater")])
    assert fb._outlets(syndicated) == 2          # six local stations count once
    assert fb.newsworthiness(independent) > fb.newsworthiness(syndicated)


def test_untranslated_claim_shows_an_english_evidence_headline():
    c = _claim("Anthropic отозвала детали IPO", [
        _Ev("paper_bets", "Will Lightcone receive money from employees soon"),
        _Ev("tech_news", "Anthropic sets IPO price range of $70 to $78 per share")])
    assert fb.display_text(c) == "Anthropic sets IPO price range of $70 to $78 per share"


def test_domain_prefers_the_topical_section_over_general_feeds():
    c = _claim("Anthropic IPO", [_Ev("tech_news"), _Ev("foreign_news"), _Ev("russian_internal")])
    assert fb.domain_of(c) == "Technology & cyber"
    assert fb.domain_of(_claim("x", [_Ev("foreign_news")])) == "Geopolitics"
    assert fb.domain_of(_claim("x", [_Ev("local_news")])) is None


def test_html_entities_are_decoded_including_the_malformed_kind():
    assert fb._clean("Botswana&039;s president &amp; De Beers") == "Botswana's president & De Beers"


def test_momentum_and_signals_drop_generic_labels():
    from worldscope import momentum, signals
    for w in ("democratic", "florida", "oregon", "senate", "watch", "feds"):
        assert w in momentum._JUNK
    for w in ("nov", "man", "congress"):
        assert w in signals._STOPWORDS


# --- connections ---------------------------------------------------------------

class _Sig:
    def __init__(self, key, sections, titles, label=None):
        self.key, self.label = key, label or key.title()
        self.sections, self.n_sections = sections, len(sections)
        self.evidence = [{"section": s, "title": t} for s, t in zip(sections, titles)]


def _patch_signals(monkeypatch, today_sigs, base_sigs):
    from worldscope import signals as sg
    import worldscope.lake as lk

    class _Lake:
        def _ensure_open(self):
            return None

        def close(self):
            pass
    monkeypatch.setattr(lk.Lake, "open", classmethod(lambda cls, *a, **k: _Lake()))
    monkeypatch.setattr(sg, "build_signals",
                        lambda today, conn: today_sigs if today.day == 12 else base_sigs)
    monkeypatch.setattr(fb, "_country_names", lambda: {"russia"})


THREE = ["tech_news", "foreign_news", "markets"]


def test_connections_show_new_cross_domain_names_only(monkeypatch):
    nvidia = _Sig("nvidia", THREE, ["Chips: the Nvidia boss resigns", "Spy story about Nvidia chief",
                                    "Shares of Nvidia fall"], label="Nvidia")
    trump = _Sig("trump", THREE, ["The Trump order", "A Trump visit", "Markets and Trump"], "Trump")
    russia = _Sig("russia", THREE, ["a", "b", "c"], "Russia")
    power = _Sig("power", THREE, ["Power supply to improve", "Power cuts", "Power Prices Rise Again"],
                 "Power")
    _patch_signals(monkeypatch, [nvidia, trump, russia, power], [trump])
    labels = [x["label"] for x in fb.cross_domain(date(2026, 11, 12))]
    assert labels == ["Nvidia"]   # Trump: crossed last week too; Russia: place; Power: not a name


def test_named_mid_sentence_ignores_title_case_headlines():
    assert fb._named_mid_sentence("Vance", [{"title": "After the rout, Vance tries to rebuild"}])
    assert not fb._named_mid_sentence("Power", [{"title": "Russian Strikes Cut Power To Odesa"}])
    assert not fb._named_mid_sentence("Power", [{"title": "Power supply to improve in winter"}])


def test_edition_built_before_collection_says_data_as_of(stub_engines, monkeypatch):
    from datetime import timedelta
    seen = {}
    real = rr._gather
    monkeypatch.setattr(rr, "_gather", lambda d: (seen.setdefault("d", d), real(d))[1])
    text = fb.build(date(2026, 11, 13), as_of=date(2026, 11, 13) - timedelta(days=1))
    assert text.splitlines()[0] == "# Morning Brief - Friday 13 November 2026"
    assert "**Data as of Thursday 12 November.**" in text
    assert seen["d"] == date(2026, 11, 12)          # assembled from the collected day


def test_edition_after_collection_has_no_data_note(stub_engines):
    assert "Data as of" not in fb.build(date(2026, 11, 12), as_of=date(2026, 11, 12))


def test_carried_forward_sections_are_labelled_not_empty(stub_engines, monkeypatch):
    claims, reports, fresh, preds, recs = rr._gather(date(2026, 11, 12))
    reports.append(_Report("people", "EMPTY"))
    monkeypatch.setattr(rr, "_gather", lambda d: (claims, reports, fresh, preds, recs))
    monkeypatch.setattr(fb, "_gap_labels", lambda: {"people": "carried from 2026-05-25"})
    text = fb.build(date(2026, 11, 12), as_of=date(2026, 11, 12))
    assert "people (carried from 2026-05-25)" in text and "people (empty)" not in text


def test_gap_labels_come_from_the_run_report(tmp_path, monkeypatch):
    import json
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "run_report.json").write_text(json.dumps({"sections": {
        "people": {"state": "carry_forward", "source_date": "2026-05-25", "error_type": None},
        "acled": {"state": "stale_after_failure", "error_type": "UpstreamAuthError"},
        "promed": {"state": "no_data", "error_type": "FeedRetired"},
        "firms": {"state": "stale_after_failure", "error_type": "MissingCredential"},
        "markets": {"state": "fresh", "error_type": None}}}))
    monkeypatch.setattr(fb, "REPO", tmp_path)
    assert fb._gap_labels() == {"people": "carried from 2026-05-25",
                                "acled": "credential rejected",
                                "promed": "feed retired upstream", "firms": "no key"}
