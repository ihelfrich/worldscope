"""
Federal Register — every U.S. executive order, presidential memo, agency rule,
proposed rule, and notice. The cleanest possible "what the U.S. government
did yesterday" feed.

API docs: https://www.federalregister.gov/developers/documentation/api/v1
No key required.

Section-adapter contract: conforms. Emits entities (filings, agencies,
presidents) and relationships (issued-by, signed-by) into the lake graph.
This is the proof-of-concept migration; other sections follow the same
pattern.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import requests

from . import Section, SectionState

API = "https://www.federalregister.gov/api/v1/documents.json"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    """URL-safe slug for entity IDs."""
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


class FederalRegisterSection(Section):
    id = "federal_register"
    title = "U.S. Federal Action"
    emoji = "🏛️"

    # Section-adapter contract metadata
    source_id = "federal-register"
    source_name = "U.S. Federal Register"
    source_url = "https://www.federalregister.gov"
    source_tier = "primary_document"
    source_license = "public-domain"
    attribution_required = False
    source_country = "US"
    source_language = "en"

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
            agencies = [a.get("name", "") for a in (d.get("agencies") or []) if a.get("name")]
            president = (d.get("president") or {}).get("name") if d.get("president") else None
            items.append({
                "id": d.get("document_number"),
                "date": d.get("publication_date"),
                "title": d.get("title", ""),
                "url": d.get("html_url", ""),
                "summary": (d.get("abstract") or "")[:600],
                "doc_type": d.get("type"),
                "agencies": ", ".join(agencies),
                "agencies_list": agencies,
                "president": president,
            })
        return items

    # ----- Contract: entity extraction --------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        entities: list[dict] = []
        doc_id = f"filing:fr-{item.get('id','')}"
        entities.append({
            "id": doc_id,
            "type": "filing",
            "canonical_name": (item.get("title") or "(untitled federal register entry)")[:300],
            "metadata": {
                "doc_type": item.get("doc_type"),
                "publication_date": item.get("date"),
                "url": item.get("url"),
            },
        })
        for agency in item.get("agencies_list") or []:
            entities.append({
                "id": f"org:fed-agency-{_slug(agency)}",
                "type": "org",
                "canonical_name": agency,
                "metadata": {"branch": "executive", "kind": "federal-agency"},
            })
        if item.get("president"):
            entities.append({
                "id": f"person:pres-{_slug(item['president'])}",
                "type": "person",
                "canonical_name": item["president"],
                "metadata": {"role": "President of the United States"},
            })
        return entities

    # ----- Contract: structured.json (entity + relationship payload) --------

    def emit_structured(self, state: SectionState) -> dict:
        base = super().emit_structured(state)
        seen_entities: dict[str, dict] = {}
        relationships: list[dict] = []

        for item in state.items:
            for e in self.extract_entities(item):
                seen_entities[e["id"]] = e
            doc_id = f"filing:fr-{item.get('id','')}"
            evidence = [item.get("_id") or self._item_id(item)]
            for agency in item.get("agencies_list") or []:
                relationships.append({
                    "from": doc_id,
                    "to": f"org:fed-agency-{_slug(agency)}",
                    "type": "issued-by",
                    "weight": 1.0,
                    "evidence": evidence,
                })
            if item.get("president"):
                relationships.append({
                    "from": doc_id,
                    "to": f"person:pres-{_slug(item['president'])}",
                    "type": "signed-by",
                    "weight": 1.0,
                    "evidence": evidence,
                })

        base["entities_added"] = list(seen_entities.values())
        base["relationships"] = relationships
        return base

    # ----- Contract: enrich raw record with entity IDs ----------------------

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record
