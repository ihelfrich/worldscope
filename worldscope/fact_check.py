"""fact_check — extract and validate numeric claims in composed briefings.

The desk-officer markdown in ``briefings/<date>.md`` is narrative text
written after the data pull. This module turns its numeric assertions into
an auditable claim ledger and validates the claims that have deterministic
same-day lake evidence.

The original implementation only checked asset prices. That path is still
supported, but the canonical output is now ``claims.json``:

  - asset prices: validated against ``markets`` / ``markets_global``
  - percentages and yield/rates: validated when a known subject resolves
  - population/counts/statutes/dates/fx: surfaced for review or skipped

CLI:
  python -m worldscope.fact_check briefings/2026-05-28.md
  python -m worldscope.fact_check briefings/2026-05-28.md --annotate
  python -m worldscope.fact_check briefings/2026-05-28.md --write-ledger dist/data/claims.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Auditable validation config
# ---------------------------------------------------------------------------

VALIDATION_CONFIG: dict[str, Any] = {
    "asset_price": {
        # Canonical asset -> symbols/names seen in markets records + tolerance
        # as relative percentage divergence.
        "bitcoin":         {"symbols": ["BTC"], "asset_class": "crypto",       "tol": 0.05},
        "btc":             {"symbols": ["BTC"], "asset_class": "crypto",       "tol": 0.05},
        "ethereum":        {"symbols": ["ETH"], "asset_class": "crypto",       "tol": 0.05},
        "eth":             {"symbols": ["ETH"], "asset_class": "crypto",       "tol": 0.05},
        "wti":             {"symbols": ["WTI", "CL"], "asset_class": "commodity",  "tol": 0.05},
        "wti crude":       {"symbols": ["WTI", "CL"], "asset_class": "commodity",  "tol": 0.05},
        "brent":           {"symbols": ["BRENT", "BZ"], "asset_class": "commodity", "tol": 0.05},
        "gold":            {"symbols": ["GLD", "XAU", "GOLD"], "asset_class": "commodity", "tol": 0.03},
        "silver":          {"symbols": ["SLV", "XAG", "SILVER"], "asset_class": "commodity", "tol": 0.05},
        "natural gas":     {"symbols": ["NG", "UNG"], "asset_class": "commodity", "tol": 0.08},
        "s&p 500":         {"symbols": ["SPY", "S&P 500", "^SPX"], "asset_class": "equity_index", "tol": 0.02},
        "nasdaq":          {"symbols": ["QQQ", "Nasdaq 100", "^NDX"], "asset_class": "equity_index", "tol": 0.02},
        "nasdaq 100":      {"symbols": ["QQQ", "Nasdaq 100", "^NDX"], "asset_class": "equity_index", "tol": 0.02},
        "russell 2000":    {"symbols": ["IWM", "Russell 2000"], "asset_class": "equity_index", "tol": 0.025},
        "ftse":            {"symbols": ["FTSE", "FTSE 100"], "asset_class": "equity_index", "tol": 0.025},
        "ftse 100":        {"symbols": ["FTSE", "FTSE 100"], "asset_class": "equity_index", "tol": 0.025},
        "dax":             {"symbols": ["DAX"], "asset_class": "equity_index", "tol": 0.025},
        "nikkei":          {"symbols": ["NIKKEI", "Nikkei 225"], "asset_class": "equity_index", "tol": 0.025},
    },
    # Absolute tolerance in percentage points for rates/yields.
    "yield_rate": {
        "2-year treasury":  {"symbols": ["DGS2"], "tol_abs": 0.05},
        "10-year treasury": {"symbols": ["DGS10"], "tol_abs": 0.05},
        "30-year treasury": {"symbols": ["DGS30"], "tol_abs": 0.05},
        "fed funds":        {"symbols": ["DFF", "FEDFUNDS"], "tol_abs": 0.05},
    },
    # Relative tolerance for percentage change claims when a market subject
    # resolves to chg24/change_pct in the lake.
    "percentage": {"tol_abs": 0.05},
    "calendar_date": {"past_context_window": 50},
}

# Backwards-compatible name used by existing tests and callers.
ASSET_REGISTRY: dict[str, dict[str, Any]] = VALIDATION_CONFIG["asset_price"]


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------

LEDGER_STATUSES = ("verified", "divergent", "unverified", "skipped")
LEGACY_STATUS = {
    "verified": "PASS",
    "divergent": "FAIL",
    "unverified": "UNVERIFIED",
    "skipped": "SKIPPED",
}


@dataclass
class Claim:
    claim_type: str
    subject: str
    claimed_value: Any
    unit: str
    raw_text: str
    paragraph_offset: int
    skip_reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Compatibility shims for the old asset-price-only API.
    @property
    def asset_canonical(self) -> str:
        return self.subject

    @property
    def raw_match(self) -> str:
        return self.raw_text

    @property
    def text_offset(self) -> int:
        return self.paragraph_offset


@dataclass
class Verdict:
    claim: Claim
    actual_value: Optional[Any]
    actual_symbol: Optional[str]
    actual_source_url: Optional[str]
    status: str          # "PASS" | "FAIL" | "UNVERIFIED" | "SKIPPED"
    tolerance: Optional[float]
    delta_pct: Optional[float]
    note: str = ""
    evidence_record_ids: list[str] = field(default_factory=list)
    skip_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Patterns and extraction helpers
# ---------------------------------------------------------------------------

PRICE_PATTERN = re.compile(
    r"(?P<asset>"
    r"Bitcoin|BTC|Ethereum|ETH|"
    r"WTI Crude|WTI|Brent(?:\s+crude)?|"
    r"Gold|Silver|Natural\s+gas|"
    r"S&P\s*500|Nasdaq(?:\s+100)?|Russell\s+2000|"
    r"FTSE(?:\s+100)?|DAX|Nikkei(?:\s+225)?"
    r")\s+(?:at|trades?\s+at|currently|closed\s+at|hit|approximately|around)?"
    r"\s*(?:approximately\s+|around\s+|roughly\s+|nearly\s+|near\s+|about\s+|~)?"
    r"\$\s*(?P<value>\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suffix>\s*(?:/bbl|/barrel|k|m|million|billion))?",
    re.IGNORECASE,
)

YIELD_PATTERN = re.compile(
    r"(?P<subject>"
    r"(?:2|10|30)[-\s]?year(?:\s+Treasury)?(?:\s+yield)?|"
    r"(?:two|ten|thirty)[-\s]?year(?:\s+Treasury)?(?:\s+yield)?|"
    r"Fed(?:eral)?\s+funds(?:\s+effective)?\s+rate|fed\s+funds|"
    r"DGS2|DGS10|DGS30|FEDFUNDS|DFF"
    r")\s+(?:at|to|was|is|around|near|approximately|roughly|closed\s+at)?\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage\s+points?|basis\s+points?|bps)(?=\W|$)",
    re.IGNORECASE,
)

FX_PATTERN = re.compile(
    r"\b(?P<pair>[A-Z]{3}/[A-Z]{3})\s+"
    r"(?:at|to|was|is|around|near|approximately|roughly)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\b",
)

PERCENT_PATTERN = re.compile(
    r"(?P<subject>[A-Za-z][A-Za-z0-9 /&().,'-]{0,70}?)?"
    r"(?<![\w.])"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|percentage\s+points?|basis\s+points?|bps)(?=\W|$)",
    re.IGNORECASE,
)

NAMED_ENTITY_COUNT_PATTERN = re.compile(
    r"\b(?P<value>\d{1,3}(?:,\d{3})*|\d+)\s+"
    r"(?P<unit>countries|entities|sections|sources|states|provinces|ministries|agencies)\b",
    re.IGNORECASE,
)

POPULATION_PATTERN = re.compile(
    r"\b(?P<value>\d{1,3}(?:,\d{3})+|\d{3,})\s+"
    r"(?P<unit>people|persons|residents|workers|migrants|refugees|troops|soldiers|"
    r"declarations|vessels|ships|filings|cases|fatalities|deaths|jobs|permits|"
    r"contracts|barrels|records|units)\b",
    re.IGNORECASE,
)

STATUTE_PATTERNS = [
    re.compile(
        r"\bSection\s+\d+[A-Za-z0-9().-]*\s+of\s+the\s+"
        r"[A-Z][A-Za-z\s.-]+?\s+Act\s+of\s+\d{4}\b"
    ),
    re.compile(r"\b\d+\s+U\.S\.C\.\s*§+\s*\d+[A-Za-z0-9().-]*\b"),
    re.compile(r"\b\d+\s+C\.F\.R\.\s*§+\s*\d+[A-Za-z0-9().-]*\b"),
]

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
DATE_PATTERN = re.compile(
    r"\b(?P<month>" + "|".join(MONTHS) + r")\s+"
    r"(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?\b"
)

FORECAST_CONTEXT = re.compile(
    r"\b(by\s+\w+|target|strike|polymarket|contract|forecast|hit\s+\$|"
    r"reach(?:es|ing)?|trades?\s+at\s+\d+%|"
    r"\b\d+%\s+probabilit|priced\s+as|impossib)",
    re.IGNORECASE,
)

PAST_CONTEXT = re.compile(
    r"\b(reported|announced|issued|filed|published|released|signed|closed|"
    r"met|occurred|happened|began|started|ended|took effect|as of|on)\b",
    re.IGNORECASE,
)


def _normalize_value(value_str: str, suffix: str | None = None) -> float:
    v = float(value_str.replace(",", "").replace(" ", ""))
    if suffix:
        s = suffix.strip().lower()
        if s == "k":
            v *= 1_000
        elif s in ("m", "million"):
            v *= 1_000_000
        elif s == "billion":
            v *= 1_000_000_000
    return v


def _looks_like_forecast(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 50):min(len(text), end + 50)]
    return bool(FORECAST_CONTEXT.search(window))


def _month_num(name: str) -> int:
    return MONTHS.index(name) + 1


def _canonical_asset(asset: str) -> Optional[str]:
    key = re.sub(r"\s+", " ", asset.lower().strip())
    if key in ASSET_REGISTRY:
        return key
    stem = key.split()[0]
    return stem if stem in ASSET_REGISTRY else None


def _canonical_yield_subject(subject: str) -> Optional[str]:
    s = re.sub(r"\s+", " ", subject.lower().replace("-", " ").strip())
    if "dgs2" in s or s.startswith("2 year") or s.startswith("two year"):
        return "2-year treasury"
    if "dgs10" in s or s.startswith("10 year") or s.startswith("ten year"):
        return "10-year treasury"
    if "dgs30" in s or s.startswith("30 year") or s.startswith("thirty year"):
        return "30-year treasury"
    if "fedfunds" in s or s == "dff" or "fed funds" in s or "federal funds" in s:
        return "fed funds"
    return None


def _find_asset_in_text(text: str) -> Optional[str]:
    low = text.lower()
    for key in sorted(ASSET_REGISTRY, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", low):
            return key
    return None


def _percent_to_points(value: float, unit: str) -> tuple[float, str]:
    u = unit.lower().strip()
    if u in ("bps", "basis point", "basis points"):
        return value / 100.0, "%"
    return value, "%"


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < e and span[1] > s for s, e in spans)


def _add_claim(
    claims: list[Claim],
    spans: list[tuple[int, int]],
    *,
    claim_type: str,
    subject: str,
    claimed_value: Any,
    unit: str,
    raw_text: str,
    offset: int,
    span: tuple[int, int],
    skip_reason: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    claims.append(Claim(
        claim_type=claim_type,
        subject=subject,
        claimed_value=claimed_value,
        unit=unit,
        raw_text=raw_text,
        paragraph_offset=offset,
        skip_reason=skip_reason,
        metadata=metadata or {},
    ))
    spans.append(span)


def extract_ledger_claims(text: str, *, brief_date: Optional[date] = None) -> list[Claim]:
    """Extract typed claims, including skipped claims for ledger accounting."""
    claims: list[Claim] = []
    occupied: list[tuple[int, int]] = []

    for m in PRICE_PATTERN.finditer(text):
        key = _canonical_asset(m.group("asset"))
        if not key:
            continue
        raw = m.group(0)
        value = _normalize_value(m.group("value"), m.group("suffix"))
        _add_claim(
            claims, occupied,
            claim_type="asset_price", subject=key, claimed_value=value,
            unit="USD", raw_text=raw, offset=m.start(), span=m.span(),
            skip_reason="forecast_context" if _looks_like_forecast(text, m.start(), m.end()) else None,
        )

    for m in FX_PATTERN.finditer(text):
        if _overlaps(m.span(), occupied):
            continue
        _add_claim(
            claims, occupied,
            claim_type="fx_rate", subject=m.group("pair"), claimed_value=float(m.group("value")),
            unit="", raw_text=m.group(0), offset=m.start(), span=m.span(),
            skip_reason="fx_convention_ambiguous",
        )

    for m in YIELD_PATTERN.finditer(text):
        if _overlaps(m.span(), occupied):
            continue
        subject = _canonical_yield_subject(m.group("subject"))
        if not subject:
            continue
        val, unit = _percent_to_points(float(m.group("value")), m.group("unit"))
        _add_claim(
            claims, occupied,
            claim_type="yield_rate", subject=subject, claimed_value=val,
            unit=unit, raw_text=m.group(0), offset=m.start(), span=m.span(),
        )

    for pat in STATUTE_PATTERNS:
        for m in pat.finditer(text):
            if _overlaps(m.span(), occupied):
                continue
            _add_claim(
                claims, occupied,
                claim_type="statute_citation", subject=m.group(0), claimed_value=m.group(0),
                unit="", raw_text=m.group(0), offset=m.start(), span=m.span(),
            )

    for m in DATE_PATTERN.finditer(text):
        if _overlaps(m.span(), occupied):
            continue
        year = int(m.group("year") or (brief_date.year if brief_date else date.today().year))
        try:
            parsed = date(year, _month_num(m.group("month")), int(m.group("day")))
        except ValueError:
            continue
        _add_claim(
            claims, occupied,
            claim_type="calendar_date", subject="calendar_date", claimed_value=parsed.isoformat(),
            unit="", raw_text=m.group(0), offset=m.start(), span=m.span(),
            skip_reason="forecast_context" if _looks_like_forecast(text, m.start(), m.end()) else None,
            metadata={"has_explicit_year": bool(m.group("year"))},
        )

    for m in NAMED_ENTITY_COUNT_PATTERN.finditer(text):
        if _overlaps(m.span(), occupied):
            continue
        # Years are not entity counts.
        if len(m.group("value")) == 4 and 1900 <= int(m.group("value")) <= 2100:
            continue
        _add_claim(
            claims, occupied,
            claim_type="named_entity_count", subject=m.group("unit").lower(),
            claimed_value=int(m.group("value").replace(",", "")),
            unit="count", raw_text=m.group(0), offset=m.start(), span=m.span(),
        )

    for m in POPULATION_PATTERN.finditer(text):
        if _overlaps(m.span(), occupied):
            continue
        if m.start() > 0 and text[m.start() - 1] == "$":
            continue
        _add_claim(
            claims, occupied,
            claim_type="population", subject=m.group("unit").lower(),
            claimed_value=int(m.group("value").replace(",", "")),
            unit="people" if m.group("unit").lower() in ("people", "persons", "residents") else m.group("unit").lower(),
            raw_text=m.group(0), offset=m.start(), span=m.span(),
        )

    for m in PERCENT_PATTERN.finditer(text):
        if _overlaps(m.span(), occupied):
            continue
        subject_raw = (m.group("subject") or "").strip(" ,.;:()[]")
        if not subject_raw:
            skip = "no_validator_for_subject"
            subject = ""
        else:
            subject = _find_asset_in_text(subject_raw) or _canonical_yield_subject(subject_raw) or subject_raw.lower()
            skip = None if (subject in ASSET_REGISTRY or subject in VALIDATION_CONFIG["yield_rate"]) else "no_validator_for_subject"
        val, unit = _percent_to_points(float(m.group("value")), m.group("unit"))
        if _looks_like_forecast(text, m.start(), m.end()):
            skip = "forecast_context"
        _add_claim(
            claims, occupied,
            claim_type="percentage", subject=subject, claimed_value=val,
            unit=unit, raw_text=m.group(0), offset=m.start(), span=m.span(),
            skip_reason=skip,
        )

    claims.sort(key=lambda c: c.paragraph_offset)
    return claims


def extract_claims(text: str) -> list[Claim]:
    """Compatibility wrapper: extract non-skipped claims from markdown text."""
    return [c for c in extract_ledger_claims(text) if not c.skip_reason]


# ---------------------------------------------------------------------------
# Lake lookups
# ---------------------------------------------------------------------------

def _load_extra(extra_json: str | None) -> dict[str, Any]:
    try:
        return json.loads(extra_json) if extra_json else {}
    except Exception:
        return {}


def _date_where(alias: str = "r") -> str:
    # Same-day first, then bounded prior-data fallback. Never read records
    # after the brief date for archived brief validation.
    return (
        f"(substr({alias}.ingested_at, 1, 10) = ? OR "
        f"(substr({alias}.ingested_at, 1, 10) >= date(?, '-3 days') "
        f"AND substr({alias}.ingested_at, 1, 10) <= ?))"
    )


def _lookup_market_price(
    conn: sqlite3.Connection, asset_key: str, day_iso: str,
) -> tuple[Optional[float], Optional[str], Optional[str], list[str]]:
    spec = ASSET_REGISTRY.get(asset_key)
    if not spec:
        return None, None, None, []
    rows = conn.execute(
        f"""
        SELECT r.id, r.original_text, r.original_url, r.extra_json, r.ingested_at
          FROM records r
         WHERE r.section_id IN ('markets', 'markets_global')
           AND {_date_where('r')}
         ORDER BY substr(r.ingested_at, 1, 10) = ? DESC, r.ingested_at DESC
        """,
        (day_iso, day_iso, day_iso, day_iso),
    ).fetchall()
    for record_id, orig, url, extra_json, _at in rows:
        extra = _load_extra(extra_json)
        sym = (extra.get("symbol") or "").upper()
        nm = (extra.get("name") or "").lower()
        ac = (extra.get("asset_class") or "").lower()
        if ac != spec["asset_class"]:
            continue
        symbol_match = any(
            s.upper() in (sym, nm.upper()) or nm == s.lower()
            for s in spec["symbols"]
        )
        if not symbol_match:
            continue
        close = extra.get("close")
        if close is None:
            price_m = re.search(r"\$?([\d,]+(?:\.\d+)?)", orig or "")
            if price_m:
                try:
                    close = float(price_m.group(1).replace(",", ""))
                except Exception:
                    close = None
        if close is not None:
            return float(close), sym or nm or asset_key, url, [record_id]
    return None, None, None, []


def _lookup_market_percent(
    conn: sqlite3.Connection, asset_key: str, day_iso: str,
) -> tuple[Optional[float], Optional[str], Optional[str], list[str]]:
    spec = ASSET_REGISTRY.get(asset_key)
    if not spec:
        return None, None, None, []
    rows = conn.execute(
        f"""
        SELECT r.id, r.original_text, r.original_url, r.extra_json, r.ingested_at
          FROM records r
         WHERE r.section_id IN ('markets', 'markets_global')
           AND {_date_where('r')}
         ORDER BY substr(r.ingested_at, 1, 10) = ? DESC, r.ingested_at DESC
        """,
        (day_iso, day_iso, day_iso, day_iso),
    ).fetchall()
    for record_id, orig, url, extra_json, _at in rows:
        extra = _load_extra(extra_json)
        sym = (extra.get("symbol") or "").upper()
        nm = (extra.get("name") or "").lower()
        ac = (extra.get("asset_class") or "").lower()
        if ac != spec["asset_class"]:
            continue
        if not any(s.upper() in (sym, nm.upper()) or nm == s.lower() for s in spec["symbols"]):
            continue
        val = extra.get("chg24")
        if val is None:
            val = extra.get("change_pct")
        if val is None:
            pct_m = re.search(r"([+-]?\d+(?:\.\d+)?)%", orig or "")
            if pct_m:
                val = pct_m.group(1)
        if val is not None:
            return float(val), sym or nm or asset_key, url, [record_id]
    return None, None, None, []


def _lookup_macro_rate(
    conn: sqlite3.Connection, subject: str, day_iso: str,
) -> tuple[Optional[float], Optional[str], Optional[str], list[str]]:
    spec = VALIDATION_CONFIG["yield_rate"].get(subject)
    if not spec:
        return None, None, None, []
    symbols = [s.upper() for s in spec["symbols"]]
    rows = conn.execute(
        f"""
        SELECT r.id, r.original_text, r.original_url, r.extra_json, r.ingested_at
          FROM records r
         WHERE r.section_id = 'macro'
           AND {_date_where('r')}
         ORDER BY substr(r.ingested_at, 1, 10) = ? DESC, r.ingested_at DESC
        """,
        (day_iso, day_iso, day_iso, day_iso),
    ).fetchall()
    for record_id, orig, url, extra_json, _at in rows:
        blob = f"{record_id} {orig or ''}".upper()
        if not any(s in blob for s in symbols):
            continue
        extra = _load_extra(extra_json)
        val = extra.get("value")
        if val in (None, "", "."):
            m = re.search(r"latest:\s*([+-]?\d+(?:\.\d+)?)", orig or "", flags=re.I)
            val = m.group(1) if m else None
        if val is not None:
            return float(val), symbols[0], url, [record_id]
    return None, None, None, []


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

ValidatorReturn = tuple[str, Optional[Any], list[str], str]


def _numeric_verdict(claim: Claim, actual: float, tolerance: float, *, absolute: bool) -> str:
    claimed = float(claim.claimed_value)
    if absolute:
        delta_abs = claimed - actual
        claim.metadata["divergence_pct"] = (delta_abs / actual) if actual else 0.0
        return "verified" if abs(delta_abs) <= tolerance else "divergent"
    delta = (claimed - actual) / actual if actual else 0.0
    claim.metadata["divergence_pct"] = delta
    return "verified" if abs(delta) <= tolerance else "divergent"


def validate_asset_price(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    actual, sym, src, evidence = _lookup_market_price(lake_conn, claim.subject, brief_date.isoformat())
    tol = float(ASSET_REGISTRY.get(claim.subject, {}).get("tol", 0.0))
    claim.metadata.update({"tolerance": tol, "actual_symbol": sym, "actual_source_url": src,
                           "validator": "asset_price/markets/sameday"})
    if actual is None:
        return "unverified", None, [], "no same-day market record in lake"
    status = _numeric_verdict(claim, actual, tol, absolute=False)
    return status, actual, evidence, ""


def validate_yield_rate(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    actual, sym, src, evidence = _lookup_macro_rate(lake_conn, claim.subject, brief_date.isoformat())
    tol = float(VALIDATION_CONFIG["yield_rate"].get(claim.subject, {}).get("tol_abs", 0.05))
    claim.metadata.update({"tolerance": tol, "actual_symbol": sym, "actual_source_url": src,
                           "validator": "yield_rate/fred/sameday"})
    if actual is None:
        return "unverified", None, [], "no same-day macro record in lake"
    status = _numeric_verdict(claim, actual, tol, absolute=True)
    return status, actual, evidence, ""


def validate_percentage(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    if claim.subject in VALIDATION_CONFIG["yield_rate"]:
        return validate_yield_rate(claim, lake_conn, brief_date)
    if claim.subject in ASSET_REGISTRY:
        actual, sym, src, evidence = _lookup_market_percent(lake_conn, claim.subject, brief_date.isoformat())
        tol = float(VALIDATION_CONFIG["percentage"]["tol_abs"])
        claim.metadata.update({"tolerance": tol, "actual_symbol": sym, "actual_source_url": src,
                               "validator": "percentage/markets/change_pct"})
        if actual is None:
            return "unverified", None, [], "no same-day percentage record in lake"
        status = _numeric_verdict(claim, actual, tol, absolute=True)
        return status, actual, evidence, ""
    return "skipped", None, [], "no_validator_for_subject"


def validate_population(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    claim.metadata["validator"] = "population/no_general_validator"
    return "unverified", None, [], "no general validator for population/count claim"


def validate_named_entity_count(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    claim.metadata["validator"] = "named_entity_count/no_general_validator"
    return "unverified", None, [], "no general validator for named-entity count"


def validate_statute_citation(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    claim.metadata["validator"] = "statute_citation/no_general_validator"
    return "unverified", None, [], "statute citation captured for evidence review"


def validate_calendar_date(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    claim.metadata["validator"] = "calendar_date/past_relative_to_brief"
    try:
        claimed = date.fromisoformat(str(claim.claimed_value))
    except ValueError:
        return "unverified", None, [], "could not parse calendar date"
    if claimed <= brief_date:
        return "verified", claimed.isoformat(), [], ""
    window = claim.metadata.get("context", "")
    if PAST_CONTEXT.search(window):
        return "divergent", brief_date.isoformat(), [], "future date used in past-tense context"
    return "skipped", None, [], "future_or_forecast_context"


def validate_fx_rate(claim: Claim, lake_conn: sqlite3.Connection, brief_date: date) -> ValidatorReturn:
    claim.metadata["validator"] = "fx_rate/skipped"
    return "skipped", None, [], "fx_convention_ambiguous"


VALIDATORS = {
    "asset_price": validate_asset_price,
    "percentage": validate_percentage,
    "yield_rate": validate_yield_rate,
    "population": validate_population,
    "named_entity_count": validate_named_entity_count,
    "statute_citation": validate_statute_citation,
    "calendar_date": validate_calendar_date,
    "fx_rate": validate_fx_rate,
}


def _attach_context(claims: list[Claim], text: str) -> None:
    for c in claims:
        c.metadata.setdefault(
            "context",
            text[max(0, c.paragraph_offset - 80): c.paragraph_offset + len(c.raw_text) + 80],
        )


def verify_claims(claims: list[Claim], lake_db: Path, day: date) -> list[Verdict]:
    """Run claims against the lake. Keeps the legacy PASS/FAIL status API."""
    if not lake_db.exists():
        out = []
        for c in claims:
            if c.skip_reason:
                out.append(Verdict(c, None, None, None, "SKIPPED", None, None,
                                   note=c.skip_reason, skip_reason=c.skip_reason))
            else:
                out.append(Verdict(c, None, None, None, "UNVERIFIED", None, None,
                                   note="lake DB missing"))
        return out

    conn = sqlite3.connect(f"file:{lake_db}?mode=ro", uri=True)
    out: list[Verdict] = []
    try:
        for c in claims:
            if c.skip_reason:
                out.append(Verdict(c, None, None, None, "SKIPPED", None, None,
                                   note=c.skip_reason, skip_reason=c.skip_reason))
                continue
            validator = VALIDATORS.get(c.claim_type)
            if validator is None:
                out.append(Verdict(c, None, None, None, "SKIPPED", None, None,
                                   note="no_validator_for_claim_type",
                                   skip_reason="no_validator_for_claim_type"))
                continue
            status, actual, evidence_ids, note = validator(c, conn, day)
            skip_reason = note if status == "skipped" else None
            out.append(Verdict(
                claim=c,
                actual_value=actual,
                actual_symbol=c.metadata.get("actual_symbol"),
                actual_source_url=c.metadata.get("actual_source_url"),
                status=LEGACY_STATUS[status],
                tolerance=c.metadata.get("tolerance"),
                delta_pct=c.metadata.get("divergence_pct"),
                note=note,
                evidence_record_ids=evidence_ids,
                skip_reason=skip_reason,
            ))
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Reports, annotations, ledger JSON
# ---------------------------------------------------------------------------

def annotate_markdown(text: str, verdicts: list[Verdict]) -> str:
    fails = sorted([v for v in verdicts if v.status == "FAIL"],
                   key=lambda v: -v.claim.text_offset)
    out = text
    for v in fails:
        end = v.claim.text_offset + len(v.claim.raw_match)
        if v.actual_value is None:
            actual = "unknown"
        elif isinstance(v.actual_value, (int, float)):
            prefix = "$" if v.claim.claim_type == "asset_price" else ""
            actual = f"{prefix}{v.actual_value:,.2f}"
        else:
            actual = str(v.actual_value)
        marker = f" `[⚠ actual ≈ {actual}]`"
        out = out[:end] + marker + out[end:]
    return out


def render_report(verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "No numeric claims detected."
    pass_n = sum(1 for v in verdicts if v.status == "PASS")
    fail_n = sum(1 for v in verdicts if v.status == "FAIL")
    unk_n = sum(1 for v in verdicts if v.status == "UNVERIFIED")
    skip_n = sum(1 for v in verdicts if v.status == "SKIPPED")
    lines = [f"Checked {len(verdicts)} claims: "
             f"{pass_n} PASS, {fail_n} FAIL, {unk_n} UNVERIFIED, {skip_n} SKIPPED"]
    tag_map = {"PASS": " ✓ ", "FAIL": " ✗ ", "UNVERIFIED": " ? ", "SKIPPED": " - "}
    for v in verdicts:
        c = v.claim
        actual = "(no record)" if v.actual_value is None else str(v.actual_value)
        if isinstance(v.actual_value, (int, float)):
            actual = f"{v.actual_value:,.4g}"
        delta = f"{v.delta_pct * 100:+.1f}%" if v.delta_pct is not None else "—"
        tol = f"{v.tolerance}" if v.tolerance is not None else "—"
        lines.append(
            f"{tag_map[v.status]}{c.claim_type:17s} {c.subject[:18]:18s} "
            f"claimed={c.claimed_value!s:>12s} {c.unit:>6s} "
            f"actual={actual:>14s} Δ={delta:>7s} tol={tol:>6s} "
            f"« {c.raw_text[:64]} »"
        )
        if v.note:
            lines.append(f"     note: {v.note}")
    return "\n".join(lines)


def _claim_id(brief_date: date, offset: int, raw_text: str) -> str:
    return hashlib.sha1(f"{brief_date.isoformat()}|{offset}|{raw_text}".encode("utf-8")).hexdigest()[:16]


def _ledger_status(v: Verdict) -> str:
    return {
        "PASS": "verified",
        "FAIL": "divergent",
        "UNVERIFIED": "unverified",
        "SKIPPED": "skipped",
    }[v.status]


def _generator_version() -> str:
    repo = Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", "worldscope/fact_check.py"],
            cwd=repo, text=True, capture_output=True, check=True,
        )
        return proc.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def ledger_from_verdicts(brief_date: date, verdicts: list[Verdict]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    summary = {k: 0 for k in LEDGER_STATUSES}
    for v in verdicts:
        c = v.claim
        status = _ledger_status(v)
        summary[status] += 1
        rows.append({
            "id": _claim_id(brief_date, c.paragraph_offset, c.raw_text),
            "brief_date": brief_date.isoformat(),
            "paragraph_offset": int(c.paragraph_offset),
            "raw_text": c.raw_text,
            "claim_type": c.claim_type,
            "subject": c.subject,
            "claimed_value": c.claimed_value,
            "unit": c.unit,
            "status": status,
            "evidence_record_ids": list(v.evidence_record_ids),
            "actual_value": v.actual_value,
            "tolerance": v.tolerance,
            "divergence_pct": v.delta_pct,
            "validator": c.metadata.get("validator") or f"{c.claim_type}/none",
            "skip_reason": v.skip_reason if status == "skipped" else None,
        })
    return {
        "brief_date": brief_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generator_version": _generator_version(),
        "summary": {
            "total": len(rows),
            "verified": summary["verified"],
            "divergent": summary["divergent"],
            "unverified": summary["unverified"],
            "skipped": summary["skipped"],
        },
        "claims": rows,
    }


def build_claim_ledger(md_path: Path, lake_db: Path, *,
                       day: Optional[date] = None) -> tuple[dict[str, Any], list[Verdict], str]:
    if day is None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.name)
        day = date.fromisoformat(m.group(1)) if m else date.today()
    text = md_path.read_text(encoding="utf-8")
    claims = extract_ledger_claims(text, brief_date=day)
    _attach_context(claims, text)
    verdicts = verify_claims(claims, lake_db, day)
    report = render_report(verdicts)
    return ledger_from_verdicts(day, verdicts), verdicts, report


def check_brief(md_path: Path, lake_db: Path, *,
                day: Optional[date] = None,
                annotate: bool = False,
                annotate_out: Optional[Path] = None) -> tuple[list[Verdict], str]:
    ledger, verdicts, report = build_claim_ledger(md_path, lake_db, day=day)
    if annotate:
        text = md_path.read_text(encoding="utf-8")
        annotated = annotate_markdown(text, verdicts)
        out_path = annotate_out or md_path.with_suffix(".annotated.md")
        out_path.write_text(annotated, encoding="utf-8")
    return verdicts, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="worldscope.fact_check",
        description="Verify numeric claims in a briefing markdown.")
    ap.add_argument("md_path", help="briefings/<date>.md to check")
    ap.add_argument("--lake", default="lake/db/worldscope.sqlite",
                    help="path to lake sqlite (default: lake/db/worldscope.sqlite)")
    ap.add_argument("--annotate", action="store_true",
                    help="write a .annotated.md with [⚠ actual X] markers next to FAILs")
    ap.add_argument("--write-ledger",
                    help="write the claim ledger JSON to this path")
    ap.add_argument("--fail-on", default="FAIL",
                    choices=("never", "FAIL", "UNVERIFIED"),
                    help="exit code 1 when any verdict matches this level (default: FAIL)")
    args = ap.parse_args(argv)

    md = Path(args.md_path)
    if not md.exists():
        print(f"{md} not found", file=sys.stderr)
        return 2
    lake = Path(args.lake)
    ledger, verdicts, report = build_claim_ledger(md, lake)
    print(report)
    if args.annotate:
        annotated = annotate_markdown(md.read_text(encoding="utf-8"), verdicts)
        md.with_suffix(".annotated.md").write_text(annotated, encoding="utf-8")
    if args.write_ledger:
        out_path = Path(args.write_ledger)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.fail_on == "never":
        return 0
    if args.fail_on == "FAIL" and any(v.status == "FAIL" for v in verdicts):
        return 1
    if args.fail_on == "UNVERIFIED" and any(v.status in ("FAIL", "UNVERIFIED") for v in verdicts):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
