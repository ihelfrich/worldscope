"""
markets.py — daily markets snapshot from Finnhub.

Pulls latest quote for a watchlist of major equity indices, currencies,
treasuries (via ETF proxies), and key commodities. Stores values to the
snapshot store so day-over-day deltas show up automatically.

Requires FINNHUB_API_KEY in env. Falls back to empty section if missing.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from . import Section

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"
QUOTE = "https://finnhub.io/api/v1/quote"

# (symbol, label, group). All accessible on Finnhub free tier.
WATCHLIST = [
    # Major equity indices (via ETFs to dodge the index-licensing tier)
    ("SPY",  "S&P 500 (SPY)",       "US equities"),
    ("QQQ",  "Nasdaq 100 (QQQ)",    "US equities"),
    ("IWM",  "Russell 2000 (IWM)",  "US equities"),
    ("EFA",  "MSCI EAFE (EFA)",     "Intl equities"),
    ("EEM",  "MSCI EM (EEM)",       "EM equities"),
    ("FXI",  "China large-cap (FXI)", "China equities"),
    # FX (via ETFs since free tier doesn't always have spot FX)
    ("UUP",  "DXY proxy (UUP)",     "FX"),
    ("FXE",  "EUR/USD (FXE)",       "FX"),
    ("FXY",  "JPY/USD (FXY)",       "FX"),
    # Treasuries (via ETFs)
    ("TLT",  "20+yr Treasury (TLT)", "Rates"),
    ("IEF",  "7-10yr Treasury (IEF)","Rates"),
    ("SHY",  "1-3yr Treasury (SHY)", "Rates"),
    # Commodities
    ("GLD",  "Gold (GLD)",          "Commodities"),
    ("SLV",  "Silver (SLV)",        "Commodities"),
    ("USO",  "WTI Crude (USO)",     "Commodities"),
    ("UNG",  "Natural Gas (UNG)",   "Commodities"),
    ("DBA",  "Agriculture (DBA)",   "Commodities"),
    # Credit
    ("HYG",  "High yield (HYG)",    "Credit"),
    ("LQD",  "IG corporate (LQD)",  "Credit"),
    # Vol
    ("VXX",  "VIX futures (VXX)",   "Vol"),
    # Crypto
    ("BITO", "Bitcoin futures (BITO)", "Crypto"),
]


def _fetch_quote(session, key, symbol):
    try:
        r = session.get(QUOTE, params={"symbol": symbol, "token": key}, timeout=12)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


class MarketsSection(Section):
    id = "markets"
    title = "Markets snapshot (Finnhub)"
    emoji = "📈"

    THROTTLE_S = 0.6

    def pull(self) -> list[dict]:
        key = os.environ.get("FINNHUB_API_KEY")
        if not key:
            return []
        s = requests.Session()
        s.headers["User-Agent"] = UA
        items: list[dict] = []
        for symbol, label, group in WATCHLIST:
            q = _fetch_quote(s, key, symbol)
            if not q:
                time.sleep(self.THROTTLE_S)
                continue
            c = q.get("c")  # current
            d = q.get("d")  # change
            dp = q.get("dp")  # change %
            h = q.get("h")  # high
            l = q.get("l")  # low
            t = q.get("t")  # timestamp
            if c is None or c == 0:
                time.sleep(self.THROTTLE_S)
                continue
            dt = (datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
                  if t else "")
            arrow = "▲" if (d or 0) >= 0 else "▼"
            items.append({
                "id": symbol,
                "date": dt,
                "title": f"[{group}] {label}: {c:.2f} {arrow} {(dp or 0):+.2f}%",
                "url": f"https://finance.yahoo.com/quote/{symbol}",
                "summary": (
                    f"close {c:.2f} · chg {d:+.2f} ({dp:+.2f}%) · "
                    f"day range {l:.2f}–{h:.2f}"
                    if all(v is not None for v in (c, d, dp, h, l))
                    else f"close {c:.2f}"
                ),
                "value": c,
                "change": d,
                "change_pct": dp,
                "group": group,
            })
            time.sleep(self.THROTTLE_S)
        return items
