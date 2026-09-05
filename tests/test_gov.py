"""Offline tests for GovScope (worldscope.gov.* + the gov_us Section).

These prove the system WORKS without any network: pure mappers, recency
filtering, dedup, the Section's pull() contract (success + total-failure), the
query filter, and the positions ledger roundtrip.
"""
from __future__ import annotations

from datetime import date

import pytest

from worldscope.gov import fetch
from worldscope.gov.sources import GovSource, GOV_SOURCES, sources_for_branch
from worldscope.gov import query as gq
from worldscope.gov import positions as pos

TODAY = date(2026, 6, 18)


# --------------------------- registry ------------------------------------- #
def test_registry_nonempty_and_well_formed():
    assert len(GOV_SOURCES) > 20
    branches = {s.branch for s in GOV_SOURCES}
    # every major branch/organ represented
    assert {"executive", "legislative", "judicial", "independent", "state"} <= branches
    for s in GOV_SOURCES:
        assert s.url.startswith("http")
        assert s.org and s.label
    assert len(sources_for_branch("judicial")) >= 1


# --------------------------- pure mappers --------------------------------- #
def test_map_fr_result_executive_and_presidential():
    rule = fetch.map_fr_result({
        "document_number": "2026-1", "title": "Energy Efficiency Standards",
        "type": "Rule", "publication_date": "2026-06-17",
        "html_url": "https://federalregister.gov/d/2026-1",
        "abstract": "DOE sets standards.",
        "agencies": [{"name": "Department of Energy"}],
    })
    assert rule["branch"] == "executive"
    assert rule["org"] == "Department of Energy"
    assert rule["doc_type"] == "Rule"
    assert rule["id"] == "fr-2026-1"

    eo = fetch.map_fr_result({
        "document_number": "2026-2", "title": "Executive Order on X",
        "type": "Presidential Document", "publication_date": "2026-06-17",
        "html_url": "https://federalregister.gov/d/2026-2", "abstract": "",
        "agencies": [], "president": {"name": "Jane Doe"},
    })
    assert eo["org"] == "President Jane Doe"
    assert eo["president"] == "Jane Doe"


def test_map_congress_and_courtlistener():
    b = fetch.map_congress_bill(
        {"number": "7567", "type": "hr", "title": "Farm Bill",
         "latestAction": {"actionDate": "2026-04-30", "text": "Passed House"},
         "url": "https://congress.gov/bill/x"}, 119)
    assert b["branch"] == "legislative" and b["doc_type"] == "Bill"
    assert "HR 7567" in b["title"]

    o = fetch.map_courtlistener_opinion(
        {"id": 99, "cluster": {"case_name": "A v. B", "date_filed": "2026-06-10"},
         "absolute_url": "/opinion/99/a-v-b/", "snippet": "Held: ..."})
    assert o["branch"] == "judicial"
    assert o["url"].startswith("https://www.courtlistener.com")


# --------------------------- recency + dedup ------------------------------ #
def test_within_days_and_tagging():
    src = GovSource("executive", "DOE", "https://x", "DOE News")
    items = [
        {"title": "Fresh", "url": "https://a", "date": "2026-06-17", "summary": "s"},
        {"title": "Old", "url": "https://b", "date": "2026-01-01", "summary": "s"},
        {"title": "", "url": "https://c", "date": "2026-06-18", "summary": "s"},  # dropped (no title)
    ]
    docs = fetch.tag_rss_items(src, items, days=2, today=TODAY)
    assert len(docs) == 1
    assert docs[0]["title"] == "Fresh"
    assert docs[0]["branch"] == "executive" and docs[0]["org"] == "DOE"
    assert docs[0]["doc_type"] == "Press Release"


def test_dedup_collapses_url_and_title():
    docs = [
        {"url": "https://x/1", "org": "DOE", "title": "A", "date": "2026-06-17"},
        {"url": "https://x/1?utm=1", "org": "DOE", "title": "A copy", "date": "2026-06-17"},
        {"url": "https://x/2", "org": "DOE", "title": "A", "date": "2026-06-17"},  # dup title+org
        {"url": "https://x/3", "org": "DOE", "title": "B", "date": "2026-06-17"},
    ]
    out = fetch.dedup(docs)
    titles = [d["title"] for d in out]
    assert "B" in titles
    assert len(out) == 2  # {A, B}; the url- and title-dupes removed


# --------------------------- gather_all contract -------------------------- #
def test_gather_all_partial_success(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_federal_register",
                        lambda **k: [{"id": "fr-1", "date": "2026-06-17",
                                      "title": "Rule", "url": "https://fr/1",
                                      "org": "DOE", "branch": "executive"}])
    monkeypatch.setattr(fetch, "fetch_rss_sources",
                        lambda **k: (_ for _ in ()).throw(AssertionError("should be called via kwargs")) if False else [])
    monkeypatch.setattr(fetch, "fetch_congress", lambda **k: [])
    monkeypatch.setattr(fetch, "fetch_courtlistener", lambda **k: [])
    docs = fetch.gather_all(days=2)
    assert any(d["id"] == "fr-1" for d in docs)


def test_gather_all_total_failure_raises(monkeypatch):
    def boom(**k):
        raise RuntimeError("down")
    monkeypatch.setattr(fetch, "fetch_federal_register", boom)
    # rss returns nothing AND logs an error -> counts as failure
    def rss(**k):
        if k.get("errors") is not None:
            k["errors"].append("feed down")
        return []
    monkeypatch.setattr(fetch, "fetch_rss_sources", rss)
    with pytest.raises(RuntimeError):
        fetch.gather_all(days=2, congress=False, scotus=False)


# --------------------------- the Section ---------------------------------- #
def test_gov_section_pull_and_entities(monkeypatch):
    from worldscope.sections.gov_us import GovUSSection
    sample = [{"id": "fr-1", "date": "2026-06-17", "title": "EO on Energy",
               "url": "https://fr/1", "summary": "", "branch": "executive",
               "org": "President Jane Doe", "doc_type": "Presidential Document",
               "president": "Jane Doe"}]
    monkeypatch.setattr("worldscope.sections.gov_us.gather_all", lambda **k: sample)
    sec = GovUSSection.__new__(GovUSSection)  # avoid touching the real store
    out = sec.pull()
    assert out == sample
    ents = sec.extract_entities(sample[0])
    ids = {e["id"] for e in ents}
    assert any(i.startswith("filing:gov-") for i in ids)
    assert any(i.startswith("org:gov-") for i in ids)
    assert any(i.startswith("person:pres-") for i in ids)


def test_gov_section_total_failure_raises(monkeypatch):
    from worldscope.sections.gov_us import GovUSSection
    from worldscope.sections import UpstreamHTTPError

    def boom(**k):
        raise RuntimeError("all sources failed")
    monkeypatch.setattr("worldscope.sections.gov_us.gather_all", boom)
    sec = GovUSSection.__new__(GovUSSection)
    with pytest.raises(UpstreamHTTPError):
        sec.pull()


# --------------------------- query filter --------------------------------- #
def test_query_filter():
    docs = [
        {"title": "Tariff order", "summary": "", "branch": "executive",
         "org": "USTR", "doc_type": "Rule", "date": "2026-06-17"},
        {"title": "Bridge grant", "summary": "transportation", "branch": "executive",
         "org": "Department of Transportation", "doc_type": "Notice", "date": "2026-06-15"},
        {"title": "Opinion", "summary": "", "branch": "judicial",
         "org": "SCOTUS", "doc_type": "Opinion", "date": "2026-06-10"},
    ]
    assert len(gq.filter_docs(docs, query="tariff")) == 1
    assert len(gq.filter_docs(docs, branch="judicial")) == 1
    assert len(gq.filter_docs(docs, org="transportation")) == 1
    assert len(gq.filter_docs(docs, since="2026-06-16")) == 1
    assert len(gq.filter_docs(docs, doc_type="opinion")) == 1


# --------------------------- positions ledger ----------------------------- #
VOTE_FIXTURE = {
    "congress": 119, "chamber": "House", "rollNumber": 200, "date": "2026-04-30",
    "bill": {"type": "HR", "number": "7567", "title": "Farm Bill",
             "policyArea": "Agriculture and Food"},
    "url": "https://clerk.house.gov/votes/2026200",
    "members": [
        {"name": "Thompson, Glenn", "bioguideId": "T000467", "party": "R",
         "state": "PA", "vote": "Yea"},
        {"name": "Craig, Angie", "bioguideId": "C001119", "party": "D",
         "state": "MN", "vote": "Nay"},
    ],
}


def test_positions_from_vote_mapping():
    rows = pos.positions_from_vote(VOTE_FIXTURE)
    assert len(rows) == 2
    yea = [r for r in rows if r.value == "Yea"][0]
    assert yea.stance == "support"
    assert yea.subject_id == "bill-119-hr-7567"
    assert yea.issue == "Agriculture and Food"
    nay = [r for r in rows if r.value == "Nay"][0]
    assert nay.stance == "oppose" and nay.party == "D"


def test_positions_store_roundtrip(tmp_path):
    store = tmp_path / "positions.jsonl"
    rows = pos.positions_from_vote(VOTE_FIXTURE)
    n = pos.record_positions(rows, store=store)
    assert n == 2
    # idempotent: re-recording writes nothing new
    assert pos.record_positions(rows, store=store) == 0
    loaded = pos.load_positions(store=store)
    assert len(loaded) == 2
    supporters = pos.query_positions(loaded, issue="agriculture", stance="support")
    assert len(supporters) == 1 and supporters[0]["entity_name"] == "Thompson, Glenn"
    dems = pos.query_positions(loaded, party="D")
    assert len(dems) == 1 and dems[0]["stance"] == "oppose"
