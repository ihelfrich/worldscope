"""
promed.py — ProMED-mail (International Society for Infectious Diseases)
RSS feed of unusual disease outbreaks. The closest the open web gets to
real-time biosurveillance. Posts include human, animal, plant, and
zoonotic outbreaks with location and source citations.

Feed status (2026): ISID permanently closed ProMED's public RSS feed in 2023
to stop unauthorized scraping, and the relaunched site exposes no public
RSS/JSON endpoint — the legacy path (``/promed-posts/?cat=feed``) now 404s.
There is no drop-in public replacement. This adapter reads whatever URL is set
in PROMED_FEED_URL (defaulting to the legacy path) and parses it as RSS, so a
licensed ProMED/samdesk feed or an authenticated proxy can be dropped in
without a code change. Until one is configured the section fails cleanly and
the integrity stage reports it down (rather than silently empty); official
outbreak coverage is carried by the who_don section in the meantime.

Items carry the disease name in the title; we parse common patterns
("Avian influenza - North America (12): USA") to surface country.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

# Legacy public RSS is discontinued; overridable so a working feed/proxy URL can
# be supplied via env without editing code.
FEED = os.environ.get("PROMED_FEED_URL", "https://promedmail.org/promed-posts/?cat=feed")
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
