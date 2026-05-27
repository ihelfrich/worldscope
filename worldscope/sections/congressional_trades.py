"""
congressional_trades — every disclosed STOCK Act trade by a member of Congress.

Data via the community-maintained mirrors of House + Senate disclosure portals.
These mirrors do the heavy lifting of scraping the official portals (which
publish in PDF and obscure HTML) into clean JSON. No auth required.

  - Senate Stock Watcher
    https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json
  - House Stock Watcher
    https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json

Both are nightly dumps. We filter to the last LOOKBACK_DAYS of disclosed
trades and emit them as records into the lake.

This is the foundation for the Polymarket-anomaly-vs-insider-trade
cross-reference: when a prediction market price moves sharply, the
synthesis pass can check whether any member of Congress disclosed a
related-sector trade in the past 30 days.

Section-adapter contract: conforms. Emits:
    - person:legislator-<name> for each trader
    - org:public-company-<ticker> for each traded security
    - relationships: traded (legislator -> company) with amount range
      and direction (Purchase/Sale) in the metadata
Anomalies emitted for:
    - Cluster: same legislator trading >= 10 times in a week
    - Magnitude: any trade in the $1M-$5M or $5M+ amount bands
    - Cross-section: trade in a sector that had major policy news in
      the past 7 days (the synthesis pass handles this, not this
      ingest section)
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from . import Section, SectionState

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

SENATE_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
HOUSE_URL  = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"

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

    PULL_TIMEOUT_S = 180
    LOOKBACK_DAYS = 14

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        out: list[dict] = []

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(self._pull_senate, cutoff): "senate",
                pool.submit(self._pull_house, cutoff):  "house",
            }
            for fut, chamber in futures.items():
                try:
                    out.extend(fut.result())
                except Exception as exc:
                    out.append({
                        "id": f"congress-trades-error-{chamber}",
                        "date": date.today().isoformat(),
                        "title": f"[STOCK Act {chamber} error] {type(exc).__name__}",
                        "url": "",
                        "summary": str(exc)[:300],
                        "_error": True,
                        "chamber": chamber,
                    })
        return out

    def _pull_senate(self, cutoff: date) -> list[dict]:
        resp = requests.get(SENATE_URL, headers={"User-Agent": UA}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        out = []
        for txn in data:
            # Senate Stock Watcher schema
            try:
                txn_date = date.fromisoformat(txn.get("transaction_date", "")[:10])
            except (ValueError, TypeError):
                continue
            if txn_date < cutoff: continue

            senator = (txn.get("senator") or "").strip()
            ticker = (txn.get("ticker") or "").strip().upper()
            asset = (txn.get("asset_description") or "").strip()
            txn_type = (txn.get("type") or "").strip()
            amount = (txn.get("amount") or "").strip()
            disclosure_date = txn.get("disclosure_date", "")

            iid = hashlib.sha1(
                f"senate|{senator}|{ticker or asset}|{txn_date}|{txn_type}|{amount}".encode()
            ).hexdigest()
            out.append({
                "id": iid,
                "date": txn_date.isoformat(),
                "title": (f"[Senate] {senator}: {txn_type} {ticker or asset[:50]} "
                          f"({amount})")[:300],
                "url": txn.get("ptr_link", "https://senatestockwatcher.com"),
                "summary": (f"Senator: {senator}.  Asset: {asset[:80]}.  "
                            f"Type: {txn_type}.  Amount range: {amount}.  "
                            f"Transaction date: {txn_date.isoformat()}.  "
                            f"Disclosure date: {disclosure_date}.")[:600],
                "chamber": "senate",
                "member": senator,
                "party": txn.get("party"),
                "state": txn.get("state"),
                "ticker": ticker,
                "asset_description": asset,
                "transaction_type": txn_type,
                "amount_range": amount,
                "transaction_date": txn_date.isoformat(),
                "disclosure_date": disclosure_date,
                "ptr_link": txn.get("ptr_link"),
            })
        return out

    def _pull_house(self, cutoff: date) -> list[dict]:
        resp = requests.get(HOUSE_URL, headers={"User-Agent": UA}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        out = []
        for txn in data:
            try:
                txn_date = date.fromisoformat((txn.get("transaction_date") or "")[:10])
            except (ValueError, TypeError):
                continue
            if txn_date < cutoff: continue

            rep = (txn.get("representative") or "").strip()
            ticker = (txn.get("ticker") or "").strip().upper()
            asset = (txn.get("asset_description") or "").strip()
            txn_type = (txn.get("type") or "").strip()
            amount = (txn.get("amount") or "").strip()
            disclosure_date = txn.get("disclosure_date", "")
            owner = (txn.get("owner") or "").strip()    # self / spouse / dependent

            iid = hashlib.sha1(
                f"house|{rep}|{ticker or asset}|{txn_date}|{txn_type}|{amount}|{owner}".encode()
            ).hexdigest()
            out.append({
                "id": iid,
                "date": txn_date.isoformat(),
                "title": (f"[House] {rep} ({owner or 'self'}): {txn_type} "
                          f"{ticker or asset[:50]} ({amount})")[:300],
                "url": txn.get("ptr_link", "https://housestockwatcher.com"),
                "summary": (f"Rep: {rep}.  Owner: {owner or 'self'}.  "
                            f"Asset: {asset[:80]}.  Type: {txn_type}.  "
                            f"Amount range: {amount}.  "
                            f"Transaction date: {txn_date.isoformat()}.  "
                            f"Disclosure date: {disclosure_date}.")[:600],
                "chamber": "house",
                "member": rep,
                "owner": owner,
                "district": txn.get("district"),
                "ticker": ticker,
                "asset_description": asset,
                "transaction_type": txn_type,
                "amount_range": amount,
                "transaction_date": txn_date.isoformat(),
                "disclosure_date": disclosure_date,
                "ptr_link": txn.get("ptr_link"),
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
