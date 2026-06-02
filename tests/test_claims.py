"""Tests for the claim graph (worldscope.claims)."""
from datetime import date

from worldscope import claims as cl
from worldscope.lake import Lake

TODAY = date(2026, 5, 31)


def _rec(section, title, *, source, entities, day="2026-05-31", rid=None):
    return {
        "id": rid or f"{source}-{abs(hash((source, title))) % 10**8}",
        "section_id": section, "source_id": source,
        "original_text": title, "title": title,
        "original_url": f"http://x/{source}", "record_date": day,
        "entities": entities,
    }


# Two distinctive shared entities → guaranteed >=2 shared keys.
ENTS = ["org:acme-corp", "place:berlin-summit"]


def test_two_records_sharing_two_keys_form_a_claim():
    recs = [
        _rec("foreign_news", "Acme Corp seals Berlin Summit deal", source="bbc", entities=ENTS),
        _rec("markets_global", "Acme Corp Berlin Summit pact moves markets", source="reuters", entities=ENTS),
    ]
    claims = cl.build_claims(recs, today=TODAY)
    assert len(claims) == 1
    c = claims[0]
    assert c.n_sources == 2 and c.n_sections == 2
    assert c.status == "multi_source"


def test_sharing_only_one_key_does_not_cluster():
    recs = [
        _rec("foreign_news", "Acme Corp in London", source="bbc", entities=["org:acme-corp"]),
        _rec("state_news", "Acme Corp in Tokyo", source="ap", entities=["org:acme-corp"]),
    ]
    # only one shared key -> not the same assertion -> no claim
    assert cl.build_claims(recs, today=TODAY) == []


def test_single_source_cluster_dropped():
    # Same outlet twice — not cross-source.
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit deal", source="bbc", entities=ENTS, rid="a"),
        _rec("foreign_news", "Acme Corp Berlin Summit pact", source="bbc", entities=ENTS, rid="b"),
    ]
    assert cl.build_claims(recs, today=TODAY) == []


def test_primary_source_confirms():
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit sanctioned", source="bbc", entities=ENTS),
        _rec("federal_register", "Acme Corp Berlin Summit OFAC action", source="fr", entities=ENTS),
    ]
    c = cl.build_claims(recs, today=TODAY)[0]
    assert c.status == "primary_confirmed"
    assert c.claim_type == "official_statement"  # official role wins the type


def test_contradiction_flips_status():
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit deal confirmed", source="bbc", entities=ENTS),
        _rec("local_news", "Acme Corp Berlin Summit deal reported", source="ap", entities=ENTS),
        _rec("foreign_news", "Acme Corp denies Berlin Summit deal", source="afp", entities=ENTS),
    ]
    c = cl.build_claims(recs, today=TODAY)[0]
    assert c.status == "contradicted"
    assert "deny" in c.contradiction_note


def test_claim_type_priority_news_beats_market():
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit strike", source="bbc", entities=ENTS),
        _rec("paper_bets", "Acme Corp Berlin Summit strike market", source="poly", entities=ENTS),
    ]
    c = cl.build_claims(recs, today=TODAY)[0]
    assert c.claim_type == "reported_fact"   # not market_signal


def test_denial_only_applies_to_reporting_roles():
    # 'rejects' in an official document is not a contradiction.
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit measure", source="bbc", entities=ENTS),
        _rec("federal_register", "Final rule rejects Acme Corp Berlin Summit petition", source="fr", entities=ENTS),
    ]
    c = cl.build_claims(recs, today=TODAY)[0]
    assert c.status != "contradicted"


def test_representative_title_used_as_claim_text():
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit deal reached", source="bbc", entities=ENTS),
        _rec("local_news", "Acme Corp Berlin Summit deal reached", source="ap", entities=ENTS),
        _rec("state_news", "short", source="x", entities=ENTS),
    ]
    c = cl.build_claims(recs, today=TODAY)[0]
    assert "deal reached" in c.claim_text  # the repeated headline, not "short"


def test_persist_and_confidence_movement(tmp_path):
    lk = Lake(db_path=tmp_path / "lake.sqlite")
    lk._ensure_open()
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit deal", source="bbc", entities=ENTS),
        _rec("markets_global", "Acme Corp Berlin Summit deal", source="reuters", entities=ENTS),
    ]
    claims = cl.build_claims(recs, today=TODAY)
    cl.persist_claims(lk, claims, today=TODAY)
    row = lk._ensure_open().execute(
        "SELECT status, confidence, confidence_prev FROM claims").fetchone()
    assert row["status"] == "multi_source"
    assert row["confidence_prev"] is None  # first persist
    # evidence rows written
    n_ev = lk._ensure_open().execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]
    assert n_ev == 2
    # re-persist with a (forced) higher confidence -> confidence_prev recorded
    claims[0].confidence = 0.9
    cl.persist_claims(lk, claims, today=date(2026, 6, 1))
    row2 = lk._ensure_open().execute(
        "SELECT confidence, confidence_prev FROM claims").fetchone()
    assert row2["confidence"] == 0.9 and row2["confidence_prev"] is not None


def test_render_panel_empty_and_nonempty():
    assert cl.render_claims_panel([]) == ""
    recs = [
        _rec("foreign_news", "Acme Corp Berlin Summit deal", source="bbc", entities=ENTS),
        _rec("local_news", "Acme Corp Berlin Summit deal", source="ap", entities=ENTS),
    ]
    html = cl.render_claims_panel(cl.build_claims(recs, today=TODAY))
    assert "Claims" in html and "multi-source" in html
