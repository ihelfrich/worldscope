"""
promed.py — ProMED-mail (International Society for Infectious Diseases)
outbreak alerts. The closest the open web gets to real-time
biosurveillance. Posts include human, animal, plant, and zoonotic
outbreaks with location and source citations.

STATUS (verified live 2026-10-09): ProMED has no public feed any more.

  - The WordPress-era RSS this section used,
    https://promedmail.org/promed-posts/?cat=feed, answers 404 from the
    relaunched Next.js site (www.promedmail.org). So do /feed, /rss,
    /promed-posts/feed/, /rss.xml and the WordPress REST route
    /wp-json/wp/v2/posts. No <link rel="alternate" type="application/rss+xml">
    is advertised.
  - The site does expose a Payload CMS REST endpoint,
    https://www.promedmail.org/api/posts, but that collection is the
    organisation's blog ("31 Years Strong", webinars, partnership news),
    not outbreak alerts. robots.txt disallows /api.
  - The outbreak alerts themselves (subject_line, alert_id, issue_date,
    places, diseases, generated_summary) are rendered from an internal tRPC
    client with per-account "unlocked alert" gating — i.e. a subscription
    product with no documented public contract.

Rather than scrape an internal, access-gated API (and risk emitting a
fabricated feed), this section degrades deliberately: pull() raises
FeedRetired and resolve() reports STATE_NO_DATA with an explicit message,
so the run report shows "promed: no public feed" instead of a stale
carry-forward of the last (empty) snapshot.

Re-enabling: if ISID publishes an RSS feed again (or you hold a licensed
feed URL), set PROMED_FEED_URL to it. The RSS parser below is kept intact
and is used whenever that variable is set. Items carry the disease name in
the title; we parse common patterns ("Avian influenza - North America
(12): USA") to surface country.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Optional

import requests

from . import (
    Section, SectionState, SourceUnavailable, UpstreamHTTPError, UpstreamParseError,
    STATE_NO_DATA, STATE_STALE,
)

LEGACY_FEED = "https://promedmail.org/promed-posts/?cat=feed"   # retired; 404
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"

NO_FEED_MESSAGE = (
    "ProMED no longer publishes a public RSS/JSON feed (legacy feed "
    f"{LEGACY_FEED} returns 404; alerts are now behind an account-gated "
    "internal API). Set PROMED_FEED_URL to a licensed feed URL to re-enable."
)


class FeedRetired(SourceUnavailable):
    """The upstream retired its public feed; nothing to pull, by design."""


def feed_url() -> Optional[str]:
    return (os.environ.get("PROMED_FEED_URL") or "").strip() or None


def _parse_pubdate(s: str) -> str:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_feed(content: bytes, source_id: str = "promed") -> list[dict]:
    """Parse a ProMED-style RSS body into section items."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise UpstreamParseError(f"ProMED feed is not valid XML: {e}") from e
    items: list[dict] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        date_str = _parse_pubdate(pub)
        # Title pattern: "Disease - Region (NN): Country, subloc"
        country = ""
        disease = title
        m = re.match(r"^(.+?)\s*-\s*(.+?)(?:\s*\(\d+\))?:\s*(.+)$", title)
        if m:
            disease = m.group(1).strip()
            country = m.group(3).strip()
        # Clean description HTML
        desc_clean = re.sub(r"<[^>]+>", "", desc)[:280]
        items.append({
            "id": f"promed-{hash(link) & 0xFFFFFFFF:x}",
            "date": date_str,
            "title": title,
            "url": link,
            "summary": desc_clean,
            "country": country,
            "disease": disease,
            "topics": ["health", "biosecurity"],
            "_source": source_id,
        })
    return items


class PromedSection(Section):
    id = "promed"
    title = "ProMED-mail outbreak feed"
    emoji = "🦠"

    PULL_TIMEOUT_S = 45

    def pull(self) -> list[dict]:
        url = feed_url()
        if not url:
            raise FeedRetired(NO_FEED_MESSAGE)
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"ProMED feed request failed: {e}") from e
        return parse_feed(resp.content, self.id)

    def resolve(self, *, today: Optional[date] = None) -> SectionState:
        """A retired feed is reported as no_data with the explanation, not as a
        stale carry-forward of whatever the last successful pull was."""
        state = super().resolve(today=today)
        if state.state == STATE_STALE and state.error_type == FeedRetired.__name__:
            state.state = STATE_NO_DATA
            state.items = []
            state.new = []
            state.comparison_date = None
            state.source_date = None
        return state
