"""Multiple-testing control for a strategy search.

"Update the approach every day until we find a method that beats the market"
is, stated literally, a specification search. Nothing in worldscope currently
guards against it: with ~40 sections, many keys, and daily re-fitting, the
effective number of hypotheses per year is large enough that some rule will
show an excellent in-sample record by chance alone.

Three standard corrections, implemented numpy-only:

  Deflated Sharpe Ratio   Bailey & Lopez de Prado (2014). The probability the
                          observed Sharpe exceeds what the best of N trials
                          would produce under a null of zero true skill.
  PBO via CSCV            Bailey, Borwein, Lopez de Prado & Zhu (2015). The
                          probability the in-sample-best configuration
                          underperforms the median out of sample.
  Hansen's SPA            Hansen (2005). Tests whether the best of k models
                          genuinely beats a benchmark, correcting for the fact
                          that the best of k was selected by looking.

Every test here pairs a positive case with a deliberately-broken baseline, so
a test that always passes would be visible as such.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from worldscope.scoring import multiple_testing as mt


RNG = lambda seed: np.random.default_rng(seed)


# --------------------------------------------------------------------------- #
# Sharpe and PSR
# --------------------------------------------------------------------------- #

def test_sharpe_of_a_known_series():
    r = np.array([0.01, 0.02, 0.03, 0.04])
    expected = r.mean() / r.std(ddof=1)
    assert mt.sharpe_ratio(r) == pytest.approx(expected)


def test_sharpe_of_a_constant_series_is_undefined():
    assert mt.sharpe_ratio(np.array([0.01, 0.01, 0.01])) is None


def test_sharpe_needs_at_least_two_observations():
    assert mt.sharpe_ratio(np.array([0.01])) is None


def test_psr_rises_with_track_record_length():
    """The same Sharpe observed over more periods is stronger evidence."""
    short = mt.probabilistic_sharpe_ratio(
        sharpe=0.15, n_obs=30, skew=0.0, kurtosis=3.0, sr_benchmark=0.0)
    long = mt.probabilistic_sharpe_ratio(
        sharpe=0.15, n_obs=3000, skew=0.0, kurtosis=3.0, sr_benchmark=0.0)
    assert 0.0 < short < long < 1.0


def test_psr_at_the_benchmark_is_one_half():
    p = mt.probabilistic_sharpe_ratio(
        sharpe=0.10, n_obs=100, skew=0.0, kurtosis=3.0, sr_benchmark=0.10)
    assert p == pytest.approx(0.5, abs=1e-9)


def test_psr_penalises_negative_skew_and_fat_tails():
    base = mt.probabilistic_sharpe_ratio(
        sharpe=0.2, n_obs=250, skew=0.0, kurtosis=3.0, sr_benchmark=0.0)
    skewed = mt.probabilistic_sharpe_ratio(
        sharpe=0.2, n_obs=250, skew=-1.5, kurtosis=3.0, sr_benchmark=0.0)
    fat = mt.probabilistic_sharpe_ratio(
        sharpe=0.2, n_obs=250, skew=0.0, kurtosis=9.0, sr_benchmark=0.0)
    assert skewed < base, "negative skew must reduce confidence"
    assert fat < base, "fat tails must reduce confidence"


# --------------------------------------------------------------------------- #
# Expected maximum Sharpe under the null
# --------------------------------------------------------------------------- #

def test_expected_max_sharpe_grows_with_the_number_of_trials():
    a = mt.expected_max_sharpe(n_trials=10, var_sharpe=0.01)
    b = mt.expected_max_sharpe(n_trials=1000, var_sharpe=0.01)
    assert 0 < a < b


def test_expected_max_sharpe_matches_the_order_statistic_by_simulation():
    """The Bailey-Lopez de Prado approximation against brute-force sampling.

    E[max of N standard normals] * sd, checked to within Monte Carlo error.
    """
    n_trials, var = 50, 0.04
    approx = mt.expected_max_sharpe(n_trials=n_trials, var_sharpe=var)
    draws = RNG(7).normal(0.0, math.sqrt(var), size=(20000, n_trials))
    empirical = draws.max(axis=1).mean()
    assert approx == pytest.approx(empirical, rel=0.05)


def test_a_single_trial_still_returns_a_finite_threshold():
    assert mt.expected_max_sharpe(n_trials=1, var_sharpe=0.01) >= 0.0


# --------------------------------------------------------------------------- #
# Deflated Sharpe Ratio
# --------------------------------------------------------------------------- #

def test_dsr_of_pure_noise_searched_hard_is_not_significant():
    """The whole point. Take the best of 500 noise strategies; the naive
    Sharpe looks good, the deflated one does not."""
    rng = RNG(11)
    n_trials, T = 500, 250
    trials = rng.normal(0.0, 0.01, size=(T, n_trials))
    sharpes = np.array([mt.sharpe_ratio(trials[:, i]) for i in range(n_trials)])
    best = int(np.argmax(sharpes))

    naive = sharpes[best]
    assert naive > 0.1, "the best of 500 noise runs should look good naively"

    dsr = mt.deflated_sharpe_ratio(trials[:, best], n_trials=n_trials,
                                   var_sharpe=float(np.var(sharpes, ddof=1)))
    assert dsr < 0.95, f"noise passed deflation at {dsr:.3f}"


def test_dsr_of_genuine_skill_survives_deflation():
    """The deliberately-broken baseline for the test above: if this also came
    out insignificant, the estimator would just be rejecting everything."""
    rng = RNG(13)
    n_trials, T = 500, 1000
    trials = rng.normal(0.0, 0.01, size=(T, n_trials))
    skilled = rng.normal(0.004, 0.01, size=T)          # SR ~ 0.4 per period
    sharpes = np.array([mt.sharpe_ratio(trials[:, i]) for i in range(n_trials)])

    dsr = mt.deflated_sharpe_ratio(skilled, n_trials=n_trials,
                                   var_sharpe=float(np.var(sharpes, ddof=1)))
    assert dsr > 0.99, f"real skill was rejected at {dsr:.3f}"


def test_dsr_is_a_probability():
    rng = RNG(3)
    r = rng.normal(0.001, 0.01, size=200)
    dsr = mt.deflated_sharpe_ratio(r, n_trials=20, var_sharpe=0.01)
    assert 0.0 <= dsr <= 1.0


def test_dsr_falls_as_the_search_widens():
    """Same track record, more configurations tried, less credible."""
    rng = RNG(5)
    r = rng.normal(0.002, 0.01, size=400)
    few = mt.deflated_sharpe_ratio(r, n_trials=5, var_sharpe=0.02)
    many = mt.deflated_sharpe_ratio(r, n_trials=5000, var_sharpe=0.02)
    assert many < few


# --------------------------------------------------------------------------- #
# PBO via CSCV
# --------------------------------------------------------------------------- #

def test_pbo_of_pure_noise_is_near_one_half():
    """With no true skill, the in-sample winner is a coin flip out of sample."""
    rng = RNG(17)
    m = rng.normal(0.0, 0.01, size=(400, 20))
    res = mt.pbo_cscv(m, n_splits=10)
    assert 0.3 <= res.pbo <= 0.7, f"noise PBO was {res.pbo:.3f}, expected ~0.5"


def test_pbo_of_a_dominant_strategy_is_near_zero():
    """The broken-baseline pair: one genuinely better column must be picked
    in sample AND hold up out of sample."""
    rng = RNG(19)
    m = rng.normal(0.0, 0.01, size=(400, 20))
    m[:, 3] += 0.01                     # a real, persistent edge
    res = mt.pbo_cscv(m, n_splits=10)
    assert res.pbo < 0.1, f"a dominant strategy scored PBO {res.pbo:.3f}"


def test_pbo_reports_the_number_of_combinations_used():
    rng = RNG(23)
    res = mt.pbo_cscv(rng.normal(size=(200, 8)), n_splits=8)
    assert res.n_combinations == math.comb(8, 4)
    assert len(res.logits) == res.n_combinations


def test_pbo_requires_an_even_number_of_splits():
    with pytest.raises(ValueError):
        mt.pbo_cscv(np.zeros((100, 5)), n_splits=7)


def test_pbo_requires_more_than_one_strategy():
    with pytest.raises(ValueError):
        mt.pbo_cscv(np.zeros((100, 1)), n_splits=4)


def test_pbo_requires_enough_rows_to_split():
    with pytest.raises(ValueError):
        mt.pbo_cscv(np.zeros((3, 4)), n_splits=10)


# --------------------------------------------------------------------------- #
# Hansen's SPA
# --------------------------------------------------------------------------- #

def test_spa_does_not_reject_when_no_model_beats_the_benchmark():
    rng = RNG(29)
    n, k = 300, 10
    benchmark = rng.normal(1.0, 0.1, size=n)          # loss series
    models = rng.normal(1.0, 0.1, size=(n, k))        # same distribution
    res = mt.spa_test(benchmark, models, n_boot=400, seed=1)
    # Threshold set from measured size, not hope: with independent
    # differentials the test's size at the nominal 5% level was 0.068 over 400
    # replications (MC se 0.011), so a single draw below 0.05 is expected
    # roughly 7% of the time. Asserting p > 0.10 on one seed would be a
    # coin-flip test. See the size table in spa_test's docstring.
    assert res.p_value > 0.01, f"SPA rejected hard on pure noise at p={res.p_value:.3f}"
    assert res.reliable is True, "independent differentials must be reliable"


def test_spa_rejects_when_one_model_genuinely_beats_the_benchmark():
    """Broken-baseline pair for the test above."""
    rng = RNG(31)
    n, k = 300, 10
    benchmark = rng.normal(1.0, 0.1, size=n)
    models = rng.normal(1.0, 0.1, size=(n, k))
    models[:, 4] -= 0.08                              # genuinely lower loss
    res = mt.spa_test(benchmark, models, n_boot=400, seed=2)
    assert res.p_value < 0.05, f"SPA missed a real winner at p={res.p_value:.3f}"


def test_spa_is_not_fooled_by_the_best_of_many_noise_models():
    """The selection-bias case SPA exists for: many models, none better, but
    the best-looking one has a flattering sample mean."""
    rng = RNG(37)
    n, k = 250, 200
    benchmark = rng.normal(1.0, 0.1, size=n)
    models = rng.normal(1.0, 0.1, size=(n, k))
    res = mt.spa_test(benchmark, models, n_boot=500, seed=3)
    assert res.p_value > 0.05, (
        f"SPA rejected on the best of {k} pure-noise models at "
        f"p={res.p_value:.3f} — that is the error it exists to prevent"
    )


def test_spa_returns_the_identity_of_the_best_model():
    rng = RNG(41)
    benchmark = rng.normal(1.0, 0.1, size=200)
    models = rng.normal(1.0, 0.1, size=(200, 6))
    models[:, 2] -= 0.10
    res = mt.spa_test(benchmark, models, n_boot=200, seed=4)
    assert res.best_model == 2


def test_spa_is_deterministic_under_a_fixed_seed():
    rng = RNG(43)
    b = rng.normal(1.0, 0.1, size=150)
    m = rng.normal(1.0, 0.1, size=(150, 5))
    a1 = mt.spa_test(b, m, n_boot=200, seed=99).p_value
    a2 = mt.spa_test(b, m, n_boot=200, seed=99).p_value
    assert a1 == a2


def test_spa_flags_itself_unreliable_under_strong_dependence():
    """The test over-rejects when differentials are autocorrelated, and the
    error runs toward declaring skill that is not there. It must say so."""
    rng = RNG(53)
    n = 300
    def ar(rho):
        e = rng.normal(0, 0.1, n)
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = rho * x[t - 1] + e[t]
        return 1.0 + x
    benchmark = ar(0.9)
    models = np.column_stack([ar(0.9) for _ in range(8)])
    res = mt.spa_test(benchmark, models, n_boot=300, seed=5)
    assert res.reliable is False
    assert res.autocorrelation_time > mt.MAX_RELIABLE_TAU
    assert "over-rejects" in res.as_dict()["caveat"].lower()


def test_spa_block_length_adapts_to_dependence():
    rng = RNG(59)
    n = 400
    iid = rng.normal(0.0, 0.1, size=(n, 4))
    ar = np.zeros((n, 4))
    for j in range(4):
        e = rng.normal(0, 0.1, n)
        for t in range(1, n):
            ar[t, j] = 0.85 * ar[t - 1, j] + e[t]
    q_iid = mt.choose_block_probability(iid)
    q_ar = mt.choose_block_probability(ar)
    assert q_ar < q_iid, "autocorrelated data must get longer blocks"
    assert q_iid > 0.3, "near-independent data must get short blocks"


def test_spa_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        mt.spa_test(np.zeros(10), np.zeros((11, 3)))


# --------------------------------------------------------------------------- #
# Stationary bootstrap
# --------------------------------------------------------------------------- #

def test_stationary_bootstrap_indices_are_in_range_and_right_length():
    idx = mt.stationary_bootstrap_indices(n=50, q=0.1, rng=RNG(2))
    assert idx.shape == (50,)
    assert idx.min() >= 0 and idx.max() < 50


def test_stationary_bootstrap_preserves_the_mean_in_expectation():
    rng = RNG(101)
    x = rng.normal(5.0, 1.0, size=400)
    means = [x[mt.stationary_bootstrap_indices(400, 0.1, rng)].mean()
             for _ in range(300)]
    assert np.mean(means) == pytest.approx(x.mean(), abs=0.05)


def test_stationary_bootstrap_retains_serial_dependence():
    """A block bootstrap must preserve autocorrelation an iid resample destroys."""
    rng = RNG(103)
    e = rng.normal(size=800)
    x = np.zeros(800)
    for t in range(1, 800):
        x[t] = 0.9 * x[t - 1] + e[t]          # strongly autocorrelated

    def ac1(v):
        v = v - v.mean()
        return float((v[:-1] * v[1:]).sum() / (v * v).sum())

    block = np.mean([ac1(x[mt.stationary_bootstrap_indices(800, 0.02, rng)])
                     for _ in range(60)])
    iid = np.mean([ac1(rng.permutation(x)) for _ in range(60)])
    assert block > 0.4, f"block bootstrap lost dependence (ac1={block:.2f})"
    assert abs(iid) < 0.15


# --------------------------------------------------------------------------- #
# Building PBO/SPA inputs from a bet ledger
# --------------------------------------------------------------------------- #

def _b(rule, day, pnl, size=100.0):
    return {"rule_id": rule, "resolved_at": f"{day}T00:00:00Z",
            "final_pnl": pnl, "size_usd": size}


def test_matrix_is_empty_with_a_single_rule():
    m, rules, dates = mt.daily_returns_by_rule(
        [_b("r1", "2026-08-01", 5.0), _b("r1", "2026-08-02", -3.0)])
    assert m.size == 0 and rules == []


def test_matrix_uses_only_overlapping_dates():
    """Padding a rule's missing days with zeros would make an inactive rule
    look like a flat low-volatility winner."""
    bets = [
        _b("r1", "2026-08-01", 5.0), _b("r1", "2026-08-02", -3.0),
        _b("r2", "2026-08-02", 2.0), _b("r2", "2026-08-03", 1.0),
    ]
    m, rules, dates = mt.daily_returns_by_rule(bets)
    assert dates == ["2026-08-02"]
    assert rules == ["r1", "r2"]
    assert m.shape == (1, 2)
    assert m[0, 0] == pytest.approx(-0.03)
    assert m[0, 1] == pytest.approx(0.02)


def test_matrix_aggregates_multiple_bets_on_one_day_by_notional():
    bets = [
        _b("r1", "2026-08-01", 10.0, 100.0), _b("r1", "2026-08-01", -4.0, 100.0),
        _b("r2", "2026-08-01", 1.0, 50.0),
    ]
    m, rules, _ = mt.daily_returns_by_rule(bets)
    assert m[0, rules.index("r1")] == pytest.approx(6.0 / 200.0)
    assert m[0, rules.index("r2")] == pytest.approx(1.0 / 50.0)


def test_matrix_skips_bets_without_a_rule_or_date():
    bets = [_b("r1", "2026-08-01", 1.0), _b(None, "2026-08-01", 99.0),
            _b("r2", "2026-08-01", 1.0), {"rule_id": "r2", "final_pnl": 5.0}]
    m, rules, _ = mt.daily_returns_by_rule(bets)
    assert rules == ["r1", "r2"]
    assert m.shape == (1, 2)


def test_matrix_feeds_pbo_directly():
    rng = RNG(71)
    bets = []
    for day in range(120):
        d = f"2026-01-{day % 28 + 1:02d}" if day < 28 else f"2026-{day//28+1:02d}-{day%28+1:02d}"
        for j, rid in enumerate(("r1", "r2", "r3", "r4")):
            bets.append(_b(rid, d, float(rng.normal(0, 5))))
    m, rules, dates = mt.daily_returns_by_rule(bets)
    if m.shape[0] >= 8 and m.shape[1] >= 2:
        res = mt.pbo_cscv(m, n_splits=4)
        assert 0.0 <= res.pbo <= 1.0


def test_null_sharpe_variance_units():
    """The units error that turned a skilled rule into noise."""
    v = mt.null_sharpe_variance(60, 0.0)
    assert v == pytest.approx(1.0 / 60)
    assert mt.null_sharpe_variance(60, 1.0) > v      # rises with SR


def test_dsr_with_the_correct_variance_recognises_skill():
    """Regression: brief.py passed var(returns)/n instead of var(Sharpe),
    overstating the deflation threshold ~66x and reporting a genuinely
    skilled 60-bet record as indistinguishable from noise."""
    rng = RNG(4)
    pnl = rng.normal(3.0, 8.0, size=60)
    sr = mt.sharpe_ratio(pnl)

    correct = mt.deflated_sharpe_ratio(
        pnl, n_trials=2, var_sharpe=mt.null_sharpe_variance(pnl.size, sr))
    wrong = mt.deflated_sharpe_ratio(
        pnl, n_trials=2, var_sharpe=float(np.var(pnl, ddof=1)) / pnl.size)

    # 0.95 is the conventional bar. This 60-observation record sits at ~0.96
    # with the right units and ~0.60 with the wrong ones, which is the
    # difference between "keep this rule" and "discard it".
    assert correct > 0.95, f"correct units rejected real skill at {correct:.3f}"
    assert wrong < 0.95 < correct, (
        f"the units error must flip the verdict: correct={correct:.3f}, "
        f"wrong={wrong:.3f}"
    )
