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

import datetime as _dt
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
/* === mobile-responsive additions === */
@media (max-width: 700px) {
  body {
    font-size: 15.5px;
  }
  .shell {
    padding: 16px 16px 60px;
  }
  .sidebar {
    display: none;
  }
  .masthead h1 {
    font-size: 26px;
  }
  .dashboard {
    grid-template-columns: 1fr;
    gap: 10px;
    margin: 14px 0 22px;
  }
  .dash-tile {
    padding: 8px 10px;
  }
  .content img {
    margin-left: -16px;
    margin-right: -16px;
    width: calc(100% + 32px);
    max-width: none;
    border-radius: 0;
  }
}
/* === site-wide top navigation === */
.topnav {
  background: var(--accent); color: #fff;
  padding: 9px 24px;
  display: flex; gap: 18px; align-items: center; flex-wrap: wrap;
  font-family: 'Inter','-apple-system','Helvetica Neue',Arial,sans-serif;
  font-size: 13px;
  border-bottom: 3px double var(--accent-2);
  margin: -24px -24px 24px;
}
.topnav a { color: #fff; text-decoration: none; opacity: 0.85; }
.topnav a:hover { opacity: 1; text-decoration: underline; }
.topnav .brand {
  font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; opacity: 1;
}
.topnav .spacer { flex: 1; }
.topnav .hub { font-size: 12px; opacity: 0.7; }
@media (max-width: 700px) {
  .topnav { padding: 9px 14px; margin: -16px -16px 18px; font-size: 12.5px; gap: 12px; }
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
    """Render watch-area tiles. Stat = count of mentions of area name in the brief.

    Only emits a tile when the area has signal (mentions > 0 OR an alert match).
    Empty zero-mention watch areas still appear in the sidebar nav (so the user
    can jump to them), but they do not pollute the above-the-fold dashboard.
    Threshold can be overridden per-area via `dashboard_min_mentions` in
    watchareas.yaml; default is 1.
    """
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
        # Empty-card filter: skip if no signal. Alert override keeps a tile
        # visible even when the area name itself is mentioned zero times,
        # since the alert may have referenced it indirectly.
        threshold = int(a.get("dashboard_min_mentions", 1))
        if mentions < threshold and not alert:
            continue
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
    if not tiles:
        return ""
    return '<div class="dashboard">' + "".join(tiles) + "</div>"


def strip_google_news_links(html_body: str) -> str:
    """Replace <a href="https://news.google.com/rss/...">X</a> with just X.

    Google News proxy URLs serve raw XML to the browser instead of
    redirecting to the article. They are unsafe to expose as clickable
    links. We surface the link text as plain text instead.
    """
    return re.sub(
        r'<a\s+href="https://news\.google\.com/rss[^"]*"[^>]*>([^<]*)</a>',
        r'\1',
        html_body,
    )


def dedupe_images(html_body: str) -> str:
    """Strip duplicate <img> tags pointing at the same src.

    Defense in depth: the synthesis prompt sometimes refers to the same chart
    in two different sections (it sees the chart as a useful illustration for
    both). Rendering both is visual redundancy. Keep first occurrence, drop
    subsequent ones.
    """
    seen: set[str] = set()

    def repl(match: re.Match) -> str:
        src_match = re.search(r'src=["\']([^"\']+)["\']', match.group(0))
        if not src_match:
            return match.group(0)
        src = src_match.group(1)
        if src in seen:
            return ""
        seen.add(src)
        return match.group(0)

    return re.sub(r"<img\b[^>]*>", repl, html_body)


def strip_stale_zip_notice(md_text: str, zip_exists_now: bool) -> str:
    """Remove the synthesis-time 'bundle zip was unavailable' DATA NOTE if the
    zip is actually present at render time.

    Race condition we are working around: the Claude synthesis prompt runs
    BEFORE the bundle.py zip-generation step on some days. The synthesis sees
    'no zip yet' and templates a DATA NOTE into the markdown. By the time the
    renderer runs, the zip exists and the notice is stale. Strip it.
    """
    if not zip_exists_now:
        return md_text
    return re.sub(
        r"^>\s*\*\*DATA NOTE:?\*\*[^\n]*bundle zip was unavailable[^\n]*(\n>.*)*\n?",
        "",
        md_text,
        flags=re.M | re.I,
    )


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


# Bleach allowlist for the desk-officer's narrative. We accept the
# constructs the markdown library actually emits (headings, paragraphs,
# emphasis, links, code, lists, tables, blockquotes) and reject
# everything else.
_BLEACH_TAGS: frozenset[str] = frozenset({
    "a", "abbr", "blockquote", "br", "code", "del", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre",
    "small", "span", "strong", "sub", "sup", "table", "tbody", "td",
    "th", "thead", "tr", "ul",
})
_BLEACH_ATTRS: dict[str, list[str]] = {
    "*":   ["id", "class", "title"],
    "a":   ["href", "title", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "th":  ["colspan", "rowspan", "scope"],
    "td":  ["colspan", "rowspan"],
}
_BLEACH_PROTOCOLS: frozenset[str] = frozenset({"http", "https", "mailto"})


def sanitize_brief_html(html_body: str) -> str:
    """Post-Markdown sanitize pass for the desk-officer's narrative.

    Uses bleach (a real HTML parser, not regex) to allowlist the tags +
    attributes + URL schemes the briefing actually uses. <script>,
    <style>, <iframe>, on*= event handlers, javascript:/data: URLs,
    encoded scheme tricks, and unquoted attribute values are all
    rejected by the parser before regex would have a chance to miss
    them.
    """
    import bleach
    return bleach.clean(
        html_body,
        tags=_BLEACH_TAGS,
        attributes=_BLEACH_ATTRS,
        protocols=_BLEACH_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )


def render_one(md_path: Path, out_dir: Path, kind: str) -> Path:
    md_text = md_path.read_text(encoding="utf-8")
    stem = md_path.stem
    # Check if today's zip actually exists at render time (race-condition
    # mitigation: synthesis prompt may have templated a "zip unavailable"
    # DATA NOTE that is now stale).
    zip_path = REPO / "dist" / "zips" / f"{stem}.zip"
    md_text = strip_stale_zip_notice(md_text, zip_path.exists())
    # Extract first H1 as title
    title_match = re.match(r"^#\s+(.+)$", md_text, flags=re.M)
    title = title_match.group(1).strip() if title_match else stem
    # Markdown body (drop the first H1 so masthead handles it)
    body_md = re.sub(r"^#\s+.+$", "", md_text, count=1, flags=re.M)
    # NOTE: attr_list intentionally dropped — it lets the desk-officer
    # markdown emit arbitrary HTML attributes via {: onclick="..."}, an
    # XSS vector even for "trusted" input. Tables, fenced code, and toc
    # are kept because the desk-officer markdown uses them.
    body_html = markdown.markdown(body_md, extensions=["tables", "fenced_code", "toc"])
    body_html = sanitize_brief_html(body_html)
    headings = extract_headings(md_text)
    body_html = inject_heading_anchors(body_html, headings)
    body_html = host_pill_links(body_html)
    body_html = callout_pass(body_html)
    body_html = dedupe_images(body_html)
    body_html = strip_google_news_links(body_html)
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
<nav class="topnav" aria-label="Primary">
  <span class="brand">WORLDSCOPE</span>
  <a href="../index.html">Today</a>
  <a href="../sections/">Sections</a>
  <a href="./index.html">Archive</a>
  <a href="../zips/{html.escape(stem)}.zip">Today's bundle</a>
  <span class="spacer"></span>
  <a class="hub" href="https://ihelfrich.github.io/" target="_blank" rel="noopener noreferrer">helfrich.github.io →</a>
</nav>
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


def _brief_meta(md_path: Path) -> dict:
    """Pull headline + teaser + first dashboard line from a briefing .md."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return {"headline": md_path.stem, "teaser": "", "word_count": 0}
    h1 = re.search(r"^#\s+(.+)$", text, flags=re.M)
    headline = h1.group(1).strip() if h1 else md_path.stem
    # Teaser: first non-empty paragraph after the first H2, with markdown stripped
    after_h2 = re.split(r"^##\s+", text, maxsplit=1, flags=re.M)
    target = after_h2[1] if len(after_h2) > 1 else text
    # Skip the heading line itself, take the next paragraph
    paras = [p.strip() for p in target.split("\n\n") if p.strip()]
    teaser = paras[1] if len(paras) > 1 else (paras[0] if paras else "")
    teaser = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", teaser)
    teaser = re.sub(r"[*_`#>]+", "", teaser)
    teaser = teaser.replace("\n", " ")
    if len(teaser) > 280:
        teaser = teaser[:277].rsplit(" ", 1)[0] + "..."
    return {
        "headline": headline,
        "teaser": teaser,
        "word_count": len(text.split()),
    }


INDEX_CSS = """
:root {
  --ink:#0B1220; --bg:#FAFBFD; --panel:#fff; --border:#D9DEE5;
  --muted:#5B6473; --accent:#1F3864; --accent-2:#2E75B6;
}
* { box-sizing:border-box; }
body {
  margin:0; font-family:'Source Serif 4','Georgia',serif;
  background:var(--bg); color:var(--ink); font-size:16.5px; line-height:1.55;
}
.shell { max-width:880px; margin:0 auto; padding:32px 24px 80px; }
.masthead {
  border-bottom:3px double var(--accent); padding-bottom:18px; margin-bottom:28px;
}
.masthead .eyebrow {
  font-family:Inter,system-ui,sans-serif; font-size:11px;
  text-transform:uppercase; letter-spacing:0.18em; color:var(--accent);
  font-weight:700;
}
.masthead h1 {
  font-size:38px; margin:8px 0 6px; letter-spacing:-0.3px; color:var(--ink);
}
.masthead .sub {
  font-family:Inter,sans-serif; font-size:14px; color:var(--muted);
}
.brief-card {
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  padding:18px 22px; margin:14px 0; display:block;
  text-decoration:none; color:inherit;
  transition:border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}
.brief-card:hover {
  border-color:var(--accent-2);
  box-shadow:0 2px 12px rgba(31,56,100,0.08);
  transform:translateY(-1px);
}
.brief-card .date {
  font-family:Inter,sans-serif; font-size:12px; color:var(--accent);
  font-weight:700; text-transform:uppercase; letter-spacing:0.08em;
}
.brief-card .headline {
  font-size:20px; margin:4px 0 6px; color:var(--ink); line-height:1.3;
}
.brief-card .teaser {
  color:#374151; font-size:14.5px; margin:0;
}
.brief-card .meta {
  font-family:Inter,sans-serif; font-size:11.5px; color:var(--muted);
  margin-top:8px;
}
.toggle {
  font-family:Inter,sans-serif; font-size:13px; color:var(--accent-2);
  margin-bottom:20px;
}
.toggle a { color:inherit; text-decoration:none; margin-right:14px; }
.toggle a.active { font-weight:700; color:var(--accent); }
footer {
  margin-top:48px; padding-top:14px; border-top:1px solid var(--border);
  font-family:Inter,sans-serif; font-size:12px; color:var(--muted);
}
"""


def render_index(out_dir: Path, kind: str) -> None:
    pages = sorted(out_dir.glob("*.html"), reverse=True)
    pages = [p for p in pages if p.name != "index.html"]
    if not pages:
        return
    src_dir = REPO / kind
    cards: list[str] = []
    for p in pages:
        md = src_dir / f"{p.stem}.md"
        meta = _brief_meta(md) if md.exists() else {"headline": p.stem, "teaser": "", "word_count": 0}
        try:
            d = _date_from_stem(p.stem, kind)
        except Exception:
            d = p.stem
        wc = meta["word_count"]
        wc_str = f"{wc:,} words" if wc else ""
        cards.append(
            f'<a class="brief-card" href="./{p.name}">'
            f'<div class="date">{html.escape(d)}</div>'
            f'<h2 class="headline">{html.escape(meta["headline"])}</h2>'
            f'<p class="teaser">{html.escape(meta["teaser"])}</p>'
            f'<div class="meta">{wc_str}</div>'
            f'</a>'
        )
    label = "Daily briefings" if kind == "briefings" else "Weekly briefings"
    other = "Weekly" if kind == "briefings" else "Daily"
    other_path = "../weekly_briefings/" if kind == "briefings" else "../briefings/"
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>WORLDSCOPE · {label}</title>
<link rel="alternate" type="application/atom+xml" title="WORLDSCOPE {label}" href="./feed.xml">
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap">
<style>{INDEX_CSS}</style>
</head><body>
<div class="shell">
  <div class="masthead">
    <div class="eyebrow">WORLDSCOPE · archive</div>
    <h1>{label}</h1>
    <div class="sub">prepared for Dr. Ian Helfrich · {len(pages)} brief{'s' if len(pages)!=1 else ''} on file</div>
  </div>
  <div class="toggle">
    <a href="./" class="active">{label}</a>
    <a href="{other_path}">{other} archive</a>
    <a href="./feed.xml">Atom feed</a>
  </div>
  {"".join(cards)}
  <footer>WORLDSCOPE · daily global intelligence · sources cited inline in every brief</footer>
</div>
</body></html>
"""
    out_dir.joinpath("index.html").write_text(page, encoding="utf-8")
    _render_feed(out_dir, kind, pages)


def _date_from_stem(stem: str, kind: str) -> str:
    """Format a stem like '2026-05-26' or '2026-W22' as a display string."""
    if kind == "briefings":
        try:
            d = _dt.date.fromisoformat(stem)
            return d.strftime("%A, %B %-d, %Y")
        except (ValueError, AttributeError):
            return stem
    return stem  # weekly: keep as 2026-W22


def _render_feed(out_dir: Path, kind: str, pages: list[Path]) -> None:
    """Atom feed at <out_dir>/feed.xml."""
    label = "Daily briefings" if kind == "briefings" else "Weekly briefings"
    src_dir = REPO / kind
    base = f"https://ihelfrich.github.io/worldscope/{kind}"
    entries: list[str] = []
    for p in pages[:50]:
        md = src_dir / f"{p.stem}.md"
        meta = _brief_meta(md) if md.exists() else {"headline": p.stem, "teaser": ""}
        # ISO date (best-effort)
        try:
            iso = _dt.date.fromisoformat(p.stem).isoformat() + "T11:00:00Z"
        except ValueError:
            iso = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        entries.append(
            f"  <entry>\n"
            f"    <title>{html.escape(meta['headline'])}</title>\n"
            f"    <link href='{base}/{p.name}'/>\n"
            f"    <id>{base}/{p.stem}</id>\n"
            f"    <updated>{iso}</updated>\n"
            f"    <summary>{html.escape(meta['teaser'])}</summary>\n"
            f"  </entry>"
        )
    feed = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>WORLDSCOPE · {label}</title>\n"
        f"  <link href='{base}/feed.xml' rel='self'/>\n"
        f"  <link href='{base}/'/>\n"
        f"  <id>{base}/feed</id>\n"
        f"  <updated>{_dt.datetime.utcnow().isoformat(timespec='seconds')}Z</updated>\n"
        f"  <author><name>WORLDSCOPE</name></author>\n"
        + "\n".join(entries) + "\n"
        "</feed>\n"
    )
    out_dir.joinpath("feed.xml").write_text(feed, encoding="utf-8")


def render_root_landing(out_root: Path) -> None:
    """Top-level dist/index.html.

    Strategy: the landing page IS the latest brief. We copy the rendered
    HTML of the newest brief verbatim and rewrite its internal nav so
    relative links still resolve from /worldscope/ instead of
    /worldscope/briefings/. This gives visitors the rich brief on first
    load instead of a sparse hero card."""
    daily_dir = out_root / "briefings"
    latest = sorted(daily_dir.glob("*.html"), reverse=True) if daily_dir.exists() else []
    latest = [p for p in latest if p.name != "index.html"]
    if not latest:
        return
    newest = latest[0]
    rich = newest.read_text(encoding="utf-8")
    # Rewrite the brief's relative topnav links so they resolve from the
    # site root instead of /briefings/.
    rich = rich.replace('href="../index.html"', 'href="./index.html"')
    rich = rich.replace('href="../sections/"', 'href="./sections/"')
    rich = rich.replace('href="./index.html">Archive', 'href="./briefings/">Archive')
    rich = rich.replace('href="../zips/', 'href="./zips/')
    # Inline-fix any other relative image references the brief used (the
    # brief MD references images by bare filename like
    # <img src="2026-05-27-anomaly_screen.png">; we need them resolved
    # from /briefings/<date>-X.png instead of /<date>-X.png).
    rich = re.sub(
        r'(<img[^>]+src=")(\d{4}-\d{2}-\d{2}-[^"]+)(")',
        r'\1./briefings/\2\3',
        rich,
    )
    (out_root / "index.html").write_text(rich, encoding="utf-8")
    return
    daily_dir = out_root / "briefings"
    weekly_dir = out_root / "weekly_briefings"
    latest_daily = sorted(daily_dir.glob("*.html"), reverse=True) if daily_dir.exists() else []
    latest_daily = [p for p in latest_daily if p.name != "index.html"]
    if not latest_daily:
        return
    newest = latest_daily[0]
    src_md = REPO / "briefings" / f"{newest.stem}.md"
    meta = _brief_meta(src_md) if src_md.exists() else {"headline": newest.stem, "teaser": "", "word_count": 0}
    try:
        date_str = _dt.date.fromisoformat(newest.stem).strftime("%A, %B %-d, %Y")
    except (ValueError, AttributeError):
        date_str = newest.stem
    latest_weekly_link = ""
    if weekly_dir.exists():
        wp = sorted(weekly_dir.glob("*.html"), reverse=True)
        wp = [p for p in wp if p.name != "index.html"]
        if wp:
            latest_weekly_link = f'<a class="latest-link" href="./weekly_briefings/{wp[0].name}">Latest weekly brief: {wp[0].stem}</a>'
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>WORLDSCOPE</title>
<link rel="alternate" type="application/atom+xml" title="WORLDSCOPE daily" href="./briefings/feed.xml">
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&display=swap">
<style>{INDEX_CSS}
.hero {{
  background:linear-gradient(135deg,#1F3864 0%,#2E75B6 100%);
  color:#fff; padding:36px 32px; border-radius:10px;
  margin-bottom:24px;
}}
.hero .eyebrow {{ color:#A5C8F0; }}
.hero h2 {{ font-size:28px; margin:6px 0 10px; color:#fff; line-height:1.2; }}
.hero p.teaser {{ color:#E0EAF6; font-size:15.5px; margin:0 0 14px; }}
.hero .cta {{ display:inline-block; background:#fff; color:#1F3864;
  padding:10px 18px; border-radius:6px; font-weight:700;
  font-family:Inter,sans-serif; font-size:13px; text-decoration:none;
  letter-spacing:0.03em; }}
.hero .cta:hover {{ background:#FAFBFD; }}
.latest-link {{ display:block; margin-top:8px; color:#A5C8F0; font-size:13px;
  font-family:Inter,sans-serif; text-decoration:none; }}
.section-nav {{ margin:32px 0 16px; display:flex; gap:14px; flex-wrap:wrap; }}
.section-nav a {{
  flex:1 1 200px; padding:14px 16px; background:#fff;
  border:1px solid var(--border); border-radius:8px;
  text-decoration:none; color:var(--ink);
  font-family:Inter,sans-serif; font-size:14px;
  transition:border-color 0.15s;
}}
.section-nav a:hover {{ border-color:var(--accent); }}
.section-nav .label {{ display:block; font-size:11px; color:var(--muted);
  text-transform:uppercase; letter-spacing:0.1em; margin-bottom:3px; }}
.section-nav .target {{ color:var(--accent); font-weight:600; font-size:16px; }}
</style>
</head><body>
<div class="shell">
  <div class="masthead">
    <div class="eyebrow">WORLDSCOPE</div>
    <h1>Daily global intelligence</h1>
    <div class="sub">prepared for Dr. Ian Helfrich · automated open-source briefing engine</div>
  </div>

  <div class="hero">
    <div class="eyebrow">Today's brief · {html.escape(date_str)}</div>
    <h2>{html.escape(meta['headline'])}</h2>
    <p class="teaser">{html.escape(meta['teaser'])}</p>
    <a class="cta" href="./briefings/{newest.name}">Read today's full brief →</a>
    {latest_weekly_link}
  </div>

  <div class="section-nav">
    <a href="./briefings/"><span class="label">Daily archive</span><span class="target">All daily briefings</span></a>
    <a href="./weekly_briefings/"><span class="label">Weekly archive</span><span class="target">Weekly cross-day synthesis</span></a>
    <a href="./briefings/feed.xml"><span class="label">Subscribe</span><span class="target">Atom feed</span></a>
  </div>

  <footer>WORLDSCOPE · 22 sections · 12 watch areas · daily at 06:00 ET · sources cited inline</footer>
</div>
</body></html>
"""
    out_root.joinpath("index.html").write_text(page, encoding="utf-8")


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
    render_root_landing(REPO / "dist")
    print(f"  landing → {REPO/'dist'/'index.html'}")


if __name__ == "__main__":
    main()
