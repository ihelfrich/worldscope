"""Tests for the cross-day story-thread tracker (worldscope.story_threads).

The linker's value is that it (1) chains clusters describing the SAME situation
across days into one thread, (2) does NOT merge unrelated situations, and (3)
reads the resulting breadth trajectory as escalating / steady / cooling. These
are pinned with hand-built daily clusters — no DB, no clustering, no model.
"""
from datetime import date

from worldscope import story_threads as stt


def _story(headline, entities, outlets, *, url="", sections=3, records=10):
    return {
        "headline": headline,
        "top_entities": entities,
        "n_outlets": outlets,
        "n_sections": sections,
        "n_records": records,
        "representative_url": url,
    }


# ---------------------------------------------------------------------------
# signature hygiene
# ---------------------------------------------------------------------------

def test_clean_entity_drops_desk_and_strips_geo_prefix():
    assert stt.clean_entity("newsroom tass") is None        # source desk → drop
    assert stt.clean_entity("country russia") == "russia"   # geo prefix stripped
    assert stt.clean_entity("city atlanta") == "atlanta"
    assert stt.clean_entity("Hezbollah") == "hezbollah"
    assert stt.clean_entity("") is None


def test_signature_blends_entities_and_headline_tokens():
    sig = stt.signature(_story("Israel strikes Beirut suburbs",
                               ["country lebanon", "newsroom tass", "Hezbollah"], 5))
    assert "e:lebanon" in sig          # prefix-stripped, namespaced
    assert "e:hezbollah" in sig
    assert "e:newsroom tass" not in sig
    assert "beirut" in sig             # headline token
    assert "the" not in sig            # stopworded


# ---------------------------------------------------------------------------
# linking: same situation chains, distinct ones don't merge
# ---------------------------------------------------------------------------

def _run(daily, **kw):
    accums = stt.link_threads(daily, **kw)
    return stt.freeze_threads(accums, as_of=date.fromisoformat(daily[-1][0]), min_days=1)


def test_same_situation_chains_across_days():
    daily = [
        ("2026-06-01", [_story("Iran nuclear talks stall", ["Iran", "nuclear"], 4)]),
        ("2026-06-02", [_story("Iran nuclear deal collapses", ["Iran", "nuclear"], 7)]),
        ("2026-06-03", [_story("Iran nuclear program escalates", ["Iran", "nuclear"], 11)]),
    ]
    threads = _run(daily)
    assert len(threads) == 1
    t = threads[0]
    assert t.days_active == 3
    assert t.first_seen == "2026-06-01" and t.last_seen == "2026-06-03"
    assert [b for _, b in t.breadth_by_day] == [4, 7, 11]


def test_distinct_situations_do_not_merge():
    daily = [
        ("2026-06-01", [_story("Iran nuclear talks", ["Iran", "nuclear"], 5),
                        _story("Brazil election runoff", ["Brazil", "election"], 5)]),
        ("2026-06-02", [_story("Iran nuclear talks resume", ["Iran", "nuclear"], 6),
                        _story("Brazil election results", ["Brazil", "election"], 6)]),
    ]
    threads = _run(daily)
    labels = " ".join(t.label.lower() for t in threads)
    assert len(threads) == 2
    assert "iran" in labels and "brazil" in labels


def test_one_cluster_joins_only_one_thread_per_day():
    # two same-day clusters cannot both grab the same prior thread
    daily = [
        ("2026-06-01", [_story("Iran nuclear", ["Iran", "nuclear"], 8)]),
        ("2026-06-02", [_story("Iran nuclear A", ["Iran", "nuclear"], 5),
                        _story("Iran nuclear B", ["Iran", "nuclear"], 4)]),
    ]
    accums = stt.link_threads(daily)
    # the second day's second cluster starts its own thread rather than double-joining
    assert len(accums) == 2


def test_gap_tolerance_breaks_a_stale_thread():
    daily = [
        ("2026-06-01", [_story("Iran nuclear", ["Iran", "nuclear"], 6)]),
        # 5-day gap exceeds the default tolerance → a new thread, not a continuation
        ("2026-06-07", [_story("Iran nuclear again", ["Iran", "nuclear"], 6)]),
    ]
    accums = stt.link_threads(daily, gap_tolerance=2)
    assert len(accums) == 2


# ---------------------------------------------------------------------------
# trajectory classification
# ---------------------------------------------------------------------------

def test_direction_escalating_steady_cooling():
    assert stt._direction([2, 3, 8])[0] == stt.ESCALATING
    assert stt._direction([5, 5, 5])[0] == stt.STEADY
    assert stt._direction([10, 9, 2])[0] == stt.COOLING
    assert stt._direction([4])[0] == stt.NEW


def test_momentum_is_last_over_prior_mean():
    _, mom = stt._direction([2, 2, 8])     # prior mean 2, last 8 → 4.0×
    assert abs(mom - 4.0) < 1e-9


def test_active_today_flag_and_freeze_min_days():
    daily = [
        ("2026-06-01", [_story("Iran nuclear", ["Iran", "nuclear"], 4)]),
        ("2026-06-02", [_story("Iran nuclear deal", ["Iran", "nuclear"], 9)]),
        # a one-day blip that must be filtered out by min_days=2
        ("2026-06-02", [_story("Lone quake in Chile", ["Chile", "earthquake"], 3)]),
    ]
    accums = stt.link_threads(daily)
    threads = stt.freeze_threads(accums, as_of=date(2026, 6, 2), min_days=2)
    assert len(threads) == 1
    assert threads[0].active_today is True
    assert threads[0].direction == stt.ESCALATING


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def test_sparkline_maps_range_to_blocks():
    s = stt.sparkline([0, 5, 10])
    assert len(s) == 3
    assert s[0] == "▁" and s[-1] == "█"
    assert stt.sparkline([4, 4, 4]) == "▄▄▄"     # flat → mid band
    assert stt.sparkline([]) == ""


def test_panel_empty_when_nothing_active_today():
    daily = [
        ("2026-06-01", [_story("Iran nuclear", ["Iran", "nuclear"], 4)]),
        ("2026-06-02", [_story("Iran nuclear deal", ["Iran", "nuclear"], 9)]),
    ]
    accums = stt.link_threads(daily)
    threads = stt.freeze_threads(accums, as_of=date(2026, 6, 9))  # long after last_seen
    assert all(not t.active_today for t in threads)
    assert stt.render_threads_panel(threads) == ""


def test_panel_and_page_render_active_thread_and_escape():
    evil = "<script>x</script>"
    daily = [
        ("2026-06-01", [_story(f"Iran {evil}", ["Iran", "nuclear"], 4)]),
        ("2026-06-02", [_story("Iran nuclear deal escalates", ["Iran", "nuclear"], 9,
                               url="http://example.test/x")]),
    ]
    threads = _run(daily)  # as_of = 2026-06-02, min_days=1
    panel = stt.render_threads_panel(threads)
    assert "Developing Situations" in panel
    assert "Iran" in panel
    body = stt.build_body(threads, today=date(2026, 6, 2))
    assert "Active today" in body
    assert "<script>x</script>" not in body  # escaped everywhere
