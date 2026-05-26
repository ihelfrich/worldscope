"""
promed.py — ProMED-mail (International Society for Infectious Diseases)
RSS feed of unusual disease outbreaks. The closest the open web gets to
real-time biosurveillance. Posts include human, animal, plant, and
zoonotic outbreaks with location and source citations.

Feed: https://promedmail.org/promed-posts/?cat=feed (RSS)

Items carry the disease name in the title; we parse common patterns
("Avian influenza - North America (12): USA") to surface country.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from . import Section

FEED = "https://promedmail.org/promed-posts/?cat=feed"
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
        except Exception:
            return []
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            return []
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
