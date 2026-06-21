"""worldscope.track_record_page — the public Forecast Track Record page.

Worldscope's headline promise is *calibrated, self-graded predictions*: the
``signals`` engine logs falsifiable "this stays salient" calls, the new
``foresight`` engine logs harder "this quiet key will emerge" calls, and the
lake **auto-grades** both from its own later records — accruing a real Brier /
calibration / skill record with no human in the loop (``scoring.track_record``).

Until now that record lived only in a SQLite table no visitor could see. A
reader had to take "calibrated predictions" on faith. This module turns the
ledger into a **first-class, static public page** (roadmap §6): every open
forecast with its resolution criteria and deadline, every resolved one with the
verdict, the headline skill metrics, the reliability table, and — the part that
makes it honest — a **head-to-head breakdown by engine** so Signals and
Foresight are scored against each other and against a climatology baseline.

Why this matters even before much has resolved
----------------------------------------------
A forecasting system earns trust two ways, and this page shows both:
  1. **Commitment** — the *open* ledger is a public, falsifiable, time-stamped
     promise made *before* the outcome is known. That is verifiable today, with
     zero resolved calls, and is exactly what a credible forecaster publishes.
  2. **Calibration** — as calls mature, the skill metrics and reliability table
     fill in, and the by-engine table shows which engine is actually pulling its
     weight.

Design constraints, matched to the rest of the codebase
-------------------------------------------------------
  * Pure, offline, stdlib-only core (plus ``scoring.track_record``, itself
    stdlib). ``build_body`` / ``render_panel`` take plain row-dicts and return
    HTML strings, so the whole thing is unit-testable with no DB and no chrome.
  * The page **chrome** (nav, head, footer) is supplied by
    ``site_builder._wrap`` — this module builds only the body, which keeps it
    free of any import cycle and matches the section-page separation.
  * Build-time / static: the page is rendered into ``dist/`` during the brief;
    a visitor downloads pre-rendered HTML and triggers zero model calls.

Run standalone against the local lake::

    python -m worldscope.track_record_page                 # print headline summary
    python -m worldscope.track_record_page --write dist    # render dist/track-record.html
"""
from __future__ import annotations

import argparse
import html as _html
import re
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from .scoring import track_record as tr

PAGE_PATH = "track-record.html"

# Friendly names for the prediction-engine `method` tags. Anything unknown falls
# back to a tidied version of the raw tag, so a new engine still renders.
_METHOD_LABELS = {
    "signal-fusion-v1": "Signals · cross-source convergence",
    "lead-lag-foresight-v1": "Foresight · lead/lag early warning",
}

# Pull a readable subject out of a resolution-criteria string. signals writes
# "… key 'iran' …"; foresight writes "… follower 'ruble' …".
_SUBJECT_RE = re.compile(r"(?:key|follower) '([^']+)'")


# ============================================================================
# Data shaping (pure)
# ============================================================================

def is_resolved(row: dict) -> bool:
    a = row.get("actual_outcome")
    return a is not None and str(a).strip() != ""


def partition(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Split into (resolved, pending)."""
    resolved, pending = [], []
    for r in rows:
        (resolved if is_resolved(r) else pending).append(r)
    return resolved, pending


def method_label(method: Optional[str]) -> str:
    if not method:
        return "Other"
    return _METHOD_LABELS.get(method, method.replace("-", " ").replace("_", " ").title())


def subject_of(row: dict) -> str:
    """Best human label for a prediction's subject."""
    m = _SUBJECT_RE.search(row.get("resolution_criteria") or "")
    if m:
        return m.group(1)
    inds = row.get("indicators_used") or row.get("indicators_used_json")
    if isinstance(inds, str):
        inds = inds.strip()
        if inds.startswith("["):
            import json
            try:
                inds = json.loads(inds)
            except Exception:
                inds = []
    if isinstance(inds, list) and inds:
        return str(inds[0])
    return (row.get("section_id") or "—")


def hit_of(row: dict) -> Optional[bool]:
    """Was a resolved prediction correct? None if unresolved."""
    if not is_resolved(row):
        return None
    return tr._norm(row.get("predicted_outcome")) == tr._norm(row.get("actual_outcome"))


def by_method_summary(rows: list[dict]) -> list[dict]:
    """Per-engine rollup: counts + skill over resolved calls, sorted by volume."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("method") or "other", []).append(r)
    out: list[dict] = []
    for method, grp in groups.items():
        resolved, pending = partition(grp)
        out.append({
            "method": method,
            "label": method_label(method),
            "total": len(grp),
            "pending": len(pending),
            "resolved": len(resolved),
            "skill": tr.score_predictions(grp),
        })
    out.sort(key=lambda d: (-d["total"], d["label"]))
    return out


# ============================================================================
# Formatting helpers (pure)
# ============================================================================

def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _f3(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.3f}"


def _signed(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:+.3f}"


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


# ============================================================================
# Body fragments (pure → HTML)
# ============================================================================

def _metric_card(label: str, value: str, note: str = "") -> str:
    note_html = (f'<div class="font-sans text-xs text-slate mt-1">{_esc(note)}</div>'
                 if note else "")
    return (
        '<div class="border border-mist rounded-lg p-4 bg-white/40">'
        f'<div class="font-sans text-xs uppercase tracking-wide text-slate">{_esc(label)}</div>'
        f'<div class="text-2xl font-semibold text-navy mt-1">{_esc(value)}</div>'
        f'{note_html}</div>'
    )


def _render_headline(skill: tr.PredictionSkill, n_total: int, n_pending: int) -> str:
    if skill.n_resolved == 0:
        cards = (
            _metric_card("Open forecasts", str(n_pending),
                         "falsifiable calls awaiting their deadline")
            + _metric_card("Resolved", "0", "skill scores appear as calls mature")
            + _metric_card("Total logged", str(n_total), "every call is time-stamped")
        )
        return (
            '<div class="grid grid-cols-1 sm:grid-cols-3 gap-3">' + cards + '</div>'
            '<p class="font-sans text-sm text-slate mt-4">No forecast has reached its '
            'resolution date yet. The open ledger below is the verifiable part today: '
            'each row is a public, time-stamped, falsifiable commitment made <em>before</em> '
            'the outcome is known. Brier, skill, and calibration fill in automatically as '
            'the lake grades these against its own later records.</p>'
        )
    bss = skill.brier_skill_score
    bss_note = ("beats the climatology baseline" if (bss or 0) > 0
                else "not yet beating climatology")
    oc = skill.overconfidence
    oc_note = ("over-confident" if (oc or 0) > 0.02
               else "under-confident" if (oc or 0) < -0.02 else "well-calibrated")
    cards = (
        _metric_card("Resolved", str(skill.n_resolved), f"of {n_total} logged")
        + _metric_card("Brier score", _f3(skill.brier), "lower is better · 0 = perfect")
        + _metric_card("Brier skill", _signed(bss), bss_note)
        + _metric_card("Accuracy", _pct(skill.accuracy), "share that came true")
        + _metric_card("Calibration error", _f3(skill.ece), "mean |predicted − observed|")
        + _metric_card("Confidence gap", _signed(oc), oc_note)
    )
    return '<div class="grid grid-cols-2 sm:grid-cols-3 gap-3">' + cards + '</div>'


def _render_method_table(summaries: list[dict]) -> str:
    if not summaries:
        return ""
    rows = []
    for s in summaries:
        sk = s["skill"]
        rows.append(
            "<tr>"
            f'<td class="py-2 pr-4">{_esc(s["label"])}</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{s["total"]}</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{s["pending"]}</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{s["resolved"]}</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{_f3(sk.brier)}</td>'
            f'<td class="py-2 text-right tabular-nums">{_signed(sk.brier_skill_score)}</td>'
            "</tr>"
        )
    return (
        '<h2 class="mt-10 mb-3 pb-2 border-b border-mist">By engine</h2>'
        '<p class="font-sans text-sm text-slate mb-3">Each engine logs a different '
        'kind of call. <strong>Signals</strong> bets an already-salient key '
        '<em>persists</em>; <strong>Foresight</strong> bets a currently-quiet key '
        '<em>emerges</em> because something that leads it just fired — a harder, '
        'lower-base-rate forecast. Scored head-to-head below.</p>'
        '<div class="overflow-x-auto"><table class="w-full text-sm font-sans">'
        '<thead><tr class="text-slate border-b border-mist">'
        '<th class="text-left font-medium py-2 pr-4">Engine</th>'
        '<th class="text-right font-medium py-2 pr-4">Logged</th>'
        '<th class="text-right font-medium py-2 pr-4">Open</th>'
        '<th class="text-right font-medium py-2 pr-4">Resolved</th>'
        '<th class="text-right font-medium py-2 pr-4">Brier</th>'
        '<th class="text-right font-medium py-2">Skill</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _render_calibration(skill: tr.PredictionSkill) -> str:
    if not skill.bins:
        return ""
    rows = []
    for b in skill.bins:
        gap = b.observed_freq - b.mean_predicted
        rows.append(
            "<tr>"
            f'<td class="py-2 pr-4 tabular-nums">{int(b.lo*100)}–{int(b.hi*100)}%</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{b.n}</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{_pct(b.mean_predicted)}</td>'
            f'<td class="py-2 pr-4 text-right tabular-nums">{_pct(b.observed_freq)}</td>'
            f'<td class="py-2 text-right tabular-nums">{_signed(gap)}</td>'
            "</tr>"
        )
    return (
        '<h2 class="mt-10 mb-3 pb-2 border-b border-mist">Reliability</h2>'
        '<p class="font-sans text-sm text-slate mb-3">For calls in each confidence '
        'band, how often did they actually come true? A perfectly-calibrated '
        'forecaster sits on the diagonal (predicted ≈ observed).</p>'
        '<div class="overflow-x-auto"><table class="w-full text-sm font-sans">'
        '<thead><tr class="text-slate border-b border-mist">'
        '<th class="text-left font-medium py-2 pr-4">Confidence band</th>'
        '<th class="text-right font-medium py-2 pr-4">Calls</th>'
        '<th class="text-right font-medium py-2 pr-4">Mean predicted</th>'
        '<th class="text-right font-medium py-2 pr-4">Observed</th>'
        '<th class="text-right font-medium py-2">Gap</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _ledger_row(row: dict, *, resolved: bool) -> str:
    subj = _esc(subject_of(row)[:60])
    conf = _pct(_safe_float(row.get("confidence")))
    method = _esc(method_label(row.get("method")))
    made = _esc(str(row.get("made_at") or "")[:10])
    if resolved:
        hit = hit_of(row)
        badge = ('<span class="text-emerald-700 font-semibold">✓ hit</span>' if hit
                 else '<span class="text-rose-700 font-semibold">✗ miss</span>')
        when = _esc(str(row.get("resolved_at") or "")[:10])
        outcome = _esc(str(row.get("actual_outcome") or "")[:12])
        return (
            '<li class="py-2 border-b border-mist/60">'
            f'<span class="font-semibold text-navy">{subj}</span> '
            f'<span class="font-sans text-xs text-slate">· predicted '
            f'{_esc(str(row.get("predicted_outcome") or "YES"))} @ {conf} · '
            f'resolved {outcome} {badge} · {when} · {method}</span>'
            '</li>'
        )
    target = _esc(str(row.get("target_date") or "")[:10])
    return (
        '<li class="py-2 border-b border-mist/60">'
        f'<span class="font-semibold text-navy">{subj}</span> '
        f'<span class="font-sans text-xs text-slate">· {conf} confidence · '
        f'by {target} · logged {made} · {method}</span>'
        '</li>'
    )


def _safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _render_ledger(pending: list[dict], resolved: list[dict],
                   *, max_open: int = 50, max_resolved: int = 50) -> str:
    # Open: soonest deadline first (the next promises to come due).
    pend_sorted = sorted(pending, key=lambda r: str(r.get("target_date") or "9999"))
    # Resolved: most recently settled first.
    res_sorted = sorted(resolved, key=lambda r: str(r.get("resolved_at") or ""), reverse=True)

    parts = ['<h2 class="mt-10 mb-3 pb-2 border-b border-mist">Open forecasts '
             f'<span class="font-sans text-sm text-slate font-normal">· {len(pending)} '
             'awaiting resolution</span></h2>']
    if pend_sorted:
        parts.append(
            '<ul class="text-sm">'
            + "".join(_ledger_row(r, resolved=False) for r in pend_sorted[:max_open])
            + '</ul>'
        )
        if len(pend_sorted) > max_open:
            parts.append(f'<p class="font-sans text-xs text-slate mt-2">'
                         f'+{len(pend_sorted) - max_open} more open</p>')
    else:
        parts.append('<p class="font-sans text-sm text-slate">No open forecasts.</p>')

    parts.append('<h2 class="mt-10 mb-3 pb-2 border-b border-mist">Recently resolved '
                 f'<span class="font-sans text-sm text-slate font-normal">· {len(resolved)} '
                 'graded</span></h2>')
    if res_sorted:
        parts.append(
            '<ul class="text-sm">'
            + "".join(_ledger_row(r, resolved=True) for r in res_sorted[:max_resolved])
            + '</ul>'
        )
    else:
        parts.append('<p class="font-sans text-sm text-slate">Nothing has resolved yet — '
                     'check back as the open calls reach their deadlines.</p>')
    return "".join(parts)


# ============================================================================
# Page body + brief panel (pure)
# ============================================================================

def build_body(rows: list[dict], *, today: Optional[date] = None) -> str:
    """Assemble the full Forecast Track Record page body (no chrome)."""
    today = today or date.today()
    resolved, pending = partition(rows)
    overall = tr.score_predictions(rows)
    summaries = by_method_summary(rows)

    intro = (
        '<h1>Forecast Track Record</h1>'
        '<p class="font-sans text-sm text-slate mt-2 mb-7">Every probabilistic call '
        "WORLDSCOPE's engines make is logged here with its resolution criteria and "
        'deadline, then <strong>auto-graded from the lake\'s own later records</strong> — '
        'no human in the loop. This is the public, falsifiable account of how well the '
        'system actually reads what happens next. '
        f'<span class="text-slate">Updated {_esc(today.isoformat())}.</span></p>'
    )
    return (
        intro
        + _render_headline(overall, len(rows), len(pending))
        + _render_method_table(summaries)
        + _render_calibration(overall)
        + _render_ledger(pending, resolved)
    )


def render_panel(rows: list[dict], *, base: str = "") -> str:
    """Compact 'Forecast track record' panel for the daily brief, in the brief's
    house section style, linking to the full page."""
    if not rows:
        return ""
    resolved, pending = partition(rows)
    overall = tr.score_predictions(rows)
    href = f"{base}{PAGE_PATH}"
    if overall.n_resolved == 0:
        body = (
            f"<p class='synth'>{len(rows)} falsifiable forecasts on the public ledger, "
            f"{len(pending)} still open — each a time-stamped commitment the lake will "
            f"grade itself. Skill scores appear as calls mature.</p>"
        )
    else:
        bss = overall.brier_skill_score
        verdict = ("beating a climatology baseline" if (bss or 0) > 0
                   else "not yet beating climatology")
        body = (
            f"<p class='synth'>{overall.n_resolved} forecasts graded · "
            f"Brier {_f3(overall.brier)} (skill {_signed(bss)}, {verdict}) · "
            f"accuracy {_pct(overall.accuracy)} · {len(pending)} still open.</p>"
        )
    return (
        "<section class='section'>"
        "<h2>📈 Forecast track record "
        f"<span class='count'>· <a href='{_esc(href)}'>full ledger →</a></span></h2>"
        f"{body}"
        "</section>"
    )


# ============================================================================
# Lake adapter + writer
# ============================================================================

def load_predictions(conn) -> list[dict]:
    """Read all prediction rows from the lake DB as plain dicts."""
    try:
        cur = conn.execute(
            "SELECT id, made_at, target_date, resolution_criteria, predicted_outcome, "
            "confidence, training_window_days, indicators_used_json, method, "
            "evidence_json, section_id, resolved_at, actual_outcome, brier_contribution "
            "FROM predictions"
        )
    except Exception:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def write_page(out_root: Path, body: str, *, wrap) -> Path:
    """Write dist/track-record.html using the supplied chrome wrapper (normally
    ``site_builder._wrap``). Kept as a param to avoid an import cycle."""
    crumbs = [("WORLDSCOPE", "index.html"), ("Forecasts", "")]
    html_doc = wrap(
        "Forecast Track Record", body, crumbs, base="",
        description="WORLDSCOPE forecast track record: every falsifiable, "
                    "auto-graded prediction, its calibration, and skill by engine.",
    )
    out_path = Path(out_root) / PAGE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


# ============================================================================
# CLI
# ============================================================================

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Forecast Track Record page.")
    ap.add_argument("--write", metavar="OUT_DIR",
                    help="render <OUT_DIR>/track-record.html")
    args = ap.parse_args(argv)

    from .lake import Lake
    lake = Lake.open()
    try:
        conn = lake._ensure_open()
        rows = load_predictions(conn)
    finally:
        lake.close()

    resolved, pending = partition(rows)
    overall = tr.score_predictions(rows)
    summaries = by_method_summary(rows)
    print(f"[track-record] {len(rows)} forecasts logged · {len(pending)} open · "
          f"{len(resolved)} resolved")
    for s in summaries:
        print(f"  - {s['label']}: {s['total']} logged "
              f"({s['pending']} open, {s['resolved']} resolved)")
    if overall.n_resolved:
        print(f"  Brier {_f3(overall.brier)} · skill {_signed(overall.brier_skill_score)} "
              f"· accuracy {_pct(overall.accuracy)} · ECE {_f3(overall.ece)}")

    if args.write:
        from . import site_builder as sb
        body = build_body(rows)
        path = write_page(Path(args.write), body, wrap=sb._wrap)
        print(f"[track-record] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
