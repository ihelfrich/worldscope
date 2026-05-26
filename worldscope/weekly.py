"""
weekly.py — Friday-cadence weekly brief generator.

Different cadence, different scope from the daily brief:
  - 7-day comparisons instead of 24h diffs
  - Anomaly screen on the macro warehouse with 30d lookback (catches regime
    shifts, not daily noise)
  - Top movers across the week: Forbes net-worth shifts, FEC fundraising
    leaders, sanctions cluster additions
  - Calendar lookahead: known events in the next 14 days

Output: dist/weekly/YYYY-WW.html + dist/weekly/YYYY-WW.zip + a markdown
overview. Linked from the daily index when generated.

Usage:
    python -m worldscope.weekly
    python -m worldscope.weekly --out dist/weekly --week 2026-W21
"""
from __future__ import annotations

import argparse
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .calendar import fetch_calendar, upcoming
from .lib.warehouse import open as open_warehouse
from .render import render_page
from .sections import Section, SectionState, STATE_FRESH
from .sections.commentary import CommentarySection
from .sections.conflict import ConflictSection
from .sections.courtlistener import CourtListenerSection
from .sections.fec import FECSection
from .sections.federal_register import FederalRegisterSection
from .sections.forecasts import ForecastsSection
from .sections.gdelt_regions import GdeltRegionsSection
from .sections.macro import MacroSection
from .sections.markets import MarketsSection
from .sections.billionaires import BillionairesSection
from .store import SnapshotStore
from .synth import synthesize


# Sections to include in the weekly brief. Note: the daily-only sections
# (vip_flights, form4) are noisy day-to-day but uninteresting weekly, so
# we drop them. The weekly version also drops sanctions+people which
# require the 2.6GB local corpus.
WEEKLY_SECTIONS = [
    FederalRegisterSection,
    MacroSection,
    MarketsSection,
    BillionairesSection,
    CourtListenerSection,
    FECSection,
    GdeltRegionsSection,
    ConflictSection,
    ForecastsSection,
    CommentarySection,
]


def _iso_week(d: date) -> str:
    """Return ISO 8601 week id like '2026-W21'."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_dates(d: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the ISO week containing d."""
    y, w, dow = d.isocalendar()
    monday = d - timedelta(days=dow - 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def macro_weekly_recap(store: SnapshotStore) -> str:
    """7-day percent changes for tier-1 macro series, plus a wider anomaly
    screen with a 30-day lookback."""
    try:
        w = open_warehouse()
    except Exception as exc:
        return f"(warehouse unavailable: {exc})"

    lines: list[str] = []

    # Per-series 7-day change
    today = date.today()
    week_ago = today - timedelta(days=7)
    for sid in ("DGS10", "DGS2", "T10Y2Y", "DFF", "VIXCLS",
                "DCOILWTICO", "DEXUSEU", "DEXJPUS", "DEXCHUS"):
        latest = w.latest(sid, source="fred")
        if not latest or latest[1] is None:
            continue
        prior = w.query(sid, source="fred", end=week_ago).fetchall()
        if not prior:
            continue
        prior_val = prior[-1][1]
        if prior_val is None or prior_val == 0:
            continue
        delta = latest[1] - prior_val
        pct = (delta / prior_val) * 100
        arrow = "▲" if delta > 0 else "▼"
        lines.append(
            f"  {sid:12s} {latest[1]:>10.4f}  {arrow} {delta:+.4f} ({pct:+.2f}% in 7d)"
        )

    if not lines:
        return "(no warehouse data yet — run `python -m worldscope.lib.fred_loader --update`)"

    out = ["**7-day macro changes**:", ""]
    out.extend(lines)

    # Anomaly screen with 30d lookback
    out += ["", "**Anomalies (z > 1.5σ over trailing 30d)**:", ""]
    for h in w.anomaly_screen(lookback_days=30, z_threshold=1.5)[:8]:
        out.append(
            f"  {h['series_id']:12s} {h['latest_date']} = {h['latest_value']}  "
            f"({'+' if h['z']>0 else ''}{h['z']:.2f}σ)"
        )

    return "\n".join(out)


def write_overview(today: date, states: dict[str, SectionState],
                   macro_recap: str, cal_items: list) -> str:
    monday, sunday = _week_dates(today)
    out = [
        f"# WORLDSCOPE weekly brief — {_iso_week(today)}",
        f"*{monday.isoformat()} → {sunday.isoformat()}*",
        "",
        "## Headline",
        "",
        f"This week's run covered {len(states)} sections. "
        f"{sum(len(s.items) for s in states.values())} items total; "
        f"{sum(len(s.new) for s in states.values())} new since the previous run.",
        "",
        "## Macro recap",
        "",
        macro_recap,
        "",
        "## What happened (by section)",
        "",
    ]
    for sid, st in states.items():
        out.append(f"### {st.emoji} {st.title} — {len(st.new)} new / {len(st.items)} total")
        if not st.items:
            out.append("  (no items)")
            out.append("")
            continue
        for it in st.items[:6]:
            out.append(f"- ({it.get('date','?')}) {it.get('title','(no title)')}")
        if len(st.items) > 6:
            out.append(f"- _… and {len(st.items)-6} more in the data bundle_")
        out.append("")
    out += [
        "## What to watch (next 14 days)",
        "",
    ]
    if cal_items:
        for c in cal_items[:18]:
            when = c.when.date().isoformat() if c.when else "TBD"
            out.append(f"- [{when}] {c.source}: {c.title}")
    else:
        out.append("(no upcoming events in feeds)")
    return "\n".join(out)


def run(out_dir: Path | str = "dist/weekly", week: Optional[str] = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    week_id = week or _iso_week(today)

    store = SnapshotStore()
    states: dict[str, SectionState] = {}
    sections_html: list[str] = []
    for cls in WEEKLY_SECTIONS:
        sec = cls(store=store)
        st = sec.resolve(today=today)
        states[sec.id] = st
        new_ids = {it.get("_id") for it in st.new}
        synth = synthesize(sec.title, st.items, new_ids)
        sections_html.append(sec.render_html(st, synth))
        print(f"[{sec.id}] state={st.state}  {len(st.new)} new / {len(st.items)} total")

    macro_recap = macro_weekly_recap(store)
    cal_items = upcoming(fetch_calendar(), days=14)
    print(f"[calendar] {len(cal_items)} upcoming")

    overview_md = write_overview(today, states, macro_recap, cal_items)
    (out_dir / f"{week_id}.md").write_text(overview_md, encoding="utf-8")

    page = render_page(today, sections_html, out_dir,
                       overview_md=overview_md, archive_dates=None)
    # Rename the page to use week id (render_page wrote it as <date>.html)
    week_html = out_dir / f"{week_id}.html"
    (out_dir / f"{today.isoformat()}.html").rename(week_html)
    (out_dir / "index.html").write_text(week_html.read_text(encoding="utf-8"), encoding="utf-8")

    # Bundle: overview + raw per-section JSON + macro recap
    zpath = out_dir / f"{week_id}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{week_id}.html", week_html.read_text(encoding="utf-8"))
        z.writestr("overview.md", overview_md)
        z.writestr("macro_recap.txt", macro_recap)
        for sid, st in states.items():
            z.writestr(f"raw/{sid}.json",
                       json.dumps(st.items, indent=2, default=str))
        z.writestr("manifest.json", json.dumps({
            "week": week_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": {sid: {"new": len(s.new), "total": len(s.items),
                               "state": s.state} for sid, s in states.items()},
        }, indent=2))

    print(f"\n→ page : {week_html}")
    print(f"→ zip  : {zpath}")
    return week_html


def main():
    p = argparse.ArgumentParser(description="Generate the weekly Friday brief")
    p.add_argument("--out", default="dist/weekly", help="output directory")
    p.add_argument("--week", help="ISO week id (e.g. 2026-W21); default: this week")
    args = p.parse_args()
    run(out_dir=args.out, week=args.week)


if __name__ == "__main__":
    main()
