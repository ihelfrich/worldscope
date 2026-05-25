"""
commentary.py — aggregator over the economist substacks and policy blogs
the owner already follows. Pulls fresh posts (last 7 days) from a curated
RSS list.

The list mirrors what's in econscope/sources.yaml — Tooze, Setser, Levine,
Smith, Milanović, Weber, Marginal Revolution, Cochrane, plus PIIE and
VoxEU for the more institutional commentary lane.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import feedparser

from . import Section

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

FEEDS: list[tuple[str, str]] = [
    ("Adam Tooze",           "https://adamtooze.substack.com/feed"),
    ("Brad Setser",          "https://www.cfr.org/blog-feed/brad-setser"),
    ("Matt Levine",          "https://newslettershub.com/rss/money-stuff-by-matt-levine"),
    ("Noah Smith",           "https://noahpinion.substack.com/feed"),
    ("Branko Milanović",     "https://branko2f7.substack.com/feed"),
    ("Isabella Weber",       "https://isabellamweber.substack.com/feed"),
    ("Marginal Revolution",  "https://marginalrevolution.com/feed"),
    ("John Cochrane",        "https://johnhcochrane.blogspot.com/feeds/posts/default?alt=rss"),
    ("Conversable Economist", "https://conversableeconomist.blogspot.com/feeds/posts/default?alt=rss"),
    ("PIIE",                 "https://www.piie.com/rss.xml"),
    ("VoxEU columns",        "https://cepr.org/rss/voxeu/columns.xml"),
    ("Brookings",            "https://www.brookings.edu/feed/"),
]


class CommentarySection(Section):
    id = "commentary"
    title = "Commentary & analysis (last 7 days)"
    emoji = "💬"

    WINDOW_DAYS = 7
    PER_FEED = 3

    def pull(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.WINDOW_DAYS)
        items: list[dict] = []
        for author, url in FEEDS:
            try:
                feed = feedparser.parse(url, agent=UA)
            except Exception:
                continue
            kept = 0
            for e in feed.entries:
                # Published date — best-effort
                dt = None
                if getattr(e, "published_parsed", None):
                    try:
                        dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        dt = None
                if dt and dt < cutoff:
                    continue
                title = getattr(e, "title", "")
                link = getattr(e, "link", "")
                summary = (getattr(e, "summary", "") or "").strip()[:400]
                # Strip HTML tags from substack/blog feeds
                import re
                summary = re.sub(r"<[^>]+>", "", summary)
                items.append({
                    "id": link or (author + "|" + title),
                    "date": dt.date().isoformat() if dt else "",
                    "title": f"[{author}] {title}",
                    "url": link,
                    "summary": summary,
                    "author": author,
                })
                kept += 1
                if kept >= self.PER_FEED:
                    break
        # Newest first
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
