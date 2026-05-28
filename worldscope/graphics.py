"""
graphics.py — daily-infographic engine for Worldscope.

Reads the lake (SQLite + lake/sections/<section-id>/<date>/ artifacts) and
emits a five-panel PNG suite under figures/daily/<YYYY-MM-DD>/.

Design rules:
- Every renderer is defensive. If data is missing or malformed, the method
  draws a labelled placeholder rather than raising.
- No upstream API calls. Pure lake reads.
- Stdlib + matplotlib + pandas + numpy + PIL only.
- Heritage palette (Carolina + BSE teal + old gold + crimson). No em-dashes
  anywhere in text. Dollar signs in mpl text always escaped: r"\\$1,000".

Usage:
    from worldscope.graphics import DailyGraphics
    DailyGraphics().render_all()                 # today, default paths
    DailyGraphics().render_all("2026-05-27")     # specific date
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "lake" / "db" / "worldscope.sqlite"
DEFAULT_SECTIONS_ROOT = REPO_ROOT / "lake" / "sections"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "figures" / "daily"

# ---------------------------------------------------------------------------
# Heritage palette
# ---------------------------------------------------------------------------

CAROLINA_NAVY = "#13294B"
OLD_GOLD = "#D4A017"
BSE_TEAL = "#1A8A87"
INDIANA_CRIMSON = "#990000"
CAROLINA_BLUE = "#4B9CD3"
PARCHMENT = "#FAF8F3"
SLATE = "#4E5667"
MIST = "#E8E2D5"

CATEGORY_COLORS = [
    CAROLINA_NAVY, OLD_GOLD, BSE_TEAL, INDIANA_CRIMSON,
    CAROLINA_BLUE, SLATE, "#6A5D3E", "#7A3E3E",
]

TIER_COLORS = {
    "primary_document": CAROLINA_NAVY,
    "mainstream_independent": CAROLINA_BLUE,
    "mainstream_partisan_left": BSE_TEAL,
    "mainstream_partisan_right": OLD_GOLD,
    "state_controlled": INDIANA_CRIMSON,
    "aggregator": SLATE,
    "community": "#6A5D3E",
    "speculative_blog": "#7A3E3E",
    "prediction_market": OLD_GOLD,
}

# ---------------------------------------------------------------------------
# Font registration (best-effort; matplotlib falls back if missing)
# ---------------------------------------------------------------------------

_GEORGIA_CANDIDATES = [
    "/Library/Fonts/Georgia.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Georgia.ttf",
]
_HELVETICA_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
]

SERIF_FAMILY = "serif"
SANS_FAMILY = "sans-serif"


def _register_fonts() -> None:
    """Best-effort font registration. Silently skip if unavailable."""
    global SERIF_FAMILY, SANS_FAMILY
    for path in _GEORGIA_CANDIDATES:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                SERIF_FAMILY = "Georgia"
                break
            except Exception:
                continue
    for path in _HELVETICA_CANDIDATES:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                SANS_FAMILY = "Helvetica"
                break
            except Exception:
                continue


_register_fonts()

plt.rcParams.update({
    "axes.edgecolor": SLATE,
    "axes.labelcolor": SLATE,
    "xtick.color": SLATE,
    "ytick.color": SLATE,
    "axes.titlecolor": CAROLINA_NAVY,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": MIST,
    "grid.linewidth": 0.6,
    "savefig.facecolor": PARCHMENT,
    "figure.facecolor": PARCHMENT,
    "axes.facecolor": PARCHMENT,
})


# ---------------------------------------------------------------------------
# Lake-reader helpers
# ---------------------------------------------------------------------------

def _today_iso() -> str:
    return date.today().isoformat()


def _safe_iso(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


@dataclass
class LakeView:
    """Read-only view of the lake the graphics engine needs."""

    db_path: Path
    sections_root: Path

    def connect(self) -> Optional[sqlite3.Connection]:
        if not self.db_path.exists():
            return None
        return sqlite3.connect(str(self.db_path))

    def section_dates(self) -> list[str]:
        if not self.sections_root.exists():
            return []
        dates = set()
        for sec_dir in self.sections_root.iterdir():
            if not sec_dir.is_dir():
                continue
            for d in sec_dir.iterdir():
                if d.is_dir() and len(d.name) == 10 and d.name.count("-") == 2:
                    dates.add(d.name)
        return sorted(dates)

    def section_dirs_for(self, date_iso: str) -> list[Path]:
        if not self.sections_root.exists():
            return []
        out = []
        for sec_dir in sorted(self.sections_root.iterdir()):
            if not sec_dir.is_dir():
                continue
            cand = sec_dir / date_iso
            if cand.is_dir():
                out.append(cand)
        return out

    def section_volume(self, date_iso: str) -> list[tuple[str, int, str]]:
        """Return [(section_id, item_count, source_tier), ...] for the date.

        First tries the SQLite records table; falls back to counting lines in
        raw.jsonl under lake/sections/<id>/<date>/.
        """
        rows: dict[str, tuple[int, str]] = {}
        # Tier lookup from sources table
        tier_by_section: dict[str, str] = {}
        con = self.connect()
        if con is not None:
            try:
                cur = con.cursor()
                cur.execute(
                    "SELECT section_id, COUNT(*), MIN(source_id) "
                    "FROM records WHERE record_date = ? GROUP BY section_id",
                    (date_iso,),
                )
                section_to_source: dict[str, str] = {}
                for sid, n, source_id in cur.fetchall():
                    rows[sid] = (int(n), "primary_document")
                    section_to_source[sid] = source_id
                if section_to_source:
                    cur.execute("SELECT id, tier FROM sources")
                    src_tier = {sid: tier for sid, tier in cur.fetchall()}
                    for sid, source_id in section_to_source.items():
                        if source_id in src_tier:
                            tier_by_section[sid] = src_tier[source_id]
            except sqlite3.Error:
                pass
            finally:
                con.close()
        # Fall back / supplement with raw.jsonl line counts
        for sec_dir in self.section_dirs_for(date_iso):
            sid = sec_dir.parent.name
            raw = sec_dir / "raw.jsonl"
            if not raw.exists():
                continue
            try:
                with raw.open("r", encoding="utf-8") as fh:
                    n_lines = sum(1 for line in fh if line.strip())
            except OSError:
                continue
            if sid not in rows:
                rows[sid] = (n_lines, tier_by_section.get(sid, "aggregator"))
            else:
                # Prefer the larger of the two counts
                existing, tier = rows[sid]
                rows[sid] = (max(existing, n_lines), tier_by_section.get(sid, tier))
        return [(sid, n, tier) for sid, (n, tier) in sorted(rows.items())]

    def markets_history(self, date_iso: str, days: int = 30) -> dict[str, list[tuple[str, float]]]:
        """Return {asset_name: [(date_iso, close), ...]} for the markets_global section.

        Combines SQLite records (extra_json with 'close') with raw.jsonl files.
        Sorted ascending by date. Limited to last `days` calendar days from date_iso.
        """
        target_end = _safe_iso(date_iso) or _today_iso()
        try:
            target_start = (date.fromisoformat(target_end) - timedelta(days=days)).isoformat()
        except ValueError:
            target_start = target_end
        series: dict[str, dict[str, float]] = {}

        # SQLite path
        con = self.connect()
        if con is not None:
            try:
                cur = con.cursor()
                cur.execute(
                    "SELECT record_date, original_text, extra_json "
                    "FROM records WHERE section_id = 'markets_global' "
                    "AND record_date >= ? AND record_date <= ?",
                    (target_start, target_end),
                )
                for rdate, otext, extra in cur.fetchall():
                    name, close = _extract_market(otext, extra)
                    if name and close is not None and rdate:
                        series.setdefault(name, {})[rdate] = close
            except sqlite3.Error:
                pass
            finally:
                con.close()

        # raw.jsonl fallback / supplement
        markets_root = self.sections_root / "markets_global"
        if markets_root.exists():
            for d_dir in sorted(markets_root.iterdir()):
                if not d_dir.is_dir():
                    continue
                d_iso = d_dir.name
                if d_iso < target_start or d_iso > target_end:
                    continue
                raw = d_dir / "raw.jsonl"
                if not raw.exists():
                    continue
                try:
                    with raw.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                row = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            extra = row.get("extra") or {}
                            name = extra.get("name") or row.get("title", "").split(":")[0].strip()
                            close = extra.get("close")
                            if close is None:
                                continue
                            try:
                                close = float(close)
                            except (TypeError, ValueError):
                                continue
                            if name:
                                series.setdefault(name, {})[d_iso] = close
                except OSError:
                    continue

        return {
            name: sorted(d.items())
            for name, d in series.items()
        }

    def paper_bet_pnl(self) -> list[tuple[str, float]]:
        """Return [(date_iso, cumulative_pnl), ...] over all available marks + resolutions."""
        con = self.connect()
        if con is None:
            return []
        try:
            cur = con.cursor()
            # Daily unrealized PnL deltas
            cur.execute(
                "SELECT mark_date, SUM(COALESCE(delta_vs_prev, unrealized_pnl)) "
                "FROM paper_bet_marks GROUP BY mark_date ORDER BY mark_date"
            )
            daily = {row[0]: float(row[1] or 0.0) for row in cur.fetchall()}
            # Resolutions
            cur.execute("SELECT resolved_at, SUM(final_pnl) FROM paper_bet_resolutions "
                        "GROUP BY resolved_at")
            for r_at, pnl in cur.fetchall():
                d = _safe_iso(r_at)
                if d:
                    daily[d] = daily.get(d, 0.0) + float(pnl or 0.0)
        except sqlite3.Error:
            return []
        finally:
            con.close()
        if not daily:
            return []
        cum = 0.0
        out = []
        for d in sorted(daily):
            cum += daily[d]
            out.append((d, cum))
        return out

    def resolved_bet_count(self) -> int:
        con = self.connect()
        if con is None:
            return 0
        try:
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM paper_bet_resolutions")
            return int(cur.fetchone()[0])
        except sqlite3.Error:
            return 0
        finally:
            con.close()

    def anomalies(self, end_iso: str, days: int = 30) -> list[tuple[str, str]]:
        """Return [(date_iso, category), ...] for the last `days` ending at end_iso."""
        con = self.connect()
        if con is None:
            return []
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT detected_at, category FROM anomalies ORDER BY detected_at"
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        finally:
            con.close()
        try:
            end_d = date.fromisoformat(end_iso)
        except ValueError:
            end_d = date.today()
        start_d = end_d - timedelta(days=days)
        out = []
        for ts, cat in rows:
            d_iso = _safe_iso(ts)
            if not d_iso:
                continue
            try:
                d_obj = date.fromisoformat(d_iso)
            except ValueError:
                continue
            if start_d <= d_obj <= end_d:
                out.append((d_iso, cat or "uncategorised"))
        return out

    def top_anomalies(self, date_iso: str, n: int = 5) -> list[dict]:
        """Top-N anomalies for the given date by |z_score|. Falls back across sections'
        structured.json if SQLite is empty."""
        con = self.connect()
        out: list[dict] = []
        if con is not None:
            try:
                cur = con.cursor()
                cur.execute(
                    "SELECT category, z_score, description, section_id "
                    "FROM anomalies "
                    "WHERE substr(detected_at,1,10) = ? "
                    "ORDER BY ABS(COALESCE(z_score,0)) DESC LIMIT ?",
                    (date_iso, n),
                )
                for cat, z, desc, sec in cur.fetchall():
                    out.append({
                        "category": cat or "anomaly",
                        "z_score": float(z) if z is not None else None,
                        "description": desc or "",
                        "section": sec or "",
                    })
            except sqlite3.Error:
                pass
            finally:
                con.close()
        if out:
            return out
        # Fallback: scan structured.json sidecars
        for sec_dir in self.section_dirs_for(date_iso):
            sj = sec_dir / "structured.json"
            if not sj.exists():
                continue
            try:
                data = json.loads(sj.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for a in data.get("anomalies", []) or []:
                out.append({
                    "category": a.get("category", "anomaly"),
                    "z_score": a.get("z_score"),
                    "description": a.get("description", ""),
                    "section": sec_dir.parent.name,
                })
        out.sort(key=lambda x: abs(x.get("z_score") or 0.0), reverse=True)
        return out[:n]

    def headline_number(self, date_iso: str) -> tuple[str, str, str]:
        """Return (number_string, label, context) for the day's headline metric.

        Priority: biggest |z| anomaly, else largest single-section item count today.
        """
        top = self.top_anomalies(date_iso, n=1)
        if top and top[0].get("z_score") is not None:
            a = top[0]
            z = a["z_score"]
            sign = "+" if z >= 0 else "-"
            return (
                f"{sign}{abs(z):.1f}σ",  # sigma
                a["category"][:32],
                a["description"][:80] or "anomaly flagged",
            )
        vols = self.section_volume(date_iso)
        if vols:
            sid, n, _ = max(vols, key=lambda r: r[1])
            return (str(n), sid.replace("_", " "), "items ingested today")
        return ("0", "no signal", "lake quiet")


def _extract_market(original_text: Optional[str], extra_json: Optional[str]) -> tuple[Optional[str], Optional[float]]:
    """Pull (name, close) from a markets_global record. Tolerant of missing fields."""
    name = None
    close = None
    if extra_json:
        try:
            extra = json.loads(extra_json)
        except json.JSONDecodeError:
            extra = {}
        name = extra.get("name") or extra.get("symbol")
        c = extra.get("close")
        if c is not None:
            try:
                close = float(c)
            except (TypeError, ValueError):
                close = None
    if name is None and original_text:
        # Format: "[asset_class] Name: close"
        if ":" in original_text:
            left, _, right = original_text.partition(":")
            name = left.split("]")[-1].strip() or None
            if close is None:
                try:
                    close = float(right.strip().split()[0])
                except (ValueError, IndexError):
                    close = None
    return name, close


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _placeholder(ax, message: str, *, title: Optional[str] = None) -> None:
    """Render a labelled placeholder when a panel has no data."""
    ax.clear()
    ax.set_facecolor(PARCHMENT)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    if title:
        ax.set_title(title, fontsize=12, family=SERIF_FAMILY, color=CAROLINA_NAVY, loc="left")
    ax.text(
        0.5, 0.5, message,
        ha="center", va="center",
        fontsize=11, family=SANS_FAMILY, color=SLATE,
        wrap=True,
    )


def _style_caption(fig: Figure, text: str) -> None:
    fig.text(
        0.5, 0.012, text,
        ha="center", va="bottom",
        fontsize=8, family=SANS_FAMILY, color=SLATE,
    )


def _human_int(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _format_money(x: float) -> str:
    # Escape $ before digits so matplotlib does not mathtext-parse the result.
    if abs(x) >= 1000:
        return rf"\${x:,.0f}"
    return rf"\${x:,.2f}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class DailyGraphics:
    """Render the daily-infographic suite from the lake.

    Public entry points (all return Path objects):
        render_all(date_iso=None) -> dict[name, Path]
        render_summary_card(date_iso)
        render_markets_sparklines(date_iso)
        render_section_volume(date_iso)
        render_paper_bet_scorecard(date_iso)
        render_anomaly_density(date_iso)
    """

    def __init__(
        self,
        lake_db_path: Optional[Path] = None,
        lake_sections_root: Optional[Path] = None,
        output_root: Optional[Path] = None,
    ) -> None:
        self.db_path = Path(lake_db_path) if lake_db_path else DEFAULT_DB
        self.sections_root = Path(lake_sections_root) if lake_sections_root else DEFAULT_SECTIONS_ROOT
        self.output_root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
        self.lake = LakeView(db_path=self.db_path, sections_root=self.sections_root)

    # ---- entry points ----------------------------------------------------

    def render_all(self, date_iso: Optional[str] = None) -> dict[str, Path]:
        date_iso = date_iso or _today_iso()
        out_dir = self.output_root / date_iso
        out_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, Path] = {}
        results["summary_card"] = self.render_summary_card(date_iso)
        results["markets_sparklines"] = self.render_markets_sparklines(date_iso)
        results["section_volume"] = self.render_section_volume(date_iso)
        results["paper_bet_scorecard"] = self.render_paper_bet_scorecard(date_iso)
        results["anomaly_density"] = self.render_anomaly_density(date_iso)
        return results

    # ---- summary card ----------------------------------------------------

    def render_summary_card(self, date_iso: str) -> Path:
        out_dir = self.output_root / date_iso
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "summary_card.png"

        fig = plt.figure(figsize=(12, 6.3), dpi=100)
        gs = fig.add_gridspec(2, 2, hspace=0.55, wspace=0.30,
                              left=0.06, right=0.96, top=0.80, bottom=0.10)
        ax_num = fig.add_subplot(gs[0, 0])
        ax_anom = fig.add_subplot(gs[0, 1])
        ax_pnl = fig.add_subplot(gs[1, 0])
        ax_vol = fig.add_subplot(gs[1, 1])

        try:
            self._panel_headline(ax_num, date_iso)
        except Exception as exc:
            _placeholder(ax_num, f"headline unavailable: {type(exc).__name__}",
                         title="Today's signal")

        try:
            self._panel_top_anomalies(ax_anom, date_iso)
        except Exception as exc:
            _placeholder(ax_anom, f"anomalies unavailable: {type(exc).__name__}",
                         title="Top anomalies")

        try:
            self._panel_pnl_sparkline(ax_pnl)
        except Exception as exc:
            _placeholder(ax_pnl, f"P&L unavailable: {type(exc).__name__}",
                         title="Paper-bet P&L, 30 day")

        try:
            self._panel_section_volume_mini(ax_vol, date_iso)
        except Exception as exc:
            _placeholder(ax_vol, f"volume unavailable: {type(exc).__name__}",
                         title="Section volume, today")

        # Title block. Use fig.text rather than suptitle so we can place the
        # subtitle below the title without overlap from descenders.
        fig.text(
            0.06, 0.93,
            f"Worldscope daily brief, {date_iso}",
            fontsize=20, family=SERIF_FAMILY, color=CAROLINA_NAVY,
            fontweight="bold", ha="left", va="top",
        )
        fig.text(
            0.06, 0.86,
            "Data drawn from the Worldscope lake. Per-panel sources listed below.",
            fontsize=9, family=SANS_FAMILY, color=SLATE,
            ha="left", va="top",
        )
        _style_caption(fig, f"Worldscope, ianhelfrich.com. Lake snapshot {date_iso}.")
        fig.savefig(path, dpi=100, facecolor=PARCHMENT, bbox_inches=None)
        plt.close(fig)
        return path

    def _panel_headline(self, ax, date_iso: str) -> None:
        number, label, context = self.lake.headline_number(date_iso)
        ax.clear()
        ax.set_facecolor(PARCHMENT)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("Today's signal", fontsize=12, family=SERIF_FAMILY,
                     color=CAROLINA_NAVY, loc="left")
        # Sans-serif lining figures so multi-digit numbers don't render
        # with Georgia's old-style descenders (looks like subscripts).
        ax.text(0.02, 0.62, number, fontsize=56, family=SANS_FAMILY,
                color=OLD_GOLD, fontweight="bold", va="center")
        ax.text(0.02, 0.22, label, fontsize=14, family=SANS_FAMILY,
                color=CAROLINA_NAVY, va="center")
        ax.text(0.02, 0.06, context, fontsize=9, family=SANS_FAMILY,
                color=SLATE, va="center")

    def _panel_top_anomalies(self, ax, date_iso: str) -> None:
        anoms = self.lake.top_anomalies(date_iso, n=5)
        ax.clear()
        ax.set_facecolor(PARCHMENT)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("Top anomalies (by |z|)", fontsize=12,
                     family=SERIF_FAMILY, color=CAROLINA_NAVY, loc="left")
        if not anoms:
            ax.text(0.5, 0.5,
                    "No anomalies in the lake yet.\nDetectors warming up.",
                    ha="center", va="center", fontsize=11,
                    family=SANS_FAMILY, color=SLATE)
            return
        # Only show a z-score column if at least one anomaly actually has one.
        has_z = any(a.get("z_score") is not None for a in anoms)
        y = 0.88
        for i, a in enumerate(anoms, start=1):
            z = a.get("z_score")
            ax.text(0.02, y, f"{i}.", fontsize=10, family=SERIF_FAMILY,
                    color=CAROLINA_NAVY, va="top")
            label_x = 0.08
            if has_z:
                z_str = f"{z:+.1f}σ" if z is not None else ""
                color = INDIANA_CRIMSON if (z is not None and z < 0) else BSE_TEAL
                ax.text(0.07, y, z_str, fontsize=11, family=SANS_FAMILY,
                        color=color, fontweight="bold", va="top")
                label_x = 0.22
            # Build a readable label. If description is present and unique
            # vs category, prefer it (carries the actual context). Otherwise
            # fall back to category. Uniform categories like
            # "trade-beats-market" repeated five times read as broken; the
            # description holds the specific entity / event.
            category = a.get("category", "anomaly")
            description = (a.get("description") or "").strip()
            section = a.get("section", "")
            if description and description.lower() != category.lower():
                # First clause of description, capped so it fits the column.
                first_clause = re.split(r"[.;,]", description, maxsplit=1)[0]
                label = first_clause.strip()[:46]
            else:
                # No useful description: show category, but include section
                # context inline so the row isn't a clone of every other row
                # in the same section.
                sec_pretty = section.replace("_", " ").title() if section else ""
                label = f"{category} ({sec_pretty})" if sec_pretty else category
                label = label[:46]
            ax.text(label_x, y, label, fontsize=10, family=SANS_FAMILY,
                    color=CAROLINA_NAVY, va="top")
            if section and len(label) <= 36:
                # Only show the right-aligned section pill when there's room.
                ax.text(0.97, y, section.replace("_", " "), fontsize=8,
                        family=SANS_FAMILY, color=SLATE, va="top", ha="right")
            y -= 0.17

    def _panel_pnl_sparkline(self, ax) -> None:
        pnl = self.lake.paper_bet_pnl()
        ax.clear()
        ax.set_facecolor(PARCHMENT)
        ax.set_title("Paper-bet P&L, rolling 30 day", fontsize=12,
                     family=SERIF_FAMILY, color=CAROLINA_NAVY, loc="left")
        if not pnl:
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.text(0.5, 0.5,
                    "Scorecard accumulating.\nNo resolved bets yet.",
                    ha="center", va="center", fontsize=10,
                    family=SANS_FAMILY, color=SLATE)
            return
        cutoff = max(0, len(pnl) - 30)
        window = pnl[cutoff:]
        xs = np.arange(len(window))
        ys = np.array([p[1] for p in window], dtype=float)
        ax.plot(xs, ys, color=CAROLINA_NAVY, linewidth=2)
        ax.fill_between(xs, ys, 0,
                        where=(ys >= 0), color=BSE_TEAL, alpha=0.25, interpolate=True)
        ax.fill_between(xs, ys, 0,
                        where=(ys < 0), color=INDIANA_CRIMSON, alpha=0.25, interpolate=True)
        ax.set_xticks([])
        ax.tick_params(axis="y", labelsize=8)
        ax.axhline(0, color=SLATE, linewidth=0.5, alpha=0.6)
        end_val = ys[-1] if len(ys) else 0
        ax.text(0.98, 0.04, _format_money(end_val),
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=11, family=SERIF_FAMILY,
                color=BSE_TEAL if end_val >= 0 else INDIANA_CRIMSON,
                fontweight="bold")

    def _panel_section_volume_mini(self, ax, date_iso: str) -> None:
        vols = self.lake.section_volume(date_iso)
        ax.clear()
        ax.set_facecolor(PARCHMENT)
        ax.set_title("Section volume, today", fontsize=12,
                     family=SERIF_FAMILY, color=CAROLINA_NAVY, loc="left")
        if not vols:
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.text(0.5, 0.5, "No section data ingested today.",
                    ha="center", va="center", fontsize=10,
                    family=SANS_FAMILY, color=SLATE)
            return
        vols = sorted(vols, key=lambda r: r[1], reverse=True)[:8]
        labels = [v[0].replace("_", " ")[:18] for v in vols]
        counts = [v[1] for v in vols]
        colors = [TIER_COLORS.get(v[2], SLATE) for v in vols]
        ax.grid(axis="x", visible=False)
        ax.grid(axis="y", color=MIST, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
        ax.bar(range(len(vols)), counts, color=colors, edgecolor=CAROLINA_NAVY,
               linewidth=0.5, zorder=3)
        ax.set_xticks(range(len(vols)))
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8,
                           family=SANS_FAMILY)
        ax.tick_params(axis="y", labelsize=8)
        ax.set_ylabel("items", fontsize=9, family=SANS_FAMILY, color=SLATE)

    # ---- markets sparklines ---------------------------------------------

    def render_markets_sparklines(self, date_iso: str) -> Path:
        out_dir = self.output_root / date_iso
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "markets_sparklines.png"

        history = self.lake.markets_history(date_iso, days=30)
        # If the lake has no markets_global data at all, render a placeholder
        # but ensure 12 cells so the layout reads consistently.
        target_panels = 12
        names: list[str] = sorted(history.keys()) if history else []
        # Pad with empty placeholder cells up to 12 panels.
        panel_data: list[tuple[str, list[tuple[str, float]]]] = []
        for name in names:
            panel_data.append((name, history[name]))
        while len(panel_data) < target_panels:
            panel_data.append((f"slot {len(panel_data)+1}", []))
        panel_data = panel_data[:max(target_panels, len(panel_data))]

        n = len(panel_data)
        cols = 4
        rows = (n + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(12, max(3, 1.8 * rows)),
                                 dpi=100)
        axes_flat = np.array(axes).reshape(-1)

        for i, (name, series) in enumerate(panel_data):
            ax = axes_flat[i]
            ax.set_facecolor(PARCHMENT)
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(name[:18], fontsize=9, family=SANS_FAMILY,
                         color=CAROLINA_NAVY, loc="left", pad=2)
            if not series:
                ax.text(0.5, 0.5, "first-day data",
                        ha="center", va="center", fontsize=8,
                        family=SANS_FAMILY, color=SLATE)
                continue
            if len(series) == 1:
                _, c = series[0]
                ax.plot([0], [c], marker="o", color=OLD_GOLD, markersize=6)
                ax.text(0.5, 0.20, "first-day data",
                        transform=ax.transAxes, ha="center", va="center",
                        fontsize=7, family=SANS_FAMILY, color=SLATE)
                ax.text(0.98, 0.92, f"{c:,.2f}",
                        transform=ax.transAxes, ha="right", va="top",
                        fontsize=9, family=SERIF_FAMILY, color=CAROLINA_NAVY)
                continue
            xs = np.arange(len(series))
            ys = np.array([s[1] for s in series], dtype=float)
            ax.plot(xs, ys, color=CAROLINA_NAVY, linewidth=1.5)
            ax.scatter([xs[-1]], [ys[-1]], color=OLD_GOLD, s=18, zorder=3)
            # Arrow indicating today's direction
            direction = ys[-1] - ys[-2]
            arrow = "▲" if direction > 0 else ("▼" if direction < 0 else "→")
            arrow_color = BSE_TEAL if direction > 0 else (
                INDIANA_CRIMSON if direction < 0 else SLATE
            )
            ax.text(0.97, 0.88, arrow, transform=ax.transAxes, ha="right",
                    va="top", fontsize=12, color=arrow_color, fontweight="bold")
            ax.text(0.97, 0.10, f"{ys[-1]:,.2f}", transform=ax.transAxes,
                    ha="right", va="bottom", fontsize=9, family=SERIF_FAMILY,
                    color=CAROLINA_NAVY)

        # Hide any unused axes
        for j in range(len(panel_data), len(axes_flat)):
            axes_flat[j].set_visible(False)

        fig.suptitle(
            f"Markets, last 30 days (close), as of {date_iso}",
            fontsize=16, family=SERIF_FAMILY, color=CAROLINA_NAVY,
            x=0.06, ha="left", y=0.97, fontweight="bold",
        )
        _style_caption(
            fig,
            "Source: markets_global section (Stooq, CoinGecko, exchangerate.host). "
            "Daily close. Arrow shows today vs prior day.",
        )
        fig.tight_layout(rect=[0, 0.03, 1, 0.93])
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path

    # ---- section volume --------------------------------------------------

    def render_section_volume(self, date_iso: str) -> Path:
        out_dir = self.output_root / date_iso
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "section_volume.png"

        vols = self.lake.section_volume(date_iso)
        # Drop sections with zero items today — they crowd the chart without
        # carrying information. Stub / no-data sections are summarised in
        # the brief's source-health footer instead.
        vols = [v for v in vols if v[1] > 0]
        fig, ax = plt.subplots(figsize=(10, max(4, 0.42 * max(1, len(vols)) + 1.8)),
                               dpi=100)
        if not vols:
            _placeholder(ax,
                         "No sections ingested for this date.",
                         title=f"Section volume, {date_iso}")
            _style_caption(fig, "Source: Worldscope lake records table.")
            fig.savefig(path, dpi=100, facecolor=PARCHMENT)
            plt.close(fig)
            return path

        vols = sorted(vols, key=lambda r: r[1])
        labels = [v[0].replace("_", " ") for v in vols]
        counts = [v[1] for v in vols]
        tiers = [v[2] for v in vols]
        colors = [TIER_COLORS.get(t, SLATE) for t in tiers]

        y = np.arange(len(vols))
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=MIST, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
        ax.barh(y, counts, color=colors, edgecolor=CAROLINA_NAVY, linewidth=0.6,
                zorder=3)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=10, family=SANS_FAMILY)
        ax.set_xlabel("items ingested", fontsize=10, family=SANS_FAMILY, color=SLATE)
        ax.set_title(f"Section volume, {date_iso}",
                     fontsize=15, family=SERIF_FAMILY, color=CAROLINA_NAVY,
                     loc="left", pad=12)
        for i, c in enumerate(counts):
            ax.text(c, i, f"  {_human_int(c)}", va="center", fontsize=9,
                    family=SANS_FAMILY, color=CAROLINA_NAVY)

        # Tier legend
        used_tiers = sorted(set(tiers))
        from matplotlib.patches import Patch
        handles = [
            Patch(facecolor=TIER_COLORS.get(t, SLATE), edgecolor=CAROLINA_NAVY,
                  label=t.replace("_", " "))
            for t in used_tiers
        ]
        if handles:
            ax.legend(handles=handles, loc="lower right", fontsize=8,
                      frameon=False, title="source tier",
                      title_fontsize=9)

        _style_caption(
            fig,
            f"Source: Worldscope lake (records table + raw.jsonl), {date_iso}. "
            "Tier per sources.yaml.",
        )
        fig.tight_layout(rect=[0, 0.04, 1, 1])
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path

    # ---- paper-bet scorecard --------------------------------------------

    def render_paper_bet_scorecard(self, date_iso: str) -> Path:
        out_dir = self.output_root / date_iso
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "paper_bet_scorecard.png"

        resolved = self.lake.resolved_bet_count()
        pnl = self.lake.paper_bet_pnl()

        fig, ax = plt.subplots(figsize=(10, 5), dpi=100)
        if resolved < 5 or not pnl:
            _placeholder(
                ax,
                f"Scorecard accumulating.\n{resolved} resolved bet"
                f"{'s' if resolved != 1 else ''} so far; need 5 to score.",
                title=f"Paper-bet cumulative P&L, as of {date_iso}",
            )
            _style_caption(
                fig,
                "Source: Worldscope lake paper_bet_marks + paper_bet_resolutions.",
            )
            fig.savefig(path, dpi=100, facecolor=PARCHMENT)
            plt.close(fig)
            return path

        dates_arr = [date.fromisoformat(d) for d, _ in pnl]
        ys = np.array([v for _, v in pnl], dtype=float)
        ax.plot(dates_arr, ys, color=CAROLINA_NAVY, linewidth=2.0,
                marker="o", markersize=3, markerfacecolor=OLD_GOLD,
                markeredgecolor=CAROLINA_NAVY)
        ax.fill_between(dates_arr, ys, 0,
                        where=(ys >= 0), color=BSE_TEAL, alpha=0.20,
                        interpolate=True)
        ax.fill_between(dates_arr, ys, 0,
                        where=(ys < 0), color=INDIANA_CRIMSON, alpha=0.20,
                        interpolate=True)
        ax.axhline(0, color=SLATE, linewidth=0.7, alpha=0.6)
        ax.set_title(f"Paper-bet cumulative P&L, as of {date_iso}",
                     fontsize=15, family=SERIF_FAMILY, color=CAROLINA_NAVY,
                     loc="left", pad=12)
        ax.set_xlabel("date", fontsize=10, family=SANS_FAMILY, color=SLATE)
        ax.set_ylabel("cumulative P&L (USD)", fontsize=10, family=SANS_FAMILY,
                      color=SLATE)
        # Final-value annotation
        ax.annotate(
            _format_money(ys[-1]),
            xy=(dates_arr[-1], ys[-1]),
            xytext=(8, 0), textcoords="offset points",
            fontsize=12, family=SERIF_FAMILY,
            color=BSE_TEAL if ys[-1] >= 0 else INDIANA_CRIMSON,
            fontweight="bold", va="center",
        )
        fig.autofmt_xdate()
        _style_caption(
            fig,
            "Source: Worldscope lake paper_bet_marks + paper_bet_resolutions. "
            "Includes unrealized + realized P&L.",
        )
        fig.tight_layout(rect=[0, 0.04, 1, 1])
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path

    # ---- anomaly density -------------------------------------------------

    def render_anomaly_density(self, date_iso: str) -> Path:
        out_dir = self.output_root / date_iso
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "anomaly_density.png"

        records = self.lake.anomalies(date_iso, days=30)
        try:
            end_d = date.fromisoformat(date_iso)
        except ValueError:
            end_d = date.today()
        # Build a (date, category) count matrix
        days = [(end_d - timedelta(days=i)) for i in range(29, -1, -1)]
        day_keys = [d.isoformat() for d in days]
        categories: list[str] = []
        cat_seen: set[str] = set()
        for _, c in records:
            if c not in cat_seen:
                cat_seen.add(c)
                categories.append(c)
        if not categories:
            categories = ["no anomalies"]
        counts = {c: np.zeros(len(days), dtype=int) for c in categories}
        for d_iso, c in records:
            if d_iso in day_keys and c in counts:
                counts[c][day_keys.index(d_iso)] += 1

        # Available-history check
        all_dates = self.lake.section_dates()
        history_days = len(all_dates)

        fig, ax = plt.subplots(figsize=(11, 5), dpi=100)
        if not records:
            _placeholder(
                ax,
                "No anomalies detected in the last 30 days.\n"
                "Detectors are wired but quiet.",
                title=f"Anomaly density, 30 day window ending {date_iso}",
            )
            if history_days < 14:
                fig.text(
                    0.5, 0.06,
                    f"Warm-up period: lake has {history_days} day"
                    f"{'s' if history_days != 1 else ''} of history.",
                    ha="center", va="bottom", fontsize=9, color=SLATE,
                    family=SANS_FAMILY,
                )
            _style_caption(
                fig,
                "Source: Worldscope lake anomalies table.",
            )
            fig.savefig(path, dpi=100, facecolor=PARCHMENT)
            plt.close(fig)
            return path

        bottom = np.zeros(len(days), dtype=int)
        x = np.arange(len(days))
        ax.grid(axis="x", visible=False)
        ax.grid(axis="y", color=MIST, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
        for i, c in enumerate(categories):
            color = CATEGORY_COLORS[i % len(CATEGORY_COLORS)]
            ax.bar(x, counts[c], bottom=bottom, color=color,
                   edgecolor=CAROLINA_NAVY, linewidth=0.3, label=c, zorder=3)
            bottom = bottom + counts[c]

        ax.set_xticks(x[::3])
        ax.set_xticklabels([days[i].strftime("%m-%d") for i in range(0, len(days), 3)],
                           rotation=30, ha="right", fontsize=8, family=SANS_FAMILY)
        ax.set_ylabel("anomalies", fontsize=10, family=SANS_FAMILY, color=SLATE)
        ax.set_title(f"Anomaly density, 30 day window ending {date_iso}",
                     fontsize=15, family=SERIF_FAMILY, color=CAROLINA_NAVY,
                     loc="left", pad=12)
        ax.legend(fontsize=8, frameon=False, loc="upper left",
                  title="category", title_fontsize=9, ncol=2)

        if history_days < 14:
            ax.text(
                0.99, 0.97,
                f"Warm-up period ({history_days} day"
                f"{'s' if history_days != 1 else ''} of history).",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=9, family=SANS_FAMILY, color=INDIANA_CRIMSON,
                bbox=dict(boxstyle="round,pad=0.3", facecolor=PARCHMENT,
                          edgecolor=INDIANA_CRIMSON, linewidth=0.8),
            )

        _style_caption(
            fig,
            f"Source: Worldscope lake anomalies table, window "
            f"{day_keys[0]} to {day_keys[-1]}.",
        )
        fig.tight_layout(rect=[0, 0.04, 1, 1])
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path


__all__ = ["DailyGraphics"]
