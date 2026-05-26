"""
render_brief.py — convert briefings/<date>.md and weekly_briefings/<week>.md
into publication-grade HTML at dist/briefings/<date>.html etc.

Design goals (Bloomberg / Stratfor / Economist register):

  - Two-column on desktop, single-column on mobile
  - Sticky sidebar with auto-generated TOC + jump anchors
  - Watch-area dashboard at the top (read from watchareas.yaml)
  - Anomaly callouts: any "anomaly" / "z-score" / "Δ" line is highlighted
  - Inline charts: any image in dist/briefings/ or briefings/ named
    <date>-<slug>.png is auto-discovered and inlined as a figure
  - Print-clean PDF path (@media print)
  - Embedded Leaflet event map if a sibling <date>-events.geojson exists
  - Inline links rendered as primary-source citations with hostname pills
  - Tables get banded rows and a sticky header
  - Headings get hover-anchor links

Reads:
  briefings/<date>.md         — routine output, markdown
  briefings/<date>-*.png      — routine-generated charts
  briefings/<date>-events.geojson  — optional event geometry
  watchareas.yaml             — to render the watch-area dashboard

Writes:
  dist/briefings/<date>.html
  dist/briefings/<date>-*.png  (copied so the HTML can reference them)
  dist/briefings/index.html    — points to latest + archive
  weekly_briefings/<week>.* analogous
"""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

import markdown
import yaml

REPO = Path(__file__).resolve().parent.parent
WATCH = REPO / "watchareas.yaml"

CSS = """
:root {
  --ink: #0B1220;
  --bg: #FAFBFD;
  --panel: #FFFFFF;
  --border: #D9DEE5;
  --muted: #5B6473;
  --accent: #1F3864;
  --accent-2: #2E75B6;
  --warn: #B45309;
  --danger: #B91C1C;
  --good: #047857;
  --rule: 1px solid var(--border);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: 'Source Serif 4', 'Source Serif Pro', 'Georgia', 'Iowan Old Style', serif;
  color: var(--ink);
  background: var(--bg);
  font-size: 16.5px;
  line-height: 1.55;
}
.shell {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 36px;
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px 24px 80px;
}
.sidebar {
  position: sticky;
  top: 24px;
  align-self: start;
  font-family: 'Inter', -apple-system, 'Helvetica Neue', Arial, sans-serif;
  font-size: 13px;
  line-height: 1.5;
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  padding-right: 4px;
}
.sidebar h3 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--muted);
  margin: 18px 0 6px;
  font-weight: 700;
}
.sidebar ul { list-style: none; padding: 0; margin: 0; }
.sidebar li { margin: 4px 0; }
.sidebar a {
  color: var(--ink); text-decoration: none;
  display: block; padding: 3px 8px; border-radius: 4px;
  border-left: 2px solid transparent;
}
.sidebar a:hover { background: #EEF2F7; }
.sidebar a.active {
  border-left-color: var(--accent);
  background: #EEF2F7;
  color: var(--accent);
  font-weight: 600;
}
.masthead {
  border-bottom: 3px double var(--accent);
  padding-bottom: 16px;
  margin-bottom: 24px;
}
.masthead .eyebrow {
  font-family: 'Inter', sans-serif;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  color: var(--accent);
  font-weight: 700;
}
.masthead h1 {
  font-family: 'Source Serif 4', 'Georgia', serif;
  font-size: 34px;
  line-height: 1.15;
  margin: 8px 0 6px;
  color: var(--ink);
  letter-spacing: -0.3px;
}
.masthead .dateline {
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  color: var(--muted);
}
.dashboard {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin: 18px 0 26px;
}
.dash-tile {
  background: var(--panel);
  border: var(--rule);
  border-left: 3px solid var(--accent);
  border-radius: 6px;
  padding: 10px 12px;
  font-family: 'Inter', sans-serif;
}
.dash-tile.priority-high { border-left-color: var(--danger); }
.dash-tile.priority-normal { border-left-color: var(--accent); }
.dash-tile.priority-low { border-left-color: var(--muted); }
.dash-tile.alert { background: #FEF2F2; }
.dash-tile h4 {
  margin: 0 0 4px;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink);
}
.dash-tile .dash-stat {
  font-size: 22px;
  font-family: 'Source Serif 4', serif;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: -0.5px;
}
.dash-tile .dash-meta {
  font-size: 11px;
  color: var(--muted);
  margin-top: 3px;
}
.content h1 { display: none; }  /* first H1 is the title, shown in masthead */
.content h2 {
  font-family: 'Source Serif 4', serif;
  font-size: 24px;
  margin: 36px 0 12px;
  padding-bottom: 6px;
  border-bottom: var(--rule);
  color: var(--accent);
  letter-spacing: -0.2px;
  scroll-margin-top: 24px;
}
.content h3 {
  font-family: 'Source Serif 4', serif;
  font-size: 18px;
  margin: 24px 0 8px;
  color: var(--ink);
  scroll-margin-top: 24px;
}
.content h4 {
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--muted);
  margin: 18px 0 6px;
}
.content p {
  margin: 0 0 12px;
}
.content a {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid #BBD;
}
.content a:hover { border-bottom-color: var(--accent); }
.content blockquote {
  border-left: 3px solid var(--accent-2);
  background: #EEF4FA;
  padding: 8px 14px;
  margin: 12px 0;
  font-style: italic;
  color: #1B2E4A;
}
.content code {
  font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
  font-size: 13px;
  background: #F1F4F9;
  padding: 1px 5px;
  border-radius: 3px;
}
.content pre {
  background: #0F172A;
  color: #E2E8F0;
  padding: 14px;
  border-radius: 6px;
  overflow-x: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
}
.content table {
  border-collapse: collapse;
  width: 100%;
  margin: 14px 0;
  font-size: 14.5px;
  font-family: 'Inter', sans-serif;
}
.content table th, .content table td {
  border: var(--rule);
  padding: 6px 10px;
  text-align: left;
}
.content table th {
  background: #F1F4F9;
  font-weight: 700;
  position: sticky;
  top: 0;
}
.content table tbody tr:nth-child(odd) { background: #FAFBFD; }
.content img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 16px 0;
  border: var(--rule);
  border-radius: 4px;
}
.content figure {
  margin: 18px 0;
}
.content figcaption {
  font-family: 'Inter', sans-serif;
  font-size: 12.5px;
  color: var(--muted);
  margin-top: 6px;
}
.callout {
  border: var(--rule);
  border-left: 4px solid var(--warn);
  background: #FFFBEB;
  padding: 10px 14px;
  margin: 14px 0;
  border-radius: 4px;
  font-family: 'Inter', sans-serif;
  font-size: 14.5px;
}
.callout.danger { border-left-color: var(--danger); background: #FEF2F2; }
.callout.good   { border-left-color: var(--good);   background: #ECFDF5; }
.callout-label {
  display: inline-block;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-weight: 700;
  color: var(--warn);
  margin-right: 8px;
}
.callout.danger .callout-label { color: var(--danger); }
.callout.good   .callout-label { color: var(--good); }
.hostpill {
  display: inline-block;
  font-family: 'Inter', sans-serif;
  font-size: 10.5px;
  color: var(--muted);
  background: #F1F4F9;
  border: 1px solid #D9DEE5;
  border-radius: 999px;
  padding: 1px 7px;
  margin-left: 4px;
  vertical-align: 1px;
}
.gridmap {
  width: 100%;
  height: 380px;
  border: var(--rule);
  border-radius: 6px;
  margin: 16px 0;
}
footer.brief-foot {
  margin-top: 60px;
  padding-top: 16px;
  border-top: var(--rule);
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  color: var(--muted);
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px;
}
.anchor-link {
  opacity: 0; margin-left: 6px; text-decoration: none; color: var(--accent-2);
  transition: opacity 0.15s;
}
.content h2:hover .anchor-link,
.content h3:hover .anchor-link { opacity: 1; }
@media (max-width: 900px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; max-height: none; }
}
@media print {
  .sidebar { display: none; }
  .shell { grid-template-columns: 1fr; padding: 0; max-width: 100%; }
  body { background: white; font-size: 11.5pt; }
  .dash-tile, .callout { break-inside: avoid; }
  .masthead { break-after: avoid; }
  .content h2 { break-before: avoid; }
}
"""

LEAFLET_SCRIPT = """
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  crossorigin=""/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  crossorigin=""></script>
<script>
(function(){
  const el = document.getElementById('gridmap');
  if (!el) return;
  const fc = %%GEOJSON%%;
  const m = L.map('gridmap', { scrollWheelZoom: false }).setView([20, 0], 2);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap, &copy; CARTO', maxZoom: 8
  }).addTo(m);
  L.geoJSON(fc, {
    pointToLayer: function(f, latlng) {
      const sev = (f.properties && f.properties.severity) || 1;
      return L.circleMarker(latlng, {
        radius: 4 + Math.min(sev, 8),
        color: '#B91C1C', weight: 1, fillColor: '#DC2626', fillOpacity: 0.6
      });
    },
    onEachFeature: function(f, l) {
      const p = f.properties || {};
      l.bindPopup('<b>'+(p.title||'event')+'</b><br>'+(p.summary||'')+'<br><i>'+(p.date||'')+'</i>');
    }
  }).addTo(m);
})();
</script>
"""


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60]


def extract_headings(md_text: str) -> list[tuple[int, str, str]]:
    """Return (level, text, slug) for every # heading."""
    out: list[tuple[int, str, str]] = []
    in_code = False
    for line in md_text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            out.append((level, text, slugify(text)))
    return out


def inject_heading_anchors(html_body: str, headings: list[tuple[int, str, str]]) -> str:
    """Wrap each <h2>/<h3>/<h4> with an id so the sidebar can link to it."""
    by_text = {h[1]: h[2] for h in headings}

    def repl(match: re.Match) -> str:
        tag = match.group(1)
        inner = match.group(2)
        # Strip any inner tags for matching
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        slug = by_text.get(plain) or slugify(plain)
        return f'<{tag} id="{slug}">{inner} <a class="anchor-link" href="#{slug}">§</a></{tag}>'

    return re.sub(r"<(h[234])>(.+?)</\1>", repl, html_body, flags=re.DOTALL)


def host_pill_links(html_body: str) -> str:
    """For every external link, append a small pill with the hostname."""
    def repl(match: re.Match) -> str:
        full = match.group(0)
        href = match.group(1)
        text = match.group(2)
        host = re.sub(r"^https?://(www\.)?", "", href).split("/", 1)[0]
        if not host or host.startswith("#") or host.startswith("/"):
            return full
        return f'<a href="{href}">{text}</a><span class="hostpill">{host}</span>'

    return re.sub(r'<a href="(https?://[^"]+)"[^>]*>([^<]+)</a>', repl, html_body)


def callout_pass(html_body: str) -> str:
    """Turn lines containing 'anomaly z=' / 'Δ' / 'ALERT:' into callouts."""
    def wrap(match: re.Match) -> str:
        p = match.group(0)
        body_text = re.sub(r"<[^>]+>", "", p)
        kind = ""
        label = ""
        if re.search(r"\b(ALERT|RED FLAG|breach|surge|spike)\b", body_text, re.I):
            kind = "danger"; label = "Alert"
        elif re.search(r"\bz\s*=|\banomaly\b|\bz-score\b|σ\b|\bΔ\b", body_text, re.I):
            kind = "warn"; label = "Anomaly"
        elif re.search(r"\b(cooling|easing|de-escalation)\b", body_text, re.I):
            kind = "good"; label = "Easing"
        if not kind:
            return p
        return f'<div class="callout {kind}"><span class="callout-label">{label}</span>{p}</div>'

    return re.sub(r"<p>(?:(?!</p>).)*?(?:ALERT|RED FLAG|anomaly|z-score|σ|Δ|breach|surge|spike|cooling|easing|de-escalation)(?:(?!</p>).)*?</p>",
                  wrap, html_body, flags=re.I)


def load_watch_dashboard() -> list[dict]:
    if not WATCH.exists():
        return []
    try:
        raw = yaml.safe_load(WATCH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    areas = raw.get("watch_areas") if isinstance(raw, dict) else raw
    return areas or []


def dashboard_html(areas: list[dict], md_text: str) -> str:
    """Render watch-area tiles. Stat = count of mentions of area name in the brief."""
    if not areas:
        return ""
    tiles: list[str] = []
    md_lower = md_text.lower()
    for a in areas:
        name = a.get("name", "")
        if not name:
            continue
        priority = a.get("priority", "normal")
        mentions = md_lower.count(name.lower())
        alert = ""
        # Alert if name mentioned alongside red-flag words
        snippet_re = re.compile(re.escape(name.lower()) + r".{0,200}(alert|surge|spike|breach|red flag)", re.I | re.S)
        if snippet_re.search(md_text):
            alert = "alert"
        topics = (a.get("topics") or [])[:3]
        topics_str = ", ".join(topics) if topics else ""
        slug = slugify(name)
        tiles.append(
            f'<div class="dash-tile priority-{html.escape(priority)} {alert}">'
            f'<h4><a href="#{slug}" style="color:inherit;text-decoration:none;">{html.escape(name)}</a></h4>'
            f'<div class="dash-stat">{mentions}</div>'
            f'<div class="dash-meta">{html.escape(topics_str)}</div>'
            f"</div>"
        )
    return '<div class="dashboard">' + "".join(tiles) + "</div>"


def sidebar_html(headings: list[tuple[int, str, str]], areas: list[dict]) -> str:
    out: list[str] = ['<nav class="sidebar">']
    if areas:
        out.append('<h3>Watch areas</h3><ul>')
        for a in areas:
            name = a.get("name", "")
            if not name:
                continue
            out.append(f'<li><a href="#{slugify(name)}">{html.escape(name)}</a></li>')
        out.append('</ul>')
    out.append('<h3>Sections</h3><ul>')
    for level, text, slug in headings:
        if level not in (2, 3):
            continue
        indent = "padding-left:14px;" if level == 3 else ""
        out.append(f'<li style="{indent}"><a href="#{slug}">{html.escape(text)}</a></li>')
    out.append('</ul></nav>')
    return "".join(out)


def discover_assets(brief_dir: Path, stem: str) -> tuple[list[Path], Path | None]:
    """Find <stem>-*.png siblings and a <stem>-events.geojson if present."""
    pngs = sorted(brief_dir.glob(f"{stem}-*.png"))
    geo = brief_dir / f"{stem}-events.geojson"
    return pngs, (geo if geo.exists() else None)


def render_one(md_path: Path, out_dir: Path, kind: str) -> Path:
    md_text = md_path.read_text(encoding="utf-8")
    stem = md_path.stem
    # Extract first H1 as title
    title_match = re.match(r"^#\s+(.+)$", md_text, flags=re.M)
    title = title_match.group(1).strip() if title_match else stem
    # Markdown body (drop the first H1 so masthead handles it)
    body_md = re.sub(r"^#\s+.+$", "", md_text, count=1, flags=re.M)
    body_html = markdown.markdown(body_md, extensions=["tables", "fenced_code", "attr_list", "toc"])
    headings = extract_headings(md_text)
    body_html = inject_heading_anchors(body_html, headings)
    body_html = host_pill_links(body_html)
    body_html = callout_pass(body_html)
    areas = load_watch_dashboard()
    dash = dashboard_html(areas, md_text)
    side = sidebar_html(headings, areas)
    # Discover sibling assets (PNG charts, events geojson)
    brief_dir = md_path.parent
    pngs, geo = discover_assets(brief_dir, stem)
    # Copy PNGs into dist/{kind}/ alongside the HTML
    out_dir.mkdir(parents=True, exist_ok=True)
    for png in pngs:
        shutil.copy(png, out_dir / png.name)
    map_section = ""
    if geo:
        try:
            fc = json.loads(geo.read_text(encoding="utf-8"))
            map_section = (
                '<h2 id="event-map">Event map</h2>'
                '<div id="gridmap" class="gridmap"></div>'
                + LEAFLET_SCRIPT.replace("%%GEOJSON%%", json.dumps(fc))
            )
        except Exception:
            map_section = ""
    eyebrow = "Daily briefing" if kind == "briefings" else "Weekly briefing"
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>WORLDSCOPE · {html.escape(stem)}</title>
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap">
<style>{CSS}</style>
</head><body>
<div class="shell">
{side}
<main class="content-wrap">
  <div class="masthead">
    <div class="eyebrow">WORLDSCOPE · {eyebrow}</div>
    <h1>{html.escape(title)}</h1>
    <div class="dateline">prepared for Dr. Ian Helfrich · {html.escape(stem)}</div>
  </div>
  {dash}
  <article class="content">
    {body_html}
    {map_section}
  </article>
  <footer class="brief-foot">
    <div>WORLDSCOPE · all claims trace to inline sources · synthesis grounded in bundle items</div>
    <div><a href="./index.html">archive</a></div>
  </footer>
</main>
</div>
</body></html>
"""
    out_path = out_dir / f"{stem}.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path


def render_index(out_dir: Path, kind: str) -> None:
    pages = sorted(out_dir.glob("*.html"), reverse=True)
    pages = [p for p in pages if p.name != "index.html"]
    if not pages:
        return
    rows = "\n".join(
        f'<li><a href="./{p.name}">{p.stem}</a></li>'
        for p in pages
    )
    label = "Daily briefings" if kind == "briefings" else "Weekly briefings"
    out_dir.joinpath("index.html").write_text(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>WORLDSCOPE · {label}</title>
<style>body{{font-family:Inter,system-ui,sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#0B1220}}
h1{{font-family:'Source Serif 4',Georgia,serif;color:#1F3864;border-bottom:3px double #1F3864;padding-bottom:8px}}
ul{{list-style:none;padding:0}} li{{padding:6px 0;border-bottom:1px solid #E5E7EB}}
a{{color:#1F3864;text-decoration:none}} a:hover{{text-decoration:underline}}</style>
</head><body><h1>{label}</h1><ul>{rows}</ul></body></html>
""", encoding="utf-8")


def main() -> None:
    for kind in ("briefings", "weekly_briefings"):
        src = REPO / kind
        if not src.exists():
            continue
        out_dir = REPO / "dist" / kind
        out_dir.mkdir(parents=True, exist_ok=True)
        for md in sorted(src.glob("*.md")):
            out = render_one(md, out_dir, kind)
            print(f"  {md} → {out}")
        render_index(out_dir, kind)


if __name__ == "__main__":
    main()
