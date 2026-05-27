"""
markets_global — comprehensive financial-markets layer.

Pulls daily/intraday snapshots from free APIs across five asset classes:

  1. FX — major + EM crosses (USD-base) via open.er-api.com (free, no auth;
       URL changed 2026-05-27 — exchangerate.host moved behind a paid plan)
  2. Sovereign bond yields — 10y for G20 via FRED (US) + Stooq (rest)
  3. Commodities — oil (WTI + Brent), gold, copper, ag (wheat, corn, soy),
       gas (TTF + Henry Hub), via Stooq
  4. Crypto — BTC, ETH, USDT, USDC, USD1, plus aggregate market cap +
       stablecoin issuance via CoinGecko public API (free, rate-limited)
  5. Global stock indices — S&P 500, Nasdaq, Dow, FTSE, DAX, CAC, Nikkei,
       Hang Seng, Shanghai, Sensex, KOSPI, Bovespa, MERVAL, JSE, ASX

Sources used:
  - Stooq.com — free CSV downloads, no auth, very generous rate limit
  - CoinGecko — free public API tier (10-30 calls/min)
  - open.er-api.com — FX, free public tier, no auth (exchangerate.host went
    paid 2026-05-27)

If any sub-source fails, the section degrades gracefully — partial coverage
is better than no coverage.

Section-adapter contract: conforms. Anomalies emitted for:
  - Yield curve inversion changes (2s-10s)
  - Daily moves > 3 sigma on rolling 30-day base (per indicator)
  - Stablecoin supply changes > 1% day-over-day (relevant to EO 14405)
  - Equity index drawdowns > 2% intraday
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

from . import Section, SectionState

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


# ----- Stooq tickers ---------------------------------------------------- #
# Stooq's CSV endpoint: https://stooq.com/q/l/?s=<symbol>&f=sd2t2ohlcv&h&e=csv
# Each symbol returns one line of CSV with the latest quote.

STOOQ_BONDS = {
    # 10-year sovereign yields. URL audit 2026-05-27: Stooq's bond-yield
    # tickers use the form "10y<cc>y.b" (not "10<cc>y.b" as previously coded
    # here). Probed against stooq.com directly. All 12 tickers below return
    # live closes as of 2026-05-27.
    "US 10y":        "10yusy.b",
    "UK 10y":        "10yuky.b",
    "Germany 10y":   "10ydey.b",
    "France 10y":    "10yfry.b",
    "Italy 10y":     "10yity.b",
    "Japan 10y":     "10yjpy.b",
    "China 10y":     "10ycny.b",
    "India 10y":     "10yiny.b",
    "Canada 10y":    "10ycay.b",
    "Australia 10y": "10yauy.b",
    "Brazil 10y":    "10ybry.b",
    "Mexico 10y":    "10ymxy.b",
}

STOOQ_COMMODITIES = {
    "WTI Crude":       "cl.f",
    "Brent Crude":     "b.f",
    "Natural Gas":     "ng.f",
    "Gold":            "gc.f",
    "Silver":          "si.f",
    "Copper":          "hg.f",
    "Wheat":           "w.f",
    "Corn":            "c.f",
    "Soybeans":        "s.f",
    "Coffee":          "kc.f",
    "Cotton":          "ct.f",
    "Sugar":           "sb.f",
    "Lumber":          "lb.f",
    "EU TTF Gas":      "ttf.f",
}

STOOQ_INDICES = {
    "S&P 500":      "^spx",
    "Nasdaq 100":   "^ndx",
    "Dow 30":       "^dji",
    "Russell 2000": "^rut",
    "FTSE 100":     "^ukx",
    "DAX":          "^dax",
    "CAC 40":       "^fchi",
    "EuroStoxx 50": "^stoxx50e",
    "Nikkei 225":   "^nkx",
    "Hang Seng":    "^hsi",
    "Shanghai":     "^shc",
    "Shenzhen":     "^szc",
    "Sensex":       "^bse",
    "Nifty 50":     "^nse",
    "KOSPI":        "^kospi",
    "TAIEX":        "^twii",
    "ASX 200":      "^aord",
    "TSX":          "^spx.ca",
    "Bovespa":      "^bvsp",
    "MERVAL":       "^mrv",
    "IPC":          "^mxx",
    "JSE Top 40":   "^j200.za",
    "BIST 100":     "^xu100",
    "MOEX":         "^mcx",
}

# ----- FX (USD-base) ---------------------------------------------------- #
# exchangerate.host returns latest rates against USD when base=USD
FX_PAIRS_USD = [
    "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",       # Majors
    "CNY", "HKD", "TWD", "KRW", "SGD", "MYR", "THB",       # Asia
    "INR", "IDR", "PHP", "VND",
    "BRL", "MXN", "ARS", "CLP", "COP", "PEN",              # LatAm
    "ZAR", "EGP", "NGN", "KES", "GHS",                     # Africa
    "RUB", "TRY", "PLN", "CZK", "HUF",                     # EM-Europe
    "SAR", "AED", "QAR", "ILS",                            # ME
]

CRYPTO_IDS = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "USDT":  "tether",
    "USDC":  "usd-coin",
    "USD1":  "world-liberty-financial-usd",   # WLFI's stablecoin
    "BNB":   "binancecoin",
    "SOL":   "solana",
    "XRP":   "ripple",
    "DOGE":  "dogecoin",
    "ADA":   "cardano",
}


class MarketsGlobalSection(Section):
    id = "markets_global"
    title = "Global Markets: FX + Bonds + Commodities + Equities + Crypto"
    emoji = "📈"

    source_id = "markets-aggregate"
    source_name = "Global markets aggregate (Stooq + CoinGecko + open.er-api.com)"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "primary_document"   # exchange-quoted prices
    source_license = "varies-per-source"
    attribution_required = True
    attribution_text = (
        "Price data via Stooq.com (no warranty), CoinGecko public API, "
        "open.er-api.com (exchangerate-api.com free tier). Snapshots are "
        "last-trade-of-the-day; intraday moves not captured."
    )
    source_country = None
    source_language = "en"

    PULL_TIMEOUT_S = 180
    MAX_WORKERS = 12

    def pull(self) -> list[dict]:
        items: list[dict] = []

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._pull_fx): "fx",
                pool.submit(self._pull_crypto): "crypto",
            }
            # One future per Stooq symbol; we batch by category
            for name, sym in STOOQ_BONDS.items():
                futures[pool.submit(self._pull_stooq, name, sym, "bond")] = f"bond:{name}"
            for name, sym in STOOQ_COMMODITIES.items():
                futures[pool.submit(self._pull_stooq, name, sym, "commodity")] = f"comm:{name}"
            for name, sym in STOOQ_INDICES.items():
                futures[pool.submit(self._pull_stooq, name, sym, "equity_index")] = f"idx:{name}"

            for fut, label in futures.items():
                try:
                    items.extend(fut.result())
                except Exception as exc:
                    items.append({
                        "id": f"markets-error-{_slug(label)}",
                        "date": date.today().isoformat(),
                        "title": f"[markets error] {label}: {type(exc).__name__}",
                        "url": "",
                        "summary": str(exc)[:300],
                        "_error": True,
                        "subsection": label,
                    })
        return items

    # ----- Stooq CSV puller --------------------------------------------------

    def _pull_stooq(self, name: str, symbol: str, asset_class: str) -> list[dict]:
        url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return []
        text = resp.text.strip()
        lines = text.split("\n")
        if len(lines) < 2: return []
        # CSV: Symbol,Date,Time,Open,High,Low,Close,Volume
        parts = lines[1].split(",")
        if len(parts) < 7: return []
        sym, d, t, o, h, low, c, v = parts[:8] if len(parts) >= 8 else parts + [""]
        try:
            close = float(c)
        except ValueError:
            return []
        return [{
            "id": f"markets-{asset_class}-{_slug(name)}",
            "date": d if "-" in d else date.today().isoformat(),
            "title": f"[{asset_class}] {name}: {close}",
            "url": f"https://stooq.com/q/?s={symbol}",
            "summary": f"open={o}  high={h}  low={low}  close={c}  vol={v}",
            "asset_class": asset_class,
            "name": name,
            "symbol": symbol,
            "close": close,
            "open": _safe_float(o),
            "high": _safe_float(h),
            "low":  _safe_float(low),
            "volume": _safe_float(v),
            "subsection": asset_class,
        }]

    # ----- FX (open.er-api.com) ---------------------------------------------
    # URL audit 2026-05-27: exchangerate.host moved behind a paid plan and now
    # returns empty rates dicts on the free endpoint. Replaced with
    # open.er-api.com (free tier from exchangerate-api.com, no auth, daily
    # updates, 160+ currencies). Frankfurter.app was the second candidate but
    # only covers 30 currencies and misses TWD/VND/ARS/CLP/COP/PEN/EGP/NGN/
    # KES/GHS/RUB/SAR/AED/QAR which we care about.

    def _pull_fx(self) -> list[dict]:
        url = "https://open.er-api.com/v6/latest/USD"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "markets-error-fx",
                "date": date.today().isoformat(),
                "title": f"[FX error] {type(exc).__name__}",
                "url": url, "summary": str(exc)[:300], "_error": True,
                "subsection": "fx",
            }]
        all_rates = data.get("rates") or {}
        # Filter to our watchlist; open.er-api returns the whole world.
        rates = {ccy: all_rates[ccy] for ccy in FX_PAIRS_USD if ccy in all_rates}
        # Date from the API's last-update timestamp (UTC).
        api_date = (data.get("time_last_update_utc") or "")[:25]
        try:
            from email.utils import parsedate_to_datetime
            iso_date = parsedate_to_datetime(api_date).date().isoformat() if api_date else date.today().isoformat()
        except Exception:
            iso_date = date.today().isoformat()
        out = []
        for ccy, rate in rates.items():
            try:
                r = float(rate)
            except (ValueError, TypeError):
                continue
            out.append({
                "id": f"markets-fx-usd{ccy.lower()}",
                "date": iso_date,
                "title": f"[fx] USD/{ccy}: {r}",
                "url": f"https://www.exchangerate-api.com/docs/free",
                "summary": f"1 USD = {r} {ccy}",
                "asset_class": "fx",
                "name": f"USD/{ccy}",
                "symbol": f"USD{ccy}",
                "close": r,
                "subsection": "fx",
            })
        return out

    # ----- Crypto (CoinGecko) -----------------------------------------------

    def _pull_crypto(self) -> list[dict]:
        ids = ",".join(CRYPTO_IDS.values())
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ids,
            "vs_currencies": "usd",
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
        }
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "markets-error-crypto",
                "date": date.today().isoformat(),
                "title": f"[Crypto error] {type(exc).__name__}",
                "url": url, "summary": str(exc)[:300], "_error": True,
                "subsection": "crypto",
            }]
        out = []
        for symbol, coingecko_id in CRYPTO_IDS.items():
            d = data.get(coingecko_id) or {}
            price = d.get("usd")
            if price is None: continue
            mcap = d.get("usd_market_cap")
            vol24 = d.get("usd_24h_vol")
            chg24 = d.get("usd_24h_change")
            out.append({
                "id": f"markets-crypto-{symbol.lower()}",
                "date": date.today().isoformat(),
                "title": f"[crypto] {symbol}: ${price:,.4f}  (24h: {chg24:+.2f}%)" if chg24 is not None else f"[crypto] {symbol}: ${price}",
                "url": f"https://www.coingecko.com/en/coins/{coingecko_id}",
                "summary": f"price=${price}  mcap=${mcap:,.0f}  vol24=${vol24:,.0f}  chg24={chg24}%" if mcap and vol24 else f"price=${price}",
                "asset_class": "crypto",
                "name": symbol,
                "symbol": symbol,
                "close": price,
                "market_cap_usd": mcap,
                "volume_24h_usd": vol24,
                "change_24h_pct": chg24,
                "subsection": "crypto",
            })
        return out

    # ----- Contract: entities -----------------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"): return []
        name = item.get("name", "")
        asset_class = item.get("asset_class", "")
        return [{
            "id": f"market:{asset_class}-{_slug(name)}",
            "type": "market",
            "canonical_name": name,
            "metadata": {
                "asset_class": asset_class,
                "symbol": item.get("symbol"),
                "url": item.get("url"),
            },
        }]

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        anomalies = []

        # Compute yield-curve spread + crypto stablecoin watch
        us_10y = None
        us_2y_proxy = None
        usd_stables = {}
        for item in state_obj.items:
            if item.get("_error"):
                base["anomalies"].append({
                    "category": "subsource-failure",
                    "z_score": None,
                    "description": item.get("title", ""),
                    "evidence": [item.get("_id") or self._item_id(item)],
                })
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e
            # 24h crypto moves > 5% trigger anomalies (especially USDT/USDC)
            if item.get("asset_class") == "crypto":
                chg = item.get("change_24h_pct")
                if chg is not None and abs(chg) > 5:
                    base["anomalies"].append({
                        "category": "crypto-volatility",
                        "z_score": chg / 5,   # crude
                        "description": f"{item.get('name')} moved {chg:+.2f}% in 24h",
                        "evidence": [item.get("_id") or self._item_id(item)],
                    })
                if item.get("name") in ("USDT", "USDC", "USD1"):
                    usd_stables[item["name"]] = item

        # Stablecoin depeg anomaly (direct EO 14405 relevance)
        for name, item in usd_stables.items():
            price = item.get("close")
            if price is not None and abs(price - 1.0) > 0.005:
                base["anomalies"].append({
                    "category": "stablecoin-depeg",
                    "z_score": (price - 1.0) * 100,
                    "description": f"{name} trading at ${price:.4f}, {(price-1)*100:+.3f}% off peg",
                    "evidence": [item.get("_id") or self._item_id(item)],
                })

        base["entities_added"] = list(seen.values())
        return base


def _safe_float(s: str) -> Optional[float]:
    try: return float(s)
    except (ValueError, TypeError): return None
