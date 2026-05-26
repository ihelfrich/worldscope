"""
conflict.py — GDELT-derived conflict & unrest tracker.

Filters the GDELT 2.0 DOC API to articles tagged with conflict-related GKG
themes (armed conflict, protest, kill, terror). Returns the most recent
articles across the global stream, last 48 hours, English-language.

This is the GDELT-side answer to ACLED for the daily briefing — same
phenomena (armed conflict, civil unrest, mass-casualty events) tracked by
the world's most comprehensive open news monitor. No auth required.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from . import Section

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# GDELT theme codes that mark conflict-related coverage. The query OR-combines
# these against the GDELT GKG (Global Knowledge Graph) theme field.
CONFLICT_THEMES = [
    "ARMEDCONFLICT", "KILL", "TERROR",
    "PROTEST", "CRISISLEX_C03_WELLBEING_HEALTH",
]


class ConflictSection(Section):
    id = "conflict"
    title = "Conflict & unrest (GDELT, last 48h)"
    emoji = "⚔️"

    LIMIT = 40
    HOURS = 48

    def pull(self) -> list[dict]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=self.HOURS)
        # Build the theme-OR query
        theme_clause = " OR ".join(f"theme:{t}" for t in CONFLICT_THEMES)
        params = {
            "query": f"({theme_clause}) sourcelang:english",
            "mode": "artlist",
            "format": "json",
            "maxrecords": self.LIMIT,
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
            "sort": "datedesc",
        }
        time.sleep(1.0)  # polite throttle
        data = None
        for attempt in range(2):
            try:
                resp = requests.get(DOC_API, params=params,
                                    headers={"User-Agent": UA}, timeout=25)
                if resp.status_code == 429:
                    time.sleep(8)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception:
                if attempt == 0:
                    time.sleep(8)
                else:
                    return []
        if not data:
            return []

        items: list[dict] = []
        for art in (data.get("articles") or [])[: self.LIMIT]:
            seen = art.get("seendate", "")
            try:
                dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                date_iso = dt.date().isoformat()
            except ValueError:
                date_iso = ""
            country = art.get("sourcecountry", "")
            domain = art.get("domain", "")
            lang = art.get("language", "")
            tone = art.get("tone", "")
            title = art.get("title") or "(no title)"
            items.append({
                "id": (art.get("url") or "") + "|" + seen,
                "date": date_iso,
                "title": f"[{country}] {title}",
                "url": art.get("url", ""),
                "summary": f"domain: {domain} · language: {lang} · tone: {tone}",
                "country": country,
                "domain": domain,
                "language": lang,
                "tone": tone,
            })
        return items
