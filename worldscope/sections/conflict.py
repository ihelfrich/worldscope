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

from ..lib import gdelt, gdelt_files
from . import Section, UpstreamHTTPError, UpstreamParseError


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
    BUDGET_S = 45  # inside the default 75 s section deadline, incl. a 25 s request
    HOURS = 48

    def pull(self) -> list[dict]:
        # Bulk GKG files first: no rate limit (see lib.gdelt_files). The DOC
        # API is the fallback when no file can be downloaded.
        try:
            batch = gdelt_files.recent_articles()
        except gdelt_files.GdeltFilesUnavailable:
            return self._pull_doc()
        themes = frozenset(CONFLICT_THEMES)
        cands = [a for a in batch.articles if a.themes & themes]
        items = []
        for art, echo in gdelt_files.top_stories(cands, self.LIMIT):
            country = art.country_name
            items.append({
                "id": f"{art.url}|{art.seendate}",
                "date": art.date,
                "title": f"[{country}] {art.title}" if country else art.title,
                "url": art.url,
                "summary": (f"domain: {art.domain} · language: English · tone: "
                            f"{art.tone if art.tone is not None else 'NA'}"
                            + (f" · {echo} other outlets ran this story" if echo else "")),
                "country": country,
                "domain": art.domain,
                "language": "English",
                "tone": art.tone if art.tone is not None else "",
                "themes": sorted(art.themes & themes),
            })
        return items

    def _pull_doc(self) -> list[dict]:
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
        try:
            data = gdelt.doc_get(params, timeout=25,
                                 deadline=time.monotonic() + self.BUDGET_S)
        except gdelt.GdeltUnavailable as exc:
            if "non-JSON" in str(exc):
                raise UpstreamParseError(f"GDELT DOC conflict query: {exc}") from exc
            raise UpstreamHTTPError(f"GDELT DOC conflict query failed: {exc}") from exc

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
