"""
render_brief.py: convert briefings/<date>.md and weekly_briefings/<week>.md
into publication-grade HTML at dist/briefings/<date>.html etc.

Visual design is delivered by the compiled Tailwind stylesheet at
/dist/assets/tailwind.css (built from assets/src/tailwind.input.css with the
heritage-palette daisyUI theme). This module emits semantic markup that
references those utility and component classes; it does NOT ship its own CSS.

Reads:
  briefings/<date>.md         routine output, markdown
  briefings/<date>-*.png      routine-generated charts
  briefings/<date>-events.geojson  optional event geometry
  watchareas.yaml             watch-area definitions for the dashboard

Writes:
  dist/briefings/<date>.html
  dist/briefings/<date>-*.png  (copied so the HTML can reference them)
  dist/briefings/index.html    points to latest + archive
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

# Brief pages live two levels deep under dist/ (dist/briefings/<date>.html), so
# the stylesheet sits at ../assets/tailwind.css from a brief's perspective.
ASSETS_PREFIX_BRIEF = "../assets/"
# Root-level pages (dist/index.html) reference assets at ./assets/.
ASSETS_PREFIX_ROOT = "./assets/"


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


# Shared <head> fragment: stylesheet links, font preloads, OG metadata stub.
def _head(title: str, stem: str, assets_prefix: str, *, description: str = "",
          canonical: str = "") -> str:
    desc = html.escape(description or f"WORLDSCOPE briefing: {stem}", quote=True)
    canon = html.escape(canonical or f"https://ihelfrich.github.io/worldscope/briefings/{stem}.html",
                        quote=True)
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>WORLDSCOPE · {html.escape(title)}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canon}">
<meta property="og:type" content="article">
<meta property="og:title" content="{html.escape(title, quote=True)}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{canon}">
<meta property="og:site_name" content="WORLDSCOPE">
<meta name="twitter:card" content="summary">
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=JetBrains+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="{assets_prefix}tailwind.css">
<script src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js" defer></script>
<script src="https://unpkg.com/lucide@0.452.0/dist/umd/lucide.min.js" defer></script>"""


def _navbar(*, brief_stem: str | None = None, assets_prefix: str = "../") -> str:
    """daisyUI navbar with brand, primary links, mobile collapse via Alpine.js.

    `assets_prefix` is the relative path from the current page to dist/, e.g.
    "../" for a /briefings/<date>.html page or "./" for /index.html.
    """
    zip_href = (f"{assets_prefix}zips/{html.escape(brief_stem)}.zip"
                if brief_stem else f"{assets_prefix}zips/")
    return f"""<header class="ws-navbar" x-data="{{ open: false }}">
  <div class="mx-auto max-w-7xl px-5 lg:px-8 flex items-center gap-6 py-3">
    <a href="{assets_prefix}index.html" class="ws-brand text-sm">WORLDSCOPE</a>
    <nav class="hidden lg:flex items-center gap-5 text-sm font-sans" aria-label="Primary">
      <a href="{assets_prefix}index.html">Today</a>
      <a href="{assets_prefix}sections/">Sections</a>
      <a href="{assets_prefix}briefings/">Archive</a>
      <a href="{html.escape(zip_href, quote=True)}">Bundle</a>
    </nav>
    <div class="flex-1"></div>
    <a class="hidden lg:inline-block text-xs text-mist hover:text-white pl-4 ml-2 border-l border-white/20"
       href="https://ihelfrich.github.io/" target="_blank" rel="noopener noreferrer">
      helfrich.github.io →
    </a>
    <button type="button" class="ws-mobile-toggle" @click="open = !open"
            :aria-expanded="open.toString()" aria-controls="ws-mobile-menu"
            aria-label="Toggle navigation">
      <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
           stroke-linejoin="round" x-show="!open" aria-hidden="true">
        <line x1="3" y1="6" x2="21" y2="6"></line>
        <line x1="3" y1="12" x2="21" y2="12"></line>
        <line x1="3" y1="18" x2="21" y2="18"></line>
      </svg>
      <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
           stroke-linejoin="round" x-show="open" x-cloak aria-hidden="true">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
    </button>
  </div>
  <nav id="ws-mobile-menu" class="lg:hidden border-t border-white/10 bg-navy-soft"
       x-show="open" x-cloak x-transition>
    <ul class="flex flex-col px-5 py-3 gap-2 text-sm font-sans">
      <li><a href="{assets_prefix}index.html" class="block py-1.5">Today</a></li>
      <li><a href="{assets_prefix}sections/" class="block py-1.5">Sections</a></li>
      <li><a href="{assets_prefix}briefings/" class="block py-1.5">Archive</a></li>
      <li><a href="{html.escape(zip_href, quote=True)}" class="block py-1.5">Today's bundle</a></li>
      <li><a href="https://ihelfrich.github.io/" target="_blank" rel="noopener noreferrer" class="block py-1.5">helfrich.github.io</a></li>
    </ul>
  </nav>
</header>"""


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
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        slug = by_text.get(plain) or slugify(plain)
        return (f'<{tag} id="{slug}">{inner} '
                f'<a class="heading-anchor" href="#{slug}" aria-label="link to section">§</a>'
                f'</{tag}>')

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
        return (f'<a href="{href}">{text}</a>'
                f'<span class="ws-hostpill">{host}</span>')

    return re.sub(r'<a href="(https?://[^"]+)"[^>]*>([^<]+)</a>', repl, html_body)


def callout_pass(html_body: str) -> str:
    """Turn lines containing 'anomaly z=' / 'Δ' / 'ALERT:' into Tailwind callouts."""
    def wrap(match: re.Match) -> str:
        p = match.group(0)
        body_text = re.sub(r"<[^>]+>", "", p)
        kind = ""
        label = ""
        if re.search(r"\b(ALERT|RED FLAG|breach|surge|spike)\b", body_text, re.I):
            kind = "is-danger"; label = "Alert"
        elif re.search(r"\bz\s*=|\banomaly\b|\bz-score\b|σ\b|Δ\b",
                       body_text, re.I):
            kind = ""; label = "Anomaly"
        elif re.search(r"\b(cooling|easing|de-escalation)\b", body_text, re.I):
            kind = "is-good"; label = "Easing"
        if not label:
            return p
        return (f'<div class="ws-callout {kind}">'
                f'<span class="ws-label">{label}</span>{p}</div>')

    return re.sub(
        r"<p>(?:(?!</p>).)*?(?:ALERT|RED FLAG|anomaly|z-score|σ|Δ"
        r"|breach|surge|spike|cooling|easing|de-escalation)(?:(?!</p>).)*?</p>",
        wrap, html_body, flags=re.I,
    )


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

    Empty zero-mention watch areas still appear in the sidebar nav (so the user
    can jump to them), but they do not pollute the above-the-fold dashboard.
    Threshold can be overridden per-area via `dashboard_min_mentions`.
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
        snippet_re = re.compile(
            re.escape(name.lower()) + r".{0,200}(alert|surge|spike|breach|red flag)",
            re.I | re.S,
        )
        if snippet_re.search(md_text):
            alert = "is-alert"
        threshold = int(a.get("dashboard_min_mentions", 1))
        if mentions < threshold and not alert:
            continue
        topics = (a.get("topics") or [])[:3]
        topics_str = ", ".join(topics) if topics else ""
        slug = slugify(name)
        priority_cls = {"high": "is-high", "low": "is-low"}.get(priority, "")
        tiles.append(
            f'<div class="ws-dash-tile {priority_cls} {alert}">'
            f'<h4><a href="#{slug}" class="text-inherit no-underline hover:text-navy">'
            f'{html.escape(name)}</a></h4>'
            f'<div class="ws-stat">{mentions}</div>'
            f'<div class="ws-meta">{html.escape(topics_str)}</div>'
            f"</div>"
        )
    if not tiles:
        return ""
    return ('<section aria-label="Watch-area dashboard" '
            'class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 my-6">'
            + "".join(tiles) + '</section>')


def strip_google_news_links(html_body: str) -> str:
    """Replace <a href="https://news.google.com/rss/...">X</a> with just X.

    Google News proxy URLs serve raw XML to the browser instead of redirecting
    to the article. They are unsafe to expose as clickable links.
    """
    return re.sub(
        r'<a\s+href="https://news\.google\.com/rss[^"]*"[^>]*>([^<]*)</a>',
        r'\1',
        html_body,
    )


def dedupe_images(html_body: str) -> str:
    """Strip duplicate <img> tags pointing at the same src."""
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
    """Remove the synthesis-time 'bundle zip was unavailable' DATA NOTE when
    the zip is actually present at render time."""
    if not zip_exists_now:
        return md_text
    return re.sub(
        r"^>\s*\*\*DATA NOTE:?\*\*[^\n]*bundle zip was unavailable[^\n]*(\n>.*)*\n?",
        "",
        md_text,
        flags=re.M | re.I,
    )


def sidebar_html(headings: list[tuple[int, str, str]], areas: list[dict]) -> str:
    out: list[str] = ['<nav class="ws-sidebar" aria-label="Table of contents">']
    if areas:
        out.append('<h3>Watch areas</h3><ul>')
        for a in areas:
            name = a.get("name", "")
            if not name:
                continue
            out.append(
                f'<li><a href="#{slugify(name)}">{html.escape(name)}</a></li>'
            )
        out.append('</ul>')
    out.append('<h3>Sections</h3><ul>')
    for level, text, slug in headings:
        if level not in (2, 3):
            continue
        indent_cls = " pl-4" if level == 3 else ""
        out.append(
            f'<li><a class="{indent_cls}" href="#{slug}">{html.escape(text)}</a></li>'
        )
    out.append('</ul></nav>')
    return "".join(out)


def discover_assets(brief_dir: Path, stem: str) -> tuple[list[Path], Path | None]:
    """Find <stem>-*.png siblings and a <stem>-events.geojson if present."""
    pngs = sorted(brief_dir.glob(f"{stem}-*.png"))
    geo = brief_dir / f"{stem}-events.geojson"
    return pngs, (geo if geo.exists() else None)


def _brief_network_seed() -> str:
    """Inline JSON seed for the ambient canvas (today's cross-section recurrences)."""
    import datetime as _dt2
    today = _dt2.date.today().isoformat()
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


def render_one(md_path: Path, out_dir: Path, kind: str) -> Path:
    md_text = md_path.read_text(encoding="utf-8")
    stem = md_path.stem
    zip_path = REPO / "dist" / "zips" / f"{stem}.zip"
    md_text = strip_stale_zip_notice(md_text, zip_path.exists())
    # Extract first H1 as title
    title_match = re.match(r"^#\s+(.+)$", md_text, flags=re.M)
    title = title_match.group(1).strip() if title_match else stem
    body_md = re.sub(r"^#\s+.+$", "", md_text, count=1, flags=re.M)
    body_html = markdown.markdown(
        body_md,
        extensions=["tables", "fenced_code", "attr_list", "toc"],
    )
    headings = extract_headings(md_text)
    body_html = inject_heading_anchors(body_html, headings)
    body_html = host_pill_links(body_html)
    body_html = callout_pass(body_html)
    body_html = dedupe_images(body_html)
    body_html = strip_google_news_links(body_html)
    areas = load_watch_dashboard()
    dash = dashboard_html(areas, md_text)
    side = sidebar_html(headings, areas)
    brief_dir = md_path.parent
    pngs, geo = discover_assets(brief_dir, stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    for png in pngs:
        shutil.copy(png, out_dir / png.name)
    map_section = ""
    if geo:
        try:
            fc = json.loads(geo.read_text(encoding="utf-8"))
            map_section = (
                '<h2 id="event-map">Event map</h2>'
                '<div id="gridmap" class="w-full h-96 rounded-md border border-mist '
                'my-4 overflow-hidden"></div>'
                + LEAFLET_SCRIPT.replace("%%GEOJSON%%", json.dumps(fc))
            )
        except Exception:
            map_section = ""
    eyebrow = "Daily briefing" if kind == "briefings" else "Weekly briefing"
    seed = html.escape(_brief_network_seed(), quote=True)
    head = _head(stem, stem, ASSETS_PREFIX_BRIEF,
                 description=f"WORLDSCOPE {eyebrow.lower()}: {title}",
                 canonical=f"https://ihelfrich.github.io/worldscope/{kind}/{stem}.html")
    navbar = _navbar(brief_stem=stem, assets_prefix="../")
    page = f"""<!doctype html>
<html lang="en" data-theme="heritage"><head>
{head}
</head><body class="bg-parchment">
<div class="ws-bg" aria-hidden="true">
  <canvas id="ws-network"></canvas>
</div>
<script type="application/json" id="ws-network-seed">{seed}</script>
<script src="../assets/network.js" defer></script>
{navbar}
<div class="ws-shell grid grid-cols-1 lg:grid-cols-[14rem_minmax(0,1fr)] gap-9">
  <aside class="hidden lg:block">
    {side}
  </aside>
  <main>
    <header class="ws-masthead">
      <div class="ws-eyebrow">WORLDSCOPE · {eyebrow}</div>
      <h1>{html.escape(title)}</h1>
      <div class="ws-dateline">prepared for Dr. Ian Helfrich · {html.escape(stem)}</div>
    </header>
    {dash}
    <article class="prose prose-slate max-w-none">
      {body_html}
      {map_section}
    </article>
    <footer class="ws-foot mt-16 pt-5 border-t border-mist font-sans text-xs text-slate flex flex-wrap justify-between gap-3">
      <div>WORLDSCOPE · all claims trace to inline sources · synthesis grounded in bundle items</div>
      <div><a href="./index.html" class="text-navy hover:text-gold">archive</a></div>
    </footer>
  </main>
</div>
<script>document.addEventListener('DOMContentLoaded',function(){{if(window.lucide)window.lucide.createIcons();}});</script>
</body></html>
"""
    out_path = out_dir / f"{stem}.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path


def _brief_meta(md_path: Path) -> dict:
    """Pull headline + teaser + word count from a briefing .md."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return {"headline": md_path.stem, "teaser": "", "word_count": 0}
    h1 = re.search(r"^#\s+(.+)$", text, flags=re.M)
    headline = h1.group(1).strip() if h1 else md_path.stem
    after_h2 = re.split(r"^##\s+", text, maxsplit=1, flags=re.M)
    target = after_h2[1] if len(after_h2) > 1 else text
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


def render_index(out_dir: Path, kind: str) -> None:
    pages = sorted(out_dir.glob("*.html"), reverse=True)
    pages = [p for p in pages if p.name != "index.html"]
    if not pages:
        return
    src_dir = REPO / kind
    cards: list[str] = []
    for p in pages:
        md = src_dir / f"{p.stem}.md"
        meta = _brief_meta(md) if md.exists() else {
            "headline": p.stem, "teaser": "", "word_count": 0,
        }
        try:
            d = _date_from_stem(p.stem, kind)
        except Exception:
            d = p.stem
        wc = meta["word_count"]
        wc_str = f"{wc:,} words" if wc else ""
        cards.append(f"""<a class="ws-section-card" href="./{p.name}">
  <div class="font-sans text-xs font-bold uppercase tracking-widest text-navy">{html.escape(d)}</div>
  <h2 class="ws-name mt-1">{html.escape(meta['headline'])}</h2>
  <p class="ws-desc">{html.escape(meta['teaser'])}</p>
  <div class="ws-count">{wc_str}</div>
</a>""")
    label = "Daily briefings" if kind == "briefings" else "Weekly briefings"
    other = "Weekly" if kind == "briefings" else "Daily"
    other_path = "../weekly_briefings/" if kind == "briefings" else "../briefings/"
    head = _head(label, label, ASSETS_PREFIX_BRIEF,
                 description=f"WORLDSCOPE {label.lower()} archive",
                 canonical=f"https://ihelfrich.github.io/worldscope/{kind}/")
    navbar = _navbar(assets_prefix="../")
    page = f"""<!doctype html>
<html lang="en" data-theme="heritage"><head>
{head}
<link rel="alternate" type="application/atom+xml" title="WORLDSCOPE {label}" href="./feed.xml">
</head><body class="bg-parchment">
{navbar}
<div class="ws-shell-narrow">
  <header class="ws-masthead">
    <div class="ws-eyebrow">WORLDSCOPE · archive</div>
    <h1>{label}</h1>
    <div class="ws-dateline">prepared for Dr. Ian Helfrich · {len(pages)} brief{'s' if len(pages)!=1 else ''} on file</div>
  </header>
  <div class="font-sans text-sm text-navy mb-5 flex flex-wrap gap-4">
    <span class="font-bold">{label}</span>
    <a href="{other_path}" class="text-slate hover:text-gold no-underline">{other} archive</a>
    <a href="./feed.xml" class="text-slate hover:text-gold no-underline">Atom feed</a>
  </div>
  <div class="grid grid-cols-1 gap-3">
    {"".join(cards)}
  </div>
  <footer class="ws-foot mt-16 pt-5 border-t border-mist font-sans text-xs text-slate">
    WORLDSCOPE · daily global intelligence · sources cited inline in every brief
  </footer>
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
    return stem


def _render_feed(out_dir: Path, kind: str, pages: list[Path]) -> None:
    """Atom feed at <out_dir>/feed.xml."""
    label = "Daily briefings" if kind == "briefings" else "Weekly briefings"
    src_dir = REPO / kind
    base = f"https://ihelfrich.github.io/worldscope/{kind}"
    entries: list[str] = []
    for p in pages[:50]:
        md = src_dir / f"{p.stem}.md"
        meta = _brief_meta(md) if md.exists() else {
            "headline": p.stem, "teaser": "",
        }
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

    Strategy: the landing page IS the latest brief. We copy the rendered HTML
    of the newest brief verbatim and rewrite its internal nav and asset paths
    so relative links still resolve from /worldscope/ instead of
    /worldscope/briefings/. This gives visitors the rich brief on first load
    instead of a sparse hero card.
    """
    daily_dir = out_root / "briefings"
    latest = sorted(daily_dir.glob("*.html"), reverse=True) if daily_dir.exists() else []
    latest = [p for p in latest if p.name != "index.html"]
    if not latest:
        return
    newest = latest[0]
    rich = newest.read_text(encoding="utf-8")
    # Re-anchor every ../ relative link to ./.
    # This includes the stylesheet (../assets/tailwind.css), the network script
    # (../assets/network.js), all nav links, and the bundle download.
    rich = rich.replace('href="../assets/', 'href="./assets/')
    rich = rich.replace('src="../assets/', 'src="./assets/')
    rich = rich.replace('href="../index.html"', 'href="./index.html"')
    rich = rich.replace('href="../sections/"', 'href="./sections/"')
    rich = rich.replace('href="../briefings/"', 'href="./briefings/"')
    rich = rich.replace('href="../zips/', 'href="./zips/')
    # The brief's "archive" footer link is `./index.html` from /briefings/ but
    # would clash with the root index from /. Rewrite to point at the archive.
    rich = rich.replace('href="./index.html" class="text-navy hover:text-gold">archive</a>',
                        'href="./briefings/" class="text-navy hover:text-gold">archive</a>')
    # The brief's nav links Today (../index.html) now collide with the root
    # index it lives in. They already resolved correctly to ./index.html
    # because of the rewrites above; the result is a self-link, which is fine.
    # Image refs: the brief markdown uses bare filenames like
    # <img src="2026-05-27-anomaly_screen.png">; resolve them through
    # /briefings/ when served from the root.
    rich = re.sub(
        r'(<img[^>]+src=")(\d{4}-\d{2}-\d{2}-[^"]+)(")',
        r'\1./briefings/\2\3',
        rich,
    )
    (out_root / "index.html").write_text(rich, encoding="utf-8")


def main() -> None:
    for kind in ("briefings", "weekly_briefings"):
        src = REPO / kind
        if not src.exists():
            continue
        out_dir = REPO / "dist" / kind
        out_dir.mkdir(parents=True, exist_ok=True)
        for md in sorted(src.glob("*.md")):
            out = render_one(md, out_dir, kind)
            print(f"  {md} -> {out}")
        render_index(out_dir, kind)
    render_root_landing(REPO / "dist")
    print(f"  landing -> {REPO/'dist'/'index.html'}")


if __name__ == "__main__":
    main()
