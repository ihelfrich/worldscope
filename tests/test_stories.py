"""Tests for the event-level story-clustering engine (worldscope.stories)."""
from datetime import date

from worldscope import stories as st


TODAY = date(2026, 5, 31)


def _rec(section, title, *, source=None, day="2026-05-31", rid=None, url="",
         lang="en", tier="mainstream_independent", entities=None):
    return {
        "id": rid or f"{section}-{abs(hash((section, title))) % 10**8}",
        "section_id": section,
        "source_id": source or section,
        "original_text": title,
        "title": title,
        "original_url": url,
        "original_lang": lang,
        "record_date": day,
        "tier": tier,
        "entities": entities or [],
    }


# ---- feature extraction ----------------------------------------------------

def test_entity_keys_decode_and_filter():
    rec = _rec("foreign_news", "Strike near Kharkiv", entities=["place:kharkiv", "the"])
    keys = st._entity_keys(rec)
    assert "kharkiv" in keys
    assert "the" not in keys  # pure stopword dropped


def test_title_tokens_drop_stopwords_and_shorts():
    toks = st._title_tokens(_rec("x", "The new US sanctions on Russian oil exports"))
    assert "sanctions" in toks and "russian" in toks and "oil" in toks
    assert "the" not in toks and "new" not in toks  # stopwords
    assert "us" not in toks  # stopword + too short


# ---- clustering core -------------------------------------------------------

def test_records_about_same_event_cluster_across_sources():
    recs = [
        _rec("foreign_news", "EU agrees new sanctions on Russian oil exports",
             source="reuters", entities=["place:russia", "topic:sanctions"]),
        _rec("gdelt_regions", "Brussels approves Russian oil sanctions package",
             source="ap", entities=["place:russia", "topic:sanctions"]),
        _rec("commentary", "Why the Russian oil sanctions matter for prices",
             source="substack", entities=["place:russia", "topic:sanctions"]),
        # Unrelated lone item — should not join the cluster, and being
        # single-source must not surface as a top story.
        _rec("usgs_quakes", "Magnitude 5.1 earthquake strikes off Tonga",
             source="usgs", entities=["place:tonga"]),
    ]
    stories = st.cluster_records(recs, today=TODAY)
    assert len(stories) == 1
    s = stories[0]
    assert s.n_records == 3
    assert s.n_outlets == 3
    assert s.n_sections == 3
    assert "russia" in s.top_entities


def test_single_source_item_is_not_a_top_story():
    recs = [_rec("foreign_news", "A quiet local council meeting was held today",
                 source="local")]
    assert st.cluster_records(recs, today=TODAY) == []


def test_two_shared_entities_join_without_token_overlap():
    # Different wording, but two shared entities -> same story.
    recs = [
        _rec("a", "Warsh meets Powell amid rate debate", source="reuters",
             entities=["person:warsh-kevin", "person:powell-jerome"]),
        _rec("b", "Fed leadership transition: the Powell-Warsh handover",
             source="bloomberg",
             entities=["person:warsh-kevin", "person:powell-jerome"]),
    ]
    stories = st.cluster_records(recs, today=TODAY)
    assert len(stories) == 1 and stories[0].n_outlets == 2


def test_multilingual_coverage_is_counted():
    recs = [
        _rec("foreign_news", "Russia oil sanctions tighten", source="reuters",
             lang="en", entities=["place:russia", "topic:sanctions"]),
        _rec("russian_internal", "Russia oil sanctions response escalates",
             source="tass", lang="ru", entities=["place:russia", "topic:sanctions"]),
    ]
    stories = st.cluster_records(recs, today=TODAY)
    assert stories and stories[0].n_languages == 2


def test_broader_coverage_ranks_higher():
    recs = []
    for i in range(4):
        recs.append(_rec(f"sec{i}", "Iran nuclear talks resume in Geneva",
                         source=f"src{i}", entities=["place:iran", "topic:nuclear"]))
    for i in range(2):
        recs.append(_rec(f"econ{i}", "Copper prices climb on supply worry",
                         source=f"csrc{i}", entities=["topic:copper"]))
    stories = st.cluster_records(recs, today=TODAY)
    assert stories[0].top_entities and "iran" in stories[0].top_entities
    assert stories[0].n_outlets == 4
    assert stories[0].score > stories[1].score


def test_evidence_is_one_per_source_and_diverse():
    recs = [
        _rec("a", "Gaza ceasefire talks advance in Cairo", source="reuters",
             tier="mainstream_independent", entities=["place:gaza", "topic:ceasefire"]),
        _rec("a", "Gaza ceasefire talks advance, sources say", source="reuters",
             tier="mainstream_independent", entities=["place:gaza", "topic:ceasefire"]),
        _rec("b", "Cairo Gaza ceasefire negotiations progress", source="ap",
             tier="mainstream_independent", entities=["place:gaza", "topic:ceasefire"]),
    ]
    stories = st.cluster_records(recs, today=TODAY)
    assert stories
    ev_sources = [e["source"] for e in stories[0].evidence]
    assert len(ev_sources) == len(set(ev_sources))  # deduped by source


def test_recency_decay_demotes_stale_clusters():
    fresh = [
        _rec("a", "Taiwan strait incursion reported", source="r1", day="2026-05-31",
             entities=["place:taiwan", "topic:incursion"]),
        _rec("b", "Taiwan strait incursion confirmed", source="r2", day="2026-05-31",
             entities=["place:taiwan", "topic:incursion"]),
    ]
    stale = [
        _rec("c", "Sahel coup aftermath continues", source="r3", day="2026-05-29",
             entities=["place:sahel", "topic:coup"]),
        _rec("d", "Sahel coup aftermath update", source="r4", day="2026-05-29",
             entities=["place:sahel", "topic:coup"]),
    ]
    stories = st.cluster_records(fresh + stale, today=TODAY, window_days=3)
    by_handle = {tuple(s.top_entities): s for s in stories}
    taiwan = next(s for s in stories if "taiwan" in s.top_entities)
    sahel = next(s for s in stories if "sahel" in s.top_entities)
    assert taiwan.score > sahel.score  # same breadth, fresher wins


def test_out_of_window_records_excluded():
    recs = [
        _rec("a", "Iran talks today", source="r1", day="2026-05-31",
             entities=["place:iran"]),
        _rec("b", "Iran talks today too", source="r2", day="2026-05-31",
             entities=["place:iran"]),
        _rec("c", "Iran talks long ago", source="r3", day="2026-01-01",
             entities=["place:iran"]),
    ]
    stories = st.cluster_records(recs, today=TODAY, window_days=2)
    assert stories and stories[0].n_records == 2  # January record excluded


# ---- panel + artifact ------------------------------------------------------

def test_render_panel_is_html_or_empty():
    assert st.render_stories_panel([]) == ""
    recs = [
        _rec("a", "Russia oil sanctions tighten", source="reuters",
             entities=["place:russia", "topic:sanctions"]),
        _rec("b", "Russia oil sanctions package approved", source="ap",
             entities=["place:russia", "topic:sanctions"]),
    ]
    html = st.render_stories_panel(st.cluster_records(recs, today=TODAY))
    assert "<section class='section'>" in html
    assert "Top Stories" in html


def test_write_artifact_round_trips(tmp_path):
    recs = [
        _rec("a", "Iran nuclear talks resume", source="reuters",
             entities=["place:iran", "topic:nuclear"]),
        _rec("b", "Iran nuclear negotiations restart", source="ap",
             entities=["place:iran", "topic:nuclear"]),
    ]
    stories = st.cluster_records(recs, today=TODAY)
    path = st.write_stories_artifact(TODAY, stories, out_root=tmp_path)
    assert path.exists()
    import json
    payload = json.loads(path.read_text())
    assert payload["method"] == st.METHOD
    assert payload["story_count"] == len(stories)
    assert payload["stories"][0]["n_outlets"] == 2


def test_story_ids_are_stable_for_same_handle():
    recs = [
        _rec("a", "Iran nuclear talks resume", source="reuters",
             entities=["place:iran", "topic:nuclear"]),
        _rec("b", "Iran nuclear negotiations restart", source="ap",
             entities=["place:iran", "topic:nuclear"]),
    ]
    a = st.cluster_records(recs, today=TODAY)[0].id
    b = st.cluster_records(recs, today=TODAY)[0].id
    assert a == b
