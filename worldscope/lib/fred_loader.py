"""
fred_loader.py — backfill the warehouse from FRED for the macro watchlist.

Reads the same watchlist as `worldscope.sections.macro` and pulls each
series' full observation history into the DuckDB warehouse. After the
first backfill (~30 sec for the 21-series watchlist), subsequent runs
only fetch observations newer than what's already stored.

Usage:
    python -m worldscope.lib.fred_loader --backfill
    python -m worldscope.lib.fred_loader --update
"""
from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from .warehouse import open as open_warehouse

API = "https://api.stlouisfed.org/fred/series/observations"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Mirror sections.macro WATCHLIST. Could import directly, but the loader
# needs to stay decoupled so removing the section doesn't lose the watchlist.
WATCHLIST = [
    ("DFF",         "Fed Funds Effective Rate",          "Rates"),
    ("DGS2",        "2-Year Treasury",                    "Rates"),
    ("DGS10",       "10-Year Treasury",                   "Rates"),
    ("DGS30",       "30-Year Treasury",                   "Rates"),
    ("SOFR",        "SOFR",                                "Rates"),
    ("T10Y2Y",      "10y-2y Spread",                      "Rates"),
    ("CPIAUCSL",    "CPI (headline, SA)",                  "Inflation"),
    ("CPILFESL",    "CPI Core (ex food & energy)",         "Inflation"),
    ("PCEPILFE",    "Core PCE",                            "Inflation"),
    ("UNRATE",      "Unemployment rate",                   "Labor"),
    ("PAYEMS",      "Nonfarm payrolls",                    "Labor"),
    ("JTSJOL",      "Job openings (JOLTS)",                "Labor"),
    ("GDPC1",       "Real GDP",                            "Growth"),
    ("PCE",         "Personal consumption",                "Growth"),
    ("WALCL",       "Fed balance sheet",                   "Money"),
    ("M2SL",        "M2 money supply",                     "Money"),
    ("DEXUSEU",     "EUR/USD",                             "FX"),
    ("DEXJPUS",     "JPY/USD",                             "FX"),
    ("DEXCHUS",     "CNY/USD",                             "FX"),
    ("DCOILWTICO",  "WTI crude oil",                       "Commodities"),
    ("VIXCLS",      "VIX",                                 "Vol"),
]


def fetch_series(series_id: str, *, start: Optional[date] = None) -> list[tuple[date, Optional[float]]]:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set")
    params = {
        "series_id": series_id, "api_key": key, "file_type": "json",
        "sort_order": "asc", "limit": 100000,
    }
    if start:
        params["observation_start"] = start.isoformat()
    r = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    obs_raw = r.json().get("observations") or []
    out: list[tuple[date, Optional[float]]] = []
    for o in obs_raw:
        d = date.fromisoformat(o["date"])
        v = o.get("value")
        try:
            val = None if v in (".", None, "") else float(v)
        except (TypeError, ValueError):
            val = None
        out.append((d, val))
    return out


def backfill(*, since: Optional[date] = None) -> dict:
    """Pull every series in the watchlist from `since` (default: full history)."""
    wh = open_warehouse()
    stats = {"series": 0, "rows": 0, "errors": []}
    for sid, label, group in WATCHLIST:
        try:
            obs = fetch_series(sid, start=since)
        except Exception as exc:
            stats["errors"].append(f"{sid}: {exc}")
            continue
        n = wh.upsert_observations("fred", sid, obs)
        wh.upsert_meta("fred", sid, label=label, group_label=group)
        stats["series"] += 1
        stats["rows"] += n
        print(f"  [{sid:12s}] {n:>5d} observations  ({label})")
    wh.close()
    return stats


def update(*, lookback_days: int = 60) -> dict:
    """Incremental update: pull recent observations and upsert."""
    since = date.today() - timedelta(days=lookback_days)
    return backfill(since=since)


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--backfill", action="store_true", help="full historical load (slow)")
    g.add_argument("--update", action="store_true", help="incremental update of last 60 days")
    args = p.parse_args()
    s = backfill() if args.backfill else update()
    print(f"\nDONE — {s['series']} series, {s['rows']} rows; "
          f"errors: {len(s['errors'])}")
    for e in s["errors"]:
        print(f"  ERR: {e}")


if __name__ == "__main__":
    main()
