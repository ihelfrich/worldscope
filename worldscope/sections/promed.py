"""
promed.py — ProMED-mail (International Society for Infectious Diseases)
RSS feed of unusual disease outbreaks. The closest the open web gets to
real-time biosurveillance. Posts include human, animal, plant, and
zoonotic outbreaks with location and source citations.

Feed: https://promedmail.org/promed-post/?feed=rss2 (RSS)

Items carry the disease name in the title; we parse common patterns
("Avian influenza - North America (12): USA") to surface country.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

# The best remaining candidate, not a working feed. See pull() for what was
# verified on 2026-08-24: every RSS path either 404s or answers 200 with the
# site's HTML shell. Kept pointed here so the failure is specific and the next
# person starts from the last thing tried rather than re-deriving it.
FEED = "https://promedmail.org/promed-post/?feed=rss2"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"


def _parse_pubdate(s: str) -> str:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).date().isoformat()
        except ValueError:
            continue
    return ""


class PromedSection(Section):
    id = "promed"
    title = "ProMED-mail outbreak feed"
    emoji = "🦠"

    PULL_TIMEOUT_S = 45

    def pull(self) -> list[dict]:
        try:
            resp = requests.get(FEED, headers={"User-Agent": UA}, timeout=25)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"ProMED feed request failed: {e}") from e
        # ProMED migrated to a Next.js single-page app. Every RSS path now
        # answers 200 with the HTML shell instead of a feed, which XML-parses
        # into zero <item> elements — so the section reported a world with no
        # outbreak reports in it rather than a relocated feed.
        #
        # Verified 2026-08-24: /promed-posts/?cat=feed 404, /feed/ 404,
        # /promed-post/?feed=rss2 returns 200 text/html. The site's Payload CMS
        # API at /api/posts exists but holds 5 announcement posts, not the
        # outbreak archive; the archive collection was not discoverable
        # unauthenticated. Raising is the honest state until it is found.
        # getattr: the content sniff is the real check, and a Content-Type is
        # advisory anyway. Not depending on it keeps this working against any
        # response-like object.
        ctype = (getattr(resp, "headers", {}) or {}).get("Content-Type", "").lower()
        body_head = resp.content[:200].lstrip()
        if b"<rss" not in body_head and b"<feed" not in body_head:
            raise UpstreamParseError(
                f"ProMED returned {ctype or 'an unknown content type'} rather "
                f"than RSS ({FEED}). The site is now a single-page app and the "
                f"feed has moved; find the current endpoint before trusting "
                f"this section's silence."
            )

        try:
            root = ET.fromstring(resp.content)
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
                "_source": self.id,
            })
        return items
