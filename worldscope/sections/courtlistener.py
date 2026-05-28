"""
courtlistener.py — recent court opinions of consequence.

Pulls from CourtListener v4 search across high-signal courts:
  - SCOTUS               (US Supreme Court)
  - CIT                  (US Court of International Trade — tariffs/trade)
  - All US Courts of Appeals (1st-11th Circuits, DC, Federal)
  - High-profile state supreme courts

Uses the COURTLISTENER_API_TOKEN already provisioned in econscope/.env.
Free tier; authenticated calls lift rate limits.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from . import Section

API = "https://www.courtlistener.com/api/rest/v4/search/"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Tier-1 federal courts to watch (court_id values per CourtListener taxonomy).
WATCH_COURTS = [
    "scotus",       # Supreme Court of the United States
    "cit",          # US Court of International Trade
    "ca1", "ca2", "ca3", "ca4", "ca5",
    "ca6", "ca7", "ca8", "ca9", "ca10", "ca11",
    "cadc",         # DC Circuit
    "cafc",         # Federal Circuit
]


class CourtListenerSection(Section):
    id = "courtlistener"
    title = "Court opinions of consequence (federal)"
    emoji = "⚖️"

    DAYS = 14
    LIMIT = 30

    def pull(self) -> list[dict]:
        token = os.environ.get("COURTLISTENER_API_TOKEN")
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        start = (datetime.now(timezone.utc) - timedelta(days=self.DAYS)).strftime("%Y-%m-%d")
        items: list[dict] = []
        seen_ids: set[int] = set()
        successes = 0
        failures: list[str] = []
        for court in WATCH_COURTS:
            try:
                r = requests.get(
                    API,
                    params={
                        "type": "o",
                        "court": court,
                        "order_by": "dateFiled desc",
                        "filed_after": start,
                    },
                    headers=headers,
                    timeout=20,
                )
                r.raise_for_status()
                data = r.json()
                successes += 1
            except Exception as exc:
                failures.append(f"{court}:{type(exc).__name__}:{exc}")
                continue
            for res in (data.get("results") or [])[:8]:
                rid = res.get("cluster_id") or res.get("id") or 0
                if rid and rid in seen_ids:
                    continue
                if rid:
                    seen_ids.add(rid)
                date_str = res.get("dateFiled") or res.get("date_filed") or ""
                items.append({
                    "id": str(rid) if rid else (res.get("absolute_url") or ""),
                    "date": date_str[:10],
                    "title": f"[{court.upper()}] {res.get('caseName','')}",
                    "url": f"https://www.courtlistener.com{res.get('absolute_url','')}",
                    "summary": (res.get("snippet") or "")[:400],
                    "court": court,
                })
                if len(items) >= self.LIMIT:
                    return items
        if failures:
            print(f"[{self.id}] {len(failures)}/{len(WATCH_COURTS)} courts failed: "
                  + "; ".join(failures[:5])
                  + (f" (+{len(failures)-5} more)" if len(failures) > 5 else ""))
        if successes == 0 and failures:
            raise RuntimeError(
                f"All {len(failures)} CourtListener court fetches failed; "
                f"first error: {failures[0]}"
            )
        return items
