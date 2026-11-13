"""Claims must cluster on what records say, not on where they came from."""
from datetime import date

from worldscope import claims as cl
from worldscope import signals as sg

D = date(2026, 11, 12)


def _rec(i, title, section, source, ents):
    return {"id": f"r{i}", "title": title, "section_id": section, "source_id": source,
            "record_date": D.isoformat(), "entities": ents}


def test_provenance_tags_are_not_keys():
    keys = sg.record_keys({"title": "Governor Newsom proclaims Veterans Day",
                           "entities": ["org:newsroom-ca-governor", "place:state-california",
                                        "place:country-ukraine", "org:source-liveuamap",
                                        "person:gavin-newsom"]})
    assert not any(k.startswith(("newsroom", "state ", "country ", "source ")) for k in keys)
    assert "gavin newsom" in keys


def test_watch_area_label_is_not_mined():
    keys = sg.record_keys({"title": "[Israel-Iran-Hezbollah axis · keywords] Hamas hit in Gaza",
                           "entities": ["mention:israel-iran-hezbollah"]})
    assert keys == {"hamas", "gaza"}


def test_one_newsroom_unrelated_releases_do_not_form_a_claim():
    prov = ["org:newsroom-ca-governor", "place:state-california"]
    recs = [_rec(1, "Governor proclaims Veterans Day", "state_news", "ca-gov", prov),
            _rec(2, "Funding for psychedelic treatment research", "state_news", "ca-gov2", prov),
            _rec(3, "Emergency proclamations for Humboldt recovery", "state_news", "ca-gov3", prov)]
    assert cl.build_claims(recs, today=D) == []


def test_same_story_across_outlets_still_clusters():
    recs = [_rec(1, "Kushner and Witkoff land in Moscow for Putin meeting", "foreign_news", "bbc",
                 ["org:newsroom-bbc"]),
            _rec(2, "Witkoff, Kushner meet Putin in Moscow", "ukraine_theater", "liveuamap",
                 ["org:source-liveuamap", "place:country-ukraine"])]
    claims = cl.build_claims(recs, today=D)
    assert len(claims) == 1 and claims[0].n_sections == 2
