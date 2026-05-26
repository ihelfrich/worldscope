"""HTML render layer. Writes one briefing page per day to dist/YYYY-MM-DD.html
plus an index.html that mirrors the most recent."""
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
  </style>
</head>
<body>
<header>
  <div class="titleblock">
    <h1>WORLDSCOPE — Daily Briefing</h1>
    <div class="sub">{date_long} · prepared for Dr. Ian Helfrich</div>
  </div>
  <div class="actions">
    <a href="./zips/{date_str}.zip" download>⬇ Download today's package (.zip)</a>
  </div>
</header>
"""

FOOT = """
<footer>WORLDSCOPE · sources cited inline · synthesis grounded in numbered items only</footer>
</body></html>
"""


def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML for the overview block. Avoids a markdown dep
    by handling only the constructs we actually emit."""
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
            out.append(f"<h2>{line[2:]}</h2>")
        elif line.startswith("## "):
            out.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("### "):
            out.append(f"<h4>{line[4:]}</h4>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    # Bold/italic light pass
    html = "\n".join(out)
    import re
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"<em>\1</em>", html)
    return html


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
        + "\n".join(sections_html)
        + FOOT
    )
    out_path = out_dir / f"{date_obj.isoformat()}.html"
    out_path.write_text(page, encoding="utf-8")
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    return out_path
