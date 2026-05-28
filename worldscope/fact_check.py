"""fact_check — verify numeric price claims in a composed brief against
the structured market data the same day's pipeline ingested.

The daily brief has two layers:

  1. The data layer (worldscope/sections/markets_global.py, markets.py)
     pulls structured price data — symbol, name, asset_class, close,
     chg24 — into the lake and snapshot store.
  2. The narrative layer (the desk-officer Claude session, run outside
     this repo against the bundled brief) writes briefings/<date>.md.

When the narrative drifts — "Bitcoin at approximately $104,000" while
the lake says BTC=$74,816 — it's an LLM hallucination of a number that
was right there in the items handed to the model. This module catches
that drift before render_brief.py converts the markdown to HTML.

Approach:

  - Parse the markdown for "<asset> at [approximately] $<value>" and
    similar patterns.
  - For each match, resolve the asset name against the lake's markets
    records for the brief's date.
  - Compare claimed price to actual; report PASS / FAIL / UNVERIFIED
    with the tolerance used.
  - Optionally annotate the markdown inline with [⚠ actual $X] markers
    so a reader sees the divergence.

CLI:
  python -m worldscope.fact_check briefings/2026-05-28.md
  python -m worldscope.fact_check briefings/2026-05-28.md --annotate
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional


# Canonical asset → (symbol set, asset_class, tolerance_pct)
# Tolerance is "how far off the claim can be before we flag it." Tight for
# FX (sub-1% moves matter), loose for indices (5-figure values, rounding
# common), moderate for crypto.
ASSET_REGISTRY: dict[str, dict[str, Any]] = {
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
    "s&p 500":         {"symbols": ["SPY", "S&P 500"], "asset_class": "equity_index", "tol": 0.02},
    "nasdaq":          {"symbols": ["QQQ", "Nasdaq 100"], "asset_class": "equity_index", "tol": 0.02},
    "nasdaq 100":      {"symbols": ["QQQ", "Nasdaq 100"], "asset_class": "equity_index", "tol": 0.02},
    "russell 2000":    {"symbols": ["IWM", "Russell 2000"], "asset_class": "equity_index", "tol": 0.025},
    "ftse":            {"symbols": ["FTSE", "FTSE 100"], "asset_class": "equity_index", "tol": 0.025},
    "ftse 100":        {"symbols": ["FTSE", "FTSE 100"], "asset_class": "equity_index", "tol": 0.025},
    "dax":             {"symbols": ["DAX"], "asset_class": "equity_index", "tol": 0.025},
    "nikkei":          {"symbols": ["NIKKEI", "Nikkei 225"], "asset_class": "equity_index", "tol": 0.025},
}

# Match patterns like "Bitcoin at $74,816", "WTI crude at approximately $77",
# "Gold at $2,415". Asset name comes BEFORE the price; "$" is required.
# We split into two passes: (a) asset-then-price, (b) asset-then-percent.
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


@dataclass
class Claim:
    asset_canonical: str
    claimed_value: float
    raw_match: str
    text_offset: int     # offset into the markdown


@dataclass
class Verdict:
    claim: Claim
    actual_value: Optional[float]
    actual_symbol: Optional[str]
    actual_source_url: Optional[str]
    status: str          # "PASS" | "FAIL" | "UNVERIFIED"
    tolerance: float
    delta_pct: Optional[float]
    note: str = ""


def _normalize_value(value_str: str, suffix: str | None) -> float:
    """Convert '74,816.50' / '83' / '150k' to a float."""
    v = float(value_str.replace(",", "").replace(" ", ""))
    if suffix:
        s = suffix.strip().lower()
        if s == "k":             v *= 1_000
        elif s in ("m", "million"):  v *= 1_000_000
        elif s == "billion":     v *= 1_000_000_000
    return v


# A claim is NOT a spot-price assertion when it's a forecast contract
# name ("Bitcoin $150k by June 30"), a strike, or hypothetical reference.
# The pattern also picks up "BTC $150k by June 30" — the contract title —
# which we must filter or the checker will flag the strike as wrong.
FORECAST_CONTEXT = re.compile(
    r"\b(by\s+\w+|target|strike|polymarket|contract|forecast|hit\s+\$|"
    r"reach(?:es|ing)?|trades?\s+at\s+\d+%|"
    r"\b\d+%\s+probabilit|priced\s+as|impossib)",
    re.IGNORECASE,
)


def _looks_like_forecast(text: str, start: int, end: int) -> bool:
    """Return True when the claim's surrounding context (40 chars before +
    40 after) looks like a forecast/contract reference, not a current
    spot-price assertion."""
    window = text[max(0, start - 50):min(len(text), end + 50)]
    return bool(FORECAST_CONTEXT.search(window))


def extract_claims(text: str) -> list[Claim]:
    """Scan markdown text for asset-price claims, skipping forecast refs."""
    claims: list[Claim] = []
    for m in PRICE_PATTERN.finditer(text):
        asset = m.group("asset")
        key = asset.lower().strip()
        # Normalize multi-token names
        key = re.sub(r"\s+", " ", key)
        if key not in ASSET_REGISTRY:
            stem = key.split()[0]
            if stem in ASSET_REGISTRY:
                key = stem
            else:
                continue
        if _looks_like_forecast(text, m.start(), m.end()):
            continue
        value = _normalize_value(m.group("value"), m.group("suffix"))
        claims.append(Claim(
            asset_canonical=key,
            claimed_value=value,
            raw_match=m.group(0),
            text_offset=m.start(),
        ))
    return claims


def _lookup_market_price(
    conn: sqlite3.Connection, asset_key: str, day_iso: str,
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Find the canonical close for an asset on a date in the lake.

    Returns (price, symbol_or_name, source_url). The lake's records table
    holds market items with extra_json containing close + symbol + name +
    asset_class. We match on symbol OR canonical name."""
    spec = ASSET_REGISTRY.get(asset_key)
    if not spec:
        return None, None, None
    # Prefer same-day records; widen search if needed
    for window in ("=", ">="):
        if window == "=":
            where = "AND substr(r.ingested_at, 1, 10) = ?"
            params: list[Any] = [day_iso]
        else:
            where = "AND substr(r.ingested_at, 1, 10) >= date(?, '-2 days')"
            params = [day_iso]
        rows = conn.execute(
            f"""
            SELECT r.original_text, r.original_url, r.extra_json, r.ingested_at
              FROM records r
             WHERE r.section_id IN ('markets', 'markets_global')
               {where}
             ORDER BY r.ingested_at DESC
            """,
            params,
        ).fetchall()
        for orig, url, extra_json, _at in rows:
            try:
                extra = json.loads(extra_json) if extra_json else {}
            except Exception:
                extra = {}
            sym = (extra.get("symbol") or "").upper()
            nm  = (extra.get("name")   or "").lower()
            ac  = (extra.get("asset_class") or "").lower()
            if ac != spec["asset_class"]: continue
            symbol_match = any(s.upper() in (sym, nm.upper()) or
                               nm == s.lower() for s in spec["symbols"])
            if not symbol_match:
                continue
            close = extra.get("close")
            if close is None:
                # Try parsing from original_text e.g. "[crypto] BTC: $74,816"
                price_m = re.search(r"\$([\d,\.]+)", orig or "")
                if price_m:
                    try: close = float(price_m.group(1).replace(",", ""))
                    except Exception: close = None
            if close is not None:
                return float(close), sym or nm or asset_key, url
        if window == ">=":
            break
    return None, None, None


def verify_claims(claims: list[Claim], lake_db: Path,
                   day: date) -> list[Verdict]:
    """Run each claim against the lake's market records."""
    if not lake_db.exists():
        return [Verdict(c, None, None, None, "UNVERIFIED", 0.0, None,
                         note="lake DB missing") for c in claims]
    conn = sqlite3.connect(f"file:{lake_db}?mode=ro", uri=True)
    out: list[Verdict] = []
    try:
        for c in claims:
            actual, sym, src = _lookup_market_price(conn, c.asset_canonical, day.isoformat())
            spec = ASSET_REGISTRY[c.asset_canonical]
            tol  = float(spec["tol"])
            if actual is None:
                out.append(Verdict(c, None, None, None, "UNVERIFIED", tol, None,
                                     note="no same-day record in lake"))
                continue
            delta = (c.claimed_value - actual) / actual if actual else 0.0
            status = "PASS" if abs(delta) <= tol else "FAIL"
            out.append(Verdict(c, actual, sym, src, status, tol, delta))
    finally:
        conn.close()
    return out


def annotate_markdown(text: str, verdicts: list[Verdict]) -> str:
    """Insert inline [⚠ actual $X.XX] callouts immediately after every
    FAIL claim. PASS / UNVERIFIED left unchanged."""
    # Sort by offset descending so insertions don't shift later offsets.
    fails = sorted([v for v in verdicts if v.status == "FAIL"],
                    key=lambda v: -v.claim.text_offset)
    out = text
    for v in fails:
        end = v.claim.text_offset + len(v.claim.raw_match)
        # Find the end of the matched substring within the original text
        actual = f"{v.actual_value:,.2f}" if v.actual_value else "unknown"
        marker = f" `[⚠ actual ≈ ${actual}]`"
        out = out[:end] + marker + out[end:]
    return out


def render_report(verdicts: list[Verdict]) -> str:
    """Human-readable text report."""
    if not verdicts:
        return "No asset-price claims detected."
    lines = []
    pass_n = sum(1 for v in verdicts if v.status == "PASS")
    fail_n = sum(1 for v in verdicts if v.status == "FAIL")
    unk_n  = sum(1 for v in verdicts if v.status == "UNVERIFIED")
    lines.append(f"Checked {len(verdicts)} claims: "
                 f"{pass_n} PASS, {fail_n} FAIL, {unk_n} UNVERIFIED")
    for v in verdicts:
        c = v.claim
        tag = {"PASS":" ✓ ", "FAIL":" ✗ ", "UNVERIFIED":" ? "}[v.status]
        actual = f"${v.actual_value:,.2f}" if v.actual_value is not None else "(no record)"
        delta = f"{v.delta_pct*100:+.1f}%" if v.delta_pct is not None else "—"
        lines.append(f"{tag}{c.asset_canonical:14s}  "
                     f"claimed=${c.claimed_value:>14,.2f}  "
                     f"actual={actual:>16s}  "
                     f"Δ={delta:>7s}  "
                     f"(tol±{v.tolerance*100:.1f}%)  "
                     f"« {c.raw_match[:64]} »")
        if v.note:
            lines.append(f"     note: {v.note}")
    return "\n".join(lines)


def check_brief(md_path: Path, lake_db: Path, *,
                day: Optional[date] = None,
                annotate: bool = False,
                annotate_out: Optional[Path] = None) -> tuple[list[Verdict], str]:
    """High-level entrypoint. Returns (verdicts, report_text)."""
    if day is None:
        # Infer date from filename like "2026-05-28.md"
        m = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.name)
        day = date.fromisoformat(m.group(1)) if m else date.today()
    text = md_path.read_text(encoding="utf-8")
    claims = extract_claims(text)
    verdicts = verify_claims(claims, lake_db, day)
    report = render_report(verdicts)
    if annotate:
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
        description="Verify numeric price claims in a briefing markdown.")
    ap.add_argument("md_path", help="briefings/<date>.md to check")
    ap.add_argument("--lake", default="lake/db/worldscope.sqlite",
                    help="path to lake sqlite (default: lake/db/worldscope.sqlite)")
    ap.add_argument("--annotate", action="store_true",
                    help="write a .annotated.md with [⚠ actual $X] markers next to FAILs")
    ap.add_argument("--fail-on", default="FAIL",
                    choices=("never", "FAIL", "UNVERIFIED"),
                    help="exit code 1 when any verdict matches this level (default: FAIL)")
    args = ap.parse_args(argv)

    md = Path(args.md_path)
    if not md.exists():
        print(f"{md} not found", file=sys.stderr); return 2
    lake = Path(args.lake)
    verdicts, report = check_brief(md, lake, annotate=args.annotate)
    print(report)
    if args.fail_on == "never":
        return 0
    if args.fail_on == "FAIL"        and any(v.status == "FAIL"        for v in verdicts): return 1
    if args.fail_on == "UNVERIFIED"  and any(v.status in ("FAIL","UNVERIFIED") for v in verdicts): return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
