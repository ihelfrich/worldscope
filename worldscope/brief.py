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
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from .bundle import make_bundle
from .calendar import fetch_calendar, upcoming
from .overview import build_overview
from .render import render_page
from .lib.watchareas import load_watch_areas, tag_items
from .sections import SectionState
from .sections.acled import AcledSection
from .sections.bea import BeaSection
from .sections.billionaires import BillionairesSection
from .sections.bls import BlsSection
from .sections.cisa_kev import CisaKevSection
from .sections.epss import EpssSection
from .sections.commentary import CommentarySection
from .sections.tech_news import TechNewsSection
from .sections.conflict import ConflictSection
from .sections.congress import CongressSection
from .sections.congressional_record import CongressionalRecordSection
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
from .sections.who_don import WhoDonSection
from .sections.reliefweb import ReliefWebSection
from .sections.gdacs import GdacsSection
from .sections.usgs_quakes import UsgsQuakesSection
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
    # Three sources whose credentials had been configured for months and read
    # by zero code. BEA and BLS sit next to macro because they answer the same
    # question from primary statistical agencies rather than via FRED.
    BeaSection,
    BlsSection,
    CongressSection,
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
    WhoDonSection,
    CisaKevSection,
    EpssSection,
    WikidataChangesSection,
    ReliefWebSection,
    GdacsSection,
    UsgsQuakesSection,
    ForecastsSection,
    CommentarySection,
    TechNewsSection,
    # Must run AFTER congressional_trades, gdelt_gkg, form4 and
    # congressional_record: it reads those sections' lake artifacts to build
    # its per-figure signal index.
    CongressionalRecordSection,
    PoliticalFiguresSection,
    # MUST RUN LAST: placement reads every other section's summary.md
    # from today before deciding where to place paper bets.
    PaperBetPlacementSection,
    # remaining (sketched, not built):
    #   MaritimeSection        — AISStream vessels-of-interest watchlist
    #   ElectionsSection       — global election calendar (Democracy Intl + ParlGov)
    #   AnomalySection         — surface DuckDB warehouse anomaly screen
]


# Stages whose failure should redden the run. The rest degrade: a flaky
# upstream must never cost the day's brief.
REQUIRED_STAGES = frozenset({
    "graphics", "maps", "cross-section", "signals", "claims",
    "scorecard", "site-builder",
})


def _run_stage(label: str, fn, report: Optional[dict] = None) -> None:
    """Run one post-section stage, recording its fate.

    This used to catch every exception and print. `daily-brief.yml` installed
    the bare package, so `embeddings`, `graphics`, `maps`, `ukraine-maps` and
    the DuckDB warehouse ImportError'd on their local imports and vanished
    into a log line nobody read — 100 green runs out of the last 100, while
    five of eleven stages produced nothing.

    Two tiers now:
      ImportError  a missing dependency is a deployment defect, not a runtime
                   hiccup. Raised, so the run goes red immediately.
      anything else recorded and survived, then the job fails at the end if a
                   REQUIRED stage was among them.
    """
    started = time.monotonic()
    entry = {"status": "ok", "error_type": None, "error_message": None,
             "required": label in REQUIRED_STAGES}
    try:
        fn()
    except (ImportError, ModuleNotFoundError) as ex:
        entry.update(status="failed", error_type=type(ex).__name__,
                     error_message=str(ex))
        if report is not None:
            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
            report[label] = entry
        print(f"::error::[{label}] missing dependency: {ex}. "
              f"Install the 'ci' extra: pip install -e '.[ci]'")
        raise
    except Exception as ex:
        entry.update(status="failed", error_type=type(ex).__name__,
                     error_message=str(ex)[:500])
        print(f"[{label}] failed: {type(ex).__name__}: {ex}")
    finally:
        entry["duration_ms"] = int((time.monotonic() - started) * 1000)
        if report is not None and label not in report:
            report[label] = entry


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

    # Defensive post-section stages. Each runs the lake-derived analytics and
    # rendering for today; any one failing logs but never blocks the brief.
    # Run in order — site_builder reads what the earlier stages wrote.
    def _stage_embeddings() -> None:
        # Populate the multilingual embedding index for today. Drives the
        # cross-language MCP semantic search and the headline dedup module.
        from .embeddings import EmbeddingIndex  # local import keeps brief lazy
        per_section = EmbeddingIndex().index_today(today.isoformat())
        new_embeds = sum(per_section.values())
        if new_embeds:
            print(f"[embeddings] indexed {new_embeds} new records across "
                  f"{len(per_section)} sections")

    def _stage_graphics() -> None:
        # Render the daily-infographic suite from the lake.
        from .graphics import DailyGraphics  # local import keeps brief lazy
        for gname, gpath in DailyGraphics().render_all(today.isoformat()).items():
            print(f"[graphics] {gname}: {gpath}")

    def _stage_maps() -> None:
        # Render the daily world/US map suite from the lake.
        from .cartography import DailyMaps  # local import keeps brief lazy
        for mname, mpath in DailyMaps().render_all(today.isoformat()).items():
            print(f"[maps] {mname}: {mpath}")

    def _stage_ukraine_maps() -> None:
        # Ukraine theater maps. Independent of the world/US suite so a failure
        # here doesn't block them, and vice versa.
        from .cartography_ukraine import UkraineMaps
        for mname, mpath in UkraineMaps().render_all(today.isoformat()).items():
            print(f"[ukraine-maps] {mname}: {mpath}")

    def _stage_signals() -> None:
        # Cross-source signal fusion. Reads the lake the sections just populated,
        # ranks entities/themes by how many INDEPENDENT sections corroborate them,
        # logs the strongest as falsifiable predictions (auto-graded later to build
        # a calibrated track record), grades any predictions now due, and prepends
        # a "Signals" panel to the brief. Failure here never blocks the brief.
        from . import signals as _sg
        from .lake import Lake
        lake = Lake.open()
        try:
            conn = lake._ensure_open()
            sigs = _sg.build_signals(today=today, days=_sg.DEFAULT_WINDOW_DAYS, conn=conn)
            preds = _sg.signals_to_predictions(sigs, today=today)
            n_written = _sg.persist_predictions(lake, preds)
            graded = _sg.grade_due_predictions(lake, conn, today=today)
            panel = _sg.render_signals_panel(sigs, preds)
            if panel:
                sections_html.insert(0, panel)
            print(f"[signals] {len(sigs)} cross-source signals · "
                  f"wrote {n_written} predictions · graded {graded} due")
        finally:
            lake.close()

    def _stage_integrity() -> None:
        # Data-integrity report. Classifies every section (fresh / stale / empty /
        # failed / no-key / skipped) from the lake + source_health, writes a _meta
        # artifact, and prepends an honest "Data integrity" panel — the
        # auto-generated, accurate replacement for hand-written DATA NOTE prose.
        from . import integrity as _ig
        from .lake import Lake
        lake = Lake.open()
        try:
            conn = lake._ensure_open()
            sids = [c.id for c in SECTION_REGISTRY]
            reports = _ig.assess(conn, sids, today=today, store=store)
            _ig.write_artifact(today, reports)
            panel = _ig.render_integrity_panel(reports)
            if panel:
                sections_html.insert(0, panel)
            print(f"[integrity] {_ig.summary_line(reports)}")
        finally:
            lake.close()

    def _stage_radar() -> None:
        # Research radar. Reads the same populated lake the sections wrote,
        # flags developments (surges + novel multi-section emergence) into the
        # anomalies table, scores every source's credibility by cross-source
        # corroboration, seeds candidate new sources, persists a _meta artifact
        # the desk-officer routine can read, and prepends a "Research radar"
        # panel to the brief. Failure here never blocks the brief.
        from . import radar as _rd
        from .lake import Lake
        lake = Lake.open()
        try:
            conn = lake._ensure_open()
            records = _rd._load_records(today, _rd.RADAR_WINDOW_DAYS, conn)
            agg = _rd.aggregate_keys(records, today=today, days=_rd.RADAR_WINDOW_DAYS)
            devs = _rd.detect_developments(agg, today=today, days=_rd.RADAR_WINDOW_DAYS)
            creds = _rd.score_sources(
                records, health_by_source=_rd._health_map(conn),
                tier_by_source=_rd._tier_map(conn))
            candidates = _rd.discover_candidate_sources(
                records, known_hosts=_rd._known_hosts(conn))
            n_anom = _rd.persist_developments(lake, devs, today=today)
            _rd.write_radar_artifact(today, devs, creds, candidates)
            panel = _rd.render_radar_panel(devs, creds)
            if panel:
                sections_html.insert(0, panel)
            print(f"[radar] {len(devs)} developments · {n_anom} anomalies written "
                  f"· {len(creds)} sources scored · {len(candidates)} candidate sources")
        finally:
            lake.close()

    def _stage_claims() -> None:
        # The evidence engine: cluster the lake's records into typed, evidence-
        # graded claims (status from corroboration + source tier, with denial
        # detection), persist claims + claim_evidence, and prepend a "Claims"
        # panel. Reads the same records signals/radar use. Never blocks a brief.
        from . import claims as _cl
        from .lake import Lake
        lake = Lake.open()
        try:
            conn = lake._ensure_open()
            cls = _cl.build_from_lake(today=today, days=_cl.DEFAULT_WINDOW_DAYS, conn=conn)
            n = _cl.persist_claims(lake, cls, today=today)
            panel = _cl.render_claims_panel(cls)
            if panel:
                sections_html.insert(0, panel)
            n_contra = sum(1 for c in cls if c.status == "contradicted")
            print(f"[claims] {len(cls)} claims · {n_contra} contradicted · persisted {n}")
        finally:
            lake.close()

    def _stage_cross_section() -> None:
        # Stage 1 analytical pass: cross-section entity recurrence. Pre-computes
        # which entities appear in 3+ sections today and writes
        # lake/sections/_meta/<date>/cross_section.json. The desk-officer
        # routine reads this instead of deriving recurrence from raw text it
        # cannot hold in attention.
        from .analysis.cross_section import write as _cs_write
        print(f"[cross-section] {_cs_write(today.isoformat())}")

    def _stage_stories() -> None:
        # Event-level story clustering: the Top Stories front page. Groups the
        # day's records into story threads by shared entities + headline token
        # overlap (folding in embeddings when the index is populated), ranks
        # them by independent cross-source coverage breadth, persists a
        # top_stories.json artifact, and prepends a "Top Stories" panel. Reads
        # the same lake signals/radar/claims use. Runs LAST among the
        # panel stages so Top Stories lands at the very top of the brief.
        from . import stories as _st
        from .lake import Lake
        lake = Lake.open()
        try:
            conn = lake._ensure_open()
            sts = _st.build_stories(today=today, days=_st.DEFAULT_WINDOW_DAYS, conn=conn)
            _st.write_stories_artifact(today, sts)
            panel = _st.render_stories_panel(sts)
            if panel:
                sections_html.insert(0, panel)
            multi = sum(1 for s in sts if s.n_languages > 1)
            print(f"[stories] {len(sts)} top stories clustered · "
                  f"{multi} multi-language")
        finally:
            lake.close()

    def _stage_site_builder() -> None:
        # Build per-section drill-down pages from the lake. Without this the
        # public Pages site only shows the synthesized brief; the ~5,000 raw
        # records per day are invisible. Writes dist/sections/<id>/<date>.html.
        from .site_builder import build_all as _site_build
        site_stats = _site_build(Path(out_dir), days_to_render=7)
        print(f"[site-builder] {site_stats['sections']} sections, "
              f"{site_stats['section_pages']} index pages, "
              f"{site_stats['day_pages']} day pages")

    # Per-stage outcome, written to dist/run_report.json below. Before this,
    # a stage that failed left only a print in a log nobody read.
    stage_report: dict[str, dict] = {}

    def _search_diagnostics(bets: list[dict]) -> dict:
        """Multiple-testing control over the strategy search itself.

        "Revise the method every day until it beats the market" is a
        specification search. These are the numbers that say whether a good
        result survived being searched for. They are computed on real-money
        resolved bets only, and each declines to report rather than emit a
        meaningless point estimate on a thin sample.
        """
        import numpy as _np
        from .lake import Lake as _Lake
        from .scoring import multiple_testing as _mt
        from .scoring import venues as _v

        lake = _Lake.open()
        try:
            n_trials = lake.trial_count()
            violations = lake.preregistration_violations()
        finally:
            lake.close()

        real = _v.partition(bets)[_v.REAL_MONEY]
        pnl = _np.array([float(b.get("final_pnl") or 0.0) for b in real])

        out: dict = {
            "n_registered_rules": n_trials,
            "preregistration_violations": len(violations),
            "violation_reasons": sorted({v["reason"] for v in violations}),
            "n_real_money_resolved": int(pnl.size),
        }

        # DSR needs a dispersion of Sharpes across trials to define its
        # threshold. With one rule and no cross-trial variance there is
        # nothing to deflate against, and inventing a number would be worse
        # than saying so.
        if pnl.size < _mt.MIN_OBS_FOR_SHARPE or n_trials < 2:
            out["deflated_sharpe"] = None
            out["note"] = (
                f"not computable yet: {pnl.size} resolved real-money bets "
                f"across {n_trials} registered rule(s). Needs at least "
                f"{_mt.MIN_OBS_FOR_SHARPE} bets and 2 rules."
            )
            return out

        sr = _mt.sharpe_ratio(pnl)
        out["sharpe_naive"] = None if sr is None else round(sr, 4)

        # var_sharpe is the dispersion of SHARPE RATIOS across trials. Measure
        # it from the rules that actually ran when there are two or more;
        # otherwise fall back to the estimator's null sampling variance.
        # Passing the variance of returns here instead understates DSR by
        # orders of magnitude.
        trial_matrix, _rids, _dates = _mt.daily_returns_by_rule(real)
        if trial_matrix.size and trial_matrix.shape[1] >= 2:
            trial_sharpes = [_mt.sharpe_ratio(trial_matrix[:, j])
                             for j in range(trial_matrix.shape[1])]
            trial_sharpes = [x for x in trial_sharpes if x is not None]
            var_sharpe = (float(_np.var(trial_sharpes, ddof=1))
                          if len(trial_sharpes) >= 2
                          else _mt.null_sharpe_variance(pnl.size, sr or 0.0))
            out["var_sharpe_source"] = "observed across registered rules"
        else:
            var_sharpe = _mt.null_sharpe_variance(pnl.size, sr or 0.0)
            out["var_sharpe_source"] = "null sampling variance (single rule)"

        out["deflated_sharpe"] = round(_mt.deflated_sharpe_ratio(
            pnl, n_trials=n_trials, var_sharpe=var_sharpe), 4)
        out["note"] = (
            "deflated_sharpe is the probability the record beats what the "
            "best of n_registered_rules noise runs would have produced; "
            "HIGH is good, below ~0.95 is not evidence of skill"
        )

        # PBO and SPA compare configurations against each other, so they need
        # two or more rules with overlapping live history. They switch on by
        # themselves the first day that exists, rather than sitting as dead
        # code waiting to be remembered.
        matrix, rule_ids, dates = _mt.daily_returns_by_rule(real)
        if matrix.size and matrix.shape[0] >= 16 and matrix.shape[1] >= 2:
            try:
                pbo = _mt.pbo_cscv(matrix, n_splits=8)
                out["pbo"] = pbo.as_dict()
            except ValueError as exc:
                out["pbo"] = {"unavailable": str(exc)}

            # Benchmark is DOING NOTHING (zero return), not the best rule.
            # Testing the best rule against the others answers "is my best rule
            # better than my other rules", which is not the question. The
            # question is whether any rule beats not trading, given that the
            # best of them was chosen by looking.
            losses = -matrix          # SPA is stated in losses; lower is better
            spa = _mt.spa_test(_np.zeros(matrix.shape[0]), losses,
                               n_boot=500, seed=0)
            out["spa"] = spa.as_dict()
            out["spa"]["benchmark"] = "no position (zero return)"
            if spa.best_model is not None and spa.best_model < len(rule_ids):
                out["spa"]["best_rule"] = rule_ids[spa.best_model]
        else:
            out["pbo"] = None
            out["spa"] = None
            out["cross_rule_note"] = (
                f"PBO and SPA compare rules against each other and need >= 2 "
                f"rules with >= 16 overlapping days of resolved bets; "
                f"currently {len(rule_ids)} rule(s), {len(dates)} shared days."
            )
        return out

    def _stage_scorecard() -> None:
        """Write the forecast scorecard where a human will actually see it.

        track_record has always computed the right numbers -- Brier skill vs
        climatology, ECE, realized edge over the market's own implied
        probability. It was reachable only from the MCP server, from
        paper_bets' own summary, and from graphics.py (which never ran,
        because matplotlib was not installed). So the fact that the system was
        scoring -1.42 skill vs climatology, and had placed zero bets in 66
        days, never surfaced anywhere Ian would read it.
        """
        from .lake import Lake
        from .scoring.track_record import (
            headline_edge, score_paper_bets_by_venue, score_predictions_split,
        )
        lake = Lake.open()
        try:
            conn = lake._ensure_open()
            preds = [dict(r) for r in conn.execute(
                "SELECT * FROM predictions WHERE actual_outcome IS NOT NULL "
                "AND actual_outcome != ''")]
            bets = [dict(r) for r in conn.execute(
                "SELECT b.*, r.final_outcome, r.final_pnl, r.holding_period_days "
                "  FROM paper_bets b "
                "  JOIN paper_bet_resolutions r ON r.bet_id = b.id")]
        finally:
            lake.close()

        split = score_predictions_split(preds)
        edge = headline_edge(bets)
        by_venue = score_paper_bets_by_venue(bets)
        search = _search_diagnostics(bets)

        scorecard = {
            "search_integrity": search,
            "predictions": {
                "self_graded": split["self_graded"]["skill"].as_dict(),
                "self_graded_caveat": split["self_graded"]["caveat"],
                "externally_resolved": split["externally_resolved"]["skill"].as_dict(),
            },
            "bets": {
                "headline_edge": edge,
                "by_venue": {k: v.as_dict() for k, v in by_venue.items()},
            },
        }
        out = Path(out_dir) / "scorecard.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")

        ext = split["externally_resolved"]["skill"]
        print(f"[scorecard] externally-resolved: n={ext.n_resolved} "
              f"brier_skill={ext.brier_skill_score} ece={ext.ece}")
        print(f"[scorecard] real-money edge: {edge['note']}")

    for label, fn in (
        ("embeddings", _stage_embeddings),
        ("graphics", _stage_graphics),
        ("maps", _stage_maps),
        ("ukraine-maps", _stage_ukraine_maps),
        ("cross-section", _stage_cross_section),
        ("integrity", _stage_integrity),
        ("signals", _stage_signals),
        ("radar", _stage_radar),
        ("claims", _stage_claims),
        # Runs after the other panel stages so its insert(0) puts Top Stories
        # at the very top of the page — it is the front-page experience.
        ("stories", _stage_stories),
        ("site-builder", _stage_site_builder),
        # Last: it reads what every other stage wrote.
        ("scorecard", _stage_scorecard),
    ):
        _run_stage(label, fn, stage_report)

    # Write the run report before anything else can fail. It is the only
    # artifact that says what actually ran, and it is most valuable precisely
    # on the runs that went wrong.
    failed_required = [
        name for name, e in stage_report.items()
        if e["status"] == "failed" and e["required"]
    ]
    run_report = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date": today.isoformat(),
        "stages": stage_report,
        "sections": {
            sid: {
                "state": st.state,
                "record_count": len(st.items),
                "new_count": len(st.new),
                "error_type": st.error_type,
                "error": st.error,
            }
            for sid, st in states.items()
        },
        "failed_required_stages": failed_required,
        "ok": not failed_required,
    }
    report_path = Path(out_dir) / "run_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(run_report, indent=2, default=str),
                           encoding="utf-8")
    for name, entry in sorted(stage_report.items()):
        if entry["status"] == "failed":
            level = "error" if entry["required"] else "warning"
            print(f"::{level}::stage {name} failed: "
                  f"{entry['error_type']}: {entry['error_message']}")

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
        except Exception as cx:
            print(f"[mirror] copy {src_path.name} failed: "
                  f"{type(cx).__name__}: {cx}")
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
    store.close()
    return page


def main() -> int:
    p = argparse.ArgumentParser(description="Generate today's WORLDSCOPE briefing")
    p.add_argument("--section", action="append", help="restrict to specific section id(s)")
    p.add_argument("--out", default="dist", help="output directory (default: dist)")
    p.add_argument("--allow-stage-failure", action="store_true",
                   help="exit 0 even if a required stage failed (debugging only)")
    args = p.parse_args()
    run(args.section, out_dir=args.out)

    # The brief is written either way -- a degraded brief beats no brief. But
    # the RUN fails, so a required stage cannot go dark behind a green check
    # the way graphics, maps and embeddings did for three months.
    report_path = Path(args.out) / "run_report.json"
    if args.allow_stage_failure or not report_path.exists():
        return 0
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    failed = report.get("failed_required_stages") or []
    if failed:
        print(f"::error::required stage(s) failed: {', '.join(failed)}. "
              f"The brief was still written; see dist/run_report.json.")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
