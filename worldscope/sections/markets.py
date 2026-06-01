"""
markets.py — daily markets snapshot from Finnhub.

Pulls latest quote for a watchlist of major equity indices, currencies,
treasuries (via ETF proxies), and key commodities. Stores values to the
snapshot store so day-over-day deltas show up automatically.

Uses Finnhub when FINNHUB_API_KEY is set; otherwise falls back to Yahoo's
keyless chart endpoint, so the section populates with or without the key. Only a
total fetch failure (every symbol, on the chosen provider) is treated as an
outage and raised.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from . import Section, UpstreamHTTPError

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"
QUOTE = "https://finnhub.io/api/v1/quote"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

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


# Both fetchers return a normalized {c, d, dp, h, l, t} dict (Finnhub's shape)
# or None on a per-symbol failure. One bad ticker must not fail the section.

def _fetch_quote_finnhub(session, key, symbol) -> Optional[dict]:
    try:
        r = session.get(QUOTE, params={"symbol": symbol, "token": key}, timeout=12)
        r.raise_for_status()
        q = r.json()
    except (requests.RequestException, ValueError):
        return None
    c = q.get("c")
    if c in (None, 0):
        return None
    return {"c": c, "d": q.get("d"), "dp": q.get("dp"),
            "h": q.get("h"), "l": q.get("l"), "t": q.get("t")}


def _fetch_quote_yahoo(session, symbol) -> Optional[dict]:
    """Keyless fallback. Yahoo's v8 chart endpoint carries price + previous
    close in `meta`; we derive the change to match Finnhub's shape."""
    try:
        r = session.get(YAHOO.format(symbol=symbol),
                        params={"range": "1d", "interval": "1d"}, timeout=12)
        r.raise_for_status()
        result = (((r.json() or {}).get("chart") or {}).get("result") or [])
    except (requests.RequestException, ValueError):
        return None
    meta = (result[0].get("meta") if result else None) or {}
    c = meta.get("regularMarketPrice")
    pc = meta.get("chartPreviousClose") or meta.get("previousClose")
    if c is None or not pc:
        return None
    d = c - pc
    return {"c": c, "d": d, "dp": (d / pc) * 100 if pc else 0.0,
            "h": meta.get("regularMarketDayHigh"),
            "l": meta.get("regularMarketDayLow"),
            "t": meta.get("regularMarketTime")}


class MarketsSection(Section):
    id = "markets"
    title = "Markets snapshot"
    emoji = "📈"

    THROTTLE_S = 0.6

    def pull(self) -> list[dict]:
        key = os.environ.get("FINNHUB_API_KEY")
        provider = "Finnhub" if key else "Yahoo"
        s = requests.Session()
        s.headers["User-Agent"] = UA
        items: list[dict] = []
        fetch_failures = 0
        for symbol, label, group in WATCHLIST:
            q = (_fetch_quote_finnhub(s, key, symbol) if key
                 else _fetch_quote_yahoo(s, symbol))
            if not q:
                fetch_failures += 1
                time.sleep(self.THROTTLE_S)
                continue
            c = q.get("c")  # current
            d = q.get("d")  # change
            dp = q.get("dp")  # change %
            h = q.get("h")  # high
            l = q.get("l")  # low
            t = q.get("t")  # timestamp
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
        # If every symbol failed to fetch (e.g. bad API key or Finnhub down),
        # that's an outage, not an empty market. Raise so the base class marks
        # the section STATE_STALE and carries the last good snapshot forward
        # rather than recording a misleading empty_ok.
        if not items and fetch_failures == len(WATCHLIST):
            raise UpstreamHTTPError(
                f"all {fetch_failures} {provider} quote fetches failed "
                f"(provider down / blocked)"
            )
        return items
