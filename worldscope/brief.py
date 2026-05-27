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
from .lib.watchareas import load_watch_areas, tag_items
from .sections import SectionState
from .sections.acled import AcledSection
from .sections.billionaires import BillionairesSection
from .sections.cisa_kev import CisaKevSection
from .sections.commentary import CommentarySection
from .sections.conflict import ConflictSection
from .sections.congressional_trades import CongressionalTradesSection
from .sections.courtlistener import CourtListenerSection
from .sections.fec import FECSection
from .sections.federal_register import FederalRegisterSection
from .sections.firms import FirmsSection
from .sections.forecasts import ForecastsSection
from .sections.form4 import Form4Section
from .sections.gdelt_gkg import GdeltGkgSection
from .sections.gdelt_regions import GdeltRegionsSection
from .sections.macro import MacroSection
from .sections.markets import MarketsSection
from .sections.mediacloud import MediaCloudSection
from .sections.people import PeopleSection
from .sections.promed import PromedSection
from .sections.reliefweb import ReliefWebSection
from .sections.sanctions import SanctionsSection
from .sections.chinese_internal import ChineseInternalSection
from .sections.foreign_news import ForeignNewsSection
from .sections.local_news import LocalNewsSection
from .sections.markets_global import MarketsGlobalSection
from .sections.paper_bet_placement import PaperBetPlacementSection
from .sections.paper_bets import PaperBetsSection
from .sections.political_figures import PoliticalFiguresSection
from .sections.russian_internal import RussianInternalSection
from .sections.sanctions_procurement import SanctionsProcurementSection
from .sections.state_bills import StateBillsSection
from .sections.state_news import StateNewsSection
from .sections.ukraine_theater import UkraineTheaterSection
from .sections.ukrainian_internal import UkrainianInternalSection
from .sections.vip_flights import VipFlightsSection
from .sections.weather import WeatherSection
from .sections.wikidata_changes import WikidataChangesSection
from .store import SnapshotStore
from .synth import synthesize
from .trends import section_trend

SECTION_REGISTRY = [
    FederalRegisterSection,
    StateBillsSection,
    StateNewsSection,
    LocalNewsSection,
    ForeignNewsSection,
    ChineseInternalSection,
    RussianInternalSection,
    UkrainianInternalSection,
    UkraineTheaterSection,
    PaperBetsSection,
    WeatherSection,
    MacroSection,
    MarketsSection,
    MarketsGlobalSection,
    SanctionsProcurementSection,
    CongressionalTradesSection,
    BillionairesSection,
    PeopleSection,
    SanctionsSection,
    CourtListenerSection,
    Form4Section,
    FECSection,
    GdeltRegionsSection,
    GdeltGkgSection,
    MediaCloudSection,
    ConflictSection,
    AcledSection,
    FirmsSection,
    VipFlightsSection,
    PromedSection,
    CisaKevSection,
    WikidataChangesSection,
    ReliefWebSection,
    ForecastsSection,
    CommentarySection,
    # Must run AFTER congressional_trades, gdelt_gkg, and form4: it reads
    # those sections' lake artifacts to build its per-figure signal index.
    PoliticalFiguresSection,
    # MUST RUN LAST: placement reads every other section's summary.md
    # from today before deciding where to place paper bets.
    PaperBetPlacementSection,
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
    watch_areas = load_watch_areas()
    states: dict[str, SectionState] = {}
    sections_html: list[str] = []
    source_attribution: dict[str, dict] = {}
    for cls in SECTION_REGISTRY:
        if section_ids and cls.id not in section_ids:
            continue
        sec = cls(store=store)
        state = sec.resolve(today=today)
        # Tag every item with the watch areas it falls into. The renderer
        # and the routine prompt both rely on `watch_areas` being present.
        if watch_areas and state.items:
            tag_items(state.items, watch_areas, source_id=sec.id)
        states[sec.id] = state
        # Mirror the section's output into the new lake (raw.jsonl +
        # summary.md + structured.json + records/entities/relationships
        # SQLite tables). This runs alongside the legacy snapshot path so
        # the existing brief continues unchanged while the lake fills in.
        # Failures here log but never block the brief.
        try:
            sec.to_lake(state)
        except Exception as lake_exc:
            print(f"[{sec.id}] to_lake failed: "
                  f"{type(lake_exc).__name__}: {lake_exc}")
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

    # 1a. Populate the multilingual embedding index for today. Drives the
    # cross-language MCP semantic search and the headline dedup module.
    # Defensive; failure never blocks the brief.
    try:
        from .embeddings import EmbeddingIndex  # local import keeps brief lazy
        per_section = EmbeddingIndex().index_today(today.isoformat())
        new_embeds = sum(per_section.values())
        if new_embeds:
            print(f"[embeddings] indexed {new_embeds} new records across "
                  f"{len(per_section)} sections")
    except Exception as ex:  # pragma: no cover
        print(f"[embeddings] index_today failed: {type(ex).__name__}: {ex}")

    # 1b. Render the daily-infographic suite from the lake. Defensive; a
    # graphics failure never blocks the brief.
    try:
        from .graphics import DailyGraphics  # local import keeps brief lazy
        graphics_paths = DailyGraphics().render_all(today.isoformat())
        for gname, gpath in graphics_paths.items():
            print(f"[graphics] {gname}: {gpath}")
    except Exception as gx:  # pragma: no cover
        print(f"[graphics] suite failed: {type(gx).__name__}: {gx}")

    # 1c. Render the daily map suite from the lake. Same defensive posture.
    try:
        from .cartography import DailyMaps  # local import keeps brief lazy
        map_paths = DailyMaps().render_all(today.isoformat())
        for mname, mpath in map_paths.items():
            print(f"[maps] {mname}: {mpath}")
    except Exception as mx:  # pragma: no cover
        print(f"[maps] suite failed: {type(mx).__name__}: {mx}")

    # 1d. Ukraine theater maps. Independent of the world/US suite so a
    # failure here doesn't block them, and vice versa.
    try:
        from .cartography_ukraine import UkraineMaps
        ukr_paths = UkraineMaps().render_all(today.isoformat())
        for mname, mpath in ukr_paths.items():
            print(f"[ukraine-maps] {mname}: {mpath}")
    except Exception as ux:  # pragma: no cover
        print(f"[ukraine-maps] suite failed: {type(ux).__name__}: {ux}")

    # 1e. Mirror the generated PNGs into briefings/<date>-<name>.png so the
    # renderer's discover_assets() finds them. Without this, the maps and
    # graphics generated above ended up in figures/daily/... but never made
    # it into the rendered HTML. This is the actual fix for the recurring
    # "where are the maps" problem.
    import shutil as _shutil
    _repo_root = Path(__file__).resolve().parent.parent
    briefings_dir = _repo_root / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    stem = today.isoformat()
    mirrored = 0
    for src_path in (
        list((_repo_root / "figures" / "daily" / stem).glob("*.png"))
        + list((_repo_root / "figures" / "daily" / stem / "maps").glob("*.png"))
    ):
        dest = briefings_dir / f"{stem}-{src_path.name}"
        try:
            _shutil.copy(src_path, dest)
            mirrored += 1
        except Exception:
            pass
    if mirrored:
        print(f"[mirror] copied {mirrored} generated graphics+maps into briefings/")

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
