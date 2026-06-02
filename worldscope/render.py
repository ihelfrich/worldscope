"""HTML render layer. Writes one data-brief page per day to dist/YYYY-MM-DD.html
(the full all-sections drill-down).

It deliberately does NOT write dist/index.html: the site homepage is the
publication-grade "landing" page emitted by tools/render_brief.py (heritage
Tailwind + Alpine living-brief UI). Letting this plain renderer also write
index.html caused the homepage to flip between the two whenever the daily data
pull ran after the briefing render. The landing page is now the sole owner of
index.html; every site_builder nav already points "Today" → index.html."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional


HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>WORLDSCOPE — {date_str}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
      max-width: 920px; margin: 0 auto; padding: 28px 24px 60px;
      background: #FAFBFD; color: #1F2937; line-height: 1.55;
    }}
    header {{
      border-bottom: 2px solid #1F3864; padding-bottom: 14px; margin-bottom: 24px;
      display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 14px;
    }}
    header .titleblock h1 {{
      margin: 0 0 4px; font-size: 26px; color: #1F3864; letter-spacing: -0.3px;
    }}
    header .titleblock .sub {{ color: #6B7280; font-size: 14px; }}
    header .actions a {{
      display: inline-block; background: #1F3864; color: #fff; text-decoration: none;
      padding: 10px 16px; border-radius: 8px; font-size: 14px; font-weight: 600;
      box-shadow: 0 2px 6px rgba(31,56,100,0.25); transition: background 0.15s;
    }}
    header .actions a:hover {{ background: #2E75B6; }}
    .overview {{
      background: #fff; border: 1px solid #C8CDD3; border-left: 4px solid #1F3864;
      border-radius: 10px; padding: 18px 22px; margin-bottom: 22px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    }}
    .overview h2 {{ color: #1F3864; margin-top: 0; font-size: 20px; }}
    .overview h3 {{ color: #1F3864; margin: 16px 0 6px; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .overview p, .overview li {{ font-size: 14.5px; }}
    section.section {{
      background: #fff; border: 1px solid #E5E7EB; border-radius: 10px;
      padding: 18px 22px; margin: 18px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    section.section h2 {{ margin: 0 0 8px; font-size: 18px; color: #1F3864; }}
    section.section h2 .count {{ font-size: 12px; color: #6B7280; font-weight: normal; }}
    p.synth {{
      font-size: 15px; color: #111827; background: #F0F4F8;
      padding: 12px 14px; border-left: 3px solid #2E75B6;
      border-radius: 4px; margin: 10px 0 14px;
    }}
    ul.items {{ list-style: none; padding: 0; margin: 0; }}
    ul.items li {{
      padding: 8px 0; border-bottom: 1px solid #F3F4F6; font-size: 14px;
    }}
    ul.items li:last-child {{ border-bottom: none; }}
    ul.items a {{ color: #1F3864; text-decoration: none; font-weight: 500; }}
    ul.items a:hover {{ text-decoration: underline; }}
    .new-badge {{
      background: #F59E0B; color: #fff; font-size: 10px; font-weight: 700;
      padding: 2px 6px; border-radius: 3px; margin-right: 6px; letter-spacing: 0.5px;
    }}
    .stale-badge {{
      display: inline-block; margin-left: 8px;
      font-size: 11px; font-weight: 600; padding: 3px 8px;
      border-radius: 4px; letter-spacing: 0.3px; vertical-align: middle;
    }}
    .stale-carry  {{ background: #FFF2CC; color: #856404; border: 1px solid #E6C75A; }}
    .stale-failed {{ background: #FCE4D6; color: #8B3A0E; border: 1px solid #D27F5A; }}
    .stale-none   {{ background: #E5E7EB; color: #4B5563; border: 1px solid #C8CDD3; }}
    .items li.empty {{ color: #6B7280; font-style: italic; }}
    .meta {{ color: #6B7280; font-size: 12px; }}
    .abs {{ color: #374151; font-size: 13px; margin-top: 3px; }}
    footer {{
      margin-top: 36px; padding-top: 14px; border-top: 1px solid #E5E7EB;
      color: #6B7280; font-size: 12px; text-align: center;
    }}
    nav.archive {{ margin: 10px 0 22px; font-size: 13px; }}
    nav.archive a {{ color: #2E75B6; text-decoration: none; margin-right: 10px; }}
    nav.archive a:hover {{ text-decoration: underline; }}

    /* ---- living-brief enhancements: typing ticker + sideways deck ----
       All progressive: every element below is inert markup that JS animates.
       With JS off, the ticker stays display:none and the deck toggle is a
       no-op, so the page is byte-for-byte the classic reading view. */
    .ws-toolbar {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
    .ws-viewbtn {{
      display: inline-block; background: #fff; color: #1F3864; cursor: pointer;
      border: 1px solid #1F3864; padding: 9px 14px; border-radius: 8px;
      font-size: 13px; font-weight: 600; font-family: inherit; transition: all 0.15s;
    }}
    .ws-viewbtn:hover {{ background: #1F3864; color: #fff; }}
    #ws-ticker {{
      display: none; align-items: center; gap: 12px;
      background: #0F1B2D; color: #E6ECF5; border-radius: 10px;
      padding: 11px 16px; margin: 0 0 20px; min-height: 44px;
      font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
      font-size: 14px; overflow: hidden;
    }}
    #ws-ticker .lab {{
      color: #F59E0B; font-weight: 700; letter-spacing: 0.12em;
      font-size: 11px; flex: none;
    }}
    #ws-ticker .gt {{ color: #7FB2E8; flex: none; }}
    #ws-ticker-line {{ white-space: nowrap; overflow: hidden; text-overflow: clip; }}
    #ws-ticker .caret {{
      display: inline-block; width: 8px; height: 1.05em; background: #F59E0B;
      margin-left: 1px; vertical-align: -2px; flex: none;
      animation: wsblink 1.05s steps(1) infinite;
    }}
    @keyframes wsblink {{ 0%, 50% {{ opacity: 1; }} 50.01%, 100% {{ opacity: 0; }} }}
    @media (prefers-reduced-motion: reduce) {{ #ws-ticker .caret {{ animation: none; }} }}

    /* sideways deck: horizontal scroll-snap columns */
    #ws-sections.ws-deck {{
      display: flex; gap: 18px; overflow-x: auto; overflow-y: hidden;
      scroll-snap-type: x mandatory; scroll-behavior: smooth;
      padding: 4px 0 16px; margin: 0 -4px;
    }}
    #ws-sections.ws-deck > section.section {{
      flex: 0 0 min(560px, 86vw); scroll-snap-align: start;
      height: 72vh; overflow-y: auto; margin: 0;
    }}
    #ws-sections.ws-deck::-webkit-scrollbar {{ height: 9px; }}
    #ws-sections.ws-deck::-webkit-scrollbar-thumb {{ background: #C8CDD3; border-radius: 99px; }}
    .ws-deck-nav {{
      position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%);
      display: none; gap: 10px; z-index: 50;
    }}
    .ws-deck-nav.show {{ display: flex; }}
    .ws-deck-nav button {{
      width: 46px; height: 46px; border-radius: 50%; border: none; cursor: pointer;
      background: #1F3864; color: #fff; font-size: 20px; line-height: 1;
      box-shadow: 0 4px 14px rgba(31,56,100,0.35); transition: background 0.15s;
    }}
    .ws-deck-nav button:hover {{ background: #2E75B6; }}
    @media (max-width: 600px) {{
      #ws-sections.ws-deck > section.section {{ flex-basis: 92vw; height: 76vh; }}
    }}
  </style>
</head>
<body>
<header>
  <div class="titleblock">
    <h1>WORLDSCOPE — Daily Briefing</h1>
    <div class="sub">{date_long} · prepared for Dr. Ian Helfrich</div>
  </div>
  <div class="actions ws-toolbar">
    <button type="button" id="ws-view-toggle" class="ws-viewbtn" aria-pressed="false">▦ Deck view</button>
    <a href="./zips/{date_str}.zip" download>⬇ Download today's package (.zip)</a>
  </div>
</header>
<div id="ws-ticker" aria-label="Live headline ticker" aria-live="off">
  <span class="lab">LIVE</span><span class="gt">&raquo;</span><span id="ws-ticker-line"></span><span class="caret" aria-hidden="true"></span>
</div>
"""

FOOT = """
<div class="ws-deck-nav" id="ws-deck-nav" aria-hidden="true">
  <button type="button" id="ws-deck-prev" aria-label="Previous section">&#8592;</button>
  <button type="button" id="ws-deck-next" aria-label="Next section">&#8594;</button>
</div>
<footer>WORLDSCOPE · sources cited inline · synthesis grounded in numbered items only</footer>
<script>
(function () {
  "use strict";
  var sections = document.getElementById("ws-sections");

  /* ---- typing headline ticker: type a key headline, hold, erase, next ---- */
  var line = document.getElementById("ws-ticker-line");
  var bar = document.getElementById("ws-ticker");
  if (line && bar && sections) {
    var heads = [];
    var secs = sections.querySelectorAll("section.section");
    for (var s = 0; s < secs.length; s++) {
      var h2 = secs[s].querySelector("h2");
      var label = "";
      if (h2) {
        var clone = h2.cloneNode(true);
        var strip = clone.querySelectorAll(".count, .stale-badge");
        for (var k = 0; k < strip.length; k++) { strip[k].remove(); }
        label = clone.textContent.replace(/\\s+/g, " ").trim();
      }
      var links = secs[s].querySelectorAll("ul.items li a");
      for (var l = 0; l < links.length && l < 2; l++) {
        var t = links[l].textContent.replace(/\\s+/g, " ").trim();
        if (t && t.length > 3) {
          heads.push(label ? (label.toUpperCase() + "  \\u2014  " + t) : t);
        }
      }
    }
    if (heads.length) {
      bar.style.display = "flex";
      var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      var i = 0, j = 0, del = false;
      function tick() {
        var full = heads[i];
        if (reduce) {
          line.textContent = full;
          i = (i + 1) % heads.length;
          return setTimeout(tick, 4000);
        }
        if (!del) {
          j++; line.textContent = full.slice(0, j);
          if (j >= full.length) { del = true; return setTimeout(tick, 2400); }
          return setTimeout(tick, 34);
        } else {
          j--; line.textContent = full.slice(0, j);
          if (j <= 0) { del = false; i = (i + 1) % heads.length; return setTimeout(tick, 420); }
          return setTimeout(tick, 16);
        }
      }
      setTimeout(tick, 600);
    }
  }

  /* ---- sideways deck view: flip the stack into horizontal columns ---- */
  var toggle = document.getElementById("ws-view-toggle");
  var nav = document.getElementById("ws-deck-nav");
  if (toggle && sections) {
    var isDeck = function () { return sections.classList.contains("ws-deck"); };
    var step = function (dir) {
      var w = Math.max(320, Math.round(sections.clientWidth * 0.85));
      sections.scrollBy({ left: dir * w, behavior: "smooth" });
    };
    var setDeck = function (on) {
      sections.classList.toggle("ws-deck", on);
      toggle.textContent = on ? "\\u25A4 Reading view" : "\\u25A6 Deck view";
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      if (nav) {
        nav.classList.toggle("show", on);
        nav.setAttribute("aria-hidden", on ? "false" : "true");
      }
      try { localStorage.setItem("ws-view", on ? "deck" : "read"); } catch (e) {}
      if (on) { sections.scrollLeft = 0; }
    };
    toggle.addEventListener("click", function () { setDeck(!isDeck()); });
    var prev = document.getElementById("ws-deck-prev");
    var next = document.getElementById("ws-deck-next");
    if (prev) prev.addEventListener("click", function () { step(-1); });
    if (next) next.addEventListener("click", function () { step(1); });
    document.addEventListener("keydown", function (e) {
      if (!isDeck()) return;
      if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (e.key === "Escape") { setDeck(false); }
    });
    try { if (localStorage.getItem("ws-view") === "deck") setDeck(true); } catch (e) {}
  }
})();
</script>
</body></html>
"""


def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML for the overview block. Avoids a markdown dep
    by handling only the constructs we actually emit.

    The overview text is LLM-generated from untrusted upstream feed content,
    so each line's text is HTML-escaped *before* it is wrapped in a tag. The
    bold/italic regex pass then runs on the already-escaped text, turning our
    own ``*``/``**`` markers into tags without letting raw feed HTML through.
    """
    import html as _html

    def esc(text: str) -> str:
        return _html.escape(text, quote=False)

    out: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("")
            continue
        if line.startswith("# "):
            out.append(f"<h2>{esc(line[2:])}</h2>")
        elif line.startswith("## "):
            out.append(f"<h3>{esc(line[3:])}</h3>")
        elif line.startswith("### "):
            out.append(f"<h4>{esc(line[4:])}</h4>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc(line[2:])}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{esc(line)}</p>")
    if in_list:
        out.append("</ul>")
    # Bold/italic light pass — runs on escaped text, so only our own markers
    # become tags.
    html_out = "\n".join(out)
    import re
    html_out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_out)
    html_out = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"<em>\1</em>", html_out)
    return html_out


def render_page(
    date_obj: date,
    sections_html: list[str],
    out_dir: Path,
    *,
    overview_md: Optional[str] = None,
    archive_dates: list[date] | None = None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_html = ""
    if archive_dates:
        links = " ".join(
            f"<a href='./{d.isoformat()}.html'>{d.isoformat()}</a>"
            for d in archive_dates[-30:]
        )
        archive_html = f"<nav class='archive'>archive · {links}</nav>"
    overview_html = ""
    if overview_md:
        overview_html = f"<div class='overview'>{_md_to_html(overview_md)}</div>"
    page = (
        HEAD.format(
            date_str=date_obj.isoformat(),
            date_long=date_obj.strftime("%A, %B %-d, %Y"),
        )
        + archive_html
        + overview_html
        + "<main id='ws-sections' class='ws-stack'>"
        + "\n".join(sections_html)
        + "</main>"
        + FOOT
    )
    out_path = out_dir / f"{date_obj.isoformat()}.html"
    out_path.write_text(page, encoding="utf-8")
    # NB: intentionally does not write index.html — the landing page from
    # tools/render_brief.py owns the homepage (see module docstring).
    return out_path
