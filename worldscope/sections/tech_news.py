"""
tech_news.py — IT, information-systems, and cybersecurity news from a curated
set of high-signal, reputable outlets. This is the dedicated technology lane:
security advisories, enterprise IT, infrastructure, and innovation — the corpus
the weekly IT & Information Systems handbook (tools/render_it_weekly.py) is
built from.

Feeds are tagged with a coarse category (security | enterprise | innovation |
policy) so downstream synthesis can bucket the week. Pure RSS/Atom, no keys.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import feedparser

from . import Section

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# (outlet, url, category). Categories: security, enterprise, innovation, policy.
FEEDS: list[tuple[str, str, str]] = [
    # — security / risk —
    ("Krebs on Security",      "https://krebsonsecurity.com/feed/",                      "security"),
    ("BleepingComputer",       "https://www.bleepingcomputer.com/feed/",                 "security"),
    ("The Hacker News",        "https://feeds.feedburner.com/TheHackersNews",            "security"),
    ("The Record",             "https://therecord.media/feed/",                          "security"),
    ("Cybersecurity Dive",     "https://www.cybersecuritydive.com/feeds/news/",          "security"),
    ("Schneier on Security",   "https://www.schneier.com/feed/atom/",                    "security"),
    ("CISA advisories",        "https://www.cisa.gov/cybersecurity-advisories/all.xml",  "security"),
    ("Zero Day Initiative",    "https://www.zerodayinitiative.com/blog?format=rss",      "security"),
    # — enterprise IT / infrastructure —
    ("The Register",           "https://www.theregister.com/headlines.atom",             "enterprise"),
    ("InfoWorld",              "https://www.infoworld.com/index.rss",                    "enterprise"),
    ("Ars Technica",           "https://feeds.arstechnica.com/arstechnica/index",        "enterprise"),
    ("Computer Weekly",        "https://www.computerweekly.com/rss/All-Computer-Weekly-content.xml", "enterprise"),
    # — innovation / research —
    ("MIT Technology Review",  "https://www.technologyreview.com/feed/",                 "innovation"),
    ("IEEE Spectrum",          "https://spectrum.ieee.org/feeds/feed.rss",               "innovation"),
    ("TechCrunch",             "https://techcrunch.com/feed/",                           "innovation"),
    # — policy / regulation —
    ("Tech Policy Press",      "https://www.techpolicy.press/rss/",                      "policy"),
]


class TechNewsSection(Section):
    id = "tech_news"
    title = "IT, InfoSys & cybersecurity news (last 7 days)"
    emoji = "🖥️"

    WINDOW_DAYS = 7
    PER_FEED = 5

    def pull(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.WINDOW_DAYS)
        items: list[dict] = []
        for outlet, url, category in FEEDS:
            try:
                feed = feedparser.parse(url, agent=UA)
            except Exception:
                continue
            kept = 0
            for e in feed.entries:
                dt = None
                for attr in ("published_parsed", "updated_parsed"):
                    tp = getattr(e, attr, None)
                    if tp:
                        try:
                            dt = datetime(*tp[:6], tzinfo=timezone.utc)
                            break
                        except (TypeError, ValueError):
                            dt = None
                if dt and dt < cutoff:
                    continue
                title = (getattr(e, "title", "") or "").strip()
                link = getattr(e, "link", "")
                summary = re.sub(r"<[^>]+>", "", (getattr(e, "summary", "") or "")).strip()[:400]
                if not title:
                    continue
                items.append({
                    "id": link or f"{outlet}|{title}",
                    "date": dt.date().isoformat() if dt else "",
                    "title": f"[{outlet}] {title}",
                    "url": link,
                    "summary": summary,
                    "outlet": outlet,
                    "category": category,
                    "source_kind": category,
                    "topics": ["technology", category],
                })
                kept += 1
                if kept >= self.PER_FEED:
                    break
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
