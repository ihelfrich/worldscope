"""Tests for the temporal lead/lag early-warning engine (worldscope.foresight).

The engine's whole value rests on two opposite guarantees, and these tests pin
both down with synthetic series (no DB, no model):

  1. When a genuine lead/lag pattern is planted in a long-enough history, the
     miner recovers it AND certifies it as *predictive* (clears the
     Bonferroni-corrected binomial bar).
  2. When the series are pure noise / short, NOTHING is certified predictive —
     the guard against mining coincidence out of a thin lake.

Plus the pure primitives (surge/elevation/binomial), the today-only warning
gate, the lake-prediction shape, and self-grading.
"""
from datetime import date, timedelta

from worldscope import foresight as fs


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def test_surge_requires_threshold_and_rise_over_baseline():
    # steady at 2 → never a surge (no rise over its own baseline)
    assert fs.surge_indices([2, 2, 2, 2, 2], surge_min=2) == []
    # a clear jump from a quiet baseline IS a surge
    idx = fs.surge_indices([0, 0, 0, 3, 0], surge_min=2)
    assert idx == [3]
    # below threshold never surges however much it rises
    assert fs.surge_indices([0, 0, 1, 1], surge_min=2) == []


def test_elevated_mask_uses_threshold():
    assert fs.elevated_mask([0, 1, 2, 3], elev_min=2) == [False, False, True, True]


def test_binom_sf_is_a_valid_one_sided_tail():
    # certainty bounds
    assert fs.binom_sf(0, 5, 0.3) == 1.0
    assert fs.binom_sf(6, 5, 0.3) == 0.0  # impossible: more hits than trials
    # all-hits against a low base rate is very unlikely
    assert fs.binom_sf(5, 5, 0.2) == 0.2 ** 5
    # monotal: more hits → smaller (or equal) tail
    p_hi = fs.binom_sf(4, 6, 0.3)
    p_lo = fs.binom_sf(2, 6, 0.3)
    assert p_hi <= p_lo


def test_shares_token_blocks_self_and_overlap():
    assert fs._shares_token("iran", "iran")
    assert fs._shares_token("iran nuclear", "iran")        # substring/token
    assert not fs._shares_token("iran", "lebanon")


# ---------------------------------------------------------------------------
# series construction from records
# ---------------------------------------------------------------------------

def _rec(section, title, day):
    return {
        "id": f"{section}-{title}-{day}",
        "section_id": section,
        "original_text": title,
        "title": title,
        "record_date": day,
        "entities": [],
    }


def test_intensity_counts_distinct_sections_per_day_and_prunes_sparse():
    today = date(2026, 6, 21)
    recs = [
        # 'gondor' referenced by two distinct sections on the same day → intensity 2
        _rec("foreign_news", "Gondor mobilizes", "2026-06-20"),
        _rec("conflict", "Gondor border clash", "2026-06-20"),
        _rec("foreign_news", "Gondor again", "2026-06-19"),
        # 'mordor' appears once only → too sparse, pruned by min_key_days
        _rec("foreign_news", "Mordor stirs", "2026-06-18"),
    ]
    axis = fs._date_axis(today, 5)
    key_sections = fs.daily_key_sections(recs, today=today, window_days=5)
    intensity = fs.build_intensity(key_sections, axis, min_key_days=2)
    assert "gondor" in intensity
    assert "mordor" not in intensity                 # only 1 active day
    # the 06-20 cell holds 2 distinct sections
    i_2020 = axis.index("2026-06-20")
    assert intensity["gondor"][i_2020] == 2


# ---------------------------------------------------------------------------
# mining: recovers a planted rule, rejects noise
# ---------------------------------------------------------------------------

def _axis(n):
    start = date(2026, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_mining_recovers_a_strong_planted_lead_lag_rule():
    # 80-day history. LEADER surges every 7 days; FOLLOWER is elevated exactly
    # 2 days after every leader surge and almost never otherwise → a real,
    # significant lag-2 rule that must survive the corrected bar.
    n = 80
    axis = _axis(n)
    leader = [0] * n
    follower = [0] * n
    for t in range(3, n - 3, 7):
        leader[t] = 3                  # a surge (>= surge_min, rises over baseline)
        follower[t + 2] = 3            # elevated 2 days later
    intensity = {"leader": leader, "follower": follower}
    rules = fs.mine_lead_lag(intensity, axis)
    pred = [r for r in rules if r.predictive]
    assert pred, "a strong, well-supported lag-2 rule should be certified predictive"
    top = pred[0]
    assert top.leader == "leader" and top.follower == "follower"
    assert top.lag == 2
    assert top.precision == 1.0
    assert top.p_value < fs.DEFAULT_ALPHA


def test_pure_noise_yields_no_predictive_rules():
    # Genuinely unstructured (seeded-random) series with no engineered
    # precedence. Nothing should clear the Bonferroni-corrected significance bar.
    import random
    rng = random.Random(1234)
    n = 60
    axis = _axis(n)
    intensity = {
        f"key{k}": [3 if rng.random() < 0.2 else 0 for _ in range(n)]
        for k in range(8)
    }
    rules = fs.mine_lead_lag(intensity, axis)
    assert not [r for r in rules if r.predictive]


def test_same_subject_pairs_are_never_mined():
    n = 40
    axis = _axis(n)
    s = [3 if i % 6 == 0 else 0 for i in range(n)]
    # 'iran' and 'iran nuclear' share a token → must not form a rule
    rules = fs.mine_lead_lag({"iran": s, "iran nuclear": s}, axis)
    assert all(not (r.leader in r.follower or r.follower in r.leader) for r in rules)


# ---------------------------------------------------------------------------
# warnings: today-only trigger, follower must be quiet
# ---------------------------------------------------------------------------

def _planted_intensity(n, lag=2):
    leader = [0] * n
    follower = [0] * n
    for t in range(3, n - 3, 7):
        leader[t] = 3
        follower[t + lag] = 3
    return {"leader": leader, "follower": follower}


def test_warning_fires_only_when_leader_surges_today_and_follower_quiet():
    n = 80
    today = date(2026, 1, 1) + timedelta(days=n - 1)
    axis = _axis(n)
    intensity = _planted_intensity(n, lag=2)
    # make the LAST day a fresh leader surge with the follower still quiet
    intensity["leader"][-1] = 3
    intensity["follower"][-1] = 0
    rules = fs.mine_lead_lag(intensity, axis)
    warnings = fs.todays_warnings(rules, intensity, axis, today=today)
    assert any(w.leader == "leader" and w.follower == "follower" for w in warnings)
    w = warnings[0]
    assert w.fired_iso == today.isoformat()
    assert w.target_iso == (today + timedelta(days=w.lag)).isoformat()
    assert fs.CONF_FLOOR <= w.confidence <= fs.CONF_CEIL


def test_no_warning_when_follower_already_elevated():
    n = 80
    today = date(2026, 1, 1) + timedelta(days=n - 1)
    axis = _axis(n)
    intensity = _planted_intensity(n, lag=2)
    intensity["leader"][-1] = 3
    intensity["follower"][-1] = 3      # already elevated → nothing to warn about
    rules = fs.mine_lead_lag(intensity, axis)
    warnings = fs.todays_warnings(rules, intensity, axis, today=today)
    assert not any(w.follower == "follower" for w in warnings)


# ---------------------------------------------------------------------------
# predictions + self-grading
# ---------------------------------------------------------------------------

def test_warnings_to_predictions_shape_and_method():
    today = date(2026, 6, 21)
    rule = fs.LeadLag(
        leader="sanctions", follower="ruble", lag=3, support=6, hits=6,
        precision=1.0, base_rate=0.2, lift=5.0, p_value=1e-5, score=9.9,
        last_lead_iso=today.isoformat(), predictive=True,
    )
    w = fs.EarlyWarning(
        leader="sanctions", follower="ruble", lag=3, fired_iso=today.isoformat(),
        target_iso=(today + timedelta(days=3)).isoformat(), confidence=0.7, rule=rule)
    preds = fs.warnings_to_predictions([w], today=today)
    assert len(preds) == 1
    p = preds[0]
    assert p["method"] == fs.METHOD
    assert p["predicted_outcome"] == "YES"
    assert "follower 'ruble'" in p["resolution_criteria"]
    assert p["target_date"] == (today + timedelta(days=3)).isoformat()
    # the follower key must be recoverable from the criteria for grading
    assert fs._recover_follower(p["resolution_criteria"]) == "ruble"


def test_grade_follower_outcome_yes_and_no():
    made = date(2026, 6, 1)
    target = date(2026, 6, 4)
    # follower elevated (2 distinct sections) on a day inside the window → YES
    yes_recs = [
        _rec("foreign_news", "Ruble slides", "2026-06-02"),
        _rec("markets", "Ruble under pressure", "2026-06-02"),
    ]
    assert fs.grade_follower_outcome("ruble", yes_recs, made=made, target=target) == "YES"
    # only one section → not elevated → NO
    no_recs = [_rec("foreign_news", "Ruble slides", "2026-06-02")]
    assert fs.grade_follower_outcome("ruble", no_recs, made=made, target=target) == "NO"


def test_grade_respects_window_bounds():
    made = date(2026, 6, 1)
    target = date(2026, 6, 4)
    # both references land OUTSIDE (made, target] → NO even though elevated
    recs = [
        _rec("foreign_news", "Ruble slides", "2026-06-10"),
        _rec("markets", "Ruble under pressure", "2026-06-10"),
    ]
    assert fs.grade_follower_outcome("ruble", recs, made=made, target=target) == "NO"


# ---------------------------------------------------------------------------
# panel rendering is defensive
# ---------------------------------------------------------------------------

def test_panel_empty_when_nothing_predictive_or_warned():
    # tentative-only rules and no warnings → no panel (honest silence)
    rule = fs.LeadLag(
        leader="a", follower="b", lag=1, support=2, hits=2, precision=1.0,
        base_rate=0.4, lift=2.5, p_value=0.16, score=1.0,
        last_lead_iso="2026-06-21", predictive=False)
    assert fs.render_foresight_panel([rule], []) == ""


def test_panel_renders_predictive_rules():
    rule = fs.LeadLag(
        leader="sanctions", follower="ruble", lag=3, support=8, hits=8,
        precision=1.0, base_rate=0.15, lift=6.0, p_value=1e-7, score=12.0,
        last_lead_iso="2026-06-21", predictive=True)
    html = fs.render_foresight_panel([rule], [])
    assert "Foresight" in html
    assert "sanctions" in html and "ruble" in html
