"""worldscope.foresight — temporal lead/lag mining → falsifiable early-warning
forecasts.

Worldscope already has three engines that read the lake *across sources*:

  * ``signals.py`` — *convergence now*: what is salient across many independent
    sections **today**.
  * ``radar.py``   — *change vs. baseline*: what **spiked** or **broke in** for
    the first time.
  * ``stories.py`` — *event clustering*: the day's records grouped into the
    real-world events covering them.

All three read a single day (or a short window) as a snapshot. None of them
exploit the one axis the committed lake uniquely owns: **time**. The lake is a
day-by-day git history of ~40 sources, so it holds the answer to a different,
higher-value question —

    *Which signals reliably come BEFORE which others?*

When "sanctions chatter" surges, does "FX volatility" tend to elevate three days
later? When a country lights up in conflict data, do its neighbours follow? That
**lead/lag precedence** is latent structure the platform's own vision asks for
("read latent trends and make calibrated predictions about what happens next")
and the roadmap explicitly calls out ("correlation / lead-lag screens across
datasets"). This module mines it.

What it does
------------
1. Builds, for every salient **key** (the same entity/phrase keys ``signals``
   fuses on), a daily **intensity** series over a long window — intensity =
   the number of *distinct sections* referencing that key that day, so a day's
   value already encodes cross-source breadth, not raw volume.
2. Mines **lead/lag rules** ``A ⇒ B (lag L)``: a *surge* in leader ``A`` is
   followed, ``L`` days later, by ``B`` being *elevated* far more often than
   ``B``'s own base rate would predict. Each rule is scored by precision, the
   **lift** over that base rate, and the support behind it. Anti-spurious
   guards drop pairs that are really the same key (shared token / substring).
3. Emits today's **early warnings**: learned rules whose *leader surged today*
   and whose *follower is currently quiet* → a forward-looking, falsifiable
   forecast that the quiet key will become active within the lag horizon.
4. Logs the strongest warnings as **predictions** in the lake's existing shape,
   so they flow into the very same Brier / calibration / skill track-record loop
   ``signals`` predictions do — and ``grade_due_predictions`` settles them later
   from the lake's *own* future records, with no human in the loop.
5. Persists a ``foresight.json`` artifact under ``lake/sections/_meta/<date>/``
   (the convention ``radar``/``stories`` use) and prepends a "Foresight" panel
   to the brief.

Why this is a different, harder prediction than ``signals`` makes
----------------------------------------------------------------
``signals`` predicts **persistence**: a key already salient today stays salient.
That is a relatively easy, high-base-rate call. ``foresight`` predicts
**emergence**: a key that is *quiet today* becomes active *because something
that leads it just fired*. That is a low-base-rate, genuinely anticipatory call —
exactly the kind whose calibrated track record is worth something.

Design constraints, identical to ``signals.py`` / ``radar.py``
--------------------------------------------------------------
  * Pure, offline, stdlib-only core. Every miner/scorer takes plain dicts /
    lists and returns dataclasses, so the whole thing is unit-testable with no
    DB and no model.
  * Thin adapters read the populated lake SQLite DB (production) or the
    committed JSONL section files (local/CI fallback), reusing ``signals``'
    loaders and key extraction so all engines see identical records.
  * Nothing here can abort a brief: the brief-stage wrapper swallows failures.

Run standalone against the local lake::

    python -m worldscope.foresight --days 45                 # print lead/lag rules + warnings
    python -m worldscope.foresight --days 45 --write         # + persist predictions, grade due
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from . import signals as sg
from .lake import LAKE_SECTIONS

REPO = Path(__file__).resolve().parent.parent
LAKE_META = LAKE_SECTIONS / "_meta"

METHOD = "lead-lag-foresight-v1"

# ---- tuning knobs (all overridable through the public functions) ------------
DEFAULT_WINDOW_DAYS = 45     # how much history the lead/lag miner reads
DEFAULT_LAG_MAX = 5          # search leads of 1..LAG_MAX days
DEFAULT_SURGE_MIN = 2        # leader must reach >= this many sections to "surge"
DEFAULT_ELEV_MIN = 2         # follower counts as "elevated" at >= this many sections
DEFAULT_MIN_SUPPORT = 3      # a *predictive* rule needs >= this many lead events
DEFAULT_MIN_PRECISION = 0.6  # P(follower elevated | leader surged) floor
DEFAULT_MIN_LIFT = 1.5       # precision must beat the follower's base rate by this
DEFAULT_MIN_KEY_DAYS = 3     # ignore keys that are active on fewer than this many days
DEFAULT_ALPHA = 0.01         # family-wise significance target (Bonferroni-corrected)
DEFAULT_TOP_RULES = 40
CONF_FLOOR, CONF_CEIL = 0.55, 0.75   # conservative confidence band for warnings


# ---------------------------------------------------------------------------
# Significance — the guard against mining noise out of a short history
# ---------------------------------------------------------------------------
# With ~N keys the candidate space is O(N² · lag_max) hypotheses, so at any raw
# p-threshold a flood of "100% precision" coincidences will clear it. The honest
# fix is a one-sided **binomial** test — *how surprising is it that a follower
# with base rate p was elevated `hits` of `support` times after the leader
# fired?* — combined with a **Bonferroni** correction over the number of pairs
# actually tested. A rule only earns the `predictive` flag (and the right to emit
# a self-graded prediction) when it survives that corrected bar, so a thin lake
# yields few/no predictive rules rather than a confident-looking fiction.

def binom_sf(hits: int, n: int, p: float) -> float:
    """P(X >= hits) for X ~ Binomial(n, p): the chance of doing at least this
    well by luck if the follower were independent of the leader. Pure stdlib;
    n is tiny (a window's worth of surge days), so the exact sum is cheap."""
    if hits <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0 if hits > 0 else 1.0
    if p >= 1.0:
        return 1.0
    tail = 0.0
    for k in range(hits, n + 1):
        tail += math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))
    return min(max(tail, 0.0), 1.0)


# ============================================================================
# Activity series
# ============================================================================

def daily_key_sections(
    records: Iterable[dict], *, today: date, window_days: int,
) -> dict[str, dict[str, set]]:
    """``{key: {iso_date: {sections…}}}`` over the window.

    Reuses ``signals``' key extraction and noise filter so the keys here are the
    exact keys the rest of the platform fuses on. The value is the *set of
    distinct sections* that referenced the key that day — so downstream
    intensity is cross-source breadth, immune to one chatty source spamming a
    term."""
    horizon = today - timedelta(days=window_days)
    out: dict[str, dict[str, set]] = {}
    for rec in records:
        if sg.is_noise_record(rec):
            continue
        day = sg._parse_day(rec)
        if day is None or day < horizon or day > today:
            continue
        section = str(rec.get("section_id") or rec.get("section") or "?")
        iso = day.isoformat()
        for key in sg.record_keys(rec):
            out.setdefault(key, {}).setdefault(iso, set()).add(section)
    return out


def _date_axis(today: date, window_days: int) -> list[str]:
    """Ordered list of ISO dates, oldest→newest, spanning the window."""
    start = today - timedelta(days=window_days)
    n = (today - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(n + 1)]


def build_intensity(
    key_sections: dict[str, dict[str, set]],
    axis: list[str],
    *,
    min_key_days: int = DEFAULT_MIN_KEY_DAYS,
) -> dict[str, list[int]]:
    """Project the per-key section sets onto the common date axis as an integer
    **intensity** series (distinct-section count per day). Keys active on fewer
    than ``min_key_days`` distinct days are dropped — too sparse to learn from."""
    out: dict[str, list[int]] = {}
    for key, by_day in key_sections.items():
        active_days = sum(1 for d in axis if by_day.get(d))
        if active_days < min_key_days:
            continue
        out[key] = [len(by_day.get(d, ())) for d in axis]
    return out


# ============================================================================
# Surge / elevation primitives (pure, index-based — trivially testable)
# ============================================================================

def surge_indices(
    series: list[int], *, surge_min: int = DEFAULT_SURGE_MIN, baseline_days: int = 7,
) -> list[int]:
    """Indices where the key *surges*: intensity reaches ``surge_min`` AND rises
    at least one full section above its trailing baseline (mean of the prior
    ``baseline_days`` values). Requiring a rise over the local baseline is what
    makes a surge a *development*, not just a steadily-busy key."""
    out: list[int] = []
    for i, v in enumerate(series):
        if v < surge_min:
            continue
        if i == 0:
            # no history to establish a baseline; require a clear standalone spike
            if v >= surge_min + 1:
                out.append(i)
            continue
        lo = max(0, i - baseline_days)
        prior = series[lo:i]
        baseline = sum(prior) / len(prior) if prior else 0.0
        if v >= baseline + 1.0:
            out.append(i)
    return out


def elevated_mask(series: list[int], *, elev_min: int = DEFAULT_ELEV_MIN) -> list[bool]:
    """Per-day boolean: is the follower *elevated* (>= ``elev_min`` sections)?"""
    return [v >= elev_min for v in series]


# ============================================================================
# Lead/lag rule mining
# ============================================================================

@dataclass
class LeadLag:
    leader: str
    follower: str
    lag: int
    support: int            # # leader-surge days with a valid follower day at +lag
    hits: int               # # of those where the follower was elevated
    precision: float        # hits / support
    base_rate: float        # follower's unconditional elevated rate
    lift: float             # precision / base_rate
    p_value: float          # one-sided binomial tail P(X >= hits | base_rate)
    score: float
    last_lead_iso: str      # most recent leader-surge date driving this rule
    predictive: bool        # survives every bar incl. corrected significance
    examples: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "leader": self.leader, "follower": self.follower, "lag": self.lag,
            "support": self.support, "hits": self.hits,
            "precision": round(self.precision, 3), "base_rate": round(self.base_rate, 3),
            "lift": round(self.lift, 3), "p_value": round(self.p_value, 6),
            "score": round(self.score, 4),
            "last_lead_iso": self.last_lead_iso, "predictive": self.predictive,
            "examples": self.examples,
        }

    @property
    def rationale(self) -> str:
        kind = "predictive" if self.predictive else "provisional"
        return (
            f"When '{self.leader}' surges, '{self.follower}' is elevated "
            f"{self.lag}d later in {self.hits}/{self.support} cases "
            f"({int(round(self.precision * 100))}%, {self.lift:.1f}× its "
            f"{int(round(self.base_rate * 100))}% base rate; p={self.p_value:.3g}) "
            f"— {kind}."
        )


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _shares_token(a: str, b: str) -> bool:
    """True if two keys are really the same subject — substring or a shared
    non-trivial token — so we never 'discover' a key leading itself."""
    if a == b or a in b or b in a:
        return True
    ta = {t for t in _TOKEN_RE.findall(a) if len(t) > 2}
    tb = {t for t in _TOKEN_RE.findall(b) if len(t) > 2}
    return bool(ta & tb)


def _recency_weight(last_idx: int, n: int, *, half_life: float = 14.0) -> float:
    """Down-weight rules whose evidence is stale: a rule last seen at the window
    edge counts full, one from weeks ago decays."""
    age = max(0, (n - 1) - last_idx)
    return 0.5 ** (age / half_life)


def mine_lead_lag(
    intensity: dict[str, list[int]],
    axis: list[str],
    *,
    lag_max: int = DEFAULT_LAG_MAX,
    surge_min: int = DEFAULT_SURGE_MIN,
    elev_min: int = DEFAULT_ELEV_MIN,
    min_support: int = DEFAULT_MIN_SUPPORT,
    min_precision: float = DEFAULT_MIN_PRECISION,
    min_lift: float = DEFAULT_MIN_LIFT,
    alpha: float = DEFAULT_ALPHA,
    top_n: int = DEFAULT_TOP_RULES,
) -> list[LeadLag]:
    """Mine ordered lead/lag rules ``A ⇒ B (lag L)`` from the intensity panel.

    For each leader ``A`` we take its surge days; for each candidate follower
    ``B`` and lag ``L`` we measure how often ``B`` is elevated ``L`` days after
    ``A`` surges, versus ``B``'s unconditional elevated rate. A rule is kept as
    **tentative** if it has support and positive lift, and flagged **predictive**
    only once it clears the support / precision / lift bars *and* survives a
    one-sided binomial significance test **Bonferroni-corrected** for the number
    of (leader, follower, lag) hypotheses tested. Only predictive rules emit
    forecasts downstream — so a short, noisy history yields few/no predictive
    rules instead of confident-looking coincidences.

    Pure and deterministic: identical inputs → identical, stably-sorted output.
    """
    n = len(axis)
    # Pre-compute each key's surge indices, elevated mask, and base rate once.
    surges: dict[str, list[int]] = {}
    elev: dict[str, list[bool]] = {}
    base: dict[str, float] = {}
    for key, series in intensity.items():
        surges[key] = surge_indices(series, surge_min=surge_min)
        mask = elevated_mask(series, elev_min=elev_min)
        elev[key] = mask
        base[key] = (sum(1 for x in mask if x) / n) if n else 0.0

    leaders = sorted(k for k, s in surges.items() if s)
    candidates: list[LeadLag] = []
    n_tests = 0
    for a in leaders:
        a_surges = surges[a]
        for b, b_mask in elev.items():
            if _shares_token(a, b):
                continue
            b_base = base[b]
            if b_base <= 0.0 or b_base >= 1.0:
                continue  # a follower that's never (or always) elevated can't lift
            for lag in range(1, lag_max + 1):
                lead_events = [t for t in a_surges if t + lag < n]
                support = len(lead_events)
                if support < 2:   # need at least a pair to claim any pattern
                    continue
                n_tests += 1      # a real hypothesis was evaluated → counts toward correction
                hit_idx = [t for t in lead_events if b_mask[t + lag]]
                hits = len(hit_idx)
                if hits == 0:
                    continue
                precision = hits / support
                lift = precision / b_base
                if lift <= 1.0:
                    continue
                p_value = binom_sf(hits, support, b_base)
                last_idx = max(lead_events)
                rec_w = _recency_weight(last_idx, n)
                # reward significance directly: -log10(p) lifts genuinely
                # surprising rules above merely-high-precision ones.
                sig_w = min(-math.log10(p_value + 1e-12) / 3.0, 3.0)
                score = (precision * math.log1p(support)
                         * min(lift, 5.0) * rec_w * (1.0 + sig_w))
                examples = [
                    {"lead_date": axis[t], "follow_date": axis[t + lag]}
                    for t in hit_idx[-3:]
                ]
                candidates.append(LeadLag(
                    leader=a, follower=b, lag=lag, support=support, hits=hits,
                    precision=precision, base_rate=b_base, lift=lift,
                    p_value=p_value, score=score, last_lead_iso=axis[last_idx],
                    predictive=False, examples=examples,
                ))

    # Bonferroni: the per-rule bar tightens with the size of the search. Floor
    # the corrected alpha so a vast candidate space can't make it literally
    # impossible — but it stays strict enough to reject lone coincidences.
    corrected = max(alpha / max(1, n_tests), 1e-9)
    rules: list[LeadLag] = []
    for r in candidates:
        r.predictive = (r.support >= min_support
                        and r.precision >= min_precision
                        and r.lift >= min_lift
                        and r.p_value <= corrected)
        rules.append(r)

    # Keep, per (leader, follower), only the single best lag — otherwise one
    # strong association floods the list at every nearby lag.
    best: dict[tuple[str, str], LeadLag] = {}
    for r in rules:
        k = (r.leader, r.follower)
        cur = best.get(k)
        if cur is None or r.score > cur.score:
            best[k] = r
    out = sorted(best.values(), key=lambda r: (-r.score, r.leader, r.follower, r.lag))
    return out[:top_n]


# ============================================================================
# Today's early warnings  →  falsifiable predictions
# ============================================================================

@dataclass
class EarlyWarning:
    leader: str
    follower: str
    lag: int
    fired_iso: str          # the date the leader surged (the trigger)
    target_iso: str         # deadline by which the follower is expected to elevate
    confidence: float
    rule: LeadLag

    def to_dict(self) -> dict:
        return {
            "leader": self.leader, "follower": self.follower, "lag": self.lag,
            "fired_iso": self.fired_iso, "target_iso": self.target_iso,
            "confidence": round(self.confidence, 4), "rule": self.rule.to_dict(),
        }


def _conf_from(precision: float, lift: float) -> float:
    """Map a predictive rule's strength to a conservative confidence, capped well
    below certainty (these are anticipatory, low-base-rate calls)."""
    base = CONF_FLOOR + 0.25 * (precision - 0.5) + 0.03 * min(max(lift - 1.0, 0.0), 4.0)
    return round(min(max(base, CONF_FLOOR), CONF_CEIL), 4)


def todays_warnings(
    rules: list[LeadLag],
    intensity: dict[str, list[int]],
    axis: list[str],
    *,
    today: date,
    surge_min: int = DEFAULT_SURGE_MIN,
    elev_min: int = DEFAULT_ELEV_MIN,
    max_warnings: int = 12,
) -> list[EarlyWarning]:
    """From the predictive rules, emit a warning for each whose **leader surged
    today** and whose **follower is currently quiet** (not already elevated) —
    i.e. a genuine, not-yet-realized anticipation. A rule that fires on a
    follower already lit up isn't a forecast; it's a coincidence, so we skip it.
    """
    if not axis or axis[-1] != today.isoformat():
        return []
    last = len(axis) - 1
    out: list[EarlyWarning] = []
    seen: set[tuple[str, str]] = set()
    for r in rules:
        if not r.predictive:
            continue
        lead_series = intensity.get(r.leader)
        foll_series = intensity.get(r.follower)
        if not lead_series or not foll_series:
            continue
        # leader must have surged *today* (the trigger lands now)…
        if last not in surge_indices(lead_series, surge_min=surge_min):
            continue
        # …and the follower must not already be elevated (else nothing to warn)
        if foll_series[last] >= elev_min:
            continue
        kk = (r.leader, r.follower)
        if kk in seen:
            continue
        seen.add(kk)
        target = today + timedelta(days=r.lag)
        out.append(EarlyWarning(
            leader=r.leader, follower=r.follower, lag=r.lag,
            fired_iso=today.isoformat(), target_iso=target.isoformat(),
            confidence=_conf_from(r.precision, r.lift), rule=r,
        ))
    out.sort(key=lambda w: w.rule.score, reverse=True)
    return out[:max_warnings]


def _prediction_id(leader: str, follower: str, lag: int, made: str) -> str:
    return hashlib.sha1(
        f"{METHOD}|{leader}|{follower}|{lag}|{made}".encode()).hexdigest()


def warnings_to_predictions(
    warnings: list[EarlyWarning], *, today: date,
) -> list[dict]:
    """Turn early warnings into lake-shaped predictions. Each resolves YES if the
    *currently quiet* follower becomes elevated within the lag horizon — a
    falsifiable, lake-auto-gradable emergence call. The follower key and the
    elevation bar are embedded in the criteria so grading can recover them with
    no extra state."""
    made = today.isoformat()
    out: list[dict] = []
    for w in warnings:
        out.append({
            "id": _prediction_id(w.leader, w.follower, w.lag, made),
            "target_date": w.target_iso,
            "resolution_criteria": (
                f"Lead/lag early warning: '{w.leader}' surged on {made}; "
                f"resolves YES if follower '{w.follower}' is elevated "
                f"(>= {DEFAULT_ELEV_MIN} distinct WORLDSCOPE sections on some day) "
                f"between {made} and {w.target_iso}; otherwise NO."
            ),
            "predicted_outcome": "YES",
            "confidence": w.confidence,
            "training_window_days": DEFAULT_WINDOW_DAYS,
            "indicators_used": [w.leader],
            "method": METHOD,
            "evidence": [],
            "_follower": w.follower,   # internal: grading convenience; not a column
        })
    return out


# ============================================================================
# Grading (self-resolving from the lake's own later records)
# ============================================================================

_FOLLOWER_RE = re.compile(r"follower '([^']+)'")


def _recover_follower(resolution_criteria: str) -> Optional[str]:
    m = _FOLLOWER_RE.search(resolution_criteria or "")
    return m.group(1) if m else None


def grade_follower_outcome(
    follower: str,
    records: Iterable[dict],
    *,
    made: date,
    target: date,
    elev_min: int = DEFAULT_ELEV_MIN,
) -> str:
    """Settle one warning: 'YES' if ``follower`` is elevated (referenced by
    >= ``elev_min`` distinct sections) on *any* day in ``(made, target]``."""
    by_day: dict[str, set] = {}
    for rec in records:
        day = sg._parse_day(rec)
        if day is None or day <= made or day > target:
            continue
        if follower in sg.record_keys(rec):
            section = str(rec.get("section_id") or rec.get("section") or "?")
            by_day.setdefault(day.isoformat(), set()).add(section)
    for sections in by_day.values():
        if len(sections) >= elev_min:
            return "YES"
    return "NO"


def grade_due_predictions(lake, conn, *, today: date) -> int:
    """Find our matured, unresolved predictions and settle each from the lake's
    own record history. Mirrors ``signals.grade_due_predictions``."""
    cur = conn.execute(
        "SELECT id, made_at, target_date, resolution_criteria FROM predictions "
        "WHERE method = ? AND (actual_outcome IS NULL OR actual_outcome = '') "
        "AND target_date IS NOT NULL AND target_date <= ?",
        (METHOD, today.isoformat()),
    )
    due = [dict(r) for r in cur.fetchall()]
    if not due:
        return 0
    earliest = min(date.fromisoformat(r["made_at"][:10]) for r in due)
    span = (today - earliest).days + 1
    records = sg.load_records_from_db(conn, today=today, days=span)
    if not records:
        records = sg.load_records_from_jsonl(today=today, days=span)
    graded = 0
    for r in due:
        follower = _recover_follower(r["resolution_criteria"])
        if not follower:
            continue
        made = date.fromisoformat(r["made_at"][:10])
        target = date.fromisoformat(r["target_date"][:10])
        outcome = grade_follower_outcome(follower, records, made=made, target=target)
        lake.resolve_prediction(
            prediction_id=r["id"], resolved_at=today.isoformat(),
            actual_outcome=outcome,
        )
        graded += 1
    return graded


def persist_predictions(lake, predictions: list[dict]) -> int:
    """Write foresight predictions to the lake (skips internal ``_*`` fields)."""
    n = 0
    for p in predictions:
        lake.add_prediction(
            prediction_id=p["id"],
            target_date=p.get("target_date"),
            resolution_criteria=p.get("resolution_criteria", ""),
            predicted_outcome=p.get("predicted_outcome", "YES"),
            confidence=float(p.get("confidence", CONF_FLOOR)),
            training_window_days=p.get("training_window_days"),
            indicators_used=p.get("indicators_used", []),
            method=p.get("method", METHOD),
            evidence=p.get("evidence", []),
            section_id="foresight",
        )
        n += 1
    return n


# ============================================================================
# Brief panel
# ============================================================================

def render_foresight_panel(
    rules: list[LeadLag], warnings: list[EarlyWarning], *,
    max_warnings: int = 8, max_rules: int = 8,
) -> str:
    """Render the 'Foresight' section in the brief's house style. Leads with
    today's *actionable* early warnings, then the learned lead/lag rulebook."""
    import html as _html

    predictive = [r for r in rules if r.predictive]
    if not warnings and not predictive:
        return ""

    blocks: list[str] = []

    if warnings:
        rows = []
        for w in warnings[:max_warnings]:
            rows.append(
                "<li>"
                f"<span class='new-badge'>+{w.lag}d</span>"
                f"<strong>{_html.escape(w.leader[:50])}</strong> "
                f"→ <strong>{_html.escape(w.follower[:50])}</strong>"
                f"<span class='meta'> · watch through {w.target_iso} · "
                f"{int(round(w.confidence * 100))}% emerge</span>"
                f"<div class='abs'>{_html.escape(w.rule.rationale)}</div>"
                "</li>"
            )
        blocks.append(
            "<p class='synth'><strong>Early warnings (today's triggers):</strong> "
            "a key that leads each of these fired today while the followed key is "
            "still quiet — logged as falsifiable emergence predictions, "
            "auto-graded against the lake.</p>"
            f"<ul class='items'>{''.join(rows)}</ul>"
        )

    if predictive:
        rows = []
        for r in predictive[:max_rules]:
            rows.append(
                "<li>"
                f"<span class='new-badge'>{r.lift:.1f}×</span>"
                f"<strong>{_html.escape(r.leader[:46])}</strong> "
                f"⇒ <strong>{_html.escape(r.follower[:46])}</strong> "
                f"<span class='meta'>(lag {r.lag}d)</span>"
                f"<div class='abs'>{_html.escape(r.rationale)}</div>"
                "</li>"
            )
        blocks.append(
            "<p class='synth'><strong>Learned lead/lag rulebook:</strong> "
            "precedence patterns mined from the lake's day-by-day history, "
            "ranked by lift over base rate × support.</p>"
            f"<ul class='items'>{''.join(rows)}</ul>"
        )

    return (
        "<section class='section'>"
        "<h2>🔭 Foresight — lead/lag early warning "
        f"<span class='count'>· {len(warnings)} warnings / "
        f"{len(predictive)} rules</span></h2>"
        f"{''.join(blocks)}"
        "</section>"
    )


# ============================================================================
# Artifact
# ============================================================================

def write_foresight_artifact(
    today: date, rules: list[LeadLag], warnings: list[EarlyWarning],
    *, meta_root: Path = LAKE_META,
) -> Path:
    """Persist foresight.json under lake/sections/_meta/<date>/ (same convention
    radar/stories use). The desk-officer routine can read this directly."""
    out_dir = meta_root / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": today.isoformat(),
        "method": METHOD,
        "params": {
            "window_days": DEFAULT_WINDOW_DAYS, "lag_max": DEFAULT_LAG_MAX,
            "surge_min": DEFAULT_SURGE_MIN, "elev_min": DEFAULT_ELEV_MIN,
            "min_support": DEFAULT_MIN_SUPPORT, "min_precision": DEFAULT_MIN_PRECISION,
            "min_lift": DEFAULT_MIN_LIFT,
        },
        "n_rules": len(rules),
        "n_predictive": sum(1 for r in rules if r.predictive),
        "warnings": [w.to_dict() for w in warnings],
        "rules": [r.to_dict() for r in rules],
    }
    out_path = out_dir / "foresight.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_path


# ============================================================================
# Orchestration (used by the brief stage and the CLI)
# ============================================================================

def build_foresight(
    *, today: date, days: int = DEFAULT_WINDOW_DAYS, conn=None,
) -> tuple[list[LeadLag], list[EarlyWarning], dict[str, list[int]], list[str]]:
    """Load records (DB if a populated connection is given, else JSONL), build
    the intensity panel, mine lead/lag rules, and derive today's warnings.
    Returns ``(rules, warnings, intensity, axis)`` so callers can reuse the
    panel without recomputing."""
    records: list[dict] = []
    if conn is not None:
        try:
            records = sg.load_records_from_db(conn, today=today, days=days)
        except Exception:
            records = []
    if not records:
        records = sg.load_records_from_jsonl(today=today, days=days)

    axis = _date_axis(today, days)
    key_sections = daily_key_sections(records, today=today, window_days=days)
    intensity = build_intensity(key_sections, axis)
    rules = mine_lead_lag(intensity, axis)
    warnings = todays_warnings(rules, intensity, axis, today=today)
    return rules, warnings, intensity, axis


# ============================================================================
# CLI
# ============================================================================

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Temporal lead/lag early-warning miner.")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="as-of date (YYYY-MM-DD); default today")
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--write", action="store_true",
                    help="persist warning predictions to the lake and grade due ones")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.date)
    rules, warnings, _intensity, _axis = build_foresight(today=today, days=args.days)
    predictive = [r for r in rules if r.predictive]

    print(f"[foresight] {len(rules)} lead/lag rules mined as of {today} "
          f"(window {args.days}d); {len(predictive)} predictive, "
          f"{len(warnings)} fire today.\n")
    if warnings:
        print("Early warnings (leader surged today, follower still quiet):")
        for i, w in enumerate(warnings[:args.top], 1):
            print(f"{i:2}. [{int(w.confidence*100)}% | +{w.lag}d → {w.target_iso}] "
                  f"{w.leader} ⇒ {w.follower}")
            print(f"      {w.rule.rationale}")
        print()
    print(f"Top rules (by lift × support, predictive ✓):")
    for i, r in enumerate(rules[:args.top], 1):
        flag = "✓" if r.predictive else "·"
        print(f"{i:2}. {flag} [{r.lift:.1f}× | {int(r.precision*100)}% | "
              f"lag {r.lag}d | n={r.support}] {r.leader} ⇒ {r.follower}")

    if args.write:
        from .lake import Lake
        lake = Lake.open()
        conn = lake._ensure_open()
        preds = warnings_to_predictions(warnings, today=today)
        n = persist_predictions(lake, preds)
        graded = grade_due_predictions(lake, conn, today=today)
        write_foresight_artifact(today, rules, warnings)
        print(f"\n[foresight] wrote {n} predictions; graded {graded} due predictions.")
        lake.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
