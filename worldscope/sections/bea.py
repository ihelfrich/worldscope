"""bea.py — U.S. Bureau of Economic Analysis national accounts (NIPA).

Why this section exists
=======================
`macro` already carries GDP and personal income, but it reads them from FRED,
which republishes BEA one step removed and flattens each account into a single
number. A brief that reasons about *composition* — which component of GDP
turned, whether personal outlays outran personal income — needs the table, not
the headline: the line structure, the metric, and the units as the compiling
agency published them. BEA is `primary_document` tier where FRED is an
aggregator, so the same fact arrives here with a higher trust weight.

*** Why every response goes through results_blocks() ***
=========================================================
BEA answers an authentication failure with **HTTP 200** (verified against a
live probe, 2026-08-24)::

    {"BEAAPI":{"Request":{...},"Results":{"Error":{
        "APIErrorCode":"1",
        "APIErrorDescription":"Invalid Request - Invalid API UserId."}}}}

`raise_for_status()` passes on that body, `Results["Data"]` is simply absent,
and a naive parser yields zero items. A revoked or rotated key would look
exactly like a quiet day at the Bureau of Economic Analysis — the silent-sensor
failure mode this codebase was rebuilt to eliminate. So the error signal is
read out of the *body* before any data is touched, and it raises. Nothing in
this module can return [] for a source that did not answer.

Documented approximations and deliberate choices
================================================
  * **The table watchlist is validated against BEA's live catalog, strictly.**
    `pull()` calls GetParameterValues(TableName) first and raises
    UpstreamSchemaError if any watched table id is absent. These are the most
    stable ids in NIPA (Table 1.1.1 has been Table 1.1.1 for decades), so an
    absent one means the watchlist is wrong, not that the economy went quiet.
    Failing loudly on day one is the point: a wrong id would otherwise emit a
    permanently half-empty section that no one would notice.
  * **UNIT_MULT is carried, never applied.** BEA reports the scale exponent
    alongside the value; rescaling here would mean inventing a magnitude we
    cannot verify against the published table. `value` is exactly what BEA
    returned and `unit_mult` travels with it for any consumer that needs
    absolute dollars.
  * **`url` is the BEA landing page for the account plus a `#<table>-L<line>`
    fragment.** BEA's iTable deep-link format is a stateful `reqid/step` query
    string we cannot confirm without a live browser round trip, and a
    fabricated link that 404s is worse than an honest one that resolves to the
    right release page. The fragment records which table and line the number
    came from; BEA's page has no such anchor, so it lands at the top. It is
    also load-bearing: `Section._dedup_display_items` collapses items that
    share a URL (query string stripped, fragment kept), so a single shared
    landing page would render this whole section as one line.
  * **Item ids are period-scoped** (`bea:NIPA:<table>:L<line>:<period>`), so a
    new quarter arrives as a NEW item. An *annual revision* to an already-
    published quarter therefore updates the value in place and does not show a
    NEW badge; the revised number is still what the brief renders.
  * **At most MAX_LINES_PER_TABLE lines per table are emitted**, aggregates
    first. Every item carries `table_lines_available` so the truncation is
    visible in the data rather than hidden by it.

Cost: one GetParameterValues call plus one GetData call per watched table
(5 requests/run), well inside BEA's 100-requests-per-minute ceiling, so no
backoff machinery is warranted here.
"""
from __future__ import annotations

import calendar
import os
import re
from datetime import date
from typing import Any, NamedTuple, Optional

import requests

from . import (
    MissingCredential,
    Section,
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
    UpstreamSchemaError,
)

__version__ = "0.1.0"

API = "https://apps.bea.gov/api/data"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"
DATASET = "NIPA"

GDP_LANDING = "https://www.bea.gov/data/gdp/gross-domestic-product"
INCOME_LANDING = "https://www.bea.gov/data/income-saving/personal-income"


class TableSpec(NamedTuple):
    table: str        # NIPA TableName, validated against the live catalog
    freq: str         # A | Q | M — BEA rejects a frequency a table does not publish
    group: str        # display grouping in the brief
    url: str


# Headline quarterly product account plus the monthly income-and-outlays
# account. Percent change *and* levels *and* contributions, because the three
# answer different questions: how fast, how big, and which component moved.
WATCHLIST: tuple[TableSpec, ...] = (
    TableSpec("T10101", "Q", "GDP growth", GDP_LANDING),
    TableSpec("T10102", "Q", "GDP contributions", GDP_LANDING),
    TableSpec("T10105", "Q", "GDP levels", GDP_LANDING),
    TableSpec("T20600", "M", "Personal income", INCOME_LANDING),
)

# Aggregates that lead each table. Matched on exact (normalized) equality so a
# nested detail line ("Goods", "Durable goods") cannot displace its own parent.
HEADLINE_LINES: tuple[str, ...] = (
    "gross domestic product",
    "personal consumption expenditures",
    "gross private domestic investment",
    "net exports of goods and services",
    "exports",
    "imports",
    "government consumption expenditures and gross investment",
    "personal income",
    "disposable personal income",
    "personal outlays",
    "personal saving",
    "personal saving as a percentage of disposable personal income",
)

MAX_LINES_PER_TABLE = 15

# BEA's error code for a bad UserID in the 2026-08-24 probe. The description
# match below is the primary signal; the code is the fallback for a reworded
# message.
_AUTH_ERROR_CODE = "1"
_AUTH_PHRASES = (
    "userid", "user id", "api key", "apikey", "api userid",
    "not registered", "invalid api",
)

# 2026Q2 / 2026M06 / 2026 (annual). BEA is inconsistent about zero-padding the
# month across datasets, so the month group accepts one or two digits.
_PERIOD_RE = re.compile(r"^(\d{4})(?:Q([1-4])|M(\d{1,2}))?$")

_MISSING_VALUES = {"", "...", "(na)", "(d)", "n/a", "na", "--"}


# --------------------------------------------------------------------------- #
# Envelope handling — the auth-failure-on-HTTP-200 defense
# --------------------------------------------------------------------------- #

def _redact(text: str, key: Optional[str]) -> str:
    """Strip the API key out of anything that becomes an error message.

    requests puts the full effective URL into its exception strings, and the
    key travels in the query string as `UserID=`. Section errors are stored in
    the snapshot DB and rendered into the brief's HTML, so an unredacted
    ConnectionError would publish a GitHub Actions secret.
    """
    if not key:
        return text
    return text.replace(key, "***")


def _raise_api_error(err: Any) -> None:
    """Translate a BEA error object into the right typed failure. Never returns."""
    if isinstance(err, list):
        err = err[0] if err else {}
    if not isinstance(err, dict):
        raise UpstreamHTTPError(f"bea: unrecognized API error payload: {err!r}")

    code = str(err.get("APIErrorCode") or "").strip()
    detail = err.get("ErrorDetail")
    desc = (
        err.get("APIErrorDescription")
        or (detail.get("Description") if isinstance(detail, dict) else "")
        or "unspecified BEA API error"
    )
    desc = str(desc).strip()

    lowered = desc.lower()
    if code == _AUTH_ERROR_CODE or any(p in lowered for p in _AUTH_PHRASES):
        raise UpstreamAuthError(
            f"bea: credential rejected (APIErrorCode {code or '?'}): {desc}"
        )
    # Any other API error is still a source that did not answer. It raises as
    # an HTTP failure rather than degrading into an empty day.
    raise UpstreamHTTPError(f"bea: API error {code or '?'}: {desc}")


def results_blocks(payload: Any) -> list[dict]:
    """Return the Results block(s) from a BEA envelope, raising on any error.

    BEA nests everything under `BEAAPI` and puts errors in one of two places:
    `BEAAPI.Error` (request-level, e.g. an unknown method) and
    `BEAAPI.Results.Error` (the auth rejection, which arrives with HTTP 200).
    `Results` is an object for a single-table request and a list when BEA
    splits the answer, so both shapes are normalized to a list here.
    """
    if not isinstance(payload, dict):
        raise UpstreamSchemaError(
            f"bea: expected a JSON object, got {type(payload).__name__}"
        )
    api = payload.get("BEAAPI")
    if not isinstance(api, dict):
        raise UpstreamSchemaError("bea: response has no BEAAPI envelope")

    if api.get("Error"):
        _raise_api_error(api["Error"])

    results = api.get("Results")
    if results is None:
        raise UpstreamSchemaError("bea: BEAAPI envelope carried no Results block")

    blocks = results if isinstance(results, list) else [results]
    out: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise UpstreamSchemaError(
                f"bea: Results entry was {type(block).__name__}, not an object"
            )
        if block.get("Error"):
            _raise_api_error(block["Error"])
        out.append(block)
    if not out:
        raise UpstreamSchemaError("bea: Results block was empty")
    return out


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_table_catalog(payload: Any) -> dict[str, str]:
    """TableName -> Description from GetParameterValues(NIPA, TableName).

    An empty catalog is a broken source, not a quiet day: NIPA has published
    hundreds of tables continuously since the API existed.
    """
    out: dict[str, str] = {}
    for block in results_blocks(payload):
        values = block.get("ParamValue") or []
        if isinstance(values, dict):
            values = [values]
        for v in values:
            if not isinstance(v, dict):
                continue
            # BEA labels this parameter TableName for NIPA; older payloads and
            # some datasets use TableID / Key, so accept the aliases rather
            # than silently reading an empty catalog.
            name = v.get("TableName") or v.get("TableID") or v.get("Key") or ""
            desc = v.get("Description") or v.get("Desc") or ""
            name = str(name).strip()
            if name:
                out[name] = str(desc).strip()
    if not out:
        raise UpstreamSchemaError(
            "bea: GetParameterValues(TableName) returned no tables — NIPA "
            "always publishes a catalog, so this is a broken response"
        )
    return out


def parse_value(text: Any) -> Optional[float]:
    """BEA sends numbers as display strings: '30,353.9', '-1.2', '...'."""
    s = str(text if text is not None else "").strip()
    if s.lower() in _MISSING_VALUES:
        return None
    s = s.replace(",", "")
    # Some BEA series show a suppressed/negative value in parentheses.
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def parse_data(payload: Any, *, table: str = "") -> list[dict]:
    """Normalize a GetData response into flat rows.

    Raises rather than returning [] on an absent or unusable Data block: the
    requested window is always two full years of a published national account,
    so zero rows means the request or the schema is wrong, never that the
    quarter was quiet.
    """
    label = table or "GetData"
    saw_data = False
    rows: list[dict] = []

    for block in results_blocks(payload):
        data = block.get("Data")
        if data is None:
            continue
        saw_data = True
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise UpstreamSchemaError(
                f"bea: {label} Data was {type(data).__name__}, not a list"
            )
        for raw in data:
            if not isinstance(raw, dict):
                continue
            period = str(raw.get("TimePeriod") or "").strip()
            desc = str(raw.get("LineDescription") or "").strip()
            line = str(raw.get("LineNumber") or "").strip()
            if not period or not desc:
                continue
            value_text = str(raw.get("DataValue") or "").strip()
            rows.append({
                "table": str(raw.get("TableName") or table or "").strip(),
                "series_code": str(raw.get("SeriesCode") or "").strip(),
                "line": line,
                "line_description": desc,
                "period": period,
                "value": parse_value(value_text),
                "value_text": value_text,
                # METRIC_NAME is the descriptive unit for NIPA ("Percent change,
                # annual rate"); CL_UNIT is the fallback name used elsewhere.
                "units": str(raw.get("METRIC_NAME") or raw.get("CL_UNIT") or "").strip(),
                "unit_mult": str(raw.get("UNIT_MULT") or "").strip(),
            })

    if not saw_data:
        raise UpstreamSchemaError(
            f"bea: {label} response carried no Data block (BEA answered, but "
            f"not with observations)"
        )
    if not rows:
        raise UpstreamSchemaError(
            f"bea: {label} returned zero usable rows for a two-year window — "
            f"treating a published national account with no observations as a "
            f"broken source, not a quiet quarter"
        )
    if not any(r["units"] for r in rows):
        # Units are part of what this section promises to emit. Losing them
        # silently would publish bare numbers with no scale.
        raise UpstreamSchemaError(
            f"bea: {label} rows carried neither METRIC_NAME nor CL_UNIT — "
            f"upstream schema drift, units cannot be reported"
        )
    return rows


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #

def period_key(period: str) -> tuple[int, int]:
    """Sortable (year, sub-period) key. Unparseable periods sort first so they
    can never win a max() and be mistaken for the latest observation."""
    m = _PERIOD_RE.match((period or "").strip())
    if not m:
        return (0, 0)
    year = int(m.group(1))
    if m.group(2):
        return (year, int(m.group(2)))
    if m.group(3):
        return (year, int(m.group(3)))
    return (year, 0)


def period_to_date(period: str) -> str:
    """ISO date for the END of the period the observation covers.

    The lake stores this as `record_date`, and the economically meaningful
    date for a quarterly aggregate is the quarter it measures, not the day BEA
    published it. (The publication date is not in the GetData response at all.)
    """
    m = _PERIOD_RE.match((period or "").strip())
    if not m:
        return ""
    year = int(m.group(1))
    if m.group(2):
        month = int(m.group(2)) * 3
    elif m.group(3):
        month = int(m.group(3))
    else:
        month = 12
    if not 1 <= month <= 12:
        return ""
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _headline_rank(line_description: str) -> int:
    norm = (line_description or "").strip().lower().rstrip(".")
    try:
        return HEADLINE_LINES.index(norm)
    except ValueError:
        return len(HEADLINE_LINES)


def _line_sort(row: dict) -> tuple[int, int, str]:
    try:
        line_no = int(row.get("line") or 0)
    except ValueError:
        line_no = 0
    return (_headline_rank(row.get("line_description", "")), line_no, row.get("line", ""))


# --------------------------------------------------------------------------- #
# Item construction
# --------------------------------------------------------------------------- #

def build_items(spec: TableSpec, rows: list[dict],
                table_description: str = "") -> list[dict]:
    """One item per line of `spec.table` at its most recent period."""
    latest = max((r["period"] for r in rows), key=period_key, default="")
    if not latest or period_key(latest) == (0, 0):
        raise UpstreamSchemaError(
            f"bea: {spec.table} returned no parseable TimePeriod "
            f"(saw {sorted({r['period'] for r in rows})[:5]})"
        )

    current = sorted((r for r in rows if r["period"] == latest), key=_line_sort)
    available = len(current)
    iso_date = period_to_date(latest)

    items: list[dict] = []
    for row in current[:MAX_LINES_PER_TABLE]:
        shown = row["value_text"] or "—"
        units = row["units"]
        # The scale exponent is reported, not applied — see the module docstring.
        scale = (f" ×10^{row['unit_mult']}"
                 if row["unit_mult"] not in ("", "0") else "")
        items.append({
            "id": f"bea:{DATASET}:{spec.table}:L{row['line'] or '?'}:{latest}",
            "date": iso_date,
            "title": f"[{spec.group}] {row['line_description']} · {shown}"
                     + (f" ({units})" if units else ""),
            # The fragment is provenance AND the thing that keeps the display
            # deduper from collapsing every line onto one landing page.
            "url": f"{spec.url}#{spec.table}-L{row['line'] or '0'}",
            "summary": (
                f"{latest}: {shown} {units}{scale} · {spec.table} line "
                f"{row['line'] or '?'}"
                + (f" · {table_description}" if table_description else "")
            ).strip(),
            "dataset": DATASET,
            "table": spec.table,
            "table_description": table_description,
            "frequency": spec.freq,
            "group": spec.group,
            "line": row["line"],
            "line_description": row["line_description"],
            "series_code": row["series_code"],
            "period": latest,
            "value": row["value"],
            "value_text": row["value_text"],
            "units": units,
            "unit_mult": row["unit_mult"],
            "is_headline": _headline_rank(row["line_description"]) < len(HEADLINE_LINES),
            # Makes the MAX_LINES_PER_TABLE truncation visible downstream.
            "table_lines_available": available,
        })
    return items


# --------------------------------------------------------------------------- #
# Section
# --------------------------------------------------------------------------- #

class BeaSection(Section):
    id = "bea"
    title = "BEA national accounts (GDP, personal income)"
    emoji = "📐"

    source_id = "bea-nipa"
    source_name = "U.S. Bureau of Economic Analysis — National Income and Product Accounts"
    source_url = "https://apps.bea.gov/api/data"
    source_tier = "primary_document"
    source_license = "public-domain"
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 90

    # Capability contract: BEA has no keyless mode — every request needs a
    # registered UserID — so this is required, not optional.
    requires_env = ('BEA_API_KEY',)

    # ---- HTTP ------------------------------------------------------------- #

    def _get(self, session: requests.Session, key: str, **params: Any) -> dict:
        """One BEA call. Every failure mode raises a typed SourceUnavailable."""
        query = {"UserID": key, "ResultFormat": "JSON", **params}
        try:
            resp = session.get(API, params=query, timeout=30)
        except requests.RequestException as exc:
            # requests embeds the effective URL (and therefore the key) in its
            # exception text; redact before it reaches the snapshot store.
            raise UpstreamHTTPError(
                _redact(f"bea: {type(exc).__name__}: {exc}", key)
            ) from exc
        if resp.status_code != 200:
            raise UpstreamHTTPError(
                f"bea: {params.get('method', '?')} returned HTTP {resp.status_code}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamParseError(
                _redact(f"bea: response body was not JSON: {exc}", key)
            ) from exc

    # ---- pull ------------------------------------------------------------- #

    @staticmethod
    def _years(today: Optional[date] = None) -> str:
        """Current and prior calendar year, so a January run still sees a full
        set of quarters/months from the year that just closed."""
        y = (today or date.today()).year
        return f"{y - 1},{y}"

    def pull(self) -> list[dict]:
        key = os.environ.get("BEA_API_KEY")
        if not key:
            # The base class gates on requires_env before pull() is reached;
            # this keeps the invariant true if the section is ever driven
            # directly (e.g. run_section) rather than through resolve().
            raise MissingCredential("bea: BEA_API_KEY not set")

        session = requests.Session()
        session.headers["User-Agent"] = UA

        catalog = parse_table_catalog(self._get(
            session, key,
            method="GetParameterValues",
            DataSetName=DATASET,
            ParameterName="TableName",
        ))

        missing = [s.table for s in WATCHLIST if s.table not in catalog]
        if missing:
            sample = sorted(catalog)[:8]
            raise UpstreamSchemaError(
                f"bea: watched NIPA table(s) {missing} are absent from BEA's "
                f"live TableName catalog ({len(catalog)} tables, e.g. {sample}). "
                f"Fix WATCHLIST — a stale id must not quietly halve this section."
            )

        years = self._years()
        items: list[dict] = []
        for spec in WATCHLIST:
            payload = self._get(
                session, key,
                method="GetData",
                DataSetName=DATASET,
                TableName=spec.table,
                Frequency=spec.freq,
                Year=years,
            )
            rows = parse_data(payload, table=spec.table)
            items.extend(build_items(spec, rows, catalog.get(spec.table, "")))

        if not items:
            # Unreachable while parse_data raises on empty, kept as a backstop
            # so a future refactor cannot reintroduce the silent-empty path.
            raise UpstreamSchemaError(
                "bea: every watched table parsed but produced no items"
            )
        return items

    # ---- structured sidecar ------------------------------------------------ #

    def emit_structured(self, state: "SectionState") -> dict:
        base = super().emit_structured(state)
        for it in state.items:
            if it.get("table") != "T10101":
                continue
            if _headline_rank(it.get("line_description", "")) != 0:
                continue  # only the top-line GDP row
            value = it.get("value")
            if value is None or value >= 0:
                continue
            base["anomalies"].append({
                "category": "macro-contraction",
                # BEA publishes no dispersion with the release, so there is no
                # honest z-score to compute here. This is a magnitude proxy —
                # the size of the contraction, halved to keep it on the same
                # rough scale as other sections' scores — and the description
                # states the actual number.
                "z_score": round(abs(float(value)) / 2.0, 2),
                "description": (
                    f"Real GDP contracted {value}% "
                    f"({it.get('units') or 'annual rate'}) in {it.get('period')} "
                    f"— BEA {it.get('table')}"
                ),
                "evidence": [it.get("_id") or it.get("id", "")],
            })
        return base
