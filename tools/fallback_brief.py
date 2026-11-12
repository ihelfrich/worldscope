"""fallback_brief.py — a deterministic Morning Brief for days the desk-officer
composer cannot run.

Why: from 2026-10-25 to 2026-11-12 no brief was composed at all — the repo
had no Claude credential, the Compose workflow skipped every day while
reporting success, and the whole downstream chain (render, archive, phone
notification) went silent for 18 days. Yet the evidence engine under the
board kept running the entire time. This module turns that engine's output
into a brief, so a lapsed credential DEGRADES the product instead of
silencing it.

Every line is assembled from the lake and the engines that already drive
the board — claim graph (corroboration-graded), cross-day momentum, the
entity graph, cross-source predictions, section integrity. There is no
model-written prose, and the edition says so at the top. It follows the
real brief's conventions (`# Morning Brief - <date>` H1, `## Headline`, ...)
so render, the notification picker and the archive treat it normally, and
it carries a machine marker so automation can tell it from a composed one.

    python tools/fallback_brief.py --date 2026-11-12 --out briefings/2026-11-12.md
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import render_reasoned as rr  # noqa: E402  (claims/integrity/predictions gather)

MARKER = "<!-- worldscope:fallback-brief -->"

# Domain grouping mirrors the board (tools/render_board.py) without importing
# it, so a board-layout change can't break the fallback path.
_DOMAINS = {
    "Conflict & security": ["conflict", "acled", "ukraine_theater", "firms", "vip_flights"],
    "Geopolitics": ["foreign_news", "people", "political_figures", "commentary",
                    "gdelt_regions", "gdelt_gkg", "mediacloud", "wikidata_changes"],
    "Markets & macro": ["markets_global", "macro", "forecasts", "paper_bets", "markets",
                        "billionaires", "congressional_trades", "form4"],
    "Technology & cyber": ["cisa_kev", "epss", "tech_news"],
    "Health & humanitarian": ["who_don", "reliefweb", "promed"],
    "Earth & disaster": ["usgs_quakes", "weather", "gdacs"],
    "US governance": ["federal_register", "state_bills", "state_news", "local_news",
                      "courtlistener", "fec", "sanctions", "sanctions_procurement"],
    "State media": ["chinese_internal", "russian_internal", "ukrainian_internal"],
}
_SEC_DOMAIN = {s: d for d, secs in _DOMAINS.items() for s in secs}


def is_fallback(path: Path) -> bool:
    """True when ``path`` holds a brief this module wrote."""
    try:
        return MARKER in Path(path).read_text(encoding="utf-8")[:400]
    except OSError:
        return False


def _clean(text: str, n: int = 220) -> str:
    text = re.sub(r"<[^>]*>?", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return rr._oneline(text, n)


def _n(k: int, noun: str) -> str:
    return f"{k} {noun}" + ("" if k == 1 else "s")


def _label(claim) -> str:
    return rr._STAT.get(claim.status, ("", claim.status))[1]


def _lang(claim) -> str:
    tag = rr._lang_tag(claim.claim_text)
    return f" _[{tag}, untranslated]_" if tag else ""


def build(today: date) -> str:
    claims, reports, fresh, preds, _recs = rr._gather(today)
    claims = [c for c in claims if rr._relevant(c)]
    ranked = sorted(claims, key=lambda c: (c.status != "contradicted", c.n_sources,
                                           c.confidence), reverse=True)
    out: list[str] = []
    w = out.append

    w(f"# Morning Brief - {today.strftime('%A %d %B %Y').replace(' 0', ' ')}")
    w("")
    w(MARKER)
    w("> **Automated edition.** The desk-officer composer did not run today, so this "
      "brief was assembled deterministically from the WORLDSCOPE evidence engine: "
      "corroboration-graded claims, cross-day momentum, the entity graph and "
      "cross-source signals. It contains no model-written analysis — every line "
      "traces to records in the lake.")
    w("")

    # ---- source health ------------------------------------------------------
    n = len(reports)
    bad = [r for r in reports if r.status not in ("FRESH", "SKIPPED")]
    gaps = ", ".join(f"{r.section_id} ({r.status.lower()})" for r in bad[:14])
    w(f"**Source health:** {fresh}/{n} sections fresh."
      + (f" Gaps: {gaps}." if gaps else " No gaps."))
    w("")
    w("---")
    w("")

    # ---- headline -----------------------------------------------------------
    w("## Headline")
    w("")
    if ranked:
        top = ranked[0]
        w(f"**{_clean(top.claim_text, 260)}**{_lang(top)}")
        w("")
        w(f"Assessed **{_label(top)}** at {int(top.confidence * 100)}% confidence: "
          f"{top.n_sources} sources across {top.n_sections} independent sections "
          f"({', '.join(top.topics[:6])})."
          + (f" Note: {top.contradiction_note}" if top.contradiction_note else ""))
    else:
        w("No claim cleared the cross-source corroboration bar today.")
    w("")

    # ---- key developments ---------------------------------------------------
    if len(ranked) > 1:
        w("## Key developments")
        w("")
        for c in ranked[1:11]:
            w(f"- **{_label(c)}** · {_clean(c.claim_text)}{_lang(c)} "
              f"— {_n(c.n_sources, 'source')} · {_n(c.n_sections, 'section')}")
        w("")

    # ---- momentum -----------------------------------------------------------
    try:
        from worldscope.momentum import build_momentum
        mom = build_momentum(today)
    except Exception as exc:  # never let one engine sink the brief
        print(f"[fallback] momentum unavailable: {exc}")
        mom = {"days": [], "stories": []}
    if mom.get("stories"):
        w(f"## Developing — story momentum across {len(mom['days'])} days")
        w("")
        for st in mom["stories"]:
            w(f"- **{st['klass'].upper()}** · {st['label']} — {_clean(st['headline'], 170)} "
              f"_(today {st['records'][-1]}/day · peak {st['peak']} · "
              f"present {st['days_present']}/{len(mom['days'])} days)_")
        w("")

    # ---- by domain ----------------------------------------------------------
    buckets: dict[str, list] = defaultdict(list)
    for c in ranked:
        doms = [_SEC_DOMAIN[t] for t in c.topics if t in _SEC_DOMAIN]
        if doms:
            buckets[max(set(doms), key=doms.count)].append(c)
    if buckets:
        w("## By domain")
        w("")
        for dom in _DOMAINS:
            items = buckets.get(dom)
            if not items:
                continue
            w(f"**{dom}**")
            w("")
            for c in items[:3]:
                w(f"- {_clean(c.claim_text, 180)}{_lang(c)} _({_label(c).lower()}, "
                  f"{c.n_sources} src)_")
            w("")

    # ---- connections --------------------------------------------------------
    try:
        from worldscope.graph_view import connections
        ents = connections(today.isoformat()).get("entities", [])
    except Exception as exc:
        print(f"[fallback] entity graph unavailable: {exc}")
        ents = []
    if ents:
        w("## Connections — who links across domains")
        w("")
        for e in ents[:8]:
            byverb: dict[str, list] = defaultdict(list)
            for ed in e.get("edges", []):
                byverb[ed["type"]].append(ed["other"])
            rel = "; ".join(f"{v}: {', '.join(o[:3])}" for v, o in byverb.items())
            w(f"- **{e['name']}** ({e['type']}, {e['n_domains']} domains)"
              + (f" — {rel}" if rel else ""))
        w("")

    # ---- outlook ------------------------------------------------------------
    if preds:
        w("## Outlook — falsifiable calls")
        w("")
        for p in preds[:6]:
            m = rr._KEY_RE.search(p.get("resolution_criteria", ""))
            label = m.group(1) if m else (p.get("_key") or "signal")
            w(f"- {label} stays cross-source-salient — "
              f"{int(float(p.get('confidence', 0)) * 100)}% · resolves {p.get('target_date', '')}")
        w("")

    w("---")
    w("")
    w(f"_Assembled {today.isoformat()} by tools/fallback_brief.py from the WORLDSCOPE "
      "lake. Status labels come from the claim graph's corroboration grading; "
      "momentum compares each story with its own baseline across the window. "
      "A composed edition replaces this one once the composer is restored._")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a deterministic fallback Morning Brief")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing composed brief (never needed in CI)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists() and out.stat().st_size and not is_fallback(out) and not args.force:
        print(f"[fallback] {out} already holds a composed brief; leaving it alone")
        return 0
    text = build(date.fromisoformat(args.date))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[fallback] wrote {out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
