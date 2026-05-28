"""site_builder.py: generate per-section drill-down pages.

The Pages site has historically published only the synthesized daily brief
plus a downloadable zip. The lake holds ~5,000 raw records/day across 24
sections and the brief surfaces maybe 50 of them. Everything else is
visible only if a reader downloads the zip and grep-s the raw JSON.

This module generates browsable per-section pages so the reader can drill
from the brief's prose down to the actual records that fed it:

  dist/sections/index.html                            list of sections + latest counts
  dist/sections/<section_id>/index.html               per-section archive of all dates
  dist/sections/<section_id>/<date>.html              all records for that section/date
                                                       with source links, original text,
                                                       and entity tags

Chrome is shared with the homepage via lib.page_chrome.page_shell so design
tokens, ⌘K palette, sunset toggle, and Evidence Drawer hooks all flow through.

Run via: python -m worldscope.site_builder --out dist
Wires into worldscope/brief.py step 1f after the renderer.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import defaultdict
from datetime import date as _date, datetime as _dt
from pathlib import Path
from urllib.parse import quote as _urlquote

from .lib.page_chrome import page_shell, footer_block

REPO = Path(__file__).resolve().parent.parent
LAKE = REPO / "lake" / "sections"

PAGES_BASE = "https://ihelfrich.github.io/worldscope"

PRETTY_NAMES = {
    "cisa_kev": "CISA Known Exploited Vulnerabilities",
    "fec": "FEC Campaign Finance",
    "gdelt_gkg": "GDELT Global Knowledge Graph",
    "gdelt_regions": "GDELT Regional Tone",
    "us_nmtc": "US New Markets Tax Credit",
    "vip_flights": "VIP Aircraft Tracking",
    "ukraine_theater": "Ukraine Theater",
}


def pretty_section(sid: str) -> str:
    if sid in PRETTY_NAMES:
        return PRETTY_NAMES[sid]
    return sid.replace("_", " ").title()


def safe_url(url: str) -> str:
    if not url:
        return ""
    u = url.strip().lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return ""
    if "news.google.com/rss/" in u:
        return ""
    return url


def safe_path_segment(s: str) -> str:
    return _urlquote(s, safe="")


def is_stub_record(rec: dict) -> bool:
    title = (rec.get("title") or rec.get("original_text") or "").lower()
    if "[stub]" in title or "incumbent not verified" in title:
        return True
    if "slot reserved" in title:
        return True
    return False


def normalize_entities(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item[:80])
        elif isinstance(item, dict):
            cn = item.get("canonical_name") or item.get("name") or item.get("id")
            if cn:
                out.append(str(cn)[:80])
    return out


def safe_extra(rec: dict) -> dict:
    extra = rec.get("extra")
    return extra if isinstance(extra, dict) else {}


SECTION_DESCRIPTIONS = {
    "federal_register": "Executive orders, federal rules, and presidential documents",
    "macro": "FRED macro indicators",
    "markets": "US equity, FX, and Treasury markets",
    "markets_global": "Global indices, sovereign bond yields, and commodities",
    "billionaires": "Forbes real-time billionaire net-worth changes",
    "people": "Wikidata changes for tracked public figures",
    "sanctions": "OFAC SDN list and EU/UK sanctions designations",
    "sanctions_procurement": "OFAC + DSCA + FARA + USASpending + CFIUS aggregator",
    "courtlistener": "Federal and state civil/criminal court filings",
    "form4": "SEC Form 4 insider trade filings",
    "fec": "Federal Election Commission campaign finance",
    "congressional_trades": "STOCK Act PTRs via Quiver Quantitative",
    "political_figures": "613-figure US political watchlist with anomaly scoring",
    "gdelt_regions": "GDELT GKG regional tone and theme aggregation",
    "gdelt_gkg": "GDELT GKG themes, entities, and tone",
    "mediacloud": "Global media volume tracking",
    "conflict": "Conflict events and security incidents",
    "acled": "ACLED Armed Conflict Location and Event Data",
    "firms": "NASA FIRMS thermal anomaly detections",
    "vip_flights": "Government aircraft tracking (ADS-B)",
    "promed": "ProMED disease outbreak reports",
    "cisa_kev": "CISA Known Exploited Vulnerabilities catalog",
    "wikidata_changes": "Wikidata edits for tracked entities",
    "reliefweb": "ReliefWeb humanitarian situation reports",
    "forecasts": "Polymarket + Kalshi + PredictIt + Manifold prediction markets",
    "commentary": "Curated economist + analyst substack feed",
    "weather": "NOAA NWS active alerts + SPC outlooks + USGS quakes + NHC tropical",
    "state_news": "67 feeds across 50 US states + DC",
    "state_bills": "OpenStates state legislation aggregator",
    "local_news": "St. Louis + Atlanta hyperlocal",
    "foreign_news": "37 countries, multi-tier foreign-language and English-language",
    "chinese_internal": "Chinese-language domestic press with Claude translation",
    "russian_internal": "Russian-language press: state + business + in-exile",
    "ukrainian_internal": "Ukrainian press: national + Kyiv-local + government",
    "ukraine_theater": "Total-theater monitoring: ACLED + FIRMS + DeepStateMap + OSINT",
    "paper_bets": "Paper-betting prediction-market simulation (no real money)",
    "paper_bet_placement": "Daily paper-bet placement decisions",
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _read_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _crumbs(items: list[tuple[str, str]]) -> str:
    """Build a crumbs strip styled to match the homepage chrome.
    items: list of (label, href). Last item is current-page; an empty href
    marks it as the current page.
    """
    parts: list[str] = []
    last = len(items) - 1
    for i, (label, href) in enumerate(items):
        if href:
            parts.append(
                f'<a href="{html.escape(href, quote=True)}" '
                f'class="text-slate hover:text-navy transition-colors font-semibold">'
                f'{html.escape(label)}</a>'
            )
        else:
            aria = ' aria-current="page"' if i == last else ''
            parts.append(
                f'<span class="text-ink font-semibold"{aria}>{html.escape(label)}</span>'
            )
    sep = ' <span class="text-mist" aria-hidden="true">/</span> '
    return (
        '<nav class="max-w-[1400px] mx-auto px-7 pt-6 font-sans text-[11px] '
        'uppercase tracking-[0.10em] text-slate-dim" aria-label="Breadcrumb">'
        + sep.join(parts) +
        '</nav>'
    )


def _record_to_html(rec: dict) -> str:
    """Render one lake record as a frosty card with Evidence-Drawer hooks."""
    if rec.get("_error"):
        return ""
    extra = safe_extra(rec)
    title = rec.get("title") or rec.get("original_text", "")
    title = re.sub(r"\s+", " ", str(title)).strip()[:240]
    url = safe_url(rec.get("url") or rec.get("original_url") or "")
    summary = rec.get("summary") or ""
    if not summary and rec.get("original_text"):
        body = str(rec["original_text"])
        if title and body.startswith(title):
            body = body[len(title):].lstrip(" -:")
        summary = body
    summary = re.sub(r"\s+", " ", str(summary or "")).strip()[:600]
    source_label = (rec.get("source_label")
                    or extra.get("source_label")
                    or rec.get("source_id", ""))
    tier = rec.get("source_tier") or extra.get("source_tier", "")
    entities = normalize_entities(rec.get("entities"))
    entities_display = [
        e.split(":", 1)[1] if ":" in e else e
        for e in entities
    ]
    title_html = (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
        f'class="text-ink hover:text-navy bg-gradient-to-b from-transparent from-[calc(100%-1px)] '
        f'to-gold to-[calc(100%-1px)] bg-no-repeat bg-[length:100%_1px] bg-bottom '
        f'hover:bg-[length:100%_2px] transition-all pb-px">{html.escape(title)}</a>'
        if url else f'<span class="text-ink">{html.escape(title)}</span>'
    )
    body_html = (
        f'<p class="mt-2 text-[14.5px] leading-relaxed text-ink/85 font-serif">{html.escape(summary)}</p>'
        if summary else ""
    )
    ent_html = ""
    if entities_display:
        tags = "".join(
            f'<span class="inline-block bg-parchment border border-mist rounded px-2 py-0.5 text-[11px] text-slate">{html.escape(e)}</span>'
            for e in entities_display[:8]
        )
        ent_html = f'<div class="mt-2 flex flex-wrap gap-1 font-sans">{tags}</div>'
    tier_html = (
        f'<span class="ml-1.5 font-sans text-[10.5px] uppercase tracking-[0.06em] text-slate-dim">{html.escape(str(tier))}</span>'
        if tier else ""
    )
    return (
        '<article class="bg-panel border border-mist rounded-lg p-4 my-2.5 shadow-card '
        'hover:border-slate-dim hover:shadow-lift transition-all">'
        f'<h3 class="font-serif font-semibold text-[16.5px] leading-snug tracking-[-0.15px] m-0">{title_html}</h3>'
        '<div class="mt-1.5">'
        f'<span class="inline-block font-sans text-[11px] font-semibold text-navy bg-mist '
        f'rounded px-2 py-0.5 tracking-wide">{html.escape(str(source_label) or "?")}</span>'
        f'{tier_html}'
        '</div>'
        f'{body_html}{ent_html}'
        '</article>'
    )


def _network_seed_for_today() -> str:
    """Pull today's cross-section recurrences to feed the ambient
    canvas seed. Same fallback as the homepage."""
    today = _date.today().isoformat()
    cs_path = REPO / "lake" / "sections" / "_meta" / today / "cross_section.json"
    if not cs_path.exists():
        return "{}"
    try:
        data = json.loads(cs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "{}"
    rec_items = (data.get("by_confidence", {}).get("high", [])
                 + data.get("by_confidence", {}).get("medium", []))
    compact = [
        {"name": r.get("canonical_name", ""),
         "type": r.get("entity_type", ""),
         "sections": r.get("n_sections", 0)}
        for r in rec_items[:30]
    ]
    return json.dumps({"day": today, "recurrences": compact})


def _wrap(title: str, body_main: str, crumbs: list[tuple[str, str]],
          *, base: str = "", description: str = "",
          canonical: str = "") -> str:
    """Compose a page with the unified chrome (frosty palette, ⌘K palette,
    sunset toggle, ambient canvas, Evidence Drawer).

    body_main is the main content for the shell. crumbs render above it.
    `base` is the prefix for asset/topnav links.
    """
    crumb_html = _crumbs(crumbs)
    shell_body = (
        crumb_html +
        '<main class="max-w-[1400px] mx-auto px-7 pt-4 pb-16">'
        f'{body_main}'
        '</main>'
        f'{footer_block()}'
    )
    return page_shell(
        title=f"WORLDSCOPE · {title}",
        body_html=shell_body,
        description=description or f"WORLDSCOPE: {title}",
        canonical=canonical or f"{PAGES_BASE}/",
        base=base,
        network_seed_json=_network_seed_for_today(),
        include_chat=False,
    )


def _list_section_dates(section_id: str) -> list[str]:
    section_dir = LAKE / section_id
    if not section_dir.exists():
        return []
    return sorted(
        (d.name for d in section_dir.iterdir() if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", d.name)),
        reverse=True,
    )


def _record_count_for_date(section_id: str, day: str) -> int:
    raw = LAKE / section_id / day / "raw.jsonl"
    if not raw.exists():
        return 0
    return sum(1 for _ in raw.open("r", encoding="utf-8"))


def render_section_day(section_id: str, day: str, out_root: Path) -> Path:
    """Render dist/sections/<section_id>/<day>.html with all records."""
    raw = _read_jsonl(LAKE / section_id / day / "raw.jsonl")
    raw = [r for r in raw if not is_stub_record(r) and not r.get("_error")]
    raw_sorted = sorted(
        raw,
        key=lambda r: ((r.get("source_label") or safe_extra(r).get("source_label") or "").lower(),
                       (r.get("title") or r.get("original_text") or "").lower()),
    )
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in raw_sorted:
        label = (r.get("source_label")
                 or safe_extra(r).get("source_label")
                 or r.get("source_id") or "(unknown)")
        by_source[label].append(r)

    sources_html_parts: list[str] = []
    for label, recs in sorted(by_source.items(), key=lambda kv: -len(kv[1])):
        cards = "".join(_record_to_html(r) for r in recs[:60])
        more = (f'<p class="mt-2 font-sans text-[12px] text-slate-dim">+{len(recs) - 60} more not shown on this page</p>'
                if len(recs) > 60 else "")
        anchor = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-") or "src"
        sources_html_parts.append(
            f'<h2 id="src-{html.escape(anchor)}" '
            'class="font-serif text-[22px] font-bold mt-10 mb-3 pb-2 '
            'border-b border-mist text-navy tracking-[-0.2px]">'
            f'{html.escape(str(label))} '
            '<span class="font-sans font-normal text-[13px] text-slate">'
            f'· {len(recs)} record{"s" if len(recs)!=1 else ""}</span>'
            '</h2>'
            + cards + more
        )

    n_total = sum(len(v) for v in by_source.values())
    pretty_id = pretty_section(section_id)
    title = f"{pretty_id} · {day}"
    desc = SECTION_DESCRIPTIONS.get(section_id, "")
    sid_seg = safe_path_segment(section_id)
    crumbs = [
        ("WORLDSCOPE", "../../index.html"),
        ("Sections", "../"),
        (pretty_id, "./index.html"),
        (day, ""),
    ]
    body = (
        '<header class="mb-6 mt-3">'
        f'<h1 class="font-serif text-[38px] font-bold tracking-[-0.6px] leading-tight text-ink">'
        f'{html.escape(pretty_id)} '
        f'<span class="text-slate font-normal">· {html.escape(day)}</span></h1>'
        f'<p class="mt-2 font-sans text-[13px] text-slate">'
        f'{html.escape(desc)} · <strong class="text-ink font-semibold">{n_total}</strong> record'
        + ("s" if n_total != 1 else "") + f' across <strong class="text-ink font-semibold">{len(by_source)}</strong> source'
        + ("s" if len(by_source) != 1 else "") + '</p>'
        '</header>'
        + "".join(sources_html_parts)
    )
    out_dir = out_root / "sections" / section_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{day}.html"
    canonical = f"{PAGES_BASE}/sections/{sid_seg}/{day}.html"
    out_path.write_text(
        _wrap(title, body, crumbs, base="../../", canonical=canonical,
              description=f"{pretty_id} drill-down for {day}: {n_total} records across {len(by_source)} sources."),
        encoding="utf-8",
    )
    return out_path


def render_section_index(section_id: str, out_root: Path,
                         *, rendered_dates: list[str] | None = None) -> Path:
    """Render dist/sections/<section_id>/index.html listing dates."""
    all_dates = _list_section_dates(section_id)
    rendered = set(rendered_dates) if rendered_dates is not None else set(all_dates)
    rows: list[str] = []
    for d in all_dates:
        n = _record_count_for_date(section_id, d)
        n_str = f'{n} record{"s" if n != 1 else ""}'
        if d in rendered:
            rows.append(
                f'<a href="./{html.escape(d, quote=True)}.html" '
                'class="flex items-baseline justify-between bg-panel border border-mist rounded-md '
                'px-4 py-3 my-1.5 font-sans hover:border-navy hover:bg-gradient-to-r hover:from-panel hover:to-parchment '
                'hover:translate-x-0.5 transition-all">'
                f'<span class="text-[14.5px] text-navy font-semibold">{html.escape(d)}</span>'
                f'<span class="text-[12px] text-slate">{n_str}</span>'
                '</a>'
            )
        else:
            rows.append(
                '<div class="flex items-baseline justify-between bg-panel border border-mist rounded-md '
                'px-4 py-3 my-1.5 font-sans opacity-55">'
                f'<span class="text-[14.5px] text-navy font-semibold">{html.escape(d)}</span>'
                f'<span class="text-[12px] text-slate">{n_str} (archived only, no rendered page)</span>'
                '</div>'
            )
    pretty_id = pretty_section(section_id)
    title = f"{pretty_id} archive"
    desc = SECTION_DESCRIPTIONS.get(section_id, "")
    sid_seg = safe_path_segment(section_id)
    crumbs = [
        ("WORLDSCOPE", "../../index.html"),
        ("Sections", "../"),
        (pretty_id, ""),
    ]
    body = (
        '<header class="mb-6 mt-3">'
        f'<h1 class="font-serif text-[38px] font-bold tracking-[-0.6px] leading-tight text-ink">'
        f'{html.escape(pretty_id)}</h1>'
        f'<p class="mt-2 font-sans text-[13px] text-slate">'
        f'{html.escape(desc)} · <strong class="text-ink font-semibold">{len(all_dates)}</strong> day'
        + ("s" if len(all_dates) != 1 else "") + ' on file · '
        + f'<strong class="text-ink font-semibold">{len(rendered)}</strong> with rendered page</p>'
        '</header>'
        + "".join(rows)
    )
    out_dir = out_root / "sections" / section_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    canonical = f"{PAGES_BASE}/sections/{sid_seg}/"
    out_path.write_text(
        _wrap(title, body, crumbs, base="../../", canonical=canonical,
              description=f"{pretty_id} archive: {len(all_dates)} days of records."),
        encoding="utf-8",
    )
    return out_path


def render_sections_root(out_root: Path) -> Path:
    """Render dist/sections/index.html — frosty card grid of all sections."""
    if not LAKE.exists():
        LAKE.mkdir(parents=True, exist_ok=True)
    section_ids = sorted(
        d.name for d in LAKE.iterdir() if d.is_dir() and not d.name.startswith("_")
    )
    cards: list[str] = []
    for sid in section_ids:
        dates = _list_section_dates(sid)
        latest = dates[0] if dates else None
        latest_n = _record_count_for_date(sid, latest) if latest else 0
        desc = SECTION_DESCRIPTIONS.get(sid, "")
        sid_seg = safe_path_segment(sid)
        cards.append(
            f'<a href="./{html.escape(sid_seg, quote=True)}/" '
            'class="block bg-panel border border-mist border-l-4 border-l-navy rounded-xl '
            'p-5 shadow-card hover:-translate-y-0.5 hover:border-l-gold hover:shadow-lift '
            'transition-all">'
            f'<div class="font-serif font-bold text-[18.5px] tracking-[-0.2px] text-ink">'
            f'{html.escape(pretty_section(sid))}</div>'
            f'<div class="mt-1 font-sans text-[13px] text-slate leading-snug">{html.escape(desc)}</div>'
            '<div class="mt-3 pt-2.5 border-t border-mist font-sans text-[11.5px] '
            'uppercase tracking-[0.04em] font-semibold text-navy">'
            f'{len(dates)} day' + ("s" if len(dates) != 1 else "")
            + ' on file · latest ' + html.escape(latest or "(none)")
            + f' · {latest_n} record' + ("s" if latest_n != 1 else "") +
            '</div>'
            '</a>'
        )
    total_records = sum(_record_count_for_date(sid, _list_section_dates(sid)[0])
                        if _list_section_dates(sid) else 0
                        for sid in section_ids)
    body = (
        '<header class="mb-6 mt-3">'
        '<h1 class="font-serif text-[38px] font-bold tracking-[-0.6px] leading-tight text-ink">Sections</h1>'
        f'<p class="mt-2 font-sans text-[13px] text-slate">'
        f'<strong class="text-ink font-semibold">{len(section_ids)}</strong> active sections in the WORLDSCOPE lake '
        f'· <strong class="text-ink font-semibold">{total_records:,}</strong> records ingested today. '
        'Click any section to drill into its archive and per-day records.</p>'
        '</header>'
        '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3.5 mt-5">'
        f'{"".join(cards)}'
        '</div>'
    )
    crumbs = [("WORLDSCOPE", "../index.html"), ("Sections", "")]
    out_dir = out_root / "sections"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(
        _wrap("Sections", body, crumbs, base="../",
              canonical=f"{PAGES_BASE}/sections/",
              description=f"WORLDSCOPE section index: {len(section_ids)} active data sources."),
        encoding="utf-8",
    )
    return out_path


def render_404(out_root: Path) -> Path:
    """Site-styled 404 page so broken links land somewhere navigable."""
    body = (
        '<header class="mb-6 mt-3">'
        '<h1 class="font-serif text-[38px] font-bold tracking-[-0.6px] leading-tight text-ink">404, not found</h1>'
        '<p class="mt-2 font-sans text-[13px] text-slate">'
        'That page is not in WORLDSCOPE. '
        '<a href="/worldscope/" class="text-navy font-semibold hover:text-gold transition-colors">Today\'s brief</a>; '
        '<a href="/worldscope/sections/" class="text-navy font-semibold hover:text-gold transition-colors">Sections</a>.'
        '</p>'
        '</header>'
    )
    out_path = out_root / "404.html"
    out_path.write_text(
        _wrap("404", body, [("WORLDSCOPE", "/worldscope/"), ("404", "")],
              base="", canonical=f"{PAGES_BASE}/404.html",
              description="Page not found"),
        encoding="utf-8",
    )
    return out_path


def render_sitemap(out_root: Path, urls: list[str]) -> Path:
    """Emit a sitemap.xml covering every rendered URL."""
    today = _dt.utcnow().date().isoformat()
    entries = "\n".join(
        f"  <url><loc>{html.escape(u, quote=True)}</loc><lastmod>{today}</lastmod></url>"
        for u in sorted(set(urls))
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )
    out_path = out_root / "sitemap.xml"
    out_path.write_text(xml, encoding="utf-8")
    return out_path


def build_all(out_root: Path, *, days_to_render: int = 7) -> dict:
    """Build the full section site. Returns counts for logging."""
    section_ids = sorted(
        d.name for d in LAKE.iterdir() if d.is_dir() and not d.name.startswith("_")
    ) if LAKE.exists() else []

    section_pages = 0
    day_pages = 0
    sitemap_urls: list[str] = [
        f"{PAGES_BASE}/",
        f"{PAGES_BASE}/sections/",
        f"{PAGES_BASE}/briefings/",
    ]
    for sid in section_ids:
        sid_seg = safe_path_segment(sid)
        dates = _list_section_dates(sid)[:days_to_render]
        for day in dates:
            render_section_day(sid, day, out_root)
            day_pages += 1
            sitemap_urls.append(f"{PAGES_BASE}/sections/{sid_seg}/{day}.html")
        render_section_index(sid, out_root, rendered_dates=dates)
        section_pages += 1
        sitemap_urls.append(f"{PAGES_BASE}/sections/{sid_seg}/")
    render_sections_root(out_root)
    render_404(out_root)
    render_sitemap(out_root, sitemap_urls)
    return {
        "sections": len(section_ids),
        "section_pages": section_pages,
        "day_pages": day_pages,
        "sitemap_urls": len(sitemap_urls),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build per-section drill-down pages.")
    parser.add_argument("--out", type=Path, default=REPO / "dist")
    parser.add_argument("--days", type=int, default=7,
                        help="Number of recent days per section to render (default 7)")
    args = parser.parse_args()
    stats = build_all(args.out, days_to_render=args.days)
    print(f"[site-builder] {stats['sections']} sections, "
          f"{stats['section_pages']} index pages, "
          f"{stats['day_pages']} day pages written under {args.out}/sections/")
