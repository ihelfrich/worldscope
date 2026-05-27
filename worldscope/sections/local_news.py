"""
local_news — hyperlocal coverage for St. Louis, MO and Atlanta, GA.

12-15 feeds per city covering:
    - The major daily paper
    - Public radio + TV affiliates
    - Independent newsrooms (Axios, Patch, Civic Circle, etc.)
    - City government press feed (mayor + council where available)
    - County government feeds

Section-adapter contract: conforms.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from . import Section, SectionState
from .state_news import _parse_rss   # reuse the stdlib RSS parser

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


# (city, feed_url, source_label, tier)
#
# URL audit 2026-05-27: 8 of 16 original feeds were 403/404. Fixes applied:
#   - STLPR: rss.xml -> /index.rss (verified 200, RSS)
#   - St. Louis Post-Dispatch: /feeds/news/rss/ -> /search/?f=rss (TownNews
#     standard query form; verified 200, RSS)
#   - STLPR news (news.stlpublicradio.org): host retired; remove (covered by
#     STLPR /index.rss)
#   - Riverfront Times: now behind Cloudflare 403 (no public RSS); replaced
#     with Google News site-search feed
#   - WABE: /feed/ -> /news/feed/ (verified 200, RSS)
#   - FOX 5 Atlanta: /rss -> /rss.xml (verified 200, RSS)
#   - CBS46 Atlanta: rebranded to Atlanta News First. No RSS; replaced with
#     Google News site-search feed for atlantanewsfirst.com.
#   - AJC arc/outboundfeeds RSS retired (404). Replaced with Google News
#     site-search feed for ajc.com.
#   - First Alert 4 (KMOV) returns HTML at the ?clienttype=rss endpoint
#     (Gray TV no longer serves RSS); replaced with Google News site-search
#     for firstalert4.com.
FEEDS: list[tuple[str, str, str, str]] = [
    # ---- St. Louis, MO ----
    ("St. Louis", "https://www.stlpr.org/news.rss",                                                          "St. Louis Public Radio",     "mainstream_independent"),  # verified 2026-05-27 (index.rss is empty; news.rss has items)
    ("St. Louis", "https://www.stltoday.com/search/?f=rss",                                                  "St. Louis Post-Dispatch",    "mainstream_independent"),  # verified 2026-05-27
    ("St. Louis", "https://news.google.com/rss/search?q=site%3Ariverfronttimes.com&hl=en-US&gl=US&ceid=US:en", "Riverfront Times (via Google News)", "aggregator"),       # RFT direct RSS behind Cloudflare; using Google News proxy 2026-05-27
    ("St. Louis", "https://nextstl.com/feed/",                                                               "NextSTL",                    "mainstream_independent"),
    ("St. Louis", "https://www.fox2now.com/feed/",                                                           "FOX 2 St. Louis",            "mainstream_independent"),
    ("St. Louis", "https://www.ksdk.com/feeds/syndication/rss/news",                                         "KSDK 5 On Your Side",        "mainstream_independent"),
    ("St. Louis", "https://www.stlmag.com/feed/",                                                            "St. Louis Magazine",         "mainstream_independent"),  # added 2026-05-27 to replace retired STLPR-news host
    ("St. Louis", "https://news.google.com/rss/search?q=site%3Afirstalert4.com&hl=en-US&gl=US&ceid=US:en",    "First Alert 4 (KMOV) (via Google News)", "aggregator"),   # KMOV no longer serves RSS; using Google News proxy 2026-05-27

    # ---- Atlanta, GA ----
    ("Atlanta", "https://news.google.com/rss/search?q=site%3Aajc.com&hl=en-US&gl=US&ceid=US:en",              "Atlanta Journal-Constitution (via Google News)", "aggregator"),  # AJC retired RSS; using Google News proxy 2026-05-27
    ("Atlanta", "https://www.wabe.org/news/feed/",                                                           "WABE 90.1 (NPR)",            "mainstream_independent"),  # verified 2026-05-27
    ("Atlanta", "https://www.gpb.org/news/rss.xml",                                                          "Georgia Public Broadcasting","mainstream_independent"),
    ("Atlanta", "https://www.11alive.com/feeds/syndication/rss/news",                                        "11Alive (WXIA)",             "mainstream_independent"),
    ("Atlanta", "https://www.fox5atlanta.com/rss.xml",                                                       "FOX 5 Atlanta",              "mainstream_independent"),  # verified 2026-05-27
    ("Atlanta", "https://atlantaciviccircle.org/feed/",                                                      "Atlanta Civic Circle",       "mainstream_independent"),
    ("Atlanta", "https://news.google.com/rss/search?q=site%3Aatlantanewsfirst.com&hl=en-US&gl=US&ceid=US:en", "Atlanta News First (formerly CBS46) (via Google News)", "aggregator"),  # ANF (rebrand of CBS46) has no RSS; using Google News proxy 2026-05-27
    ("Atlanta", "https://www.atlantamagazine.com/feed/",                                                     "Atlanta Magazine",           "mainstream_independent"),
    ("Atlanta", "https://saportareport.com/feed/",                                                           "SaportaReport",              "mainstream_independent"),  # added 2026-05-27 as ATL independent civic newsroom
]


class LocalNewsSection(Section):
    id = "local_news"
    title = "Local News: St. Louis + Atlanta"
    emoji = "🏙️"

    source_id = "local-news-aggregate"
    source_name = "Hyperlocal news aggregate (STL + ATL)"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mainstream_independent"
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "Per-feed attribution preserved in raw.jsonl. Headlines and excerpts "
        "≤150 characters used under fair use; full content remains the property "
        "of each source."
    )
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 120
    LOOKBACK_DAYS = 2
    MAX_WORKERS = 8

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        items: list[dict] = []

        def fetch(city: str, url: str, source_label: str, tier: str) -> list[dict]:
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                return [{
                    "id": f"local-news-error-{_slug(city)}-{_slug(source_label)}",
                    "date": date.today().isoformat(),
                    "title": f"[feed error] {source_label} ({city}): {type(exc).__name__}",
                    "url": url,
                    "summary": str(exc)[:300],
                    "city": city,
                    "source_label": source_label,
                    "source_tier": tier,
                    "_error": True,
                }]
            feed_items = _parse_rss(resp.content)
            out = []
            for it in feed_items:
                try:
                    item_date = date.fromisoformat(it.get("date", "")[:10])
                except ValueError:
                    item_date = date.today()
                if item_date < cutoff:
                    continue
                item_id = hashlib.sha1(
                    f"{city}|{source_label}|{it.get('url','')}|{it.get('title','')}".encode()
                ).hexdigest()
                it["id"] = item_id
                it["city"] = city
                it["source_label"] = source_label
                it["source_tier"] = tier
                it["title"] = f"[{city}] {it['title']}"[:300]
                out.append(it)
            return out

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = [pool.submit(fetch, c, u, lbl, tier) for c, u, lbl, tier in FEEDS]
            for fut in as_completed(futures):
                items.extend(fut.result())

        return items

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"local-news:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"):
            return []
        city = item.get("city", "")
        entities = []
        if city:
            entities.append({
                "id": f"place:city-{_slug(city)}",
                "type": "place",
                "canonical_name": city,
                "metadata": {
                    "kind": "us-city",
                    "state": {"St. Louis": "Missouri", "Atlanta": "Georgia"}.get(city),
                },
            })
        if item.get("source_label"):
            entities.append({
                "id": f"org:newsroom-{_slug(item['source_label'])}",
                "type": "org",
                "canonical_name": item["source_label"],
                "metadata": {"kind": "newsroom", "city": city, "tier": item.get("source_tier")},
            })
        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships = []
        feed_errors = []

        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e
            city = item.get("city", "")
            label = item.get("source_label", "")
            if city and label:
                relationships.append({
                    "from": f"org:newsroom-{_slug(label)}",
                    "to": f"place:city-{_slug(city)}",
                    "type": "reports-on",
                    "weight": 1.0,
                    "evidence": [item.get("_id") or self._item_id(item)],
                })

        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships
        for err in feed_errors:
            base["anomalies"].append({
                "category": "feed-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })
        return base
