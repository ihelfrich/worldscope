"""worldscope.scoring.multiple_testing — corrections for a strategy search.

Worldscope's stated purpose includes revising its method every day until it
finds one that beats the market. Taken literally that is a specification
search, and nothing in the codebase guarded against it. With ~40 sections,
many keys per section, and daily re-fitting, the effective number of
hypotheses tested per year is large enough that *some* rule will show an
excellent in-sample record by chance. The naive Sharpe of the winner is then
not evidence of anything.

Three standard corrections, in increasing order of what they ask of you:

  Deflated Sharpe Ratio (DSR)
      Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio: Correcting
      for Selection Bias, Backtest Overfitting, and Non-Normality".
      Asks: given that I tried N configurations, how surprising is this
      Sharpe? Needs only the winner's returns plus N and the dispersion of
      Sharpes across trials.

  Probability of Backtest Overfitting (PBO), via CSCV
      Bailey, Borwein, Lopez de Prado & Zhu (2015), "The Probability of
      Backtest Overfitting". Asks: when I pick the best configuration in
      sample, how often does it land below the median out of sample? Needs
      the full T x N matrix of per-period returns for every configuration.

  Hansen's SPA test
      Hansen (2005), "A Test for Superior Predictive Ability". Asks: does the
      best of k models genuinely beat a benchmark, once you account for having
      chosen it by looking at all k? Needs per-period losses and a benchmark.

Deliberately numpy-only. numpy is already a base dependency; scipy is not, and
adding one for two normal-CDF calls would be the kind of dependency that later
turns out not to be installed in CI. `_norm_cdf` and `_norm_ppf` are the only
special functions required and both are in the standard library's `math` or a
short rational approximation.

Every estimator here returns a probability or a p-value with an explicit
convention stated in its docstring, because the two families run in opposite
directions and mixing them up is the easiest way to report the reverse of what
happened:

    DSR   HIGH is good  (probability the skill is real)
    PBO   LOW is good   (probability the selection was overfit)
    SPA   LOW rejects   (p-value against "nothing beats the benchmark")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional, Sequence

import numpy as np

EULER_MASCHERONI = 0.5772156649015329

# Serial dependence in the loss differentials, above which the SPA bootstrap
# over-rejects. Measured, not assumed: at n=300 the test is nominal only when
# the differentials are near-independent (tau ~ 1). See the size table in
# spa_test's docstring. A Newey-West variance in place of the bootstrap
# variance was tried and moved size only 0.136 -> 0.120 at rho=0.7, so the
# distortion is intrinsic to the test in finite samples rather than to the
# variance estimator.
MAX_RELIABLE_TAU = 1.5

# Below this many observations a Sharpe ratio, and everything derived from it,
# is noise dressed as a statistic.
MIN_OBS_FOR_SHARPE = 20


# --------------------------------------------------------------------------- #
# Normal distribution helpers (stdlib only)
# --------------------------------------------------------------------------- #

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to about 1.15e-9 in absolute value over the open interval, which
    is far beyond what any of these statistics need.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"_norm_ppf: p must be in (0, 1), got {p}")

    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)


# --------------------------------------------------------------------------- #
# Sharpe
# --------------------------------------------------------------------------- #

def sharpe_ratio(returns: Sequence[float]) -> Optional[float]:
    """Per-period Sharpe. None when undefined (fewer than 2 obs, or zero vol).

    NOT annualised. Every formula below expects the per-period figure, and
    annualising before deflating is a common way to get DSR badly wrong.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return None
    sd = float(r.std(ddof=1))
    if sd <= 0.0:
        return None
    return float(r.mean() / sd)


def _moments(returns: np.ndarray) -> tuple[float, float]:
    """(skewness, Pearson kurtosis). Normal kurtosis is 3, not 0."""
    r = np.asarray(returns, dtype=float)
    n = r.size
    mu = r.mean()
    sd = r.std(ddof=0)
    if sd <= 0 or n < 3:
        return 0.0, 3.0
    z = (r - mu) / sd
    return float((z ** 3).mean()), float((z ** 4).mean())


def probabilistic_sharpe_ratio(*, sharpe: float, n_obs: int, skew: float,
                               kurtosis: float, sr_benchmark: float = 0.0) -> float:
    """PSR: probability the true Sharpe exceeds `sr_benchmark`.

    Bailey & Lopez de Prado (2012). HIGH is good.

        PSR = Phi( (SR - SR*) * sqrt(n-1)
                   / sqrt(1 - g3*SR + ((g4-1)/4)*SR^2) )

    `kurtosis` is Pearson (normal = 3). Negative skew and fat tails both widen
    the denominator, which is the point: the same Sharpe earned with crash
    risk is weaker evidence.
    """
    if n_obs < 2:
        return 0.5
    denom_sq = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe ** 2
    # The estimator's variance cannot be negative; extreme sample moments on
    # short series can drive it there numerically.
    denom = math.sqrt(max(denom_sq, 1e-12))
    z = (sharpe - sr_benchmark) * math.sqrt(n_obs - 1) / denom
    return _norm_cdf(z)


def expected_max_sharpe(*, n_trials: int, var_sharpe: float) -> float:
    """E[max Sharpe] across `n_trials` independent trials with NO true skill.

    Bailey & Lopez de Prado (2014), eq. for the expected maximum of N draws:

        E[max] ~ sqrt(V) * [ (1-g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)) ]

    with g the Euler-Mascheroni constant. This is the threshold a search of
    that width must clear before its winner means anything.
    """
    n = max(int(n_trials), 1)
    v = max(float(var_sharpe), 0.0)
    if v == 0.0:
        return 0.0
    if n == 1:
        # Phi^-1(0) diverges; the expected max of one draw is its mean, 0.
        return 0.0
    q1 = _norm_ppf(1.0 - 1.0 / n)
    q2 = _norm_ppf(1.0 - 1.0 / (n * math.e))
    return math.sqrt(v) * ((1.0 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2)


def null_sharpe_variance(n_obs: int, sharpe: float = 0.0) -> float:
    """Sampling variance of the Sharpe ESTIMATOR under the null of no skill.

    Var(SR_hat) ~ (1 + SR^2 / 2) / n. This is the correct fallback for
    `deflated_sharpe_ratio`'s `var_sharpe` when the Sharpe ratios of the other
    trials are not available.

    It exists because the units are easy to get catastrophically wrong.
    Passing the variance of the RETURNS, or of their mean, instead of the
    variance of the SHARPE overstates the deflation threshold by orders of
    magnitude: on a 60-observation record it turned a DSR of 0.999 into 0.598,
    i.e. reported a genuinely skilled rule as indistinguishable from noise.
    """
    n = max(int(n_obs), 2)
    return (1.0 + (float(sharpe) ** 2) / 2.0) / n


def deflated_sharpe_ratio(returns: Sequence[float], *, n_trials: int,
                          var_sharpe: float) -> float:
    """DSR: PSR measured against the expected best of `n_trials` noise runs.

    HIGH is good. A DSR below ~0.95 means the track record is not
    distinguishable from the best result a search of that width would have
    produced with no skill at all.

    `var_sharpe` is the variance of the Sharpe ratios ACROSS the trials, NOT
    the variance of returns and NOT the variance of their mean. Passing the
    wrong one silently changes the threshold by orders of magnitude, so it is
    keyword-only and `null_sharpe_variance()` exists as the correct fallback
    when cross-trial Sharpes are unavailable.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    sr = sharpe_ratio(r)
    if sr is None:
        return 0.0
    skew, kurt = _moments(r)
    threshold = expected_max_sharpe(n_trials=n_trials, var_sharpe=var_sharpe)
    return probabilistic_sharpe_ratio(
        sharpe=sr, n_obs=r.size, skew=skew, kurtosis=kurt,
        sr_benchmark=threshold,
    )


# --------------------------------------------------------------------------- #
# PBO via CSCV
# --------------------------------------------------------------------------- #

@dataclass
class PBOResult:
    pbo: float
    logits: list[float] = field(default_factory=list)
    n_combinations: int = 0
    n_strategies: int = 0

    def as_dict(self) -> dict:
        return {
            "pbo": round(self.pbo, 4),
            "n_combinations": self.n_combinations,
            "n_strategies": self.n_strategies,
            "interpretation": (
                "probability the in-sample-best configuration lands below the "
                "out-of-sample median; LOW is good, ~0.5 means the selection "
                "carries no information"
            ),
        }


def pbo_cscv(matrix: np.ndarray, *, n_splits: int = 10) -> PBOResult:
    """Combinatorially Symmetric Cross-Validation.

    `matrix` is T x N: one column of per-period returns per configuration.

    Rows are cut into `n_splits` contiguous blocks. For every way of choosing
    half the blocks as in-sample, the best configuration in sample is found and
    its RANK among all configurations out of sample is recorded. PBO is the
    fraction of splits where that rank falls below the median.

    Contiguous rather than random blocks on purpose: these are time series, and
    shuffling rows would destroy the serial dependence that makes overfitting
    detectable in the first place.

    Cost is C(n_splits, n_splits/2): 252 at 10, 12,870 at 16. The default
    trades resolution for a run that finishes inside a daily pipeline.
    """
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("pbo_cscv: matrix must be 2-D (T x N)")
    if n_splits % 2 != 0:
        raise ValueError(f"pbo_cscv: n_splits must be even, got {n_splits}")
    t_rows, n_strategies = m.shape
    if n_strategies < 2:
        raise ValueError("pbo_cscv: needs at least 2 configurations to rank")
    if t_rows < n_splits * 2:
        raise ValueError(
            f"pbo_cscv: {t_rows} rows cannot form {n_splits} usable blocks"
        )

    block = t_rows // n_splits
    blocks = [m[i * block:(i + 1) * block, :] for i in range(n_splits)]

    half = n_splits // 2
    logits: list[float] = []

    for combo in combinations(range(n_splits), half):
        rest = [i for i in range(n_splits) if i not in combo]
        is_data = np.vstack([blocks[i] for i in combo])
        oos_data = np.vstack([blocks[i] for i in rest])

        is_perf = _column_sharpes(is_data)
        oos_perf = _column_sharpes(oos_data)
        if not np.isfinite(is_perf).any() or not np.isfinite(oos_perf).any():
            continue

        best = int(np.nanargmax(is_perf))

        # Rank of the chosen configuration out of sample, 1 = worst.
        finite = np.where(np.isfinite(oos_perf), oos_perf, -np.inf)
        rank = int((finite < finite[best]).sum()) + 1
        omega = rank / (n_strategies + 1.0)
        omega = min(max(omega, 1e-9), 1.0 - 1e-9)
        logits.append(math.log(omega / (1.0 - omega)))

    if not logits:
        return PBOResult(pbo=float("nan"), n_strategies=n_strategies)

    pbo = float(np.mean([1.0 if l <= 0.0 else 0.0 for l in logits]))
    return PBOResult(pbo=pbo, logits=logits, n_combinations=len(logits),
                     n_strategies=n_strategies)


def _column_sharpes(block: np.ndarray) -> np.ndarray:
    mu = block.mean(axis=0)
    sd = block.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(sd > 0, mu / sd, np.nan)
    return out


# --------------------------------------------------------------------------- #
# Stationary bootstrap (Politis & Romano 1994)
# --------------------------------------------------------------------------- #

def integrated_autocorrelation_time(x: np.ndarray, *, max_lag: Optional[int] = None) -> float:
    """tau = 1 + 2 * sum_j rho_j, truncated where the ACF becomes noise.

    The effective number of independent observations in a serially dependent
    series of length n is roughly n / tau. It is what the bootstrap block
    length has to match.
    """
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    n = v.size
    if n < 8:
        return 1.0
    v = v - v.mean()
    denom = float((v * v).sum())
    if denom <= 0:
        return 1.0
    lim = int(max_lag or min(n // 4, 50))
    cutoff = 2.0 / math.sqrt(n)          # ~2 standard errors under white noise
    tau = 1.0
    for lag in range(1, lim + 1):
        rho = float((v[:-lag] * v[lag:]).sum() / denom)
        if abs(rho) < cutoff:
            break                        # first insignificant lag truncates
        tau += 2.0 * rho
    return float(max(tau, 1.0))


def choose_block_probability(d: np.ndarray) -> float:
    """Pick the stationary-bootstrap restart probability q from the data.

    q = 1 / mean-block-length, with the block length set from the integrated
    autocorrelation time of the loss differentials.

    This is not a tuning knob. A fixed q=0.1 (mean block 10) applied to
    near-independent differentials makes the bootstrap variance estimate so
    noisy that SPA's empirical size at the 5% level measured 0.115 with 10
    models and 0.145 with 50 — over-rejecting, which is the exact error SPA
    exists to prevent. Matching the block length to the observed dependence
    restores nominal size while still preserving real serial correlation.
    """
    arr = np.asarray(d, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    taus = [integrated_autocorrelation_time(arr[:, j]) for j in range(arr.shape[1])]
    tau = float(np.median(taus)) if taus else 1.0
    block = max(1.0, 2.0 * tau)
    return float(min(1.0, 1.0 / block))


def stationary_bootstrap_indices(n: int, q: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """Indices for one stationary-bootstrap resample of length `n`.

    Blocks have geometric length with mean 1/q and wrap around the series, so
    the resample is stationary. Serial dependence must survive resampling:
    an iid bootstrap would destroy the autocorrelation that inflates the
    variance of a mean, and SPA's p-values would be far too small.
    """
    if n <= 0:
        return np.empty(0, dtype=int)
    q = min(max(float(q), 1e-6), 1.0)
    idx = np.empty(n, dtype=int)
    idx[0] = rng.integers(0, n)
    restart = rng.random(n) < q
    steps = rng.integers(0, n, size=n)
    for t in range(1, n):
        idx[t] = steps[t] if restart[t] else (idx[t - 1] + 1) % n
    return idx


# --------------------------------------------------------------------------- #
# Hansen's SPA
# --------------------------------------------------------------------------- #

@dataclass
class SPAResult:
    p_value: float
    statistic: float
    best_model: Optional[int]
    n_models: int
    n_obs: int
    block_probability: float = 0.0
    effective_n: float = 0.0
    autocorrelation_time: float = 1.0
    reliable: bool = True

    def as_dict(self) -> dict:
        out = {
            "p_value": round(self.p_value, 4),
            "statistic": round(self.statistic, 4),
            "best_model": self.best_model,
            "n_models": self.n_models,
            "n_obs": self.n_obs,
            "effective_n": round(self.effective_n, 1),
            "autocorrelation_time": round(self.autocorrelation_time, 2),
            "block_probability": round(self.block_probability, 4),
            "reliable": self.reliable,
            "interpretation": (
                "p-value against the null that NO model beats the benchmark, "
                "correcting for having chosen the best of them by looking; "
                "LOW rejects the null"
            ),
        }
        if not self.reliable:
            out["caveat"] = (
                f"loss differentials are serially dependent "
                f"(tau={self.autocorrelation_time:.1f}, so {self.n_obs} "
                f"observations carry about {self.effective_n:.0f} independent "
                f"ones). In this regime the test OVER-rejects: measured size "
                f"at the nominal 5% level was 0.10 at tau~2, 0.14 at tau~4.5, "
                f"0.26 at tau~20. The reported p-value is therefore a LOWER "
                f"bound on the true one -- it errs toward declaring skill that "
                f"is not there, which is the dangerous direction here. Treat "
                f"it as suggestive, not as a test."
            )
        return out


def spa_test(benchmark_losses: Sequence[float], model_losses: np.ndarray, *,
             n_boot: int = 1000, q: Optional[float] = None,
             seed: Optional[int] = None) -> SPAResult:
    """Hansen (2005) Test for Superior Predictive Ability.

    `benchmark_losses` is length n; `model_losses` is n x k. Lower loss is
    better. Returns a p-value for

        H0: max_k E[L_benchmark - L_k] <= 0
            ("no model genuinely beats the benchmark")

    LOW p rejects. The test exists because comparing the single best of k
    models against a benchmark, without accounting for having searched over k,
    rejects far too often — precisely the error a daily strategy search makes.

    Implementation notes:

      * Studentised statistic, per Hansen. The unstudentised version
        (Whitels Reality Check) is dominated by the noisiest model.
      * The "consistent" recentring: a model whose sample mean is far enough
        below zero is recentred to exactly zero rather than to its own mean, so
        hopeless models stop inflating the critical value. This is Hansen's
        main improvement over White.
      * Stationary bootstrap, so serial dependence in the loss differentials
        survives resampling. `q` defaults to None, meaning the block length is
        chosen from the observed autocorrelation.

    MEASURED BEHAVIOUR (400 replications, n=300, nominal 5%):

        loss differentials      k=10    k=50
        independent             0.068   0.062     power 1.000 for a real edge
        AR(1) rho=0.5           0.104
        AR(1) rho=0.7           0.136
        AR(1) rho=0.9           0.256

    The first row is nominal within Monte Carlo error. The rest are not, and
    no choice of block length fixes them: at rho=0.9 a 300-observation series
    carries roughly 16 independent observations, which is simply too few for a
    max-statistic bootstrap over k models. This is a property of the test in
    finite samples, not of this implementation -- a q sweep confirmed the
    automatic selector matches or beats every fixed alternative at each rho.

    So `effective_n` and `reliable` are reported alongside the p-value. When
    `reliable` is False the p-value over-rejects and must not be quoted as a
    test result.
    """
    d_bench = np.asarray(benchmark_losses, dtype=float)
    losses = np.asarray(model_losses, dtype=float)
    if losses.ndim == 1:
        losses = losses.reshape(-1, 1)
    if d_bench.shape[0] != losses.shape[0]:
        raise ValueError(
            f"spa_test: benchmark has {d_bench.shape[0]} observations but "
            f"models have {losses.shape[0]}"
        )

    n, k = losses.shape
    if n < 3:
        raise ValueError("spa_test: needs at least 3 observations")

    # Positive d means the model beats the benchmark.
    d = d_bench[:, None] - losses
    d_bar = d.mean(axis=0)

    if q is None:
        q = choose_block_probability(d)

    rng = np.random.default_rng(seed)

    # Bootstrap variance of sqrt(n) * d_bar, which is what studentises the
    # statistic. Estimated by bootstrap rather than Newey-West so the block
    # structure is the single place serial dependence is handled.
    boot_means = np.empty((n_boot, k), dtype=float)
    index_draws = [stationary_bootstrap_indices(n, q, rng) for _ in range(n_boot)]
    for b, idx in enumerate(index_draws):
        boot_means[b] = d[idx].mean(axis=0)
    omega = boot_means.std(axis=0, ddof=1) * math.sqrt(n)
    omega = np.where(omega > 1e-12, omega, 1e-12)

    t_stats = math.sqrt(n) * d_bar / omega
    statistic = float(max(0.0, float(np.max(t_stats))))
    best_model = int(np.argmax(d_bar)) if k else None

    # Hansen's consistent recentring threshold.
    threshold = -math.sqrt(2.0 * math.log(math.log(n))) if n > math.e else 0.0
    g = np.where(t_stats >= threshold, d_bar, 0.0)

    boot_stats = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        z = math.sqrt(n) * (boot_means[b] - g) / omega
        boot_stats[b] = max(0.0, float(np.max(z)))

    p_value = float(np.mean(boot_stats >= statistic))
    tau = max(1.0, 1.0 / (2.0 * q)) if q > 0 else 1.0
    effective_n = n / tau
    return SPAResult(p_value=p_value, statistic=statistic,
                     best_model=best_model, n_models=k, n_obs=n,
                     block_probability=float(q),
                     effective_n=float(effective_n),
                     autocorrelation_time=float(tau),
                     reliable=bool(tau <= MAX_RELIABLE_TAU))


# --------------------------------------------------------------------------- #
# Building the inputs from a bet ledger
# --------------------------------------------------------------------------- #

def daily_returns_by_rule(bets: Sequence[dict], *,
                          rule_key: str = "rule_id",
                          date_key: str = "resolved_at",
                          pnl_key: str = "final_pnl",
                          size_key: str = "size_usd") -> tuple[np.ndarray, list[str], list[str]]:
    """Build the T x N return matrix PBO and SPA need, from resolved bets.

    Returns (matrix, rule_ids, dates) on the intersection of dates where every
    rule has at least one resolved bet. The intersection matters: CSCV compares
    configurations on the SAME periods, and padding a rule's missing days with
    zeros would make an inactive rule look like a flat, low-volatility one and
    flatter it enormously.

    Empty matrix when fewer than two rules have overlapping history, which is
    the normal state until a second rule has actually been run.
    """
    by_rule: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for b in bets:
        rid = b.get(rule_key)
        day = (b.get(date_key) or "")[:10]
        if not rid or not day:
            continue
        try:
            pnl = float(b.get(pnl_key) or 0.0)
            size = float(b.get(size_key) or 0.0)
        except (TypeError, ValueError):
            continue
        by_rule.setdefault(rid, {}).setdefault(day, []).append((pnl, size))

    if len(by_rule) < 2:
        return np.empty((0, 0)), [], []

    common = set.intersection(*(set(days) for days in by_rule.values()))
    if not common:
        return np.empty((0, 0)), [], []

    dates = sorted(common)
    rule_ids = sorted(by_rule)
    matrix = np.zeros((len(dates), len(rule_ids)), dtype=float)
    for j, rid in enumerate(rule_ids):
        for i, day in enumerate(dates):
            rows = by_rule[rid][day]
            notional = sum(s for _, s in rows)
            matrix[i, j] = (sum(p for p, _ in rows) / notional) if notional > 0 else 0.0
    return matrix, rule_ids, dates
