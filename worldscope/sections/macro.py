"""
macro.py — current values of tier-1 macroeconomic indicators (FRED).

Instead of "what FRED updated today" (which is dominated by daily-cadence
noise and is empty on US market holidays like Memorial Day), the section
pulls the LATEST observation for a curated watchlist of tier-1 indicators
and shows each at its most recent value with the as-of date.

Series watchlist covers: interest rates / yields, inflation, employment,
GDP/growth, Fed balance sheet, FX, oil, equity volatility.

Requires FRED_API_KEY in env (provisioned in econscope/.env).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import requests

from . import Section

OBS_API = "https://api.stlouisfed.org/fred/series/observations"
SER_API = "https://api.stlouisfed.org/fred/series"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Tier-1 macro watchlist. (series_id, display label, group).
WATCHLIST: list[tuple[str, str, str]] = [
    # Interest rates / yields
    ("DFF",         "Fed Funds Effective Rate",          "Rates"),
    ("DGS2",        "2-Year Treasury",                    "Rates"),
    ("DGS10",       "10-Year Treasury",                   "Rates"),
    ("DGS30",       "30-Year Treasury",                   "Rates"),
    ("SOFR",        "SOFR",                                "Rates"),
    ("T10Y2Y",      "10y–2y Spread (recession indicator)","Rates"),
    # Inflation
    ("CPIAUCSL",    "CPI (headline, SA)",                  "Inflation"),
    ("CPILFESL",    "CPI Core (ex food & energy, SA)",     "Inflation"),
    ("PCEPILFE",    "Core PCE",                            "Inflation"),
    # Labor
    ("UNRATE",      "Unemployment rate",                   "Labor"),
    ("PAYEMS",      "Nonfarm payrolls",                    "Labor"),
    ("JTSJOL",      "Job openings (JOLTS)",                "Labor"),
    # Growth
    ("GDPC1",       "Real GDP",                            "Growth"),
    ("PCE",         "Personal consumption",                "Growth"),
    # Fed balance sheet / money
    ("WALCL",       "Fed balance sheet (total assets)",    "Money"),
    ("M2SL",        "M2 money supply",                     "Money"),
    # FX
    ("DEXUSEU",     "EUR/USD",                             "FX"),
    ("DEXJPUS",     "JPY/USD",                             "FX"),
    ("DEXCHUS",     "CNY/USD",                             "FX"),
    # Commodities
    ("DCOILWTICO",  "WTI crude oil",                       "Commodities"),
    # Equity vol
    ("VIXCLS",      "VIX (S&P 500 implied vol)",           "Vol"),
]


def _fetch_latest(session: requests.Session, key: str, series_id: str):
    """Returns (observation_dict, error_str). On success error_str is None."""
    params = {
        "series_id": series_id, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": 1,
    }
    try:
        r = session.get(OBS_API, params=params, timeout=15)
        r.raise_for_status()
        obs = (r.json().get("observations") or [])
        if not obs:
            return None, "no observations returned"
        return obs[0], None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


class MacroSection(Section):
    id = "macro"
    title = "Macro indicators — latest values (FRED)"
    emoji = "📊"

    def pull(self) -> list[dict]:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise RuntimeError(
                f"[{self.id}] FRED_API_KEY not set — section cannot pull"
            )
        session = requests.Session()
        session.headers["User-Agent"] = UA
        items: list[dict] = []
        failures: list[str] = []
        for sid, label, group in WATCHLIST:
            obs, err = _fetch_latest(session, key, sid)
            if not obs:
                failures.append(f"{sid}:{err}")
                continue
            value = obs.get("value")
            date = obs.get("date", "")
            # FRED uses "." for missing
            display_value = "—" if value in (".", None, "") else value
            items.append({
                "id": sid,
                "date": date,
                "title": f"[{group}] {label} ({sid})",
                "url": f"https://fred.stlouisfed.org/series/{sid}",
                "summary": f"latest: {display_value} as of {date}",
                "value": value,
                "group": group,
            })

        # Loud-failure invariant: if EVERY FRED series fetch failed,
        # the upstream is broken (rate limit, API key revoked, network)
        # and we should not silently report "success with 0 records".
        if not items and failures:
            raise RuntimeError(
                f"[{self.id}] All {len(failures)} FRED series fetches failed; "
                f"first: {failures[0]}"
            )
        if failures:
            print(f"[{self.id}] {len(failures)}/{len(WATCHLIST)} series fetches failed: "
                  + "; ".join(failures[:6])
                  + (f" (+{len(failures)-6} more)" if len(failures) > 6 else ""))
        return items
