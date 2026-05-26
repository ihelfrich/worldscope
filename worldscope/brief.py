"""
brief.py — orchestrate one daily briefing run.

Each section resolves to a SectionState via the state machine in
sections/__init__.py. The orchestrator does NOT need to know how the
state was reached (fresh pull, carry-forward, stale-after-failure); it
just lays the resulting items + staleness markers into the page.

WORLDSCOPE_SKIP=sanctions,gdelt_regions etc. → comma-separated list of
section ids to NOT re-pull. Each skipped section uses its most-recent
snapshot from ~/.worldscope/store.sqlite (carry-forward) so locally-
generated content survives CI runs that can't see local-only data.

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
from .sections import SectionState
from .sections.acled import AcledSection
from .sections.billionaires import BillionairesSection
from .sections.commentary import CommentarySection
from .sections.conflict import ConflictSection
from .sections.courtlistener import CourtListenerSection
from .sections.fec import FECSection
from .sections.federal_register import FederalRegisterSection
from .sections.firms import FirmsSection
from .sections.forecasts import ForecastsSection
from .sections.form4 import Form4Section
from .sections.gdelt_regions import GdeltRegionsSection
from .sections.macro import MacroSection
from .sections.markets import MarketsSection
from .sections.people import PeopleSection
from .sections.sanctions import SanctionsSection
from .sections.vip_flights import VipFlightsSection
from .store import SnapshotStore
from .synth import synthesize
from .trends import section_trend

SECTION_REGISTRY = [
    FederalRegisterSection,
    MacroSection,
    MarketsSection,
    BillionairesSection,
    PeopleSection,
    SanctionsSection,
    CourtListenerSection,
    Form4Section,
    FECSection,
    GdeltRegionsSection,
    ConflictSection,
    AcledSection,
    FirmsSection,
    VipFlightsSection,
    ForecastsSection,
    CommentarySection,
    # remaining (sketched, not built):
    #   MaritimeSection        — AISStream vessels-of-interest watchlist
    #   ElectionsSection       — global election calendar (Democracy Intl + ParlGov)
    #   AnomalySection         — surface DuckDB warehouse anomaly screen
]


def _list_archive(out_dir: Path) -> list[date]:
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

    # 1. Resolve every section (fresh pull OR carry-forward OR stale-after-failure)
    states: dict[str, SectionState] = {}
    sections_html: list[str] = []
    source_attribution: dict[str, dict] = {}
    for cls in SECTION_REGISTRY:
        if section_ids and cls.id not in section_ids:
            continue
        sec = cls(store=store)
        state = sec.resolve(today=today)
        states[sec.id] = state
        synth = synthesize(sec.title, state.items, {it.get("_id") for it in state.new})
        sections_html.append(sec.render_html(state, synth))
        source_attribution[sec.id] = {
            "title": sec.title,
            "state": state.state,
            "source_date": state.source_date,
            "comparison_date": state.comparison_date,
            "error": state.error,
        }
        marker = ""
        if state.state == "carry_forward":
            marker = f"  (carried from {state.source_date})"
        elif state.state == "stale_after_failure":
            marker = f"  (STALE — failed; last good {state.source_date})"
        elif state.state == "no_data":
            marker = "  (no data)"
        print(f"[{sec.id}] state={state.state}  {len(state.new)} new / {len(state.items)} total{marker}")

    # 2. Trend stats over the last 14 days
    trends = {sid: section_trend(store, sid) for sid in states}

    # 3. Forthcoming events calendar
    cal_items = upcoming(fetch_calendar(), days=14)
    print(f"[calendar] {len(cal_items)} upcoming items")

    # 4. Cross-section overview (the analyst's morning brief)
    section_deltas = {
        sid: (s.title, {"all": s.items, "new": s.new})
        for sid, s in states.items()
    }
    overview_md = build_overview(today, section_deltas, trends, cal_items)

    # 5. Render HTML page
    archive = _list_archive(out_dir)
    if today not in archive:
        archive.append(today)
    page = render_page(
        today, sections_html, out_dir,
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

    # 7. Save the overview Markdown side-by-side
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
