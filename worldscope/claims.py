"""worldscope.claims — the evidence engine: turn the lake's records into typed,
evidence-graded **claims** instead of an item list.

A claim is not an LLM paraphrase of a headline. It is a *cluster of real records
that assert the same thing*, carrying:

  * **type** — reported_fact / official_statement / market_signal /
    statistical_anomaly / osint_observation / inference / forecast / correction
    / contradiction, derived from the *kind* of source that carries it.
  * **evidence** — the actual records, each tagged with its source tier and a
    support label (supports / refutes / context).
  * **status** — derived deterministically from the evidence structure:
    `contradicted` if anyone denies it, else `primary_confirmed` if an official/
    primary document carries it, else `multi_source` (≥2 independent sections),
    else `single_source`, else `not_enough_info`.
  * **confidence** — a conservative cross-source corroboration score, tier- and
    recency-weighted, penalized by contradiction.
  * **longitudinal identity** — keyed on the normalized assertion, so a claim
    accumulates evidence across days and its confidence *movement* is tracked.

This makes the claim graph an auditable, reproducible, zero-cost projection of
the lake: every claim is backed by record IDs you can open. An LLM can later
write nicer claim_text, but the epistemics never depend on it.

Design constraints mirror signals.py / radar.py: pure, offline, stdlib-only
core; reuses signals' record loaders + key/entity extraction so all three
engines see identical records; nothing here can abort a brief.

    python -m worldscope.claims --days 3            # print today's claims
    python -m worldscope.claims --write             # persist to the lake
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from . import signals as sg

METHOD = "claim-extract-v1"

DEFAULT_WINDOW_DAYS = 3
DEFAULT_MIN_SECTIONS = 2
DEFAULT_TOP_N = 60
CONF_FLOOR, CONF_CEIL = 0.40, 0.92

# ---- source-tier ranking (mirrors radar.TIER_PRIOR ordering) ---------------
PRIMARY_TIERS = {"primary_document", "official", "government"}
INDEPENDENT_TIERS = {"mainstream_independent", "specialist"}

# ---- section → evidence role (what KIND of evidence this section is) --------
SECTION_ROLE: dict[str, str] = {
    # official / primary-document desks
    "federal_register": "official", "sanctions": "official",
    "sanctions_procurement": "official", "courtlistener": "official",
    "fec": "official", "congressional_trades": "official", "form4": "official",
    "state_bills": "official", "cisa_kev": "official",
    # market / forward-looking
    "markets": "market", "markets_global": "market", "paper_bets": "market",
    "paper_bet_placement": "market", "forecasts": "market",
    # physical-world / OSINT sensors
    "conflict": "osint", "acled": "osint", "firms": "osint",
    "vip_flights": "osint", "ukraine_theater": "osint", "weather": "physical",
    "gdelt_regions": "reported", "gdelt_gkg": "reported",
    # analytic
    "radar": "anomaly", "signals": "anomaly",
}
ROLE_TO_TYPE = {
    "official": "official_statement", "market": "market_signal",
    "osint": "osint_observation", "physical": "osint_observation",
    "anomaly": "statistical_anomaly", "reported": "reported_fact",
}
# Which role defines the claim's *nature* when several are present. A news event
# that a prediction market also lists is a reported_fact with market
# corroboration — not a "market signal". Substance beats instrument.
ROLE_PRIORITY = ["official", "reported", "osint", "physical", "market", "anomaly"]

# Denial / refutation cues — a supporting record carrying one of these about the
# same key is treated as *refuting*, which flips the claim to `contradicted`.
# Deliberately strong cues only — weak ones ("did not", "no", "never") appear
# in ordinary and legislative text and produce false contradictions.
_DENIAL_RE = re.compile(
    r"\b(denies|denied|deny|rejects|rejected|refutes?|refuted|debunks?|debunked|"
    r"no evidence|false claim|baseless|unfounded|hoax|fabricated|disputes?|"
    r"disputed)\b", re.IGNORECASE)
# Denial detection only applies to reporting/OSINT desks — official documents
# and market data don't "deny" things in the claim sense.
_DENIABLE_ROLES = {"reported", "osint"}


def _section_default_tier(section: str) -> str:
    role = SECTION_ROLE.get(section, "reported")
    if role in ("official",):
        return "primary_document"
    if role in ("osint", "physical", "market", "anomaly"):
        return "specialist"
    return "aggregator"


def _is_denial(text: str) -> bool:
    return bool(_DENIAL_RE.search(text or ""))


def _tier_is_primary(tier: Optional[str]) -> bool:
    return (tier or "") in PRIMARY_TIERS


def _representative_title(evs: list) -> str:
    """The headline that best stands in for the cluster: most-repeated, then
    longest. A real sentence beats a bare entity name as the claim text."""
    from collections import Counter
    titles = [e.title for e in evs if e.title]
    if not titles:
        return ""
    counts = Counter(titles)
    return max(titles, key=lambda t: (counts[t], len(t)))


# ============================================================================
# Claim model
# ============================================================================

@dataclass
class Evidence:
    record_id: str
    section: str
    source_id: str
    source_tier: str
    role: str
    support_label: str        # supports | refutes
    title: str
    url: str
    record_date: str


@dataclass
class Claim:
    key: str
    claim_text: str
    claim_type: str
    status: str
    confidence: float
    actors: list[str]
    topics: list[str]              # distinct sections
    n_sources: int
    n_sections: int
    requires_followup: bool
    event_time: Optional[str]
    evidence: list[Evidence] = field(default_factory=list)
    contradiction_note: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "claim_text": self.claim_text,
            "claim_type": self.claim_type, "status": self.status,
            "confidence": round(self.confidence, 4), "actors": self.actors,
            "topics": self.topics, "n_sources": self.n_sources,
            "n_sections": self.n_sections,
            "requires_followup": self.requires_followup,
            "contradiction_note": self.contradiction_note,
            "evidence": [e.__dict__ for e in self.evidence],
        }


# ============================================================================
# Build
# ============================================================================

def _confidence(n_sections: int, n_sources: int, status: str, min_age: int) -> float:
    base = (0.50 + 0.06 * min(max(n_sections - 1, 0), 4)
            + 0.02 * min(max(n_sources - 2, 0), 5))
    if status == "primary_confirmed":
        base += 0.10
    elif status == "multi_source":
        base += 0.04
    if min_age <= 1:
        base += 0.03
    if status == "contradicted":
        base = min(base, 0.52) - 0.06
    return round(max(CONF_FLOOR, min(base, CONF_CEIL)), 4)


def build_claims(
    records: Iterable[dict], *, today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_sections: int = DEFAULT_MIN_SECTIONS,
    tier_by_source: Optional[dict[str, str]] = None,
    top_n: int = DEFAULT_TOP_N, hub_max: int = 60, min_overlap: int = 2,
) -> list[Claim]:
    """Cluster records that assert the same thing, then grade each cluster.

    The clustering is the careful part: two records belong to the same claim
    only if they share **>= min_overlap distinctive keys** (near-duplicate
    detection via union-find), with "hub" keys that appear in > ``hub_max``
    records dropped. That distinguishes "same assertion" from "merely shares a
    word" — the failure mode of single-key matching. A claim requires >= 2
    distinct sources (so the per-outlet news corpus corroborates correctly)."""
    tier_by_source = tier_by_source or {}
    horizon = today - timedelta(days=window_days)

    # 1. materialize in-window records with their key sets
    recs: list[dict] = []
    for rec in records:
        if sg.is_noise_record(rec):
            continue
        day = sg._parse_day(rec)
        if day is None or day < horizon or day > today:
            continue
        keys = sg.record_keys(rec)
        if not keys:
            continue
        section = str(rec.get("section_id") or rec.get("section") or "?")
        role = SECTION_ROLE.get(section, "reported")
        title = sg._clean_text(rec.get("title") or rec.get("original_text") or "")
        src = str(rec.get("source_id") or section)
        recs.append({
            "rid": rec.get("id") or rec.get("_id") or "",
            "section": section, "src": src, "role": role,
            "tier": tier_by_source.get(src) or _section_default_tier(section),
            "title": title[:200], "url": rec.get("original_url") or rec.get("url") or "",
            "date": day.isoformat(), "age": max((today - day).days, 0),
            "refuting": role in _DENIABLE_ROLES and _is_denial(title),
            "ents": sg._entity_names(rec), "keys": keys,
        })

    # 2. inverted index, with hub keys removed (too generic to corroborate)
    key_index: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(recs):
        for k in r["keys"]:
            key_index[k].append(i)
    hubs = {k for k, v in key_index.items() if len(v) > hub_max}

    # 3. union records sharing >= min_overlap non-hub keys
    parent = list(range(len(recs)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, r in enumerate(recs):
        shared: Counter = Counter()
        for k in r["keys"]:
            if k in hubs:
                continue
            for j in key_index[k]:
                if j > i:
                    shared[j] += 1
        for j, n in shared.items():
            if n >= min_overlap:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(recs)):
        groups[find(i)].append(i)

    # 4. grade each multi-record cluster into a claim
    claims: list[Claim] = []

    def _ev(m: dict) -> Evidence:
        return Evidence(
            record_id=m["rid"], section=m["section"], source_id=m["src"],
            source_tier=m["tier"], role=m["role"],
            support_label="refutes" if m["refuting"] else "supports",
            title=m["title"], url=m["url"], record_date=m["date"])

    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        members = [recs[i] for i in idxs]
        supports = [m for m in members if not m["refuting"]]
        refutes = [m for m in members if m["refuting"]]
        if not supports:
            continue
        sources = {m["src"] for m in supports}
        if len(sources) < 2:        # cross-source is the whole point
            continue
        sections = sorted({m["section"] for m in supports})
        has_primary = any(_tier_is_primary(m["tier"]) for m in supports)
        if refutes:
            status = "contradicted"
        elif has_primary:
            status = "primary_confirmed"
        elif len(sections) >= 2 or len(sources) >= 3:
            status = "multi_source"
        else:
            status = "single_source"

        min_age = min((m["age"] for m in supports), default=0)
        confidence = _confidence(len(sections), len(sources), status, min_age)
        chosen_role = next((r for r in ROLE_PRIORITY
                            if r in {m["role"] for m in supports}), "reported")
        claim_type = ROLE_TO_TYPE.get(chosen_role, "reported_fact")
        # stable identity: the most common non-hub key across the cluster
        keycnt = Counter(k for m in members for k in m["keys"] if k not in hubs)
        claim_key = keycnt.most_common(1)[0][0] if keycnt else (
            members[0]["rid"] or "claim")
        sup_ev = [_ev(m) for m in supports]
        ref_ev = [_ev(m) for m in refutes]
        claim_text = _representative_title(sup_ev) or claim_key
        actcnt = Counter(e for m in supports for e in m["ents"])
        note = ""
        if refutes:
            note = (f"{len(refutes)} record(s) appear to deny this "
                    f"({', '.join(sorted({m['section'] for m in refutes}))}).")
        claims.append(Claim(
            key=claim_key, claim_text=claim_text[:200], claim_type=claim_type,
            status=status, confidence=confidence,
            actors=[a for a, _ in actcnt.most_common(8)], topics=sections,
            n_sources=len(sources), n_sections=len(sections),
            requires_followup=(status in ("single_source", "contradicted")
                               or confidence < 0.55),
            event_time=min(m["date"] for m in members),
            evidence=(sup_ev + ref_ev)[:10], contradiction_note=note))

    claims.sort(key=lambda c: (c.status == "contradicted", c.confidence,
                               c.n_sources), reverse=True)
    return claims[:top_n]


# ============================================================================
# Persistence + brief panel
# ============================================================================

def persist_claims(lake, claims: list[Claim], *, today: date) -> int:
    n = 0
    for cl in claims:
        cid = lake.upsert_claim(
            claim_key=cl.key, claim_text=cl.claim_text, claim_type=cl.claim_type,
            status=cl.status, confidence=cl.confidence, actors=cl.actors,
            places=[], topics=cl.topics, n_sources=cl.n_sources,
            n_sections=cl.n_sections, event_time=cl.event_time,
            requires_followup=cl.requires_followup, when=today.isoformat(),
            method=METHOD)
        for e in cl.evidence:
            lake.add_claim_evidence(
                claim_id=cid, record_id=e.record_id, section_id=e.section,
                source_id=e.source_id, source_tier=e.source_tier,
                support_label=e.support_label, evidence_role=e.role,
                record_date=e.record_date)
        n += 1
    return n


_STATUS_COLOR = {
    "primary_confirmed": "#2F6B3A", "multi_source": "#2B4257",
    "single_source": "#9A6B00", "contradicted": "#990000",
    "not_enough_info": "#6F695C",
}
_STATUS_LABEL = {
    "primary_confirmed": "primary-confirmed", "multi_source": "multi-source",
    "single_source": "single-source", "contradicted": "contradicted",
    "not_enough_info": "unverified",
}


def render_claims_panel(claims: list[Claim], *, max_show: int = 12) -> str:
    import html as _html
    if not claims:
        return ""
    rows = []
    for cl in claims[:max_show]:
        color = _STATUS_COLOR.get(cl.status, "#6F695C")
        ev = cl.evidence[0] if cl.evidence else None
        link = ""
        if ev and ev.url:
            link = (f" — <a href='{_html.escape(ev.url, quote=True)}'>"
                    f"{_html.escape(ev.title[:90])}</a>")
        meta = (f"{_html.escape(', '.join(cl.topics[:5]))} · "
                f"{cl.n_sources} source{'s' if cl.n_sources != 1 else ''} · "
                f"{int(round(cl.confidence * 100))}%")
        note = (f"<div class='abs'>{_html.escape(cl.contradiction_note)}</div>"
                if cl.contradiction_note else "")
        rows.append(
            "<li>"
            f"<span class='new-badge' style='background:{color}'>"
            f"{_STATUS_LABEL.get(cl.status, cl.status)}</span>"
            f"<strong>{_html.escape(cl.claim_text[:110])}</strong>"
            f"<span class='meta'> · {cl.claim_type.replace('_', ' ')} · {meta}</span>"
            f"{note}{link}</li>")
    n_contra = sum(1 for c in claims if c.status == "contradicted")
    n_primary = sum(1 for c in claims if c.status == "primary_confirmed")
    synth = (
        f"<p class='synth'>{len(claims)} claims extracted from today's lake — "
        f"{n_primary} primary-source confirmed, {n_contra} contradicted. Every "
        f"claim links to the records that back it; status and confidence are "
        f"derived from how many independent sources corroborate it.</p>")
    return (
        "<section class='section'>"
        "<h2>🧾 Claims — what's asserted, and how well-supported "
        f"<span class='count'>· {len(claims)}</span></h2>"
        f"{synth}<ul class='items'>{''.join(rows)}</ul></section>")


# ============================================================================
# Orchestration + CLI
# ============================================================================

def _tier_map(conn) -> dict[str, str]:
    try:
        return {r[0]: (r[1] or "unknown")
                for r in conn.execute("SELECT id, tier FROM sources")}
    except Exception:
        return {}


def build_from_lake(*, today: date, days: int = DEFAULT_WINDOW_DAYS, conn=None):
    db_recs: list[dict] = []
    tiers: dict[str, str] = {}
    if conn is not None:
        try:
            db_recs = sg.load_records_from_db(conn, today=today, days=days)
            tiers = _tier_map(conn)
        except Exception:
            db_recs = []
    jsonl_recs = sg.load_records_from_jsonl(today=today, days=days)
    # Use whichever corpus is fuller. The DB records table carries entity links
    # (richer actors) but only holds sections that pass the FK; the JSONL
    # artifacts hold every section. Until the records table is fully populated,
    # JSONL is more complete; once it is, the DB wins and brings entities.
    records = db_recs if len(db_recs) >= len(jsonl_recs) else jsonl_recs
    return build_claims(records, today=today, window_days=days,
                        tier_by_source=tiers)


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the WORLDSCOPE claim graph.")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--write", action="store_true", help="persist to the lake")
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.date)

    from .lake import Lake
    lake = Lake.open()
    conn = lake._ensure_open()
    claims = build_from_lake(today=today, days=args.days, conn=conn)
    print(f"[claims] {len(claims)} claims as of {today} (window {args.days}d):\n")
    for i, cl in enumerate(claims[:args.top], 1):
        print(f"{i:2}. [{_STATUS_LABEL.get(cl.status, cl.status):16} "
              f"{int(cl.confidence*100)}% | {cl.claim_type}] {cl.claim_text[:64]}")
        print(f"      {', '.join(cl.topics[:8])}"
              + (f"  ⚠ {cl.contradiction_note}" if cl.contradiction_note else ""))
    if args.write:
        n = persist_claims(lake, claims, today=today)
        print(f"\n[claims] persisted {n} claims + evidence to the lake.")
    lake.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
