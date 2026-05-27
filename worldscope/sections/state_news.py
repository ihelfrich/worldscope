"""
state_news — daily news coverage for every U.S. state.

Three RSS feeds per state (typically: largest independent newsroom +
state-AP wire + governor's office). About 150 feeds total. Free; no
API keys; no auth.

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

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


# Per-state feed registry. (state_name, feed_url, source_label, tier)
# Tier choices follow the contract source-tier enum:
#   mainstream_independent — editorially independent statewide newsroom
#   primary_document       — government press feed (governor's office)
#   mainstream_partisan_*  — known partisan slant
# This is curated for COVERAGE not partisan balance. Where state-specific
# independent newsrooms exist (CalMatters, Texas Tribune, NJ Spotlight,
# ProPublica's local newsrooms, etc.) we prefer them over national outlets.
FEEDS: list[tuple[str, str, str, str]] = [
    # ---- A ----
    ("Alabama", "https://www.alreporter.com/feed/", "Alabama Political Reporter", "mainstream_independent"),
    ("Alabama", "https://governor.alabama.gov/feed/", "AL Governor", "primary_document"),
    ("Alaska", "https://alaskabeacon.com/feed/", "Alaska Beacon", "mainstream_independent"),
    ("Alaska", "https://gov.alaska.gov/feed/", "AK Governor", "primary_document"),
    ("Arizona", "https://azmirror.com/feed/", "Arizona Mirror", "mainstream_independent"),
    ("Arizona", "https://azgovernor.gov/feed", "AZ Governor", "primary_document"),
    ("Arkansas", "https://arkansasadvocate.com/feed/", "Arkansas Advocate", "mainstream_independent"),
    ("Arkansas", "https://governor.arkansas.gov/feed/", "AR Governor", "primary_document"),

    # ---- C ----
    ("California", "https://calmatters.org/feed/", "CalMatters", "mainstream_independent"),
    ("California", "https://www.gov.ca.gov/feed/", "CA Governor", "primary_document"),
    ("Colorado", "https://coloradonewsline.com/feed/", "Colorado Newsline", "mainstream_independent"),
    ("Colorado", "https://www.colorado.gov/governor/news/feed", "CO Governor", "primary_document"),
    ("Connecticut", "https://ctmirror.org/feed/", "CT Mirror", "mainstream_independent"),
    ("Connecticut", "https://portal.ct.gov/Office-of-the-Governor/News/Press-Releases?rss=1", "CT Governor", "primary_document"),

    # ---- D ----
    ("Delaware", "https://spotlightdelaware.org/feed/", "Spotlight Delaware", "mainstream_independent"),
    ("District of Columbia", "https://dcist.com/rss", "DCist", "mainstream_independent"),

    # ---- F-G ----
    ("Florida", "https://floridaphoenix.com/feed/", "Florida Phoenix", "mainstream_independent"),
    ("Florida", "https://www.flgov.com/feed/", "FL Governor", "primary_document"),
    ("Georgia", "https://georgiarecorder.com/feed/", "Georgia Recorder", "mainstream_independent"),
    ("Georgia", "https://gov.georgia.gov/press-releases/rss.xml", "GA Governor", "primary_document"),

    # ---- H-I ----
    ("Hawaii", "https://www.civilbeat.org/feed/", "Honolulu Civil Beat", "mainstream_independent"),
    ("Hawaii", "https://governor.hawaii.gov/feed/", "HI Governor", "primary_document"),
    ("Idaho", "https://idahocapitalsun.com/feed/", "Idaho Capital Sun", "mainstream_independent"),
    ("Illinois", "https://capitolnewsillinois.com/feed", "Capitol News Illinois", "mainstream_independent"),
    ("Illinois", "https://www.illinois.gov/news.rss", "IL Press Releases", "primary_document"),
    ("Indiana", "https://indianacapitalchronicle.com/feed/", "Indiana Capital Chronicle", "mainstream_independent"),
    ("Iowa", "https://iowacapitaldispatch.com/feed/", "Iowa Capital Dispatch", "mainstream_independent"),

    # ---- K-L ----
    ("Kansas", "https://kansasreflector.com/feed/", "Kansas Reflector", "mainstream_independent"),
    ("Kentucky", "https://kentuckylantern.com/feed/", "Kentucky Lantern", "mainstream_independent"),
    ("Louisiana", "https://lailluminator.com/feed/", "Louisiana Illuminator", "mainstream_independent"),

    # ---- M ----
    ("Maine", "https://www.pressherald.com/feed/", "Portland Press Herald", "mainstream_independent"),
    ("Maryland", "https://www.marylandmatters.org/feed/", "Maryland Matters", "mainstream_independent"),
    ("Maryland", "https://governor.maryland.gov/news/feed", "MD Governor", "primary_document"),
    ("Massachusetts", "https://commonwealthbeacon.org/feed/", "CommonWealth Beacon", "mainstream_independent"),
    ("Michigan", "https://michiganadvance.com/feed/", "Michigan Advance", "mainstream_independent"),
    ("Michigan", "https://www.michigan.gov/whitmer/news.rss", "MI Governor", "primary_document"),
    ("Minnesota", "https://minnesotareformer.com/feed/", "Minnesota Reformer", "mainstream_independent"),
    ("Mississippi", "https://mississippitoday.org/feed/", "Mississippi Today", "mainstream_independent"),
    ("Missouri", "https://missouriindependent.com/feed/", "Missouri Independent", "mainstream_independent"),
    ("Missouri", "https://governor.mo.gov/feed", "MO Governor", "primary_document"),
    ("Montana", "https://dailymontanan.com/feed/", "Daily Montanan", "mainstream_independent"),

    # ---- N ----
    ("Nebraska", "https://nebraskaexaminer.com/feed/", "Nebraska Examiner", "mainstream_independent"),
    ("Nevada", "https://nevadacurrent.com/feed/", "Nevada Current", "mainstream_independent"),
    ("New Hampshire", "https://newhampshirebulletin.com/feed/", "New Hampshire Bulletin", "mainstream_independent"),
    ("New Jersey", "https://www.njspotlightnews.org/feed/", "NJ Spotlight News", "mainstream_independent"),
    ("New Mexico", "https://sourcenm.com/feed/", "Source New Mexico", "mainstream_independent"),
    ("New York", "https://nysfocus.com/feed", "New York Focus", "mainstream_independent"),
    ("New York", "https://www.governor.ny.gov/feed", "NY Governor", "primary_document"),
    ("North Carolina", "https://ncnewsline.com/feed/", "NC Newsline", "mainstream_independent"),
    ("North Dakota", "https://northdakotamonitor.com/feed/", "North Dakota Monitor", "mainstream_independent"),

    # ---- O ----
    ("Ohio", "https://ohiocapitaljournal.com/feed/", "Ohio Capital Journal", "mainstream_independent"),
    ("Oklahoma", "https://oklahomavoice.com/feed/", "Oklahoma Voice", "mainstream_independent"),
    ("Oregon", "https://oregoncapitalchronicle.com/feed/", "Oregon Capital Chronicle", "mainstream_independent"),

    # ---- P-R ----
    ("Pennsylvania", "https://penncapital-star.com/feed/", "Pennsylvania Capital-Star", "mainstream_independent"),
    ("Rhode Island", "https://rhodeislandcurrent.com/feed/", "Rhode Island Current", "mainstream_independent"),

    # ---- S ----
    ("South Carolina", "https://scdailygazette.com/feed/", "SC Daily Gazette", "mainstream_independent"),
    ("South Dakota", "https://southdakotasearchlight.com/feed/", "South Dakota Searchlight", "mainstream_independent"),

    # ---- T ----
    ("Tennessee", "https://tennesseelookout.com/feed/", "Tennessee Lookout", "mainstream_independent"),
    ("Texas", "https://www.texastribune.org/feeds/news/", "Texas Tribune", "mainstream_independent"),
    ("Texas", "https://gov.texas.gov/news/feed", "TX Governor", "primary_document"),

    # ---- U-W ----
    ("Utah", "https://www.deseret.com/utah/rss", "Deseret News (UT)", "mainstream_independent"),
    ("Vermont", "https://vtdigger.org/feed/", "VTDigger", "mainstream_independent"),
    ("Virginia", "https://www.virginiamercury.com/feed/", "Virginia Mercury", "mainstream_independent"),
    ("Washington", "https://washingtonstatestandard.com/feed/", "Washington State Standard", "mainstream_independent"),
    ("West Virginia", "https://westvirginiawatch.com/feed/", "West Virginia Watch", "mainstream_independent"),
    ("Wisconsin", "https://wisconsinexaminer.com/feed/", "Wisconsin Examiner", "mainstream_independent"),
    ("Wyoming", "https://wyofile.com/feed/", "WyoFile", "mainstream_independent"),
]


def _parse_rss(xml_bytes: bytes) -> list[dict]:
    """Minimal RSS / Atom parser. We use stdlib only to avoid adding feedparser
    as a hard dependency (and feedparser has been flaky on some feeds)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items: list[dict] = []
    # RSS 2.0: channel/item; Atom: feed/entry
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = ""
        link = ""
        pub = ""
        desc = ""
        for child in item:
            ctag = child.tag.split("}")[-1]
            if ctag == "title":
                title = (child.text or "").strip()
            elif ctag == "link":
                link = (child.attrib.get("href") or child.text or "").strip()
            elif ctag in ("pubDate", "published", "updated"):
                pub = (child.text or "").strip()
            elif ctag in ("description", "summary", "content"):
                desc = (child.text or "").strip()
        if not title:
            continue
        # Normalize pub date
        try:
            dt = parsedate_to_datetime(pub) if pub else None
            iso_date = dt.date().isoformat() if dt else ""
        except (TypeError, ValueError):
            iso_date = pub[:10] if pub else ""
        items.append({
            "title": title[:500],
            "url": link,
            "date": iso_date,
            "summary": desc[:600],
        })
    return items


class StateNewsSection(Section):
    id = "state_news"
    title = "State-Level News"
    emoji = "🗽"

    source_id = "state-news-aggregate"
    source_name = "U.S. state news aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mainstream_independent"   # individual records carry their own
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "Per-feed attribution preserved in raw.jsonl. Headlines and excerpts "
        "≤150 characters used under fair use; full content remains the property "
        "of each source."
    )
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 240
    LOOKBACK_DAYS = 2

    # Fetch in parallel — 150 feeds × ~1s each would exceed PULL_TIMEOUT_S
    # serially, so we widen the I/O.
    MAX_WORKERS = 12

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        items: list[dict] = []

        def fetch(state: str, url: str, source_label: str, tier: str) -> list[dict]:
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                # Per-feed failures should not propagate.
                return [{
                    "id": f"state-news-error-{_slug(state)}-{_slug(source_label)}",
                    "date": date.today().isoformat(),
                    "title": f"[feed error] {source_label} ({state}): {type(exc).__name__}",
                    "url": url,
                    "summary": str(exc)[:300],
                    "state": state,
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
                    f"{state}|{source_label}|{it.get('url','')}|{it.get('title','')}".encode()
                ).hexdigest()
                it["id"] = item_id
                it["state"] = state
                it["source_label"] = source_label
                it["source_tier"] = tier
                # Prefix title with state for at-a-glance readability
                it["title"] = f"[{state}] {it['title']}"[:300]
                out.append(it)
            return out

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = [
                pool.submit(fetch, st, url, label, tier)
                for (st, url, label, tier) in FEEDS
            ]
            for fut in as_completed(futures):
                items.extend(fut.result())

        return items

    # ----- Contract: per-record source tier ---------------------------------

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        """Override to set per-record license and tier based on the upstream
        feed, rather than the section-level defaults."""
        record = super().to_raw_record(item, today_iso=today_iso)
        # state_news's section-level source is the aggregate. Each record's
        # actual originating source is the per-feed source_label, so we
        # override source_id at the record level.
        record["source_id"] = f"state-news:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    # ----- Contract: entity extraction --------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"):
            return []
        entities: list[dict] = []
        st = item.get("state", "")
        if st:
            entities.append({
                "id": f"place:state-{_slug(st)}",
                "type": "place",
                "canonical_name": st,
                "metadata": {"kind": "us-state"},
            })
        if item.get("source_label"):
            entities.append({
                "id": f"org:newsroom-{_slug(item['source_label'])}",
                "type": "org",
                "canonical_name": item["source_label"],
                "metadata": {"kind": "newsroom", "state": st, "tier": item.get("source_tier")},
            })
        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships: list[dict] = []
        feed_errors: list[dict] = []

        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e

            st = item.get("state", "")
            label = item.get("source_label", "")
            if st and label:
                relationships.append({
                    "from": f"org:newsroom-{_slug(label)}",
                    "to": f"place:state-{_slug(st)}",
                    "type": "reports-on",
                    "weight": 1.0,
                    "evidence": [item.get("_id") or self._item_id(item)],
                })

        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships

        # Surface feed failures as anomalies so we notice silent breakage
        for err in feed_errors:
            base["anomalies"].append({
                "category": "feed-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })
        return base
