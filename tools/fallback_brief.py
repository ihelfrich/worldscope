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
import html
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
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
# General news feeds say little about a story's domain; a claim that also
# appears in a topical section (tech_news, ukraine_theater, usgs_quakes, ...)
# belongs to that section's domain. Only all-general claims fall back to them.
_GENERAL = {"foreign_news", "local_news", "state_news", "chinese_internal",
            "russian_internal", "ukrainian_internal", "gdelt_regions", "gdelt_gkg",
            "mediacloud", "commentary"}


def domain_of(c) -> str | None:
    secs = _reporting_sections(c)
    specific = [s for s in secs if s in _SEC_DOMAIN and s not in _GENERAL]
    if specific:
        doms = [_SEC_DOMAIN[s] for s in specific]
        return max(sorted(set(doms)), key=doms.count)
    if secs & {"foreign_news", "gdelt_regions", "gdelt_gkg", "mediacloud", "commentary"}:
        return "Geopolitics"
    if secs & {"chinese_internal", "russian_internal", "ukrainian_internal"}:
        return "State media"
    return None                      # local/state-only: not a world-news domain


def is_fallback(path: Path) -> bool:
    """True when ``path`` holds a brief this module wrote."""
    try:
        return MARKER in Path(path).read_text(encoding="utf-8")[:400]
    except OSError:
        return False


def _clean(text: str, n: int = 220) -> str:
    text = html.unescape(re.sub(r"&(\d+);", r"&#\1;", text or ""))  # "&039;" too
    text = re.sub(r"<[^>]*>?", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return rr._oneline(text, n)


def _n(k: int, noun: str) -> str:
    return f"{k} {noun}" + ("" if k == 1 else "s")


def _label(claim) -> str:
    return rr._STAT.get(claim.status, ("", claim.status))[1]


def _lang_of(text: str) -> str:
    tag = rr._lang_tag(text)
    return f" _[{tag}, untranslated]_" if tag else ""


from worldscope import claim_rank as cr  # noqa: E402  (shared with the board)

_NON_REPORTING, _STRUCTURED, _LOCAL = cr.NON_REPORTING, cr.STRUCTURED, cr.LOCAL
_ROUNDUP, _TAGGED = cr.ROUNDUP, cr.TAGGED
_evidence, _reporting_sections, _outlets = cr.evidence, cr.reporting_sections, cr.outlets
display_text, _junk, newsworthiness = cr.display_text, cr.is_junk, cr.newsworthiness


_REGIONS = {"europe", "european union", "eu", "africa", "asia", "middle east", "america",
            "latin america", "north america", "south america", "the west", "nato",
            "united nations", "un", "gulf", "pacific", "atlantic", "arctic", "world",
            "united states", "us", "uk", "britain", "washington", "moscow", "beijing",
            "kyiv", "tehran", "gaza", "west bank", "taiwan", "hong kong", "crimea",
            # demonyms of countries outside the watchlists
            "french", "german", "british", "american", "european", "spanish", "dutch",
            "polish", "swedish", "greek", "egyptian", "saudi", "emirati", "qatari",
            "syrian", "lebanese", "iraqi", "afghan", "pakistani", "korean", "vietnamese",
            "thai", "filipino", "indonesian", "australian", "african", "arab", "asian"}


def _country_names() -> set[str]:
    names = set(_REGIONS)
    try:
        from worldscope.sections.acled import WATCHLIST as ACLED_COUNTRIES
        names |= {c.lower() for c in ACLED_COUNTRIES}
    except Exception:
        pass
    try:
        from worldscope.sections.gdelt_regions import ALIASES, WATCHLIST
        names |= {n.lower() for _, n in WATCHLIST}
        names |= {a for strong, weak in ALIASES.values() for a in strong + weak}
    except Exception:
        pass
    try:
        from worldscope.lake import Lake
        lake = Lake.open()
        conn = lake._ensure_open()
        names |= {r[0].lower() for r in conn.execute(
            "SELECT DISTINCT canonical_name FROM entities WHERE id LIKE 'place:country-%'")}
        lake.close()
    except Exception:
        pass
    return names


def _cross_domain_keys(sigs, places: set[str], min_domains: int) -> dict:
    out = {}
    for sig in sigs:
        if sig.key in places or sig.label.lower() in places:
            continue
        doms = sorted({_SEC_DOMAIN[x] for x in sig.sections if x in _SEC_DOMAIN})
        if len(doms) >= min_domains:
            out[sig.key] = (sig, doms)
    return out


def _named_mid_sentence(label: str, evidence: list[dict]) -> bool:
    return any(cr.named_mid_sentence(label, ev.get("title") or "") for ev in evidence)


def cross_domain(today: date, *, limit: int = 6, min_domains: int = 3,
                 baseline_days: int = 7) -> list[dict]:
    """Specific names (people, companies, organisations) that surface across
    several domains today, with one headline per domain as evidence.

    Built from the signals engine (cross-section recurrence of names in
    headlines). The lake's entity graph cannot do this: its person/org
    entities come almost entirely from state legislation, so the old section
    could only ever show legislatures and bill signings. Country and region
    names are skipped: they cross every domain every day, and so is any name
    that already crossed as many domains ``baseline_days`` ago (Trump, the
    White House): the section reports what is newly connecting.
    """
    try:
        from worldscope import signals as sg
        from worldscope.lake import Lake
        lake = Lake.open()
        conn = lake._ensure_open()
        sigs = sg.build_signals(today=today, conn=conn)
        base = sg.build_signals(today=today - timedelta(days=baseline_days), conn=conn)
        lake.close()
    except Exception as exc:  # never let one engine sink the brief
        print(f"[fallback] signals unavailable: {exc}")
        return []
    from worldscope.momentum import _JUNK
    places = _country_names() | set(_JUNK) | {"u.s", "u.s.", "white house"}
    usual = _cross_domain_keys(base, places, min_domains)
    out = []
    shown_titles: set[str] = set()
    for key, (sig, all_doms) in _cross_domain_keys(sigs, places, min_domains).items():
        if key in usual:
            continue
        if " " not in sig.label.strip() and not _named_mid_sentence(sig.label, sig.evidence):
            continue                      # "Power", "You": capitalised only as a first word
        if any((ev.get("title") or "").strip() in shown_titles for ev in sig.evidence[:1]):
            continue                      # same story as an entry above (Nvidia / Jensen Huang)
        doms: dict[str, str] = {}
        for ev in sig.evidence:
            dom = _SEC_DOMAIN.get(ev.get("section", ""))
            t = (ev.get("title") or "").strip()
            if dom and t and dom not in doms and not rr._lang_tag(t):
                doms[dom] = t
        shown_titles |= set(doms.values())
        out.append({"label": sig.label, "domains": all_doms, "n_sections": sig.n_sections,
                    "evidence": list(doms.items())[:3]})
        if len(out) >= limit:
            break
    return out


def _carried_forward() -> dict[str, str]:
    """Sections the daily run deliberately carried forward (no error), e.g.
    people/sanctions, which CI skips because their corpus lives on the owner's
    machine. The integrity view calls them "empty"; the run report knows they
    are a held snapshot. {section: snapshot date}."""
    try:
        import json
        rep = json.loads((REPO / "dist" / "run_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {sid: e.get("source_date") or "an earlier run"
            for sid, e in (rep.get("sections") or {}).items()
            if isinstance(e, dict) and e.get("state") == "carry_forward"
            and not e.get("error_type")}


def data_day(today: date) -> date:
    """The newest day the lake holds collected data for, never after ``today``.

    The daily collection has landed as late as 16:17 UTC, so an edition built
    earlier must be assembled as of the last collected day: built "as of
    today" it reported 4/42 sections fresh and every story as fading.
    """
    try:
        from worldscope.lake import Lake
        lake = Lake.open()
        conn = lake._ensure_open()
        newest = conn.execute("SELECT MAX(substr(ingested_at, 1, 10)) FROM records").fetchone()[0]
        lake.close()
        return min(today, date.fromisoformat(newest)) if newest else today
    except Exception as exc:
        print(f"[fallback] could not read lake data date: {exc}")
        return today


def build(today: date, *, as_of: date | None = None) -> str:
    """Brief headed ``today``, assembled from data collected on ``as_of``
    (default: the newest collected day, see data_day)."""
    as_of = as_of or data_day(today)
    claims, reports, fresh, preds, _recs = rr._gather(as_of)
    claims = [c for c in claims if rr._relevant(c)]
    scored = [(newsworthiness(c), c) for c in claims]
    ranked = [c for sc, c in sorted(((sc, c) for sc, c in scored if sc is not None),
                                    key=lambda t: t[0], reverse=True)]
    # Domain lines may use claims the ranking drops (a quake belongs under
    # Earth & disaster, not key developments), but never junk.
    by_domain_pool = sorted((c for c in claims if not _junk(c)),
                            key=lambda c: (len(_reporting_sections(c)), _outlets(c),
                                           c.confidence), reverse=True)
    out: list[str] = []
    w = out.append

    w(f"# Morning Brief - {today.strftime('%A %d %B %Y').replace(' 0', ' ')}")
    w("")
    w(MARKER)
    # A plain paragraph, not a blockquote: the renderer styles blockquotes as
    # pull quotes, which put this notice in quotation marks as if quoted.
    w("**Automated edition.** The desk-officer composer did not run today, so this "
      "brief was assembled deterministically from the WORLDSCOPE evidence engine: "
      "corroboration-graded claims, cross-day momentum, the entity graph and "
      "cross-source signals. It contains no model-written analysis — every line "
      "traces to records in the lake.")
    if as_of < today:
        w("")
        w(f"**Data as of {as_of.strftime('%A %d %B').replace(' 0', ' ')}.** Today's "
          "collection had not landed when this edition was assembled; it is refreshed "
          "when it does.")
    w("")

    # ---- source health ------------------------------------------------------
    n = len(reports)
    bad = [r for r in reports if r.status not in ("FRESH", "SKIPPED")]
    carried = _carried_forward()
    gaps = ", ".join(
        f"{r.section_id} (carried from {carried[r.section_id]})" if r.section_id in carried
        else f"{r.section_id} ({r.status.lower()})" for r in bad[:14])
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
        w(f"**{_clean(display_text(top), 260)}**{_lang_of(display_text(top))}")
        w("")
        w(f"Assessed **{_label(top)}** at {int(top.confidence * 100)}% confidence: "
          f"{_n(_outlets(top), 'independent outlet')} across "
          f"{_n(len(_reporting_sections(top)), 'section')} "
          f"({', '.join(sorted(_reporting_sections(top))[:6])})."
          + (f" Note: {top.contradiction_note}" if top.contradiction_note else ""))
    else:
        w("No claim cleared the cross-source corroboration bar today.")
    w("")

    # ---- key developments ---------------------------------------------------
    if len(ranked) > 1:
        w("## Key developments")
        w("")
        for c in ranked[1:11]:
            w(f"- **{_label(c)}** · {_clean(display_text(c))}{_lang_of(display_text(c))} "
              f"— {_n(_outlets(c), 'outlet')} · {_n(len(_reporting_sections(c)), 'section')}")
        w("")

    # ---- momentum -----------------------------------------------------------
    try:
        from worldscope.momentum import build_momentum
        mom = build_momentum(as_of)
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
    shown = {id(c) for c in ranked[:11]}      # headline + key developments
    for c in by_domain_pool:
        if id(c) in shown:
            continue                          # complement the lists above, don't repeat them
        dom = domain_of(c)
        if dom:
            buckets[dom].append(c)
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
                w(f"- {_clean(display_text(c), 180)}{_lang_of(display_text(c))} "
                  f"_({_label(c).lower()}, {_n(_outlets(c), 'outlet')})_")
            w("")

    # ---- connections --------------------------------------------------------
    links = cross_domain(as_of)
    if links:
        w("## Connections — names that cross domains")
        w("")
        for lk in links:
            ev = "; ".join(f"{dom}: {_clean(t, 90)}" for dom, t in lk["evidence"])
            w(f"- **{lk['label']}** — {_n(len(lk['domains']), 'domain')} "
              f"({', '.join(lk['domains'])}), {_n(lk['n_sections'], 'section')}. {ev}")
        w("")

    # ---- outlook ------------------------------------------------------------
    if preds:
        w("## Outlook — falsifiable calls")
        w("")
        groups: dict[tuple, list[str]] = defaultdict(list)
        for p in preds[:12]:
            m = rr._KEY_RE.search(p.get("resolution_criteria", ""))
            label = m.group(1) if m else (p.get("_key") or "signal")
            key = (int(float(p.get("confidence", 0)) * 100), p.get("target_date", ""))
            if label not in groups[key]:
                groups[key].append(label)
        for (conf, target), labels in groups.items():
            w(f"- Still cross-source-salient on {target} ({conf}% each): "
              + ", ".join(labels) + ".")
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
