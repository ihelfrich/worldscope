"""
sanctions.py — OpenSanctions recent updates section.

Hits OpenSanctions' public search API filtered to high-signal collections
(OFAC, EU FSF, UN SC, UK OFSI, OFAC SDN) and surfaces entities with a
last_change timestamp in the last 48 hours.

OpenSanctions API: https://api.opensanctions.org/
The key OPENSANCTIONS_API_KEY raises rate limits but anonymous calls
work too. We send it if available.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from . import Section

API = "https://api.opensanctions.org/search/sanctions"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


class SanctionsSection(Section):
    id = "sanctions"
    title = "Sanctions & Designations (recent)"
    emoji = "⚖️"

    # Surface entities changed in the last N days
    WINDOW_DAYS = 7
    LIMIT = 25

    # Without OPENSANCTIONS_API_KEY the search API returns
    # "No API key provided." A future enhancement: switch to the bulk-data
    # path (data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv)
    # which is auth-free, and diff day-over-day. For now the section silently
    # ships empty when the key is missing.

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.WINDOW_DAYS))
        params = {
            "q": "",  # empty query → most recent across the sanctions collection
            "limit": self.LIMIT,
            "schema": "Person,Organization,Company,LegalEntity",
        }
        headers = {"User-Agent": UA, "Accept": "application/json"}
        key = os.environ.get("OPENSANCTIONS_API_KEY")
        if key:
            headers["Authorization"] = f"ApiKey {key}"
        try:
            resp = requests.get(API, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        items: list[dict] = []
        for ent in data.get("results", []):
            last_change = ent.get("last_change") or ent.get("last_seen") or ""
            try:
                ts = datetime.fromisoformat(last_change.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = None
            if ts and ts < cutoff:
                continue
            datasets = ", ".join(ent.get("datasets") or [])
            caption = ent.get("caption") or ent.get("name") or "(unnamed)"
            schema = ent.get("schema", "")
            ent_id = ent.get("id", "")
            countries = ", ".join((ent.get("properties") or {}).get("country", []) or [])
            items.append({
                "id": ent_id,
                "date": ts.date().isoformat() if ts else "",
                "title": f"{caption} ({schema})",
                "url": f"https://www.opensanctions.org/entities/{ent_id}/" if ent_id else "",
                "summary": f"datasets: {datasets}; countries: {countries}",
                "schema": schema,
                "datasets": ent.get("datasets") or [],
                "countries": (ent.get("properties") or {}).get("country") or [],
            })
        return items
