"""
overview.py — the analyst's morning brief.

Combines today's section pulls, trend statistics from the last 14 days of
snapshots, and the forthcoming-events calendar into a single Markdown
document that frames the day in arc-of-events terms: not just "what
happened" but "what happened in relation to what was happening, and what's
coming up next."

When ANTHROPIC_API_KEY is set, an LLM pass writes the prose with strict
grounding (cite by index, refuse if unsupported). Without the key, a
deterministic template still produces a useful document.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

try:
    import anthropic  # type: ignore
except ImportError:
    anthropic = None  # type: ignore

from .calendar import CalendarItem


SYSTEM = """You are a research desk officer writing a daily analyst's morning brief.

Output format: Markdown. ~400-600 words. Structure:
  ## Headline (one line)
  ## What changed today
  ## How it fits the arc (recent precedents)
  ## What to watch (next 14 days)

Discipline:
  - Every concrete claim must be grounded in the provided items. Cite by
    section name + item title; never invent dates or names.
  - "Arc" framing: explicitly link today's items to recent items in the
    same section (use the carrying-terms hint).
  - "What to watch" must be grounded in the forthcoming calendar. If the
    calendar is empty, say so directly.
  - No hedging filler. If today is uneventful, say so in one sentence.
"""

PROMPT = """Today: {today_date}

SECTION PULLS:
{section_pulls}

TRENDS (last 14 days):
{trends_summary}

FORTHCOMING (next 14 days):
{calendar_summary}

Write the Markdown brief now.
"""


def _format_section_for_prompt(title: str, delta: dict, max_items: int = 8) -> str:
    new = delta.get("new", [])
    lines = [f"### {title} — {len(new)} new of {len(delta.get('all', []))} total"]
    for it in new[:max_items]:
        lines.append(f"  - ({it.get('date','?')}) {it.get('title','')}")
        s = (it.get("summary", "") or "")[:220]
        if s:
            lines.append(f"      {s}")
    if not new:
        lines.append("  (no new items today)")
    return "\n".join(lines)


def _format_trends(trends: dict) -> str:
    lines = []
    for tid, t in trends.items():
        line = (
            f"- {tid}: today={t['today_count']}, 7d-median={t['median_7d']}, "
            f"14d-median={t['median_14d']}"
        )
        if t.get("carrying_terms"):
            line += f"; carrying terms: {', '.join(t['carrying_terms'])}"
        lines.append(line)
    return "\n".join(lines) or "(no trend data — first run)"


def _format_calendar(cal: list[CalendarItem]) -> str:
    if not cal:
        return "(no forthcoming events in feeds)"
    lines = []
    for c in cal[:18]:
        when = c.when.date().isoformat() if c.when else "TBD"
        lines.append(f"- [{when}] {c.source}: {c.title}")
    return "\n".join(lines)


def build_overview(
    today: date,
    section_deltas: dict[str, tuple[str, dict]],   # section_id -> (title, delta)
    trends: dict[str, dict],
    calendar: list[CalendarItem],
) -> str:
    section_pulls = "\n\n".join(
        _format_section_for_prompt(title, delta)
        for _id, (title, delta) in section_deltas.items()
    )
    trends_summary = _format_trends(trends)
    calendar_summary = _format_calendar(calendar)

    # LLM path
    if anthropic is not None and os.environ.get("ANTHROPIC_API_KEY"):
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1200,
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": PROMPT.format(
                    today_date=today.isoformat(),
                    section_pulls=section_pulls,
                    trends_summary=trends_summary,
                    calendar_summary=calendar_summary,
                ),
            }],
        )
        return resp.content[0].text.strip()

    # Deterministic fallback
    total_new = sum(len(d["new"]) for _, (_, d) in section_deltas.items())
    lines = [
        f"# WORLDSCOPE morning brief — {today.isoformat()}",
        "",
        f"## Headline",
        f"{total_new} new items across {len(section_deltas)} section(s).",
        "",
        "## What changed today",
        section_pulls or "(no sections pulled)",
        "",
        "## How it fits the arc (last 14 days)",
        trends_summary,
        "",
        "## What to watch (recent + forthcoming, ±14 days)",
        calendar_summary,
        "",
        "*Calendar currently shows recent central-bank announcements (often forward-looking). Explicit forthcoming-event APIs — FRED release calendar, FOMC dates, Treasury auctions, SCOTUS oral argument calendar — land in a later sprint.*",
        "",
        "*Note: this is the deterministic fallback. Set `ANTHROPIC_API_KEY` for synthesized prose.*",
    ]
    return "\n".join(lines)
