"""
congressional_trades — every disclosed STOCK Act trade by a member of Congress.

Data source: Quiver Quantitative's public beta endpoint
    https://api.quiverquant.com/beta/live/congresstrading
No auth required (verified working 2026-05-27). Returns the most recent
1000 trades across both House + Senate as JSON, with pre-computed
ExcessReturn vs SPY per trade.

The original community mirrors (Senate Stock Watcher, House Stock Watcher)
went dead in 2025-2026 (senatestockwatcher.com DNS unresolvable, the S3
buckets returning 403). Quiver is the surviving free option.

This is the foundation for the Polymarket-anomaly-vs-insider-trade
cross-reference: when a prediction market price moves sharply, the
synthesis pass can check whether any member of Congress disclosed a
related-sector trade in the past 30 days. The ExcessReturn field also
lets us flag trades that have meaningfully outperformed the market
since disclosure.

Section-adapter contract: conforms. Emits:
    - person:legislator-<name> for each trader
    - org:public-company-<ticker> for each traded security
    - relationships: traded (legislator -> company) with amount range,
      direction (Purchase/Sale), and excess-return in the metadata
Anomalies emitted for:
    - Magnitude: any trade in the $1M-$5M or $5M+ amount bands
    - Outperformance: ExcessReturn vs SPY >= 25% (trade has crushed
      the market since disclosure)
    - Volume: any legislator with >= 10 trades in the window
    - Cross-source failures (Quiver endpoint down)
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from . import Section, SectionState

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Quiver Quantitative's public beta endpoint. Returns the most recent
# 1000 congressional trades, no auth required. Includes pre-computed
# ExcessReturn vs SPY per trade.
QUIVER_URL = "https://api.quiverquant.com/beta/live/congresstrading"

# Amount ranges that trigger "large-trade" anomaly
LARGE_TRADE_RANGES = {
    "$1,000,001 - $5,000,000",
    "$5,000,001 - $25,000,000",
    "$25,000,001 - $50,000,000",
    "Over $50,000,000",
}


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


class CongressionalTradesSection(Section):
    id = "congressional_trades"
    title = "Congressional STOCK Act Trades (House + Senate)"
    emoji = "💼"

    source_id = "congress-stock-watcher-aggregate"
    source_name = "House + Senate Stock Watcher community mirrors"
    source_url = "https://senatestockwatcher.com"
    source_tier = "primary_document"   # ultimately public disclosures
    source_license = "public-domain"
    attribution_required = True
    attribution_text = (
        "Data via the open-source House Stock Watcher and Senate Stock Watcher "
        "projects, mirroring the official Periodic Transaction Reports filed by "
        "members of the U.S. House and Senate under the STOCK Act."
    )
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 60
    LOOKBACK_DAYS = 30   # Quiver returns 1000 most-recent across all time;
                          # we filter to the window of interest

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        try:
            resp = requests.get(QUIVER_URL, headers={"User-Agent": UA}, timeout=45)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "congress-trades-error-quiver",
                "date": date.today().isoformat(),
                "title": f"[Quiver Quantitative error] {type(exc).__name__}",
                "url": QUIVER_URL,
                "summary": str(exc)[:300],
                "_error": True,
            }]

        out: list[dict] = []
        for txn in data:
            try:
                txn_date = date.fromisoformat((txn.get("TransactionDate") or "")[:10])
            except (ValueError, TypeError):
                continue
            if txn_date < cutoff:
                continue

            member = (txn.get("Representative") or "").strip()
            bioguide = (txn.get("BioGuideID") or "").strip()
            ticker = (txn.get("Ticker") or "").strip().upper()
            txn_type = (txn.get("Transaction") or "").strip()
            amount_range = (txn.get("Range") or "").strip()
            amount_low = txn.get("Amount")
            party = (txn.get("Party") or "").strip()
            chamber_raw = (txn.get("House") or "").strip()
            chamber = ("senate" if chamber_raw == "Senate"
                       else "house" if chamber_raw == "Representatives"
                       else "unknown")
            disclosure_date = txn.get("ReportDate", "")
            description = (txn.get("Description") or "").strip()
            ticker_type = (txn.get("TickerType") or "").strip()
            excess_return = txn.get("ExcessReturn")
            price_change = txn.get("PriceChange")
            spy_change = txn.get("SPYChange")
            last_modified = txn.get("last_modified", "")

            iid = hashlib.sha1(
                f"quiver|{bioguide}|{ticker}|{txn_date}|{txn_type}|{amount_range}".encode()
            ).hexdigest()

            try:
                excess_pct = float(excess_return) if excess_return is not None else None
            except (TypeError, ValueError):
                excess_pct = None
            ret_marker = ""
            if excess_pct is not None:
                ret_marker = f" (excess {excess_pct:+.1f}% vs SPY)"

            out.append({
                "id": iid,
                "date": txn_date.isoformat(),
                "title": (f"[{chamber.title()}] {member} ({party}): "
                          f"{txn_type} {ticker or description[:40]} "
                          f"({amount_range}){ret_marker}")[:300],
                "url": f"https://www.quiverquant.com/congresstrading/politician/{bioguide}",
                "summary": (
                    f"Member: {member} ({party}, {chamber}).  "
                    f"BioGuide: {bioguide}.  "
                    f"Asset: {ticker or '(unspecified)'} ({ticker_type}).  "
                    f"Type: {txn_type}.  Amount range: {amount_range}.  "
                    f"Transaction date: {txn_date.isoformat()}.  "
                    f"Disclosure date: {disclosure_date}.  "
                    + (f"Excess return vs SPY since disclosure: "
                       f"{excess_pct:+.2f}%.  " if excess_pct is not None else "")
                    + (f"Description: {description}.  " if description else "")
                )[:600],
                "chamber": chamber,
                "member": member,
                "bioguide_id": bioguide,
                "party": party,
                "ticker": ticker,
                "ticker_type": ticker_type,
                "asset_description": description,
                "transaction_type": txn_type,
                "amount_range": amount_range,
                "amount_low_usd": amount_low,
                "transaction_date": txn_date.isoformat(),
                "disclosure_date": disclosure_date,
                "last_modified": last_modified,
                "excess_return_pct": excess_pct,
                "price_change_pct": price_change,
                "spy_change_pct": spy_change,
            })
        return out

    # ----- Contract: entity extraction --------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"): return []
        entities = []
        member = item.get("member")
        chamber = item.get("chamber")
        if member:
            entities.append({
                "id": f"person:legislator-{_slug(member)}",
                "type": "person",
                "canonical_name": member,
                "metadata": {
                    "role": f"us-{chamber}-member",
                    "chamber": chamber,
                    "party": item.get("party"),
                    "state": item.get("state") or item.get("district"),
                },
            })
        ticker = item.get("ticker")
        if ticker and ticker not in ("--", "N/A", ""):
            entities.append({
                "id": f"org:public-company-{_slug(ticker)}",
                "type": "org",
                "canonical_name": ticker,
                "metadata": {
                    "kind": "public-company",
                    "description": item.get("asset_description"),
                },
            })
        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships = []
        feed_errors = []
        large_trades = []
        outperformers = []         # ExcessReturn vs SPY >= 25%
        member_counts: dict[str, int] = {}

        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e

            member = item.get("member") or ""
            ticker = item.get("ticker") or ""
            ev = [item.get("_id") or self._item_id(item)]

            if member and ticker:
                relationships.append({
                    "from": f"person:legislator-{_slug(member)}",
                    "to": f"org:public-company-{_slug(ticker)}",
                    "type": "traded",
                    "weight": 1.0,
                    "evidence": ev,
                })
                member_counts[member] = member_counts.get(member, 0) + 1

            # Large-amount-band anomaly
            if item.get("amount_range") in LARGE_TRADE_RANGES:
                large_trades.append(item)

            # Outperformance vs SPY since disclosure — possible insider signal
            er = item.get("excess_return_pct")
            if er is not None and abs(er) >= 25:
                outperformers.append(item)

        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships

        for lt in large_trades:
            base["anomalies"].append({
                "category": "large-congressional-trade",
                "z_score": None,
                "description": (f"{lt.get('member')} ({lt.get('chamber')}): "
                                f"{lt.get('transaction_type')} {lt.get('ticker') or 'unknown'} "
                                f"in band {lt.get('amount_range')}"),
                "evidence": [lt.get("_id") or self._item_id(lt)],
            })

        for op in outperformers:
            er = op.get("excess_return_pct", 0.0) or 0.0
            base["anomalies"].append({
                "category": "trade-beat-market",
                "z_score": er / 10.0,   # 25% excess return -> z=2.5
                "description": (f"{op.get('member')} ({op.get('chamber')}): "
                                f"{op.get('transaction_type')} {op.get('ticker','?')} "
                                f"excess {er:+.1f}% vs SPY since disclosure"),
                "evidence": [op.get("_id") or self._item_id(op)],
            })

        # Volume clustering: any member with >= 10 trades in window
        for member, count in member_counts.items():
            if count >= 10:
                base["anomalies"].append({
                    "category": "high-volume-congressional-trader",
                    "z_score": float(count) / 10,
                    "description": f"{member}: {count} disclosed trades in last "
                                   f"{self.LOOKBACK_DAYS} days",
                    "evidence": [],
                })

        for err in feed_errors:
            base["anomalies"].append({
                "category": "subsource-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })

        return base

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record
