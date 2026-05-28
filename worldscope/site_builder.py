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

The style matches tools/render_brief.py so navigation feels consistent.

Run via: python -m worldscope.site_builder --out dist
Wires into worldscope/brief.py step 1f after the renderer.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter, defaultdict
from datetime import date as _date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAKE = REPO / "lake" / "sections"

# Reused from render_brief.py to keep visual coherence. Heritage palette
# values lifted from the existing site CSS.
CSS = """
:root {
  --ink: #0B1220; --bg: #FAFBFD; --panel: #fff; --border: #D9DEE5;
  --muted: #5B6473; --accent: #1F3864; --accent-2: #2E75B6;
  --warn: #B45309; --danger: #B91C1C; --good: #047857;
  --rule: 1px solid var(--border);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: 'Source Serif 4','Georgia',serif;
  color: var(--ink); background: var(--bg);
  font-size: 16px; line-height: 1.55;
}
.topnav {
  background: var(--accent); color: #fff;
  padding: 10px 24px;
  display: flex; gap: 18px; align-items: center; flex-wrap: wrap;
  font-family: 'Inter','-apple-system','Helvetica Neue',Arial,sans-serif;
  font-size: 13px;
  border-bottom: 3px double var(--accent-2);
}
.topnav a { color: #fff; text-decoration: none; opacity: 0.85; }
.topnav a:hover { opacity: 1; text-decoration: underline; }
.topnav .brand { font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; opacity: 1; margin-right: 12px; }
.topnav .spacer { flex: 1; }
.topnav .hub { font-size: 12px; opacity: 0.7; }
.shell { max-width: 1080px; margin: 0 auto; padding: 24px 24px 80px; }
.crumbs { font-family: 'Inter',sans-serif; font-size: 12px;
  color: var(--muted); margin-bottom: 16px; letter-spacing: 0.04em; }
.crumbs a { color: var(--accent); text-decoration: none; }
.crumbs a:hover { text-decoration: underline; }
h1 { font-size: 30px; margin: 4px 0 14px; letter-spacing: -0.3px; }
h2 { font-family: 'Source Serif 4',serif; font-size: 20px;
  margin: 28px 0 10px; padding-bottom: 5px;
  border-bottom: var(--rule); color: var(--accent); }
.meta { font-family: 'Inter',sans-serif; font-size: 12.5px;
  color: var(--muted); margin: 0 0 24px; }
.section-card {
  background: var(--panel); border: var(--rule); border-left: 3px solid var(--accent);
  border-radius: 6px; padding: 14px 18px; margin: 10px 0; display: block;
  text-decoration: none; color: inherit;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.section-card:hover { border-left-color: var(--danger);
  box-shadow: 0 2px 10px rgba(31,56,100,0.07); }
.section-card .name { font-size: 17px; font-weight: 700; color: var(--ink); }
.section-card .desc { font-family: 'Inter',sans-serif; font-size: 13px;
  color: var(--muted); margin-top: 4px; }
.section-card .count { font-family: 'Inter',sans-serif; font-size: 12px;
  color: var(--accent); margin-top: 6px; letter-spacing: 0.04em; }
.record {
  border: var(--rule); border-radius: 5px;
  padding: 10px 14px; margin: 8px 0;
  background: var(--panel);
}
.record h3 { font-size: 15px; font-family: 'Inter',sans-serif;
  font-weight: 600; margin: 0 0 4px; line-height: 1.35; }
.record h3 a { color: var(--ink); text-decoration: none;
  border-bottom: 1px solid #BBD; }
.record h3 a:hover { color: var(--accent); }
.record .src {
  display: inline-block; font-family: 'Inter',sans-serif;
  font-size: 11px; color: var(--accent);
  background: #EEF2F7; border-radius: 999px;
  padding: 1px 8px; margin-right: 5px; margin-top: 2px;
}
.record .tier {
  font-family: 'Inter',sans-serif; font-size: 11px; color: var(--muted);
  margin-left: 5px;
}
.record .body {
  margin: 6px 0 0; font-size: 14.5px; color: #2c3340; line-height: 1.5;
}
.record .entities {
  margin-top: 6px; font-family: 'Inter',sans-serif; font-size: 11px;
  color: var(--muted);
}
.record .entities .tag {
  display: inline-block; background: #FAFBFD; border: 1px solid #E0E5EC;
  border-radius: 3px; padding: 0 5px; margin-right: 4px;
}
.archive-row {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 8px 12px; margin: 4px 0;
  background: var(--panel); border: var(--rule); border-radius: 4px;
  text-decoration: none; color: inherit;
  font-family: 'Inter',sans-serif;
}
.archive-row:hover { border-color: var(--accent); }
.archive-row .date { font-size: 14px; color: var(--accent); font-weight: 600; }
.archive-row .n { font-size: 12px; color: var(--muted); }
footer.foot {
  margin-top: 50px; padding-top: 14px; border-top: var(--rule);
  font-family: 'Inter',sans-serif; font-size: 12px; color: var(--muted);
  display: flex; justify-content: space-between; flex-wrap: wrap;
}
@media (max-width: 700px) {
  .shell { padding: 16px 14px 60px; }
  .topnav { padding: 10px 14px; font-size: 12.5px; gap: 12px; }
  h1 { font-size: 24px; }
  .section-card { padding: 12px 14px; }
}
"""

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


def topnav(date_anchor: str = "") -> str:
    """The shared top navigation across every page."""
    return f"""<nav class="topnav">
  <span class="brand">WORLDSCOPE</span>
  <a href="/worldscope/">Today's brief</a>
  <a href="/worldscope/sections/">Sections</a>
  <a href="/worldscope/briefings/">Archive</a>
  <a href="/worldscope/zips/">Bundles</a>
  <span class="spacer"></span>
  <a class="hub" href="https://ihelfrich.ai/" target="_blank">ihelfrich.ai →</a>
</nav>"""


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
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _read_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _record_to_html(rec: dict) -> str:
    """Render one lake record as a card."""
    if rec.get("_error"):
        return ""
    title = rec.get("title") or rec.get("original_text", "")
    title = re.sub(r"\s+", " ", title).strip()[:240]
    url = rec.get("url") or rec.get("original_url") or ""
    summary = rec.get("summary") or ""
    if not summary and rec.get("original_text"):
        body = rec["original_text"]
        if title and body.startswith(title):
            body = body[len(title):].lstrip(" -—:")
        summary = body
    summary = re.sub(r"\s+", " ", summary or "").strip()[:600]
    source_label = (rec.get("source_label")
                    or rec.get("extra", {}).get("source_label")
                    or rec.get("source_id", ""))
    tier = rec.get("source_tier") or rec.get("extra", {}).get("source_tier", "")
    entities = rec.get("entities") or []
    title_html = (
        f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(title)}</a>'
        if url else html.escape(title)
    )
    body_html = (
        f'<p class="body">{html.escape(summary)}</p>' if summary else ""
    )
    ent_html = ""
    if entities:
        tags = "".join(
            f'<span class="tag">{html.escape(str(e))}</span>'
            for e in entities[:8]
        )
        ent_html = f'<div class="entities">{tags}</div>'
    return (
        f'<div class="record">'
        f'<h3>{title_html}</h3>'
        f'<span class="src">{html.escape(source_label or "?")}</span>'
        + (f'<span class="tier">{html.escape(str(tier))}</span>' if tier else "")
        + body_html
        + ent_html
        + "</div>"
    )


def _wrap(title: str, body: str, crumbs: list[tuple[str, str]]) -> str:
    crumbs_html = " <span style=\"color:#aaa\">/</span> ".join(
        f'<a href="{href}">{html.escape(label)}</a>' if href else html.escape(label)
        for label, href in crumbs
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>WORLDSCOPE · {html.escape(title)}</title>
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap">
<style>{CSS}</style>
</head><body>
{topnav()}
<div class="shell">
  <div class="crumbs">{crumbs_html}</div>
  {body}
  <footer class="foot">
    <div>WORLDSCOPE · all records cited inline · raw bundles available per day</div>
    <div><a href="/worldscope/" style="color:inherit">↑ home</a></div>
  </footer>
</div>
</body></html>
"""


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
    """Render dist/sections/<section_id>/<day>.html with all records for that day."""
    raw = _read_jsonl(LAKE / section_id / day / "raw.jsonl")
    structured = _read_meta(LAKE / section_id / day / "structured.json")
    summary_md = (LAKE / section_id / day / "summary.md")
    summary_text = summary_md.read_text(encoding="utf-8") if summary_md.exists() else ""

    # Strip YAML front matter from summary if present.
    body_summary = re.sub(r"^---\n.*?\n---\n", "", summary_text, count=1, flags=re.S)

    # Sort records by source_label then title for deterministic output.
    raw_sorted = sorted(
        raw,
        key=lambda r: ((r.get("source_label") or r.get("extra", {}).get("source_label") or "").lower(),
                       (r.get("title") or r.get("original_text") or "").lower()),
    )

    # Group by source_label.
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in raw_sorted:
        if r.get("_error"):
            continue
        label = (r.get("source_label")
                 or r.get("extra", {}).get("source_label")
                 or r.get("source_id") or "(unknown)")
        by_source[label].append(r)

    sources_html_parts = []
    for label, recs in sorted(by_source.items(), key=lambda kv: -len(kv[1])):
        cards = "".join(_record_to_html(r) for r in recs[:60])
        more = (f'<p class="meta" style="margin-top:6px">+{len(recs) - 60} more not shown on this page</p>'
                if len(recs) > 60 else "")
        sources_html_parts.append(
            f'<h2 id="src-{re.sub(r"[^a-z0-9]+", "-", label.lower())}">'
            f'{html.escape(label)} '
            f'<span style="font-family:\'Inter\',sans-serif;font-size:13px;color:var(--muted);font-weight:400;">'
            f'· {len(recs)} record{"s" if len(recs)!=1 else ""}</span>'
            f'</h2>'
            + cards + more
        )

    n_total = sum(len(v) for v in by_source.values())
    title = f"{section_id} · {day}"
    desc = SECTION_DESCRIPTIONS.get(section_id, "")
    crumbs = [
        ("WORLDSCOPE", "/worldscope/"),
        ("Sections", "/worldscope/sections/"),
        (section_id, f"/worldscope/sections/{section_id}/"),
        (day, ""),
    ]
    body = (
        f'<h1>{html.escape(section_id)} · {html.escape(day)}</h1>'
        f'<p class="meta">{html.escape(desc)} · {n_total} record'
        + ("s" if n_total != 1 else "") + f' across {len(by_source)} source'
        + ("s" if len(by_source) != 1 else "") + "</p>"
        + "".join(sources_html_parts)
    )
    out_dir = out_root / "sections" / section_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{day}.html"
    out_path.write_text(_wrap(title, body, crumbs), encoding="utf-8")
    return out_path


def render_section_index(section_id: str, out_root: Path) -> Path:
    """Render dist/sections/<section_id>/index.html listing all dates."""
    dates = _list_section_dates(section_id)
    rows = []
    for d in dates:
        n = _record_count_for_date(section_id, d)
        rows.append(
            f'<a class="archive-row" href="./{d}.html">'
            f'<span class="date">{d}</span>'
            f'<span class="n">{n} record{"s" if n != 1 else ""}</span>'
            f'</a>'
        )
    title = f"{section_id} · archive"
    desc = SECTION_DESCRIPTIONS.get(section_id, "")
    crumbs = [
        ("WORLDSCOPE", "/worldscope/"),
        ("Sections", "/worldscope/sections/"),
        (section_id, ""),
    ]
    body = (
        f'<h1>{html.escape(section_id)}</h1>'
        f'<p class="meta">{html.escape(desc)} · {len(dates)} day'
        + ("s" if len(dates) != 1 else "") + " on file</p>"
        + "".join(rows)
    )
    out_dir = out_root / "sections" / section_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(_wrap(title, body, crumbs), encoding="utf-8")
    return out_path


def render_sections_root(out_root: Path) -> Path:
    """Render dist/sections/index.html listing every section with latest counts."""
    if not LAKE.exists():
        LAKE.mkdir(parents=True, exist_ok=True)
    section_ids = sorted(
        d.name for d in LAKE.iterdir() if d.is_dir() and not d.name.startswith("_")
    )
    cards = []
    for sid in section_ids:
        dates = _list_section_dates(sid)
        latest = dates[0] if dates else None
        latest_n = _record_count_for_date(sid, latest) if latest else 0
        desc = SECTION_DESCRIPTIONS.get(sid, "")
        cards.append(
            f'<a class="section-card" href="./{sid}/">'
            f'<div class="name">{html.escape(sid)}</div>'
            f'<div class="desc">{html.escape(desc)}</div>'
            f'<div class="count">{len(dates)} day'
            + ("s" if len(dates) != 1 else "")
            + f' on file · latest {html.escape(latest or "(none)")} · '
            + f'{latest_n} record{"s" if latest_n != 1 else ""}</div>'
            f'</a>'
        )
    body = (
        '<h1>Sections</h1>'
        f'<p class="meta">{len(section_ids)} active sections in the worldscope lake. '
        'Click any section to drill into its archive and per-day records.</p>'
        + "".join(cards)
    )
    crumbs = [("WORLDSCOPE", "/worldscope/"), ("Sections", "")]
    out_dir = out_root / "sections"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(_wrap("Sections", body, crumbs), encoding="utf-8")
    return out_path


def build_all(out_root: Path, *, days_to_render: int = 7) -> dict:
    """Build the full section site. Returns counts for logging."""
    section_ids = sorted(
        d.name for d in LAKE.iterdir() if d.is_dir() and not d.name.startswith("_")
    ) if LAKE.exists() else []

    section_pages = 0
    day_pages = 0
    for sid in section_ids:
        render_section_index(sid, out_root)
        section_pages += 1
        for day in _list_section_dates(sid)[:days_to_render]:
            render_section_day(sid, day, out_root)
            day_pages += 1
    render_sections_root(out_root)
    return {
        "sections": len(section_ids),
        "section_pages": section_pages,
        "day_pages": day_pages,
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
