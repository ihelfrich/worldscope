"""
gdelt_regions.py — top news stories from GDELT, stratified by country.

For each country in the watchlist, pulls the most-recent N articles whose
source country matches (per GDELT's FIPS-ish code). Normalizes the result
to the standard Section schema.

GDELT DOC 2.0 is multilingual and updates every 15 minutes. No API key.
The free tier is rate-limited (~1 call/sec sustained, throttled on bursts).
This section makes one call per country per run; with a 15-country
watchlist that's about 30 seconds. To keep the daily run cheap, we cap to
~8 hits per country.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests

from . import Section

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# (FIPS-ish country code GDELT recognizes, display name).
# China appears twice — once as the party-state outlet view (CH), once paired
# with HK/MO when those happen to surface — for now we just use CH and let
# the editorial framing show through whatever GDELT returns.
WATCHLIST: list[tuple[str, str]] = [
    ("CH", "China"),
    ("JA", "Japan"),
    ("KS", "South Korea"),
    ("UP", "Ukraine"),
    ("GM", "Germany"),
    ("IT", "Italy"),
    ("IS", "Israel"),
    ("IR", "Iran"),
    ("SA", "Saudi Arabia"),
    ("TU", "Turkey"),
    ("NI", "Nigeria"),
    ("EC", "Ecuador"),
    ("CO", "Colombia"),
    ("PO", "Portugal"),
    ("CA", "Canada"),
    ("MX", "Mexico"),
    ("UK", "United Kingdom"),
    ("BR", "Brazil"),
    ("IN", "India"),
]


class GdeltRegionsSection(Section):
    id = "gdelt_regions"
    title = "World News (by country, top stories)"
    emoji = "🌍"

    # Articles per country to keep, and pause between calls so we're polite.
    PER_COUNTRY = 6
    THROTTLE_S = 1.2

    def pull(self) -> list[dict]:
        items: list[dict] = []
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=36)
        for code, name in WATCHLIST:
            params = {
                "query": f"sourcecountry:{code} sourcelang:english",
                "mode": "artlist",
                "format": "json",
                "maxrecords": self.PER_COUNTRY,
                "startdatetime": start.strftime("%Y%m%d%H%M%S"),
                "enddatetime": end.strftime("%Y%m%d%H%M%S"),
                "sort": "datedesc",
            }
            try:
                resp = requests.get(DOC_API, params=params, headers={"User-Agent": UA}, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                # GDELT throttles aggressively; skip this country for this run
                time.sleep(self.THROTTLE_S)
                continue
            for art in (data.get("articles") or [])[: self.PER_COUNTRY]:
                # GDELT returns seendate like "20260525T180000Z"
                seen = art.get("seendate", "")
                try:
                    dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    dt = None
                items.append({
                    "id": art.get("url", "") + "|" + seen,
                    "date": dt.date().isoformat() if dt else "",
                    "title": f"[{name}] {art.get('title','(no title)')}",
                    "url": art.get("url", ""),
                    "summary": art.get("domain", "") + " · " + art.get("language", ""),
                    "country": name,
                    "domain": art.get("domain", ""),
                    "tone": art.get("tone", ""),
                    "language": art.get("language", ""),
                })
            time.sleep(self.THROTTLE_S)
        # Sort newest first for the briefing
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
