"""
calendar.py — forthcoming events the briefing should know about.

Sources (free; expand over time):
  - FRED release calendar (RSS): every upcoming US macro data release with date
  - Treasury auction calendar (RSS): upcoming UST auctions
  - SCOTUS oral argument calendar (scraped from supremecourt.gov)
  - Fed FOMC calendar (RSS): pre-announced meeting dates
  - ECB calendar (RSS)

This file just plumbs the easy RSS ones today. The richer adapters
(SCOTUS, IMF meetings, UN, G20, election calendars) layer on later.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import feedparser

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

CALENDAR_FEEDS = [
    # Verified working 2026-05-25. BLS/BEA/Treasury RSS blocked our default UA
    # (403); they're tracked via API adapters in a later sprint. H.4.1 is
    # dropped — duplicate/low-signal announcements.
    ("Fed press (events + announcements)", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Fed speeches",                       "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("ECB press",                          "https://www.ecb.europa.eu/rss/press.html"),
]

# Per-feed cap so a single noisy feed doesn't drown out the others.
MAX_PER_FEED = 8


@dataclass
class CalendarItem:
    source: str
    title: str
    when: Optional[datetime]
    url: str
    summary: str


def _parse_when(entry) -> Optional[datetime]:
    # Most release-calendar entries put the event date in title or summary;
    # the published date is when the calendar entry was created, not the
    # event date. Best-effort: try published first, then return None and
    # let the caller filter by title text if needed.
    if getattr(entry, "published_parsed", None):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    return None


def fetch_calendar() -> list[CalendarItem]:
    items: list[CalendarItem] = []
    for source, url in CALENDAR_FEEDS:
        try:
            feed = feedparser.parse(url, agent=UA)
            seen_titles: set[str] = set()
            kept = 0
            for e in feed.entries:
                title = getattr(e, "title", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                items.append(CalendarItem(
                    source=source,
                    title=title,
                    when=_parse_when(e),
                    url=getattr(e, "link", ""),
                    summary=getattr(e, "summary", "")[:400],
                ))
                kept += 1
                if kept >= MAX_PER_FEED:
                    break
        except Exception:
            continue
    # Sort by date (most recent / soonest first), Nones to the end
    items.sort(key=lambda it: (it.when is None, it.when or datetime.max.replace(tzinfo=timezone.utc)), reverse=False)
    return items


def upcoming(items: Iterable[CalendarItem], *, days: int = 14) -> list[CalendarItem]:
    """Items relevant to the next-14-day horizon: either recently announced
    (last 14 days — often signals what's coming) OR explicitly dated within
    the forward window OR no parseable date.

    RSS calendar feeds from central banks typically use the publication date
    of the announcement, not the date of the future event. So 'just-announced'
    items are themselves forward-looking signals."""
    now = datetime.now(timezone.utc)
    out: list[CalendarItem] = []
    for it in items:
        if it.when is None:
            out.append(it)
            continue
        dt_days = (it.when - now).total_seconds() / 86400
        if -days <= dt_days <= days:
            out.append(it)
    return out
