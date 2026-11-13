"""
gdelt_gkg.py — GDELT Global Knowledge Graph queries, watch-area-driven.

Where gdelt_regions.py pulls "top N stories per source-country," this
section runs the actual GKG taxonomy: for every configured watch area
with `themes` or `keywords`, query GDELT DOC 2.0 in artlist mode with
that filter and merge results. We also support GDELT's `near` operator
for proximity queries (lat,lon,km).

Theme reference:
  https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/
  GKG 2.0 themes: https://gdelt.github.io/

Each result is tagged with the matching watch area, plus the extracted
themes / entities / tone / source / domain so downstream tagging can
re-match against more granular watch areas.

This is rate-limited from CI; same retry-with-backoff pattern as
gdelt_regions.py. Watch areas are queried in priority order so the
budget runs out on low-priority areas first.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from ..lib import gdelt, gdelt_files
from ..lib.watchareas import WatchArea, keyword_regex, load_watch_areas
from . import Section, UpstreamHTTPError, UpstreamSchemaError



class GdeltGkgSection(Section):
    id = "gdelt_gkg"
    title = "GDELT GKG — themes / entities / watch areas"
    emoji = "📡"

    PULL_TIMEOUT_S = 180
    PER_QUERY = 25
    FILE_PER_SUBSET = 10     # per area × (themes | keywords | near) on the bulk path
    HOURS_BACK = 36
    # Wall-clock budget for the whole pull: stop issuing queries with margin to
    # spare so we return the partial result we have instead of being killed by
    # the PULL_TIMEOUT_S deadline (GDELT 429s used to make this loop overrun).
    BUDGET_S = 150

    def _fetch(self, params: dict, deadline: float = float("inf")) -> dict | None:
        """One DOC query through the shared GDELT spacer; None on failure."""
        try:
            return gdelt.doc_get(params, timeout=12, deadline=deadline)
        except gdelt.GdeltUnavailable as exc:
            self.last_error = str(exc)
            return None

    def _build_queries(self, area: WatchArea) -> list[tuple[str, str]]:
        """Return (query_string, sub_label) tuples for an area."""
        queries: list[tuple[str, str]] = []
        # Theme query: theme:THEME_NAME with OR
        if area.themes:
            theme_clause = " OR ".join(f"theme:{t.upper()}" for t in area.themes if t)
            if theme_clause:
                queries.append((f"({theme_clause})", "themes"))
        # Keyword query: fielded text search
        if area.keywords:
            # Pull top ~6 most-specific keywords (longest are usually most distinctive)
            kws = sorted([k for k in area.keywords if k], key=len, reverse=True)[:6]
            kw_clause = " OR ".join(f'"{k}"' for k in kws)
            if kw_clause:
                queries.append((f"({kw_clause}) sourcelang:english", "keywords"))
        # bbox/near: GDELT supports near:LAT,LON,KM
        if area.bbox and len(area.bbox) == 4:
            w, s, e, n = area.bbox
            lat = (s + n) / 2.0
            lon = (w + e) / 2.0
            # Diagonal radius (rough): 1° ≈ 111km; pick the larger half-extent
            km = max(abs(n - s), abs(e - w)) * 111 / 2.0
            km = min(int(km), 2500)  # GDELT caps near
            queries.append((f"near:{lat:.2f},{lon:.2f},{km}", "near"))
        return queries

    def pull(self) -> list[dict]:
        # Bulk GKG files first: no rate limit, and the watch-area matching
        # (themes, headline keywords, bbox) runs locally on ~20k articles.
        try:
            batch = gdelt_files.recent_articles()
        except gdelt_files.GdeltFilesUnavailable:
            return self._pull_doc()
        return self._pull_files(batch)

    def _pull_files(self, batch: "gdelt_files.Batch") -> list[dict]:
        areas = load_watch_areas()
        prio_rank = {"high": 0, "normal": 1, "low": 2}
        areas.sort(key=lambda a: prio_rank.get(a.priority, 1))
        out: list[dict] = []
        seen_urls: set[str] = set()
        for area in areas:
            subsets = []
            themes = frozenset(t.upper() for t in area.themes if t)
            in_box = None
            if area.bbox and len(area.bbox) == 4:
                w, s_, e, n = area.bbox
                # Location tags are per mention, so the share inside the box
                # separates stories set there from passing mentions.
                in_box = lambda a, w=w, s_=s_, e=e, n=n: bool(a.points) and sum(
                    s_ <= la <= n and w <= lo <= e for la, lo in a.points
                ) * 3 >= len(a.points)
            if themes:
                # One broad GKG theme tag is a weak signal (ENV_OIL_GAS lands
                # on celebrity pieces); with 2+ area themes, require 2. Themes
                # like TERROR are global, so an area with a bbox also needs a
                # place inside it.
                need = min(2, len(themes))
                subsets.append(("themes", [a for a in batch.articles
                                           if len(a.themes & themes) >= need
                                           and (in_box is None or in_box(a))]))
            kw_re = keyword_regex(area.keywords)
            if kw_re is not None:
                subsets.append(("keywords", [a for a in batch.articles
                                             if kw_re.search(a.title)]))
            if in_box is not None:
                subsets.append(("near", [a for a in batch.articles if in_box(a)]))
            for sub, cands in subsets:
                cands = [a for a in cands if a.url not in seen_urls]
                for art, echo in gdelt_files.top_stories(cands, self.FILE_PER_SUBSET):
                    seen_urls.add(art.url)
                    out.append({
                        "id": f"gkg-{art.seendate}-{hash(art.url) & 0xFFFFFFFF:x}",
                        "date": art.date,
                        "title": f"[{area.name} · {sub}] {art.title}",
                        "url": art.url,
                        "summary": (f"{art.domain} · English · tone "
                                    f"{art.tone if art.tone is not None else 'NA'}"
                                    + (f" · {echo} other outlets" if echo else "")),
                        "country": art.country_name,
                        "domain": art.domain,
                        "tone": art.tone,
                        "language": "English",
                        "themes": area.themes,
                        "topics": area.topics,
                        "watch_areas": [area.name],
                        "_source": self.id,
                    })
        if not out and len(batch.articles) >= 1000 and areas:
            raise UpstreamSchemaError(
                f"{len(batch.articles)} GKG articles matched none of {len(areas)} watch "
                "areas; has the GKG layout changed?")
        out.sort(key=lambda it: it.get("date", ""), reverse=True)
        return out

    def _pull_doc(self) -> list[dict]:
        areas = load_watch_areas()
        # Priority order: high first
        prio_rank = {"high": 0, "normal": 1, "low": 2}
        areas.sort(key=lambda a: prio_rank.get(a.priority, 1))
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=self.HOURS_BACK)
        sdt = start.strftime("%Y%m%d%H%M%S")
        edt = end.strftime("%Y%m%d%H%M%S")
        out: list[dict] = []
        seen_urls: set[str] = set()
        got_any = False
        self.last_error = ""
        deadline = time.monotonic() + self.BUDGET_S
        for area in areas:
            if time.monotonic() > deadline:
                break  # budget spent — return what we have, don't overrun
            for qstr, sub in self._build_queries(area):
                if time.monotonic() > deadline:
                    break
                params = {
                    "query": qstr,
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": self.PER_QUERY,
                    "startdatetime": sdt,
                    "enddatetime": edt,
                    "sort": "datedesc",
                }
                data = self._fetch(params, deadline)
                if not data:
                    continue
                got_any = True
                for art in (data.get("articles") or []):
                    url = art.get("url") or ""
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    seen = art.get("seendate", "")
                    try:
                        dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                    except ValueError:
                        dt = None
                    tone_raw = art.get("tone", "")
                    try:
                        tone = float(tone_raw) if tone_raw not in ("", None) else None
                    except ValueError:
                        tone = None
                    out.append({
                        "id": f"gkg-{seen}-{hash(url) & 0xFFFFFFFF:x}",
                        "date": dt.date().isoformat() if dt else "",
                        "title": f"[{area.name} · {sub}] {art.get('title','(no title)')}",
                        "url": url,
                        "summary": f"{art.get('domain','')} · {art.get('language','')} · tone {tone if tone is not None else 'NA'}",
                        "country": art.get("sourcecountry", ""),
                        "domain": art.get("domain", ""),
                        "tone": tone,
                        "language": art.get("language", ""),
                        "themes": area.themes,
                        "topics": area.topics,
                        "watch_areas": [area.name],
                        "_source": self.id,
                    })
        out.sort(key=lambda it: it.get("date", ""), reverse=True)
        if not out and not got_any:
            raise UpstreamHTTPError(
                f"GDELT returned nothing for any query ({self.last_error or 'rate-limited / blocked'})")
        return out
