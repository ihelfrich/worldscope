"""
people.py — running roster of world leaders + government officials.

Two sub-pulls combined into one section:

  1. Heads of state + government for every sovereign country (Wikidata SPARQL).
     ~214 entries. Refreshed daily (cached 24h client-side).
  2. Top PEPs (politically-exposed persons) for the country watchlist,
     from the local OpenSanctions corpus indexed via worldscope.lib.peps.

Together this is "every world leader and high-ranking official", as
comprehensive as Wikidata + OpenSanctions get (currently 625K+ entries).

The section delta-detects new rosters day-over-day: when a country flips
its head of state, it shows up as "new" against yesterday's snapshot.
"""
from __future__ import annotations

import os
from typing import Optional

from . import Section
from ..lib import peps, wikidata

# Countries to surface in the daily section. The full roster of 214 HoS is in
# the zip's raw/people.json — this list controls what makes the briefing page.
# (display name, ISO-2 used by OpenSanctions corpus).
# Order matters: items appear in the briefing in this order.
DAILY_WATCHLIST: list[tuple[str, str]] = [
    ("United States", "us"),
    ("China",         "cn"),
    ("Russia",        "ru"),
    ("United Kingdom","gb"),
    ("Germany",       "de"),
    ("France",        "fr"),
    ("Italy",         "it"),
    ("Japan",         "jp"),
    ("South Korea",   "kr"),
    ("Ukraine",       "ua"),
    ("Israel",        "il"),
    ("Iran",          "ir"),
    ("Saudi Arabia",  "sa"),
    ("Turkey",        "tr"),
    ("India",         "in"),
    ("Brazil",        "br"),
    ("Mexico",        "mx"),
    ("Canada",        "ca"),
    ("Nigeria",       "ng"),
    ("Portugal",      "pt"),
    ("Colombia",      "co"),
    ("Ecuador",       "ec"),
]


class PeopleSection(Section):
    id = "people"
    title = "World leaders & government officials"
    emoji = "🌐"

    PEPS_PER_COUNTRY = 5

    def pull(self) -> list[dict]:
        items: list[dict] = []

        # --- 1. Heads of state ----------------------------------------------
        try:
            hos = wikidata.current_heads_of_state()
            hog = wikidata.current_heads_of_government()
        except Exception:
            hos, hog = [], []

        hog_by_country = {h["country"]: h for h in hog}
        for h in hos:
            country = h.get("country", "")
            extras = []
            if country in hog_by_country and hog_by_country[country]["leader_name"] != h["leader_name"]:
                extras.append(f"head of govt: {hog_by_country[country]['leader_name']}")
            items.append({
                "id": f"hos:{h.get('country_qid','')}",
                "date": "",
                "title": f"[{country}] head of state: {h['leader_name']}",
                "url": f"https://www.wikidata.org/wiki/{h.get('leader_qid','')}",
                "summary": (f"position: {h.get('position','HoS')}"
                            + ((" · " + " · ".join(extras)) if extras else "")),
                "country": country,
                "kind": "head_of_state",
            })

        # If we got HoG for countries we didn't get HoS for (rare), add them
        seen_countries = {h.get("country") for h in hos}
        for h in hog:
            if h.get("country") in seen_countries:
                continue
            items.append({
                "id": f"hog:{h.get('country_qid','')}",
                "date": "",
                "title": f"[{h.get('country','')}] head of govt: {h['leader_name']}",
                "url": f"https://www.wikidata.org/wiki/{h.get('leader_qid','')}",
                "summary": "head of government (no separate HoS in Wikidata)",
                "country": h.get("country", ""),
                "kind": "head_of_government",
            })

        # --- 2. PEPs by watchlist country (ISO-coded lookup) ----------------
        if peps.is_index_built():
            for display_name, iso2 in DAILY_WATCHLIST:
                pep_rows = peps.by_country(iso2, limit=self.PEPS_PER_COUNTRY)
                for r in pep_rows:
                    items.append({
                        "id": r["id"],
                        "date": r.get("modified", "")[:10],
                        "title": f"[{display_name}] {r['name']}",
                        "url": f"https://www.opensanctions.org/entities/{r['id']}/",
                        "summary": (r.get("position") or "")[:240],
                        "country": display_name,
                        "kind": "official",
                    })
        return items
