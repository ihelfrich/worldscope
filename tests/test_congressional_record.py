"""Congressional Record (GovInfo CREC) harvest + MODS parsing.

This section exists because worldscope/sections/political_figures.py shipped
with a hardcoded

    "speeches": [],   # GovInfo speeches require key; left empty

which meant speech_volume (weight 0.15) and speech_topic_drift (weight 0.15)
scored exactly 0.0 for every figure on every day. 30% of the composite anomaly
score was structurally dead, and the weighted sum absorbed it silently — so the
"top 10 most anomalous figures" ranking was computed on 70% of its inputs.

The parser is a pure function over MODS XML so the whole join can be tested
without touching the network.
"""
from __future__ import annotations

from datetime import date

import pytest

from worldscope.sections import congressional_record as cr


# One relatedItem, trimmed from the real CREC-2026-08-20 MODS document.
MODS_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<mods xmlns="http://www.loc.gov/mods/v3">
  <relatedItem type="constituent">
    <part type="article">
      <extent unit="pages"><start>E793</start><end>E795</end></extent>
    </part>
    <extension>
      <searchTitle>HONORING CARLOS AND ELIZABETH MUNOZ; Congressional Record Vol. 172, No. 134</searchTitle>
      <granuleClass>EXTENSIONS</granuleClass>
      <accessId>CREC-2026-08-20-pt1-PgE793-2</accessId>
      <pagePrefix>E</pagePrefix>
      <chamber>HOUSE</chamber>
      <granuleDate>2026-08-20</granuleDate>
      <congMember bioGuideId="V000130" chamber="H" congress="119" party="D" role="SPEAKING" state="CA">
        <name type="parsed">Mr. VARGAS</name>
        <name type="authority-fnf">Juan Vargas</name>
      </congMember>
    </extension>
  </relatedItem>
</mods>
"""

MODS_TWO_MEMBERS = """<?xml version="1.0" encoding="UTF-8"?>
<mods xmlns="http://www.loc.gov/mods/v3">
  <relatedItem type="constituent">
    <part type="article">
      <extent unit="pages"><start>S1200</start><end>S1200</end></extent>
    </part>
    <extension>
      <searchTitle>NOMINATION OF JANE ROE</searchTitle>
      <granuleClass>SENATE</granuleClass>
      <accessId>CREC-2026-08-20-pt1-PgS1200</accessId>
      <chamber>SENATE</chamber>
      <granuleDate>2026-08-20</granuleDate>
      <congMember bioGuideId="A000001" chamber="S" role="SPEAKING" state="NY">
        <name type="authority-fnf">Alice Adams</name>
      </congMember>
      <congMember bioGuideId="B000002" chamber="S" role="SUBMITTING" state="TX">
        <name type="authority-fnf">Bob Brown</name>
      </congMember>
    </extension>
  </relatedItem>
</mods>
"""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_parses_one_speaking_member():
    rows = cr.parse_mods(MODS_ONE)
    assert len(rows) == 1
    r = rows[0]
    assert r["bioguide_id"] == "V000130"
    assert r["name"] == "Juan Vargas"
    assert r["date"] == "2026-08-20"
    assert r["chamber"] == "HOUSE"
    assert r["granule_class"] == "EXTENSIONS"
    assert r["access_id"] == "CREC-2026-08-20-pt1-PgE793-2"
    assert r["title"].startswith("HONORING CARLOS")
    # The "; Congressional Record Vol..." boilerplate is not part of the topic.
    assert "Congressional Record Vol" not in r["title"]


def test_page_span_drives_the_word_count_estimate():
    r = cr.parse_mods(MODS_ONE)[0]
    assert r["pages"] == 3           # E793..E795 inclusive
    assert r["word_count"] == 3 * cr.WORDS_PER_PAGE


def test_single_page_span():
    r = cr.parse_mods(MODS_TWO_MEMBERS)[0]
    assert r["pages"] == 1


def test_only_speaking_members_are_emitted():
    """SUBMITTING/etc. are not floor speech and must not inflate volume."""
    rows = cr.parse_mods(MODS_TWO_MEMBERS)
    assert [r["bioguide_id"] for r in rows] == ["A000001"]


def test_all_roles_when_requested():
    rows = cr.parse_mods(MODS_TWO_MEMBERS, roles=None)
    assert {r["bioguide_id"] for r in rows} == {"A000001", "B000002"}


def test_granule_without_member_is_dropped():
    xml = MODS_ONE.replace(
        '<congMember bioGuideId="V000130" chamber="H" congress="119" '
        'party="D" role="SPEAKING" state="CA">', "<other>"
    ).replace("</congMember>", "</other>")
    assert cr.parse_mods(xml) == []


def test_malformed_xml_raises_rather_than_returning_empty():
    """An unparseable body is a broken source, not a quiet day in Congress."""
    from worldscope.sections import UpstreamParseError
    with pytest.raises(UpstreamParseError):
        cr.parse_mods("<mods><unclosed>")


def test_url_points_at_the_public_granule():
    r = cr.parse_mods(MODS_ONE)[0]
    assert r["url"].startswith("https://www.govinfo.gov/app/details/")
    assert "CREC-2026-08-20-pt1-PgE793-2" in r["url"]


# --------------------------------------------------------------------------- #
# Page arithmetic
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("start,end,expected", [
    ("E793", "E795", 3),
    ("H5247", "H5247", 1),
    ("S1200", "S1210", 11),
    ("E1", "E1", 1),
    ("", "", 1),          # missing extent -> assume one page, never zero
    ("E795", "E793", 1),  # inverted -> clamp, don't emit a negative count
])
def test_page_count(start, end, expected):
    assert cr.page_count(start, end) == expected


# --------------------------------------------------------------------------- #
# Topic vectors (lexical, numpy-only — no sentence-transformers dependency)
# --------------------------------------------------------------------------- #

def test_topic_vectors_shape_and_determinism():
    titles = ["HONORING A CONSTITUENT", "NOMINATION OF JANE ROE", "TARIFF POLICY"]
    a = cr.topic_vectors(titles)
    b = cr.topic_vectors(titles)
    assert a.shape == (3, cr.TOPIC_DIM)
    assert (a == b).all(), "topic vectors must be deterministic across runs"


def test_topic_vectors_are_l2_normalised():
    import numpy as np
    v = cr.topic_vectors(["TARIFF POLICY DEBATE", "APPROPRIATIONS"])
    norms = np.linalg.norm(v, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-6)


def test_similar_titles_are_closer_than_unrelated_ones():
    import numpy as np
    v = cr.topic_vectors([
        "TARIFF POLICY ON STEEL IMPORTS",
        "TARIFF POLICY ON ALUMINUM IMPORTS",
        "NATIONAL PARK SERVICE APPROPRIATIONS",
    ])
    same = float(v[0] @ v[1])
    diff = float(v[0] @ v[2])
    assert same > diff


def test_empty_titles_give_zero_rows():
    v = cr.topic_vectors([])
    assert v.shape == (0, cr.TOPIC_DIM)


# --------------------------------------------------------------------------- #
# Section wiring
# --------------------------------------------------------------------------- #

def test_section_declares_govinfo_key():
    assert "GOVINFO_API_KEY" in cr.CongressionalRecordSection.optional_env


def test_section_id_is_stable():
    assert cr.CongressionalRecordSection.id == "congressional_record"


def test_api_key_falls_back_to_demo(monkeypatch):
    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    assert cr._api_key() == "DEMO_KEY"
    monkeypatch.setenv("GOVINFO_API_KEY", "real")
    assert cr._api_key() == "real"


def test_window_dates_are_inclusive_and_ordered():
    days = cr._window(date(2026, 8, 20), lookback=3)
    assert days == [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)]
