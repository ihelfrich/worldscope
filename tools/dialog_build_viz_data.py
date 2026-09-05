#!/usr/bin/env python3
"""
dialog_build_viz_data.py — compile the Dialog investigative-explorer data feeds.

Reads the network graph, the per-entity public-record bundles, and the
foundation 990s, and emits:
  explorer/data.js     window.DIALOG = {nodes, edges, events, geo, meta}
                       (inlined so the explorer opens from file:// with no server)
  explorer/data/*.json same feeds as plain JSON (for programmatic use)

Events carry {date, year, type, entity, label, nodeId?, title, url, value?} so
the front-end can plot them on a timeline, map them to network nodes, and
cross-filter. Geo places each org at its HQ and each person at their primary
org's location. Public-record data only.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "research_reports" / "dialog-retreat-members"
OUT = ROOT / "explorer"
DATA = OUT / "data"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# --- geo: node_id -> [lat, lon, city, country] -------------------------------
GEO = {
    "palantir": [39.7392, -104.9903, "Denver", "USA"],
    "founders_fund": [37.7749, -122.4194, "San Francisco", "USA"],
    "clarium": [37.7749, -122.4194, "San Francisco", "USA"],
    "thiel_capital": [37.7749, -122.4194, "San Francisco", "USA"],
    "thiel_foundation": [37.7749, -122.4194, "San Francisco", "USA"],
    "openai": [37.7749, -122.4194, "San Francisco", "USA"],
    "anthropic": [37.7749, -122.4194, "San Francisco", "USA"],
    "stripe": [37.7749, -122.4194, "San Francisco", "USA"],
    "sierra": [37.7749, -122.4194, "San Francisco", "USA"],
    "airtable": [37.7749, -122.4194, "San Francisco", "USA"],
    "eightvc": [30.2672, -97.7431, "Austin", "USA"],
    "cicero": [30.2672, -97.7431, "Austin", "USA"],
    "uatx": [30.2672, -97.7431, "Austin", "USA"],
    "tesla": [30.2672, -97.7431, "Austin", "USA"],
    "xai": [30.2672, -97.7431, "Austin", "USA"],
    "spacex": [33.9207, -118.3287, "Hawthorne", "USA"],
    "neuralink": [37.4275, -122.1697, "Fremont", "USA"],
    "anduril": [33.6411, -117.9187, "Costa Mesa", "USA"],
    "erebor": [37.7749, -122.4194, "San Francisco", "USA"],
    "groq": [37.4220, -122.0841, "Mountain View", "USA"],
    "psiquantum": [37.4419, -122.1430, "Palo Alto", "USA"],
    "oklo": [37.3541, -121.9552, "Santa Clara", "USA"],
    "a16z": [37.4530, -122.1817, "Menlo Park", "USA"],
    "social_capital": [37.4419, -122.1430, "Palo Alto", "USA"],
    "ribbit": [37.4419, -122.1430, "Palo Alto", "USA"],
    "greylock": [37.4530, -122.1817, "Menlo Park", "USA"],
    "thrive": [40.7128, -74.0060, "New York", "USA"],
    "microsoft": [47.6740, -122.1215, "Redmond", "USA"],
    "youtube": [37.4220, -122.0841, "Mountain View", "USA"],
    "googlex": [37.4220, -122.0841, "Mountain View", "USA"],
    "jigsaw": [40.7128, -74.0060, "New York", "USA"],
    "deepmind": [51.5336, -0.1248, "London", "UK"],
    "intuit": [37.4220, -122.0841, "Mountain View", "USA"],
    "quora": [37.4419, -122.1430, "Mountain View", "USA"],
    "duolingo": [40.4406, -79.9959, "Pittsburgh", "USA"],
    "linkedin": [37.4530, -122.1817, "Sunnyvale", "USA"],
    "facebook": [37.4847, -122.1477, "Menlo Park", "USA"],
    "oscar": [40.7128, -74.0060, "New York", "USA"],
    "relativity": [33.9207, -118.3287, "Long Beach", "USA"],
    "wellhub": [40.7128, -74.0060, "New York", "USA"],
    # finance
    "kkr": [40.7580, -73.9855, "New York", "USA"],
    "fortress": [40.7580, -73.9855, "New York", "USA"],
    "millennium": [40.7580, -73.9855, "New York", "USA"],
    "jain_global": [40.7580, -73.9855, "New York", "USA"],
    "bridgewater": [41.1415, -73.3579, "Westport", "USA"],
    "rentech": [40.9446, -73.1009, "East Setauket", "USA"],
    "galaxy": [40.7580, -73.9855, "New York", "USA"],
    "blackstone": [40.7580, -73.9855, "New York", "USA"],
    "breyer_capital": [37.4419, -122.1430, "Menlo Park", "USA"],
    "starwood": [25.7907, -80.1300, "Miami Beach", "USA"],
    "dcg": [41.0534, -73.5387, "Stamford", "USA"],
    "grayscale": [41.0534, -73.5387, "Stamford", "USA"],
    "centerview": [40.7580, -73.9855, "New York", "USA"],
    "citi": [40.7580, -73.9855, "New York", "USA"],
    "goldman": [40.7145, -74.0140, "New York", "USA"],
    "tiaa": [40.7580, -73.9855, "New York", "USA"],
    "berggruen_holdings": [34.0522, -118.2437, "Los Angeles", "USA"],
    "berggruen_inst": [34.0522, -118.2437, "Los Angeles", "USA"],
    "xn": [40.7580, -73.9855, "New York", "USA"],
    "soroban": [40.7580, -73.9855, "New York", "USA"],
    "key_square": [40.7580, -73.9855, "New York", "USA"],
    "take_two": [40.7580, -73.9855, "New York", "USA"],
    "verizon": [40.7580, -73.9855, "New York", "USA"],
    "circle": [40.7580, -73.9855, "New York", "USA"],
    "robinhood": [37.4419, -122.1430, "Menlo Park", "USA"],
    "coinbase": [37.7749, -122.4194, "San Francisco", "USA"],
    "strategy_mstr": [38.9847, -77.2375, "Tysons", "USA"],
    "verisk": [40.8259, -74.1290, "Jersey City", "USA"],
    # gov / policy / DC
    "bessent": [38.8987, -77.0335, "Washington", "USA"],
    "ltf": [38.9072, -77.0369, "Washington", "USA"],
    "atr": [38.9072, -77.0369, "Washington", "USA"],
    "cato": [38.9043, -77.0186, "Washington", "USA"],
    "new_america": [38.9072, -77.0369, "Washington", "USA"],
    "hudson": [38.9072, -77.0369, "Washington", "USA"],
    "atlantic_council": [38.9009, -77.0380, "Washington", "USA"],
    "fedsoc": [38.9072, -77.0369, "Washington", "USA"],
    "marble": [38.9072, -77.0369, "Washington", "USA"],
    "koch_fdn": [38.8816, -77.1043, "Arlington", "USA"],
    "patomak": [38.9072, -77.0369, "Washington", "USA"],
    "pclob": [38.9072, -77.0369, "Washington", "USA"],
    "affinity_partners": [25.7617, -80.1918, "Miami", "USA"],
    "purdue": [40.4237, -86.9212, "West Lafayette", "USA"],
    "kcl": [37.7749, -122.4194, "San Francisco", "USA"],
    "adl": [40.7128, -74.0060, "New York", "USA"],
    "cfr": [40.7710, -73.9640, "New York", "USA"],
    "renew_dem": [40.7128, -74.0060, "New York", "USA"],
    # academia / media
    "stanford": [37.4275, -122.1697, "Stanford", "USA"],
    "stanford_gsb": [37.4275, -122.1697, "Stanford", "USA"],
    "hoover": [37.4275, -122.1697, "Stanford", "USA"],
    "harvard": [42.3770, -71.1167, "Cambridge", "USA"],
    "uchicago": [41.7886, -87.5987, "Chicago", "USA"],
    "cmu": [40.4433, -79.9436, "Pittsburgh", "USA"],
    "nyu_stern": [40.7295, -73.9965, "New York", "USA"],
    "wharton": [39.9522, -75.1976, "Philadelphia", "USA"],
    "mercatus": [38.8304, -77.3064, "Fairfax", "USA"],
    "nyt_opinion": [40.7561, -73.9903, "New York", "USA"],
    "atlantic": [38.9072, -77.0369, "Washington", "USA"],
    "free_press": [40.7128, -74.0060, "New York", "USA"],
    "wired": [37.7749, -122.4194, "San Francisco", "USA"],
    "vox": [40.7128, -74.0060, "New York", "USA"],
    "edge_org": [40.7128, -74.0060, "New York", "USA"],
    "next_big_idea": [40.7128, -74.0060, "New York", "USA"],
    "ferriss_show": [30.2672, -97.7431, "Austin", "USA"],
    "hybe": [34.0522, -118.2437, "Los Angeles", "USA"],
    "a24": [40.7128, -74.0060, "New York", "USA"],
    "chipotle": [38.9072, -77.0369, "Newport Beach", "USA"],
    # international
    "novartis": [47.5596, 7.5886, "Basel", "Switzerland"],
    "trendyol": [41.0082, 28.9784, "Istanbul", "Turkey"],
    "alibaba": [30.2741, 120.1551, "Hangzhou", "China"],
    "meli": [-34.6037, -58.3816, "Buenos Aires", "Argentina"],
    "kaszek": [-34.6037, -58.3816, "Buenos Aires", "Argentina"],
    "atlassian": [-33.8688, 151.2093, "Sydney", "Australia"],
    "grok_ventures": [-33.8688, 151.2093, "Sydney", "Australia"],
    "xapo": [36.1408, -5.3536, "Gibraltar", "Gibraltar"],
    "king_faisal": [24.7136, 46.6753, "Riyadh", "Saudi Arabia"],
    "pif": [24.7136, 46.6753, "Riyadh", "Saudi Arabia"],
    "kpc": [29.3759, 47.9774, "Kuwait City", "Kuwait"],
    "js_group": [24.8607, 67.0011, "Karachi", "Pakistan"],
    "onzero": [48.2082, 16.3738, "Vienna", "Austria"],
    "airblue": [33.6844, 73.0479, "Islamabad", "Pakistan"],
    "entrepreneur_first": [51.5074, -0.1278, "London", "UK"],
    "aria_uk": [51.5074, -0.1278, "London", "UK"],
    "helsing": [48.1351, 11.5820, "Munich", "Germany"],
    "unitedhealth": [44.9778, -93.2650, "Minnetonka", "USA"],
    # the hub + special markers
    "dialog": [53.3498, -6.2603, "Dublin (2026 retreat)", "Ireland"],
    "reema": [38.9217, -77.0709, "Saudi Embassy, DC", "USA"],
    "bandar": [24.7136, 46.6753, "Riyadh", "Saudi Arabia"],
    "grynkewich": [50.4542, 3.9564, "SHAPE, Mons", "Belgium"],
    "kallas": [50.8503, 4.3517, "Brussels", "Belgium"],
    "kono": [35.6762, 139.6503, "Tokyo", "Japan"],
    "tugendhat": [51.5074, -0.1278, "London", "UK"],
    "stephens_l": [51.5074, -0.1278, "London", "UK"],
    "auken": [55.6761, 12.5683, "Copenhagen", "Denmark"],
    "gulati": [59.9139, 10.7522, "Oslo", "Norway"],
    "turki": [24.7136, 46.6753, "Riyadh", "Saudi Arabia"],
    "nawaf_sabah": [29.3759, 47.9774, "Kuwait City", "Kuwait"],
    "abbasi": [33.6844, 73.0479, "Islamabad", "Pakistan"],
    "ali_siddiqui": [24.8607, 67.0011, "Karachi", "Pakistan"],
    "mutlu": [41.0082, 28.9784, "Istanbul", "Turkey"],
    "narasimhan": [47.5596, 7.5886, "Basel", "Switzerland"],
    "galperin": [-34.6037, -58.3816, "Buenos Aires", "Argentina"],
    "cannon_brookes": [-33.8688, 151.2093, "Sydney", "Australia"],
}

# manual slug -> node id aliases where the auto-matcher would miss
ALIAS = {
    "stanley-mcchrystal": "mcchrystal", "michael-novogratz": "novogratz",
    "kkr-co": "kkr", "andreessen-horowitz": "a16z", "blackstone-inc": "blackstone",
    "circle-internet-group": "circle", "robinhood-markets": "robinhood",
    "coinbase-global": "coinbase", "block-inc": "block_sq", "qxo-inc": "qxo",
    "take-two-interactive": "take_two", "verizon-communications": "verizon",
    "bridgewater-associates": "bridgewater", "renaissance-technologies": "rentech",
    "millennium-management": "millennium", "fortress-investment-group": "fortress",
    "galaxy-digital": "galaxy", "schmidt-futures": "schmidt", "relativity-space": "relativity",
    "anduril-industries": "anduril", "daniel-driscoll": "driscoll", "robert-jain": "jain",
    "robert-hur": "hur", "daniel-schulman": "schulman", "marie-josee-kravis": "mj_kravis",
    "randall-kroszner": "kroszner", "lawrence-summers": "summers", "jim-o-neill": "oneill",
    "americans-for-tax-reform": "atr", "anti-defamation-league": "adl",
    "charles-koch-foundation": "koch_fdn", "berggruen-institute": "berggruen_inst",
    "marble-freedom-trust": "marble", "renew-democracy-initiative": "renew_dem",
    "new-america-foundation": "new_america", "hudson-institute": "hudson",
    "atlantic-council": "atlantic_council", "council-on-foreign-relations": "cfr",
    "cato-institute": "cato", "king-faisal-foundation": "king_faisal",
}

SECTION_TYPE = {
    "edgar": "sec", "courtlistener": "court", "federal_register": "fedreg",
    "form990": "form990",
}

# curated key events (date, label, kind)
KEY_EVENTS = [
    ["2006-01-01", "Dialog retreat founded (Thiel + Auren Hoffman)", "milestone", "dialog"],
    ["2025-08-01", "'Leading the Future' pro-AI super PAC launches (~$100M)", "milestone", "ltf"],
    ["2025-11-12", "House Oversight releases Epstein-estate documents (names incl. Summers)", "legal", "summers"],
    ["2026-02-26", "Tom Goldstein convicted (tax evasion / mortgage fraud), D. Md.", "legal", "goldstein"],
    ["2026-02-24", "DCG securities class action — motion to dismiss denied (D. Conn.)", "legal", "dcg"],
    ["2025-03-27", "Galaxy Digital / NY AG settlement re: Terra-LUNA ($200M)", "legal", "galaxy"],
    ["2026-05-18", "Musk v. Altman/OpenAI — defense verdict (N.D. Cal.)", "legal", "openai"],
    ["2026-06-15", "Dialog data leak exposed (maia arson crimew; WIRED-verified)", "leak", "dialog"],
    ["2026-06-24", "Alex Bores loses NY-12 primary (LTF's first target)", "milestone", "ltf"],
    ["2026-08-12", "Dialog 2026 retreat scheduled (Dublin, Ireland)", "milestone", "dialog"],
    ["2025-11-13", "Larry Summers steps back from public roles (incl. OpenAI board)", "milestone", "summers"],
]


def match_node(slug: str, node_ids: set, label_to_id: dict) -> str | None:
    if slug in ALIAS and ALIAS[slug] in node_ids:
        return ALIAS[slug]
    n = norm(slug)
    if n in label_to_id:
        return label_to_id[n]
    # try id direct
    for nid in node_ids:
        if norm(nid) == n:
            return nid
    return None


def main():
    net = json.loads((ROOT / "network.json").read_text())
    nodes, edges = net["nodes"], net["edges"]
    node_ids = {x["id"] for x in nodes}
    label_to_id = {norm(x["label"]): x["id"] for x in nodes}
    label_to_id.update({norm(x["id"]): x["id"] for x in nodes})

    # degree
    deg = defaultdict(int)
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    for x in nodes:
        x["degree"] = deg.get(x["id"], 0)
        if x["id"] in GEO:
            g = GEO[x["id"]]
            x["lat"], x["lon"], x["city"], x["country"] = g

    # people inherit geo from a primary org edge
    geoed = {x["id"] for x in nodes if "lat" in x}
    prio = ("found", "ceo", "chair", "partner", "co-found", "director", "president", "exec")
    by_id = {x["id"]: x for x in nodes}
    for x in nodes:
        if x["type"] != "person" or "lat" in x:
            continue
        cand = None
        for e in edges:
            if e["source"] == x["id"] and e["target"] in geoed:
                rel = e["relation"].lower()
                if any(p in rel for p in prio):
                    cand = e["target"]; break
                cand = cand or e["target"]
        if cand:
            g = by_id[cand]
            x["lat"], x["lon"], x["city"], x["country"] = g["lat"], g["lon"], g["city"], g["country"]
            x["geo_via"] = cand

    # events from raw bundles
    events = []
    for base, kinds in ((ROOT / "entities", ("edgar", "courtlistener", "federal_register")),
                        (ROOT / "foundations", ("form990", "courtlistener"))):
        for d in sorted(base.iterdir()) if base.exists() else []:
            slug = d.name
            man = d / "manifest.json"
            label = slug
            if man.exists():
                label = json.loads(man.read_text()).get("query", slug)
            nid = match_node(slug, node_ids, label_to_id)
            for kind in kinds:
                f = d / "raw" / f"{kind}.json"
                if not f.exists():
                    continue
                try:
                    items = json.loads(f.read_text())
                except Exception:
                    continue
                for it in items:
                    dt = (it.get("date") or "").strip()
                    if not dt:
                        continue
                    if len(dt) == 4 and dt.isdigit():
                        dt = f"{dt}-12-31"
                    if not re.match(r"\d{4}-\d{2}-\d{2}", dt):
                        continue
                    ev = {
                        "date": dt[:10], "year": int(dt[:4]),
                        "type": SECTION_TYPE.get(kind, kind),
                        "entity": slug, "label": label, "nodeId": nid,
                        "title": (it.get("title") or "")[:200],
                        "url": it.get("url") or "",
                    }
                    if kind == "form990":
                        ev["value"] = it.get("totrevenue")
                        ev["assets"] = it.get("totassetsend")
                    events.append(ev)
    for dt, title, kind, nid in KEY_EVENTS:
        events.append({"date": dt, "year": int(dt[:4]), "type": "key:" + kind,
                       "entity": nid, "label": title, "nodeId": nid if nid in node_ids else None,
                       "title": title, "url": ""})

    events.sort(key=lambda e: e["date"])
    geo_nodes = [x for x in nodes if "lat" in x]

    meta = {
        "title": "Dialog network — investigative explorer",
        "n_nodes": len(nodes), "n_edges": len(edges), "n_events": len(events),
        "n_geo": len(geo_nodes),
        "date_min": events[0]["date"] if events else None,
        "date_max": events[-1]["date"] if events else None,
        "node_types": sorted({x["type"] for x in nodes}),
        "event_types": sorted({e["type"] for e in events}),
        "generated_note": "public-record data only; roster presence != membership or wrongdoing",
    }

    payload = {"nodes": nodes, "edges": edges, "events": events, "meta": meta}
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (OUT / "data.js").write_text("window.DIALOG = " + json.dumps(payload) + ";\n", encoding="utf-8")
    for k, v in payload.items():
        (DATA / f"{k}.json").write_text(json.dumps(v, indent=1), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print(f"geo nodes: {len(geo_nodes)} / {len(nodes)}")
    sz = (OUT / "data.js").stat().st_size
    print(f"data.js: {sz/1024:.0f} KB")


if __name__ == "__main__":
    main()
