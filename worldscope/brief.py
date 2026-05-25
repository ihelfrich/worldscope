"""
brief.py — orchestrate one daily briefing run.

Pulls every registered section, computes 14-day trends, fetches the
forthcoming-events calendar, generates the analyst's morning brief
(overview.md), renders the HTML page with a download link, and bundles
everything into a zip in dist/zips/.

Usage:
    python -m worldscope.brief
    python -m worldscope.brief --section federal_register
    python -m worldscope.brief --out dist
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .bundle import make_bundle
from .calendar import fetch_calendar, upcoming
from .overview import build_overview
from .render import render_page
from .sections.commentary import CommentarySection
from .sections.federal_register import FederalRegisterSection
from .sections.gdelt_regions import GdeltRegionsSection
from .sections.sanctions import SanctionsSection
from .store import SnapshotStore
from .synth import synthesize
from .trends import section_trend

SECTION_REGISTRY = [
    FederalRegisterSection,
    SanctionsSection,
    GdeltRegionsSection,
    CommentarySection,
    # remaining Phase 1 sections (sketched, not built):
    #   MacroSection           — FRED daily releases + central bank press
    #   CourtListenerSection   — new opinions of consequence (CIT, SCOTUS, federal appeals)
    #   MarketsSection         — FX/yields/indices snapshot
    #   ForecastSection        — Metaculus + GoodJudgment
    #   VipFlightsSection      — OpenSky watchlist convergence
]


def _list_archive(out_dir: Path) -> list[date]:
    """Return sorted list of dates with existing briefings in the out_dir."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return []
    dates = []
    for p in out_dir.glob("*.html"):
        if p.stem == "index":
            continue
        try:
            dates.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(dates)


def run(section_ids: list[str] | None = None, *, out_dir: Path | str = "dist") -> Path:
    out_dir = Path(out_dir)
    store = SnapshotStore()
    today = date.today()

    # 1. Pull every section, compute deltas, synthesize per-section paragraph
    section_deltas: dict[str, tuple[str, dict]] = {}
    sections_html: list[str] = []
    source_attribution: dict[str, dict] = {}
    for cls in SECTION_REGISTRY:
        if section_ids and cls.id not in section_ids:
            continue
        sec = cls(store=store)
        delta = sec.delta(today=today)
        new_ids = {it["_id"] for it in delta["new"]}
        synth = synthesize(sec.title, delta["all"], new_ids)
        section_deltas[sec.id] = (sec.title, delta)
        sections_html.append(sec.render_html(delta, synth))
        source_attribution[sec.id] = {
            "title": sec.title,
            "endpoint": getattr(sec, "__module__", ""),
        }
        print(f"[{sec.id}] {len(delta['new'])} new / {len(delta['all'])} total")

    # 2. Trend stats over the last 14 days
    trends = {sid: section_trend(store, sid) for sid in section_deltas}

    # 3. Forthcoming events calendar
    cal_items = upcoming(fetch_calendar(), days=14)
    print(f"[calendar] {len(cal_items)} upcoming items")

    # 4. Cross-section overview (the analyst's morning brief)
    overview_md = build_overview(today, section_deltas, trends, cal_items)

    # 5. Render HTML page with overview + download link
    archive = _list_archive(out_dir)
    if today not in archive:
        archive.append(today)
    page = render_page(
        today,
        sections_html,
        out_dir,
        overview_md=overview_md,
        archive_dates=sorted(set(archive)),
    )

    # 6. Bundle the zip
    zpath = make_bundle(
        out_dir=out_dir,
        when=today.isoformat(),
        index_html=page.read_text(encoding="utf-8"),
        overview_md=overview_md,
        section_deltas=section_deltas,
        calendar=cal_items,
        trends=trends,
        source_attribution=source_attribution,
    )

    # 7. Also save the overview as a sibling markdown for quick reading
    (out_dir / f"{today.isoformat()}.md").write_text(overview_md, encoding="utf-8")

    print(f"\n→ page : {page}")
    print(f"→ zip  : {zpath}")
    return page


def main() -> None:
    p = argparse.ArgumentParser(description="Generate today's WORLDSCOPE briefing")
    p.add_argument("--section", action="append", help="restrict to specific section id(s)")
    p.add_argument("--out", default="dist", help="output directory (default: dist)")
    args = p.parse_args()
    run(args.section, out_dir=args.out)


if __name__ == "__main__":
    main()
