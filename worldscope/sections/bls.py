"""bls.py — U.S. Bureau of Labor Statistics headline time series.

Why this section exists
=======================
`macro` already carries CPI, unemployment and payrolls — but *as FRED mirrors*,
behind FRED_API_KEY. One unset credential therefore takes out the entire U.S.
price and labor picture at once, and FRED is a redistributor: its aliases
(CPIAUCSL, UNRATE, PAYEMS) hide which underlying BLS series is being quoted,
including the seasonal-adjustment choice that decides whether a month-over-
month number means anything. This section reads the same statistics from the
agency that produces them, over an endpoint that needs no credential at all,
under their real series ids and with BLS's own footnotes (preliminary,
suppressed, revised) intact. Deliberate redundancy on the indicators most
likely to move everything else in the brief.

Keyed vs keyless — and why the mode is printed on every row
===========================================================
BLS publishes two endpoints over the same data, with the same JSON envelope.
Per BLS's published comparison (limits not independently measured here):

  v1  no registration key, 25 queries/day/IP, 25 series and 10 years per query
  v2  registration key, 500 queries/day, 50 series and 20 years per query

We use v2 when ``BLS_API_KEY`` is set and v1 when it is not, which makes
BLS_API_KEY ``optional_env`` rather than ``requires_env``. That fallback is
exactly the kind of quiet degradation this repo was rebuilt to stop, so every
emitted item carries ``api_version``/``api_mode`` and names the mode in its
human-readable summary. Two related rules follow from the same principle:

  * A key that is *present and rejected* raises UpstreamAuthError. It never
    falls back to v1. A rejected key is a broken deployment, and a run that
    silently answered from the keyless endpoint would hide it indefinitely.
  * BLS returns **HTTP 200 on an authentication failure** — the body carries
    ``"status": "REQUEST_NOT_PROCESSED"`` while ``raise_for_status()`` passes
    and a naive parser yields zero rows. Every response body is therefore
    status-checked before it is parsed.

Deltas, and why they are not all percentages
============================================
A level with no delta is not a signal, but the correct delta differs by series
and getting it wrong is a category error, not a rounding difference:

  * an index (CPI, PPI, average hourly earnings) moves in **percent**
  * a rate already denominated in percent (unemployment) moves in
    **percentage points** — "4.2 to 4.1" is −0.1 pp, not −2.4%
  * an employment count moves in **thousands of jobs**; the percent change is
    real but nobody reads payrolls that way, so the level change leads

``DELTA_*`` on each watchlist entry selects which of the three is the headline.

Documented approximations
=========================
  * **Seasonal adjustment is a property of the series id, not something we
    apply.** CUUR/WPU are *not* seasonally adjusted; LNS/CES are. For the two
    NSA series the month-over-month change therefore contains ordinary seasonal
    movement and is *not* the seasonally-adjusted MoM that the CPI/PPI press
    releases lead with (that is CUSR0000SA0 / WPSFD4). Their 12-month change,
    however, *is* the published headline — BLS quotes NSA for year-over-year.
    Every item states its adjustment status rather than letting the reader
    assume.
  * **``change_z`` is a standardization against the series' own recent history,
    not a test statistic.** It is computed over at most ~35 monthly changes,
    those changes are serially correlated, and in the NSA case the YoY windows
    overlap. Treat |z| >= 2 as "unusual for this series lately", nothing
    stronger. It is labelled that way in the anomaly text.
  * **A monthly datum is dated to the first of its reference month.** BLS
    publishes a month, not a day; the release date is a different (and here
    unused) fact.
  * **Gaps are skipped, never bridged.** Comparisons are paired by calendar
    month, so a missing month drops that comparison rather than passing off a
    two-month step as month-over-month. The unemployment rate has exactly such
    a hole at 2025-M10 ("Data unavailable due to the 2025 lapse in
    appropriations"), which is why this is coded rather than assumed away.
"""
from __future__ import annotations

import math
import os
import re
from datetime import date
from typing import NamedTuple, Optional

import requests

from . import (
    Section,
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
    UpstreamSchemaError,
)

__version__ = "0.1.0"

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

API_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
API_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES_VIEWER = "https://data.bls.gov/timeseries"

SUCCESS = "REQUEST_SUCCEEDED"

# Headline delta for a series. See the module docstring — mixing these up turns
# a 0.1 percentage-point move in the unemployment rate into a 2.4% one.
DELTA_PCT = "percent"            # index-like: report percent change
DELTA_PP = "percentage_point"    # already a percent: report point change
DELTA_LEVEL = "level"            # a count: report the change in native units

# Three calendar years, not two. Year-over-year needs 13 months, and in January
# the latest published month can still be November of the year before last, so
# a two-year window would silently lose the YoY comparison for one month a year.
LOOKBACK_YEARS = 2

# Minimum prior changes before change_z is computed at all. Twelve is one
# seasonal cycle; below that the dispersion estimate is not worth printing.
MIN_Z_HISTORY = 12
Z_ANOMALY = 2.0


class Series(NamedTuple):
    series_id: str
    label: str
    group: str
    unit: str
    delta: str
    seasonally_adjusted: bool


# Every entry was confirmed to return data from the keyless v1 endpoint on
# 2026-08-24. Seasonal adjustment is read off the series-id prefix: CUUR/WPU
# are unadjusted, LNS/CES are adjusted.
WATCHLIST: tuple[Series, ...] = (
    Series("CUUR0000SA0", "CPI-U, all items, U.S. city average",
           "Inflation", "index 1982-84=100", DELTA_PCT, False),
    Series("WPUFD4", "PPI, final demand",
           "Inflation", "index Nov 2009=100", DELTA_PCT, False),
    Series("LNS14000000", "Unemployment rate",
           "Labor", "percent", DELTA_PP, True),
    Series("CES0000000001", "Total nonfarm payrolls",
           "Labor", "thousands of jobs", DELTA_LEVEL, True),
    Series("CES0500000003", "Average hourly earnings, total private",
           "Labor", "dollars per hour", DELTA_PCT, True),
)

# BLS period codes: M01-M12 are months, M13 is the annual average, S01/S02 are
# semiannual, Q01-Q04 quarterly. Only M13 can actually appear here (and only
# with annualaverage=true, which we do not send), but a stray annual average
# sorted in among the months would be read as a monthly step and corrupt both
# the delta and the z. Filtering is cheap insurance.
_MONTH_PERIOD = re.compile(r"^M(0[1-9]|1[0-2])$")

# The provider's own wording for a bad registration key, captured live:
#   "The key:INVALID provided by the User is invalid. Please provide a proper
#    key for the operation to be successful"
# A key is also named in the daily-quota message, which is a rate limit rather
# than a rejected credential, so the quota wording is checked first.
_QUOTA_WORDS = re.compile(r"threshold|quota|too many requests", re.I)
_AUTH_WORDS = re.compile(r"\bkey\b|registration|not\s+authorized|unauthorized", re.I)

# A per-series failure that arrives *inside* a REQUEST_SUCCEEDED body.
_BAD_SERIES = re.compile(r"invalid series|series does not exist", re.I)


class Obs(NamedTuple):
    year: int
    month: int
    value: float
    preliminary: bool


# --------------------------------------------------------------------------- #
# Response validation
#
# Everything here runs before a single row is read, because the failure mode
# this section exists to avoid is a well-formed 200 that parses to nothing.
# --------------------------------------------------------------------------- #

def check_status(payload: dict) -> None:
    """Raise the right typed failure for any non-success signal in the body.

    BLS answers HTTP 200 for authentication failures, quota exhaustion and
    malformed requests alike; the only discriminator is ``status`` plus the
    ``message`` array. A response that reaches the end of this function is one
    the parser may trust.
    """
    if not isinstance(payload, dict):
        raise UpstreamSchemaError(
            f"bls: expected a JSON object, got {type(payload).__name__}")

    status = payload.get("status")
    messages = [str(m) for m in (payload.get("message") or [])]
    joined = " | ".join(messages) or "(no message)"

    if status != SUCCESS:
        # Quota before auth: the daily-limit message also names the key.
        if _QUOTA_WORDS.search(joined):
            raise UpstreamHTTPError(
                f"bls: request refused (HTTP 200, status={status!r}) — "
                f"rate limited: {joined}")
        if _AUTH_WORDS.search(joined):
            raise UpstreamAuthError(
                f"bls: registration key rejected (HTTP 200, status={status!r}): "
                f"{joined}")
        raise UpstreamHTTPError(
            f"bls: request not processed (HTTP 200, status={status!r}): {joined}")

    # REQUEST_SUCCEEDED still carries per-series errors in message[]: a bad id
    # comes back as a success with an empty data array and a note here.
    bad = [m for m in messages if _BAD_SERIES.search(m)]
    if bad:
        raise UpstreamSchemaError(
            f"bls: request succeeded but a watched series was rejected: "
            f"{' | '.join(bad)}")


def series_blocks(payload: dict) -> dict[str, dict]:
    """Index the response by series id. Raises if the envelope is not the
    documented shape — a reshaped envelope is a broken source, not a quiet
    month."""
    results = payload.get("Results")
    if not isinstance(results, dict):
        raise UpstreamSchemaError(
            "bls: response has no 'Results' object")
    series = results.get("series")
    if not isinstance(series, list):
        raise UpstreamSchemaError(
            "bls: 'Results' has no 'series' list")
    out: dict[str, dict] = {}
    for block in series:
        if isinstance(block, dict) and block.get("seriesID"):
            out[str(block["seriesID"])] = block
    return out


def monthly_observations(block: dict) -> list[Obs]:
    """Monthly observations from one series block, oldest first.

    Non-numeric values are dropped rather than raised on: BLS writes "-" into
    a cell it could not publish, and carries the reason as a footnote. This is
    live, not hypothetical — LNS14000000 for 2025-M10 is ``"value": "-"`` with
    footnote 9, "Data unavailable due to the 2025 lapse in appropriations".
    That is a hole in a healthy series, not a broken source. A series where
    *every* cell is unusable is caught by the caller, which is where it does
    mean something.
    """
    out: list[Obs] = []
    for raw in block.get("data") or []:
        if not isinstance(raw, dict):
            continue
        period = str(raw.get("period") or "")
        if not _MONTH_PERIOD.match(period):
            continue
        try:
            value = float(str(raw.get("value")).replace(",", ""))
            year = int(str(raw.get("year")))
        except (TypeError, ValueError):
            continue
        codes = {
            str(f.get("code") or "").upper()
            for f in (raw.get("footnotes") or []) if isinstance(f, dict)
        }
        out.append(Obs(year, int(period[1:]), value, "P" in codes))
    # Sort rather than trusting the newest-first order the API happens to use;
    # the ordering is not part of any documented contract.
    out.sort(key=lambda o: (o.year, o.month))
    return out


# --------------------------------------------------------------------------- #
# Changes
# --------------------------------------------------------------------------- #

def _change(new: float, old: float, kind: str) -> Optional[float]:
    """The headline change for `kind`; None when a percent change is undefined."""
    if kind == DELTA_PCT:
        return None if old == 0 else 100.0 * (new / old - 1.0)
    return new - old


def _shift(year: int, month: int, lag: int) -> tuple[int, int]:
    """The (year, month) `lag` months before (year, month)."""
    index = year * 12 + (month - 1) - lag
    return index // 12, index % 12 + 1


def change_series(obs: list[Obs], lag: int, kind: str) -> list[float]:
    """All `lag`-month changes in the same transform as the headline delta.

    Paired by calendar month rather than by list position, so a hole in the
    series drops that comparison instead of silently redefining "last month"
    as "two months ago".
    """
    by_key = {(o.year, o.month): o for o in obs}
    out: list[float] = []
    for o in obs:
        prior = by_key.get(_shift(o.year, o.month, lag))
        if prior is None:
            continue
        delta = _change(o.value, prior.value, kind)
        if delta is not None:
            out.append(delta)
    return out


def zscore(changes: list[float]) -> Optional[float]:
    """Standardize the latest change against the ones before it.

    Excludes the current observation from the mean and sd so the reference
    distribution is not shifted by the value being judged. Returns None when
    there is too little history or no dispersion — a zero-variance series would
    otherwise report an infinite anomaly the first time it moved.
    """
    if len(changes) < MIN_Z_HISTORY + 1:
        return None
    prior, current = changes[:-1], changes[-1]
    mean = sum(prior) / len(prior)
    var = sum((x - mean) ** 2 for x in prior) / (len(prior) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (current - mean) / sd


# --------------------------------------------------------------------------- #
# Item construction
# --------------------------------------------------------------------------- #

def _fmt(value: float) -> str:
    """Level display: three decimals, trailing zeros trimmed. The decimal point
    stops the strip, so an integral value like 158850.0 keeps its zero."""
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _delta_text(diff: Optional[float], pct: Optional[float], kind: str,
                unit: str) -> str:
    if diff is None:
        return "n/a"
    if kind == DELTA_PCT:
        return "n/a" if pct is None else f"{pct:+.2f}%"
    if kind == DELTA_PP:
        return f"{diff:+.1f} pp"
    # A count: lead with the level move, keep the percent as the qualifier.
    tail = f" ({pct:+.2f}%)" if pct is not None else ""
    return f"{diff:+,.0f} {unit}{tail}"


def build_item(spec: Series, obs: list[Obs], *, api_version: str,
               api_mode: str) -> dict:
    """One brief row for one series: latest level plus MoM and YoY change."""
    latest = obs[-1]
    by_key = {(o.year, o.month): o for o in obs}
    prev = by_key.get(_shift(latest.year, latest.month, 1))
    year_ago = by_key.get(_shift(latest.year, latest.month, 12))

    # Both forms are stored for every series; `delta_kind` says which one is
    # the headline, so a downstream consumer never has to guess whether
    # `mom_change` is a percentage or a difference.
    mom_diff = latest.value - prev.value if prev else None
    yoy_diff = latest.value - year_ago.value if year_ago else None
    mom_pct = (100.0 * (latest.value / prev.value - 1.0)
               if prev and prev.value else None)
    yoy_pct = (100.0 * (latest.value / year_ago.value - 1.0)
               if year_ago and year_ago.value else None)

    # For an unadjusted series the month-over-month step is dominated by
    # seasonality, so its dispersion is standardized on the 12-month change
    # instead — differencing at the seasonal frequency is what removes it.
    z_basis = "mom" if spec.seasonally_adjusted else "yoy"
    changes = change_series(obs, 1 if z_basis == "mom" else 12, spec.delta)
    z = zscore(changes)

    period = f"{latest.year}-{latest.month:02d}"
    adjustment = ("seasonally adjusted" if spec.seasonally_adjusted
                  else "NOT seasonally adjusted")
    summary = (
        f"{_fmt(latest.value)} {spec.unit} for {period} · "
        f"MoM {_delta_text(mom_diff, mom_pct, spec.delta, spec.unit)} · "
        f"YoY {_delta_text(yoy_diff, yoy_pct, spec.delta, spec.unit)} · "
        f"{adjustment}"
    )
    if not spec.seasonally_adjusted:
        # Said plainly rather than left for the reader to infer: the MoM here
        # is not the number the CPI/PPI release leads with.
        summary += (" — its MoM change carries seasonal movement and is not the "
                    "seasonally-adjusted headline; the 12-month change is the "
                    "published one")
    if latest.preliminary:
        summary += " · preliminary, subject to revision"
    if z is not None:
        summary += f" · {z_basis.upper()} z={z:+.1f} vs own history"
    summary += f" · via BLS API {api_version} ({api_mode})"

    return {
        "id": f"bls:{spec.series_id}:{period}",
        # A monthly datum belongs to its reference month, not to the day the
        # brief ran; the first of the month is the conventional stand-in.
        "date": f"{latest.year}-{latest.month:02d}-01",
        "title": f"[{spec.group}] {spec.label} ({spec.series_id})",
        "url": f"{SERIES_VIEWER}/{spec.series_id}",
        "summary": summary,
        "series_id": spec.series_id,
        "label": spec.label,
        "group": spec.group,
        "unit": spec.unit,
        "period": period,
        "value": latest.value,
        "delta_kind": spec.delta,
        "seasonally_adjusted": spec.seasonally_adjusted,
        # Arithmetic differences in the series' native unit: percentage points
        # for a rate, thousands of jobs for payrolls, index points for an index.
        "mom_change": mom_diff,
        "yoy_change": yoy_diff,
        # Percent changes are carried for every series *except* a rate, where a
        # percent change of a percentage is a category error waiting to be
        # quoted by a downstream consumer.
        "mom_pct": None if spec.delta == DELTA_PP else mom_pct,
        "yoy_pct": None if spec.delta == DELTA_PP else yoy_pct,
        "change_z": None if z is None else round(z, 2),
        "change_z_basis": z_basis,
        "change_z_n": len(changes),
        "preliminary": latest.preliminary,
        "observations": len(obs),
        "api_version": api_version,
        "api_mode": api_mode,
    }


def parse_response(payload: dict, watchlist: tuple[Series, ...] = WATCHLIST, *,
                   api_version: str = "v1", api_mode: str = "keyless") -> list[dict]:
    """Validate the body, then emit one item per watched series.

    Raises UpstreamSchemaError when any watched series comes back without a
    usable observation. These are continuously published monthly series with
    decades of history; over a three-year window, zero observations means the
    id was retired or the request was wrong, never that the month was quiet.
    An empty return from this section would be a lie.
    """
    check_status(payload)
    blocks = series_blocks(payload)

    items: list[dict] = []
    empty: list[str] = []
    for spec in watchlist:
        obs = monthly_observations(blocks.get(spec.series_id) or {})
        if not obs:
            empty.append(spec.series_id)
            continue
        items.append(build_item(spec, obs, api_version=api_version,
                                api_mode=api_mode))

    if empty:
        raise UpstreamSchemaError(
            f"bls: no usable monthly observations for {', '.join(empty)} "
            f"(requested {len(watchlist)} series, parsed {len(items)}). These "
            f"series publish monthly without interruption; an empty result is "
            f"a broken request, not a quiet month.")

    items.sort(key=lambda it: (it["group"], it["label"]))
    return items


# --------------------------------------------------------------------------- #
# Section
# --------------------------------------------------------------------------- #

class BlsSection(Section):
    id = "bls"
    title = "BLS headline series (CPI, PPI, jobs, wages)"
    emoji = "🧮"

    source_id = "bls"
    source_name = "U.S. Bureau of Labor Statistics"
    source_url = "https://www.bls.gov"
    source_tier = "primary_document"
    source_license = "public-domain"
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 45

    # Capability contract: the v1 endpoint is fully keyless, so a missing key
    # degrades the request budget (25/day vs 500/day) and the maximum span
    # rather than disabling the section. Which mode ran is printed on every
    # item; see the module docstring.
    optional_env = ("BLS_API_KEY",)

    def _post(self, url: str, body: dict) -> dict:
        try:
            resp = requests.post(
                url, json=body,
                headers={"User-Agent": UA, "Content-Type": "application/json"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise UpstreamHTTPError(
                f"bls: request to {url} failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise UpstreamHTTPError(
                f"bls: {url} returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamParseError(
                f"bls: body was not JSON ({exc}); first 200 chars: "
                f"{resp.text[:200]!r}") from exc

    def pull(self) -> list[dict]:
        today = date.today()
        body = {
            "seriesid": [s.series_id for s in WATCHLIST],
            "startyear": str(today.year - LOOKBACK_YEARS),
            "endyear": str(today.year),
        }

        key = os.environ.get("BLS_API_KEY")
        if key:
            # No fallback to v1 from here. If this key is rejected, check_status
            # raises UpstreamAuthError and the run is recorded as failed —
            # answering from the keyless endpoint instead would hide a dead
            # credential for as long as it stayed dead.
            body["registrationkey"] = key
            url, api_version, api_mode = API_V2, "v2", "keyed"
        else:
            url, api_version, api_mode = API_V1, "v1", "keyless — BLS_API_KEY not set"

        payload = self._post(url, body)
        return parse_response(payload, WATCHLIST,
                              api_version=api_version, api_mode=api_mode)

    def emit_structured(self, state: "SectionState") -> dict:
        base = super().emit_structured(state)
        for it in state.items:
            z = it.get("change_z")
            if z is None or abs(z) < Z_ANOMALY:
                continue
            basis = "month-over-month" if it.get("change_z_basis") == "mom" \
                else "year-over-year"
            base["anomalies"].append({
                "category": "macro-series-move",
                "z_score": abs(z),
                # Named as a standardization, not a test: see the module
                # docstring on why |z| >= 2 here is descriptive only.
                "description": (
                    f"{it.get('label', it.get('series_id', ''))} "
                    f"{basis} change for {it.get('period', '')} is {z:+.1f} sd "
                    f"from the mean of the preceding changes, in a window of "
                    f"{it.get('change_z_n', 0)} (descriptive standardization "
                    f"against the series' own recent history, not a test)"),
                "evidence": [it["id"]],
            })
        # The degraded/keyed mode travels with the structured payload too, so a
        # consumer reading only structured.json still sees which endpoint ran.
        modes = sorted({it.get("api_mode", "") for it in state.items if it.get("api_mode")})
        base["api_mode"] = ", ".join(modes)
        return base
