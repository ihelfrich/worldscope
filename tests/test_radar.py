"""Tests for the research radar (worldscope.radar): developments, source
credibility, candidate-source discovery, and dataset building/exploration."""
from datetime import date, timedelta

from worldscope import radar as rd


def _rec(section, title, day="2026-05-31", rid=None, url="", entities=None,
         source=None):
    return {
        "id": rid or f"{section}-{abs(hash((section, title, day))) % 10**8}",
        "section_id": section,
        "source_id": source or f"{section}_src",
        "original_text": title,
        "title": title,
        "original_url": url,
        "record_date": day,
        "entities": entities or [],
    }


TODAY = date(2026, 5, 31)


# ---- aggregation -----------------------------------------------------------

def test_aggregate_counts_by_day_and_sections():
    recs = [
        _rec("markets", "Iran tensions rise", day="2026-05-31"),
        _rec("foreign", "Iran tensions rise", day="2026-05-31"),
        _rec("markets", "Iran tensions rise", day="2026-05-30"),
    ]
    agg = rd.aggregate_keys(recs, today=TODAY, days=14)
    assert "iran" in agg
    a = agg["iran"]
    assert a.counts_by_day["2026-05-31"] == 2
    assert a.counts_by_day["2026-05-30"] == 1
    assert a.today_sections == {"markets", "foreign"}


def test_aggregate_drops_noise_and_out_of_window():
    recs = [
        _rec("markets", "[feed error] httperror", day="2026-05-31"),
        _rec("markets", "Iran tensions", day="2026-01-01"),  # out of window
    ]
    agg = rd.aggregate_keys(recs, today=TODAY, days=14)
    assert "iran" not in agg


# ---- developments ----------------------------------------------------------

def test_novel_key_breaking_in_across_sections():
    # No prior mention anywhere in the window, three sections today → novel.
    recs = [
        _rec("markets", "Zephyr Corp halts trading", day="2026-05-31"),
        _rec("foreign", "Zephyr Corp halts trading", day="2026-05-31"),
        _rec("congress", "Zephyr Corp halts trading", day="2026-05-31"),
    ]
    agg = rd.aggregate_keys(recs, today=TODAY, days=14)
    devs = rd.detect_developments(agg, today=TODAY, days=14)
    zephyr = [d for d in devs if d.key == "zephyr corp"]
    assert zephyr and zephyr[0].category == "novel"
    assert zephyr[0].n_sections == 3


def test_surge_against_baseline():
    recs = []
    # quiet baseline: one mention/day for 10 prior days in one section
    for i in range(1, 11):
        d = (TODAY - timedelta(days=i)).isoformat()
        recs.append(_rec("markets", "Acme Corp routine note", day=d))
    # loud today across several sections
    for sec in ("markets", "foreign", "congress", "sanctions", "people"):
        recs.append(_rec(sec, "Acme Corp routine note", day="2026-05-31"))
    agg = rd.aggregate_keys(recs, today=TODAY, days=14)
    devs = rd.detect_developments(agg, today=TODAY, days=14)
    acme = [d for d in devs if d.key == "acme corp"]
    assert acme and acme[0].category == "surge"
    assert acme[0].today_count == 5
    assert acme[0].z_score >= rd.SURGE_Z


def test_steady_key_is_not_flagged():
    recs = []
    for i in range(0, 11):
        d = (TODAY - timedelta(days=i)).isoformat()
        recs.append(_rec("markets", "Steady Corp daily filing", day=d))
    agg = rd.aggregate_keys(recs, today=TODAY, days=14)
    devs = rd.detect_developments(agg, today=TODAY, days=14)
    assert not [d for d in devs if d.key == "steady corp"]


def test_development_id_is_stable_and_category_specific():
    a = rd._development_id("iran", "surge", TODAY)
    b = rd._development_id("iran", "surge", TODAY)
    c = rd._development_id("iran", "novel", TODAY)
    assert a == b and a != c


# ---- source credibility ----------------------------------------------------

def test_corroborated_source_scores_higher_than_isolated():
    recs = [
        # "alpha" is echoed across two sections → corroborated
        _rec("markets", "Alpha event", source="good_src"),
        _rec("foreign", "Alpha event", source="other_src"),
        # "lonely" only ever appears in one section via one source
        _rec("blog", "Lonely unverified rumor", source="bad_src"),
    ]
    creds = {c.source_id: c for c in rd.score_sources(recs)}
    assert creds["good_src"].corroboration_rate == 1.0
    assert creds["bad_src"].corroboration_rate == 0.0
    assert creds["good_src"].score > creds["bad_src"].score


def test_reliability_penalizes_failing_sources():
    recs = [
        _rec("markets", "Alpha event", source="src"),
        _rec("foreign", "Alpha event", source="src2"),
    ]
    healthy = rd.score_sources(recs, health_by_source={"src": 0})
    failing = rd.score_sources(recs, health_by_source={"src": 6})
    h = {c.source_id: c for c in healthy}["src"]
    f = {c.source_id: c for c in failing}["src"]
    assert f.score < h.score
    assert f.reliability < h.reliability


def test_tier_prior_lifts_unechoed_primary_source():
    # A primary-document feed whose niche content is never echoed elsewhere
    # should not be graded as harshly as an unknown-tier source with the same
    # (zero) corroboration — authority compensates for lack of echo.
    recs = [_rec("cisa_kev", f"CVE-2026-{i:04d} added", source="cisa")
            for i in range(8)]
    primary = {c.source_id: c for c in rd.score_sources(
        recs, tier_by_source={"cisa": "primary_document"})}["cisa"]
    unknown = {c.source_id: c for c in rd.score_sources(recs)}["cisa"]
    assert primary.corroboration_rate == 0.0 == unknown.corroboration_rate
    assert primary.tier_prior == 1.0
    assert primary.score > unknown.score


def test_low_record_count_flagged_low_confidence():
    recs = [_rec("markets", "Alpha event", source="thin")]
    c = {x.source_id: x for x in rd.score_sources(recs)}["thin"]
    assert c.low_confidence and c.grade == "n/a"


def test_grade_thresholds():
    assert rd._grade(0.9) == "A"
    assert rd._grade(0.72) == "B"
    assert rd._grade(0.3) == "F"


# ---- candidate source discovery --------------------------------------------

def test_discover_candidate_sources_ranks_recurring_domains():
    recs = [
        _rec("foreign", "Story A", url="https://kyivindependent.com/a"),
        _rec("markets", "Story B", url="https://kyivindependent.com/b"),
        _rec("congress", "Story C", url="https://kyivindependent.com/c"),
        _rec("foreign", "Aggregated", url="https://news.google.com/x"),  # filtered
        _rec("foreign", "Known", url="https://federalregister.gov/y"),   # known
    ]
    cands = rd.discover_candidate_sources(
        recs, known_hosts={"federalregister.gov"}, min_mentions=3)
    hosts = [c["host"] for c in cands]
    assert "kyivindependent.com" in hosts
    assert "news.google.com" not in hosts       # aggregator filtered
    assert "federalregister.gov" not in hosts    # already ingested


# ---- dataset builder + exploration -----------------------------------------

def test_build_dataset_matches_terms_and_window():
    recs = [
        _rec("markets", "Iran sanctions widen", day="2026-05-30"),
        _rec("foreign", "Iran talks stall", day="2026-05-31"),
        _rec("markets", "Unrelated weather note", day="2026-05-31"),
        _rec("markets", "Iran old news", day="2026-01-01"),  # out of window
    ]
    rows = rd.build_dataset(recs, terms=["iran"], today=TODAY, days=30)
    assert len(rows) == 2
    assert {r["date"] for r in rows} == {"2026-05-30", "2026-05-31"}
    assert all("iran" in r["title"].lower() or r["key_matched"] == "iran" for r in rows)
    # sorted by date ascending
    assert rows[0]["date"] <= rows[1]["date"]


def test_build_dataset_word_boundary_avoids_substring_false_positive():
    # 'iran' must not match the substring inside 'Tirante'.
    recs = [
        _rec("forecasts", "ATP: Fokina vs Thiago Tirante", day="2026-05-31"),
        _rec("foreign", "Iranian officials meet", day="2026-05-31"),
    ]
    rows = rd.build_dataset(recs, terms=["iran"], today=TODAY, days=30)
    titles = [r["title"] for r in rows]
    assert any("Iranian" in t for t in titles)
    assert not any("Tirante" in t for t in titles)


def test_explore_dataset_reports_trend_and_spread():
    rows = []
    # fading: heavy early, light late
    for d, n in (("2026-05-20", 5), ("2026-05-21", 4), ("2026-05-30", 1), ("2026-05-31", 1)):
        for i in range(n):
            rows.append({"date": d, "section": "markets", "source": "s",
                         "key_matched": "iran", "title": "t", "url": "", "n_entities": 0})
    stats = rd.explore_dataset(rows)
    assert stats["n_records"] == 11
    assert stats["date_range"] == ["2026-05-20", "2026-05-31"]
    assert stats["trend"] == "fading"
    assert stats["top_sections"][0][0] == "markets"


def test_explore_empty_dataset():
    assert rd.explore_dataset([])["n_records"] == 0


def test_export_dataset_writes_csv_and_note(tmp_path):
    rows = rd.build_dataset(
        [_rec("markets", "Iran sanctions widen", day="2026-05-31")],
        terms=["iran"], today=TODAY, days=30)
    stats = rd.explore_dataset(rows)
    paths = rd.export_dataset(rows, stats, slug="iran", today=TODAY, out_root=tmp_path)
    assert paths["csv"].endswith("data.csv")
    csv_text = (tmp_path / "iran" / "2026-05-31" / "data.csv").read_text()
    assert "date,section,source" in csv_text
    assert "iran" in csv_text.lower()
    assert (tmp_path / "iran" / "2026-05-31" / "exploration.md").exists()


def test_slugify():
    assert rd.slugify("Iran / Hormuz tensions!") == "iran-hormuz-tensions"
    assert rd.slugify("") == "dataset"


# ---- rendering -------------------------------------------------------------

def test_render_radar_panel_html():
    devs = [rd.Development(
        key="iran", label="Iran", category="surge", today_count=8,
        baseline_mean=2.0, z_score=3.2, n_sections=4,
        sections=["foreign", "markets", "congress", "sanctions"],
        description="Iran spiked.", evidence=[{"id": "x", "title": "t", "url": "http://e"}])]
    creds = [rd.SourceCredibility("bad", 20, 0.1, 0, 1.0, 0.32, "F", False)]
    html = rd.render_radar_panel(devs, creds)
    assert "Research radar" in html
    assert "Iran" in html
    assert "bad (F)" in html  # low-credibility footnote


def test_render_radar_panel_empty():
    assert rd.render_radar_panel([], []) == ""
