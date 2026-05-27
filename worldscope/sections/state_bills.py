"""
state_bills — every bill in every U.S. state legislature, daily diff.

Backed by the OpenStates v3 API (https://docs.openstates.org/api-v3/).
Free tier is 500 requests/day; daily ingest uses 10-25 requests, leaving
~475 for ad-hoc queries via the MCP server.

Strategy: one query against /bills with updated_since=<yesterday>, no
jurisdiction filter (so we get every state in one query). Paginate while
results > 0, capped at 25 pages (500 bills/day) which is more than any
realistic daily volume even mid-session.

Section-adapter contract: conforms. Emits:
    - filing entities for each bill
    - person entities for every sponsor
    - org entities for chambers
    - sponsored-by + co-sponsored-by + introduced-in relationships
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator

import requests

from . import Section, SectionState

API_BASE = "https://v3.openstates.org"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Document types worth featuring in the brief synthesis; everything else
# is still ingested into the lake for the MCP server to query, but the
# daily summary surfaces only these.
INTERESTING_CLASSIFICATIONS = {
    "bill", "joint resolution", "concurrent resolution",
    "constitutional amendment",
}


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


class StateBillsSection(Section):
    id = "state_bills"
    title = "State Legislative Action"
    emoji = "🏛️"

    source_id = "openstates"
    source_name = "OpenStates v3 API"
    source_url = "https://openstates.org"
    source_tier = "primary_document"
    source_license = "CC-BY-4.0"
    attribution_required = True
    attribution_text = (
        "State bill data via OpenStates v3 API "
        "(openstates.org), licensed CC-BY-4.0."
    )
    source_country = "US"
    source_language = "en"

    # OpenStates v3 requires a jurisdiction or query string per request, so
    # we loop over all 50 states + DC. With LOOKBACK_DAYS=2 and most states
    # producing < 20 updates per day, this is ~52 requests baseline + maybe
    # 10 paginated overflow = ~62/day. Well under the 500/day free tier.
    LOOKBACK_DAYS = 2
    MAX_PAGES_PER_JURISDICTION = 3    # safety cap (rarely needed)
    PER_PAGE = 20
    PULL_TIMEOUT_S = 240

    # OpenStates accepts jurisdiction names. DC + 50 states.
    JURISDICTIONS = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
        "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
        "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
        "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming",
    ]

    def pull(self) -> list[dict]:
        api_key = os.environ.get("OPENSTATES_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENSTATES_API_KEY env var is required. "
                "Get a key at openstates.org/api/ (free tier: 500 req/day)."
            )

        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))
        cutoff_iso = cutoff.strftime("%Y-%m-%d")

        items: list[dict] = []
        errors: list[dict] = []
        for jurisdiction in self.JURISDICTIONS:
            try:
                jurisdiction_items = self._pull_jurisdiction(api_key, jurisdiction, cutoff_iso)
                items.extend(jurisdiction_items)
            except Exception as exc:
                # Per the contract: one state's failure does not fail the
                # whole section. Surface as a separate quarantine-style row.
                errors.append({
                    "id": f"state-bills-error-{jurisdiction}",
                    "date": date.today().isoformat(),
                    "title": f"[ingest error] {jurisdiction}: {type(exc).__name__}",
                    "url": "",
                    "summary": str(exc)[:300],
                    "_error": True,
                    "state": jurisdiction,
                })
        items.extend(errors)
        return items

    def _pull_jurisdiction(self, api_key: str, jurisdiction: str,
                           cutoff_iso: str) -> list[dict]:
        out: list[dict] = []
        for page in range(1, self.MAX_PAGES_PER_JURISDICTION + 1):
            params = [
                ("jurisdiction", jurisdiction),
                ("updated_since", cutoff_iso),
                ("include", "sponsorships"),
                ("include", "abstracts"),
                ("per_page", self.PER_PAGE),
                ("page", page),
                ("sort", "updated_desc"),
            ]
            resp = requests.get(
                f"{API_BASE}/bills",
                params=params,
                headers={"X-API-Key": api_key, "User-Agent": UA},
                timeout=45,
            )
            if resp.status_code == 429:
                # Rate-limited; bail with what we have so far.
                break
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results") or []
            if not results:
                break
            for bill in results:
                normalized = self._normalize_bill(bill)
                if normalized is not None:
                    out.append(normalized)
            if len(results) < self.PER_PAGE:
                break
        return out

    def _normalize_bill(self, bill: dict) -> dict | None:
        """Map an OpenStates v3 /bills response object to our standard item.
        Returns None if the bill is uninteresting (procedural, etc.)."""
        classification = bill.get("classification") or []
        if isinstance(classification, str):
            classification = [classification]
        if classification and not any(c in INTERESTING_CLASSIFICATIONS for c in classification):
            return None

        jurisdiction = bill.get("jurisdiction") or {}
        state_name = jurisdiction.get("name", "")
        state_code = (jurisdiction.get("classification") or "")[:2].lower() if False else ""
        # Use the jurisdiction `name` as the human label; abbreviation comes
        # from the `id` (jocs/county:al/state etc.) which we won't try to parse.
        actions = bill.get("actions") or []
        last_action = actions[-1] if actions else {}

        sponsorships = bill.get("sponsorships") or []
        sponsor_names = [
            (s.get("name") or s.get("entity_name") or "").strip()
            for s in sponsorships
            if (s.get("name") or s.get("entity_name"))
        ]
        primary = next(
            (s for s in sponsorships
             if s.get("primary") or (s.get("classification") == "primary")),
            sponsorships[0] if sponsorships else None,
        )

        identifier = bill.get("identifier") or bill.get("id", "").split("/")[-1]
        title = bill.get("title") or "(untitled)"

        abstracts = bill.get("abstracts") or []
        abstract_text = ""
        if abstracts:
            abstract_text = (abstracts[0].get("abstract") or "")[:600]

        first_source = (bill.get("sources") or [{}])[0]

        return {
            "id": bill.get("id", ""),
            "date": (bill.get("updated_at") or "")[:10],
            "title": f"[{state_name} {identifier}] {title}"[:300],
            "url": first_source.get("url", ""),
            "summary": abstract_text or title[:600],
            "state": state_name,
            "session": bill.get("session", ""),
            "identifier": identifier,
            "classification": classification,
            "from_org": (bill.get("from_organization") or {}).get("name", ""),
            "subjects": bill.get("subject") or [],
            "sponsor_names": sponsor_names,
            "primary_sponsor": (primary or {}).get("name") if primary else None,
            "last_action_date": last_action.get("date"),
            "last_action_description": last_action.get("description"),
            "actions_count": len(actions),
        }

    # ----- Contract: entity extraction --------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        entities: list[dict] = []
        state_slug = _slug(item.get("state", ""))

        bill_eid = f"filing:state-bill-{state_slug}-{_slug(item.get('identifier',''))}"
        entities.append({
            "id": bill_eid,
            "type": "filing",
            "canonical_name": item.get("title", "")[:300],
            "metadata": {
                "state": item.get("state"),
                "session": item.get("session"),
                "identifier": item.get("identifier"),
                "classification": item.get("classification"),
                "from_org": item.get("from_org"),
                "subjects": item.get("subjects"),
                "url": item.get("url"),
                "last_action_date": item.get("last_action_date"),
                "last_action": item.get("last_action_description"),
            },
        })

        for name in item.get("sponsor_names") or []:
            entities.append({
                "id": f"person:legislator-{state_slug}-{_slug(name)}",
                "type": "person",
                "canonical_name": name,
                "metadata": {
                    "role": "state-legislator",
                    "state": item.get("state"),
                },
            })

        from_org = item.get("from_org")
        if from_org:
            entities.append({
                "id": f"org:state-chamber-{state_slug}-{_slug(from_org)}",
                "type": "org",
                "canonical_name": f"{item.get('state','')} {from_org}".strip(),
                "metadata": {
                    "kind": "state-legislative-chamber",
                    "state": item.get("state"),
                },
            })

        return entities

    # ----- Contract: structured.json ----------------------------------------

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships: list[dict] = []

        for item in state_obj.items:
            for e in self.extract_entities(item):
                seen[e["id"]] = e

            state_slug = _slug(item.get("state", ""))
            bill_eid = f"filing:state-bill-{state_slug}-{_slug(item.get('identifier',''))}"
            ev = [item.get("_id") or self._item_id(item)]

            primary = item.get("primary_sponsor")
            for name in item.get("sponsor_names") or []:
                rel_type = "sponsored-by" if name == primary else "co-sponsored-by"
                relationships.append({
                    "from": f"person:legislator-{state_slug}-{_slug(name)}",
                    "to": bill_eid,
                    "type": rel_type,
                    "weight": 1.0,
                    "evidence": ev,
                })

            from_org = item.get("from_org")
            if from_org:
                relationships.append({
                    "from": bill_eid,
                    "to": f"org:state-chamber-{state_slug}-{_slug(from_org)}",
                    "type": "introduced-in",
                    "weight": 1.0,
                    "evidence": ev,
                })

        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships
        return base

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record
