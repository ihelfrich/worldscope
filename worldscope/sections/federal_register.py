"""
Federal Register — every U.S. executive order, presidential memo, agency rule,
proposed rule, and notice. The cleanest possible "what the U.S. government
did yesterday" feed.

API docs: https://www.federalregister.gov/developers/documentation/api/v1
No key required.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import requests

from . import Section

API = "https://www.federalregister.gov/api/v1/documents.json"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


class FederalRegisterSection(Section):
    id = "federal_register"
    title = "U.S. Federal Action"
    emoji = "🏛️"

    # Document types worth featuring — drop generic notices.
    INTERESTING_TYPES = {"Rule", "Proposed Rule", "Presidential Document"}

    def pull(self) -> list[dict]:
        # Pull the last 7 days so a missed run doesn't lose context.
        start = (date.today() - timedelta(days=7)).isoformat()
        params = {
            "conditions[publication_date][gte]": start,
            "per_page": 100,
            "order": "newest",
        }
        resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items: list[dict] = []
        for d in data.get("results", []):
            if d.get("type") not in self.INTERESTING_TYPES:
                continue
            agencies = ", ".join(a.get("name", "") for a in d.get("agencies", []) or [])
            items.append({
                "id": d.get("document_number"),
                "date": d.get("publication_date"),
                "title": d.get("title", ""),
                "url": d.get("html_url", ""),
                "summary": (d.get("abstract") or "")[:600],
                "doc_type": d.get("type"),
                "agencies": agencies,
                "president": (d.get("president") or {}).get("name") if d.get("president") else None,
            })
        return items
