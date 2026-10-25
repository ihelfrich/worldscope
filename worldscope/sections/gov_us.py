"""U.S. Government Daily — everything the federal (and select state) government
did, across all branches, in one section.

Backed by GovScope (`worldscope/gov/`): the Federal Register API (all executive
departments + presidential documents) plus a curated RSS registry covering the
White House, Congress, the courts, Defense, Treasury, the Federal Reserve,
DOJ/the Attorney General, State, the intelligence community, and a seed set of
state attorneys general — merged, deduped, and diffed against yesterday by the
Section base class.

This is the "what did the government do today" companion to the existing
`federal_register` section: where that one focuses tightly on the Federal
Register, this one fans out across every branch and organ.
"""
from __future__ import annotations

from . import Section, SectionState, UpstreamHTTPError
from ._util import slug as _slug
from ..gov.fetch import gather_all


# coarse branch grouping for display + a stable emoji per branch
BRANCH_EMOJI = {
    "executive": "🏛️",
    "legislative": "🏟️",
    "judicial": "⚖️",
    "independent": "🏦",
    "state": "🗺️",
}


class GovUSSection(Section):
    id = "gov_us"
    title = "U.S. Government Daily"
    emoji = "🇺🇸"

    # Section-adapter contract metadata
    source_id = "gov-us-aggregate"
    source_name = "U.S. Government (all branches) aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "primary_document"
    source_license = "public-domain"
    attribution_required = False
    source_country = "US"
    source_language = "en"

    # Fans out across ~35 feeds + 3 APIs; give it room.
    PULL_TIMEOUT_S = 180
    LOOKBACK_DAYS = 2

    def pull(self) -> list[dict]:
        try:
            docs = gather_all(days=self.LOOKBACK_DAYS)
        except RuntimeError as exc:
            # Total blackout across every government source -> let the trust
            # layer mark the section stale rather than report an empty gov.
            raise UpstreamHTTPError(str(exc)) from exc
        return docs

    # ----- contract: entity extraction --------------------------------------
    def extract_entities(self, item: dict) -> list[dict]:
        entities: list[dict] = []
        doc_id = f"filing:gov-{item.get('id','')}"
        entities.append({
            "id": doc_id,
            "type": "filing",
            "canonical_name": (item.get("title") or "(untitled government document)")[:300],
            "metadata": {
                "branch": item.get("branch"),
                "doc_type": item.get("doc_type"),
                "publication_date": item.get("date"),
                "url": item.get("url"),
            },
        })
        org = item.get("org")
        if org:
            entities.append({
                "id": f"org:gov-{_slug(org)}",
                "type": "org",
                "canonical_name": org,
                "metadata": {"branch": item.get("branch"), "kind": "government"},
            })
        pres = item.get("president")
        if pres:
            entities.append({
                "id": f"person:pres-{_slug(pres)}",
                "type": "person",
                "canonical_name": pres,
                "metadata": {"role": "President of the United States"},
            })
        return entities

    # ----- contract: structured payload (graph) -----------------------------
    def emit_structured(self, state: SectionState) -> dict:
        base = super().emit_structured(state)
        seen: dict[str, dict] = {}
        relationships: list[dict] = []
        for item in state.items:
            for e in self.extract_entities(item):
                seen[e["id"]] = e
            doc_id = f"filing:gov-{item.get('id','')}"
            evidence = [item.get("_id") or self._item_id(item)]
            if item.get("org"):
                relationships.append({
                    "from": doc_id,
                    "to": f"org:gov-{_slug(item['org'])}",
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
        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships
        return base

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["entities"] = [e["id"] for e in self.extract_entities(item)]
        # attribute each record to its own organ so per-org provenance is kept
        record["source_id"] = f"gov:{_slug(item.get('org') or 'unknown')}"
        return record

    # ----- richer briefing summary, grouped by branch -----------------------
    def synthesize_summary(self, state: SectionState) -> str:
        new_ids = {n.get("_id") for n in state.new}
        groups: dict[str, list[dict]] = {}
        for it in state.items:
            groups.setdefault(it.get("branch", "other"), []).append(it)
        order = ["executive", "legislative", "judicial", "independent", "state", "other"]
        lines = [
            "---",
            f"section: {self.id}",
            f"title: {self.title}",
            f"date: {state.source_date or ''}",
            f"record_count: {len(state.items)}",
            f"new_today: {len(state.new)}",
            f"state: {state.state}",
            "---",
            "",
            f"## {self.title}",
            "",
            f"{len(state.new)} new of {len(state.items)} government actions today, "
            f"across {len([g for g in groups if groups[g]])} branches/organs.",
            "",
        ]
        for branch in order:
            items = groups.get(branch)
            if not items:
                continue
            emoji = BRANCH_EMOJI.get(branch, "•")
            lines.append(f"### {emoji} {branch.title()} ({len(items)})")
            for it in items[:20]:
                marker = "**NEW**  " if it.get("_id") in new_ids else ""
                org = it.get("org", "")
                lines.append(
                    f"- {marker}[{it.get('title','(no title)')}]({it.get('url','#')}) "
                    f"— *{org}*, {it.get('doc_type','')} · {it.get('date','')}"
                )
            if len(items) > 20:
                lines.append(f"  _({len(items) - 20} more from this branch in raw.jsonl)_")
            lines.append("")
        return "\n".join(lines) + "\n"
