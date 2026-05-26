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

import time
from datetime import datetime, timedelta, timezone

import requests

from ..lib.watchareas import WatchArea, load_watch_areas
from . import Section

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


class GdeltGkgSection(Section):
    id = "gdelt_gkg"
    title = "GDELT GKG — themes / entities / watch areas"
    emoji = "📡"

    PULL_TIMEOUT_S = 180
    PER_QUERY = 25
    THROTTLE_S = 2.0
    MAX_RETRIES = 3
    HOURS_BACK = 36

    def _fetch(self, params: dict) -> dict | None:
        backoff = 4.0
        for _ in range(self.MAX_RETRIES):
            try:
                resp = requests.get(DOC_API, params=params, headers={"User-Agent": UA}, timeout=25)
                if resp.status_code == 429:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError:
                time.sleep(backoff)
                backoff *= 2
            except Exception:
                return None
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
        for area in areas:
            for qstr, sub in self._build_queries(area):
                params = {
                    "query": qstr,
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": self.PER_QUERY,
                    "startdatetime": sdt,
                    "enddatetime": edt,
                    "sort": "datedesc",
                }
                data = self._fetch(params)
                time.sleep(self.THROTTLE_S)
                if not data:
                    continue
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
        return out
