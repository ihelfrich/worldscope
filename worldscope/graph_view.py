"""graph_view.py — build-time view over the lake's ENTITY KNOWLEDGE GRAPH.

The lake already accumulates entities, record->entity links, and ~13k
relationship edges (bill co-sponsorship, agency 'issued-by', insider 'traded',
'signed-by', 'reports-on'). But the rich structured entities (people, orgs)
are siloed per section by differing ids, so a person who traded a stock is a
different node than the same person in a court filing — cross-domain joins miss.

This module fixes that at READ time: it unifies entities by a normalized name
key (collapsing the silos), computes each entity's CROSS-DOMAIN footprint
(which sections mention it), pulls sample evidence, and attaches the
high-value relationship edges. No schema change, no re-ingest — it turns the
graph that already exists into a queryable 'who connects across domains' view.

Used by the board to render the 'Connections / Power & Money' panel.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LAKE = REPO / "lake" / "db" / "worldscope.sqlite"

# section -> coarse domain (mirror of the board's domains; kept local so this
# module has no import cycle with the renderer)
SECTION_DOMAIN = {
    "conflict": "war", "acled": "war", "ukraine_theater": "war", "firms": "war",
    "vip_flights": "war",
    "foreign_news": "geo", "people": "geo", "political_figures": "geo",
    "commentary": "geo", "gdelt_regions": "geo", "gdelt_gkg": "geo", "mediacloud": "geo",
    "markets_global": "mkt", "macro": "mkt", "forecasts": "mkt", "paper_bets": "mkt",
    "markets": "mkt", "billionaires": "mkt", "congressional_trades": "mkt", "form4": "mkt",
    "cisa_kev": "cyber", "epss": "cyber",
    "who_don": "health", "reliefweb": "health", "promed": "health",
    "usgs_quakes": "earth", "weather": "earth", "gdacs": "earth",
    "federal_register": "usgov", "state_bills": "usgov", "state_news": "usgov",
    "local_news": "usgov", "courtlistener": "usgov", "fec": "usgov",
    "sanctions": "usgov", "sanctions_procurement": "usgov",
    "chinese_internal": "state", "russian_internal": "state", "ukrainian_internal": "state",
    "wikidata_changes": "geo",
}

# generic / geographic names that are noise as "cross-domain entities"
_STOP = {
    "united states", "us", "u.s.", "usa", "america", "fed", "high", "low", "eu",
    "european union", "un", "united nations", "nato", "china", "russia", "iran",
    "israel", "india", "japan", "germany", "france", "italy", "ukraine", "gaza",
    "mexico", "kuwait", "congo", "democratic republic", "korea", "north korea",
    "south korea", "turkey", "saudi arabia", "uk", "united kingdom", "england",
    "washington", "moscow", "beijing", "europe", "asia", "africa", "west",
    "the white house", "white house", "congress", "senate", "house",
}
_HONORIFIC = re.compile(r"^(mr|mrs|ms|dr|sen|rep|gov|president|secretary|justice|judge)\.?\s+", re.I)
_HI_VALUE_EDGES = {"traded", "sponsored-by", "co-sponsored-by", "issued-by",
                   "signed-by", "introduced-in", "reports-on"}


def _norm(name: str) -> str:
    n = (name or "").strip()
    n = _HONORIFIC.sub("", n)
    n = re.sub(r"[^\w\s'-]", "", n).strip().lower()
    n = re.sub(r"\s+", " ", n)
    return n


def _connect(lake_path: Path) -> sqlite3.Connection | None:
    if not Path(lake_path).exists():
        return None
    c = sqlite3.connect(str(lake_path))
    c.row_factory = sqlite3.Row
    return c


def connections(today: str | None = None, *, lake_path: Path | None = None,
                days: int = 10, limit: int = 16) -> dict:
    """Return the top cross-domain entities (unified by normalized name) with
    their domain footprint, sample evidence, and high-value graph edges."""
    lake_path = lake_path or DEFAULT_LAKE
    conn = _connect(lake_path)
    if conn is None:
        return {"entities": [], "edges_total": 0}
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
             - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

    # type rank so the unified node keeps the most informative type
    type_rank = {"person": 0, "org": 1, "filing": 2, "event": 3, "place": 4,
                 "market": 5, "mention": 9}
    try:
        rows = conn.execute(
            """
            SELECT e.id eid, e.type etype, e.canonical_name name,
                   r.section_id section, r.original_text text, r.record_date rdate
            FROM record_entities re
            JOIN entities e ON e.id = re.entity_id
            JOIN records r ON r.id = re.record_id
            WHERE r.ingested_at >= ?
            """, (since,)).fetchall()
    except sqlite3.Error as exc:
        print(f"[graph_view] read failed: {exc}")
        conn.close()
        return {"entities": [], "edges_total": 0}

    # unify by normalized name
    agg: dict[str, dict] = {}
    for r in rows:
        key = _norm(r["name"])
        if not key or len(key) < 4 or key in _STOP:
            continue
        dom = SECTION_DOMAIN.get(r["section"], "other")
        a = agg.setdefault(key, {"name": r["name"], "type": r["etype"], "ids": set(),
                                 "sections": set(), "domains": set(), "mentions": 0,
                                 "evidence": []})
        a["ids"].add(r["eid"])
        a["sections"].add(r["section"])
        a["domains"].add(dom)
        a["mentions"] += 1
        # keep the best (most specific) type + a clean display name
        if type_rank.get(r["etype"], 9) < type_rank.get(a["type"], 9):
            a["type"] = r["etype"]
            a["name"] = r["name"]
        if r["text"] and len(a["evidence"]) < 4:
            a["evidence"].append({"section": r["section"],
                                  "text": re.sub(r"<[^>]+>", "", r["text"])[:160]})

    # candidate pool: structured entities spanning >=2 domains (or >=2 sections),
    # with a real-looking name. We attach edges, then keep only those with a
    # genuine graph relationship so every card is substantive (no bare mentions).
    def _good_name(name: str, etype: str) -> bool:
        nm = (name or "").strip()
        if len(nm) < 4 or any(ch.isdigit() for ch in nm):
            return False
        if etype == "person" and len(nm.split()) < 2:    # drop bare surnames
            return False
        if nm.isupper() and len(nm) <= 5:                 # stray acronyms
            return False
        return True

    pool = [a for a in agg.values()
            if a["type"] in {"person", "org", "filing", "event"}
            and _good_name(a["name"], a["type"])
            and (len(a["domains"]) >= 2 or len(a["sections"]) >= 2)]
    pool.sort(key=lambda a: (len(a["domains"]), len(a["sections"]), a["mentions"]),
              reverse=True)
    pool = pool[:60]

    edges_total = conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    name_by_id = {r[0]: r[1] for r in conn.execute("SELECT id, canonical_name FROM entities")}
    for a in pool:
        a["edges"] = []
        seen = set()
        for i in list(a["ids"])[:40]:
            for er in conn.execute(
                "SELECT from_entity f, to_entity t, type ty FROM relationships "
                "WHERE (from_entity=? OR to_entity=?) LIMIT 30", (i, i)):
                if er["ty"] not in _HI_VALUE_EDGES:
                    continue
                outward = er["f"] in a["ids"]
                other = name_by_id.get(er["t"] if outward else er["f"], "?")
                sig = (er["ty"], other, outward)
                if other == "?" or sig in seen:
                    continue
                seen.add(sig)
                a["edges"].append({"type": er["ty"], "other": other[:48], "out": outward})
            if len(a["edges"]) >= 8:
                break

    # keep only entities with a real edge; rank edges-first so the substantive
    # connections (trades, signings, sponsorships) lead.
    ranked = [a for a in pool if a["edges"]]
    ranked.sort(key=lambda a: (len(a["edges"]), len(a["domains"]), a["mentions"]),
                reverse=True)
    ranked = ranked[:limit]
    conn.close()

    out = []
    for a in ranked:
        out.append({
            "name": a["name"], "type": a["type"],
            "domains": sorted(a["domains"]), "sections": sorted(a["sections"]),
            "n_domains": len(a["domains"]), "mentions": a["mentions"],
            "evidence": a["evidence"], "edges": a["edges"][:8],
        })
    return {"entities": out, "edges_total": edges_total, "date": today}


if __name__ == "__main__":
    import sys
    g = connections(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"{len(g['entities'])} cross-domain entities · {g['edges_total']} edges in graph")
    for e in g["entities"][:12]:
        ed = "; ".join(f"{x['type']}→{x['other']}" for x in e["edges"][:3])
        print(f"  [{e['type']:6}] {e['name'][:28]:28} {e['n_domains']}dom {e['domains']}"
              + (f"  | {ed}" if ed else ""))
