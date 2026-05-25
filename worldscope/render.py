"""HTML render layer. Writes one briefing page per day to dist/YYYY-MM-DD.html
plus an index.html that lists the archive."""
from __future__ import annotations

from datetime import date
from pathlib import Path

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
      max-width: 880px; margin: 0 auto; padding: 28px 24px 60px;
      background: #FAFBFD; color: #1F2937; line-height: 1.55;
    }}
    header {{
      border-bottom: 2px solid #1F3864; padding-bottom: 14px; margin-bottom: 24px;
    }}
    header h1 {{
      margin: 0 0 4px; font-size: 26px; color: #1F3864; letter-spacing: -0.3px;
    }}
    header .sub {{
      color: #6B7280; font-size: 14px;
    }}
    section.section {{
      background: #fff; border: 1px solid #E5E7EB; border-radius: 10px;
      padding: 18px 22px; margin: 18px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    section.section h2 {{
      margin: 0 0 8px; font-size: 18px; color: #1F3864;
    }}
    section.section h2 .count {{
      font-size: 12px; color: #6B7280; font-weight: normal;
    }}
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
    .meta {{ color: #6B7280; font-size: 12px; }}
    .abs {{ color: #374151; font-size: 13px; margin-top: 3px; }}
    footer {{
      margin-top: 36px; padding-top: 14px; border-top: 1px solid #E5E7EB;
      color: #6B7280; font-size: 12px; text-align: center;
    }}
    nav.archive {{ margin-bottom: 14px; font-size: 13px; }}
    nav.archive a {{ color: #2E75B6; text-decoration: none; margin-right: 10px; }}
  </style>
</head>
<body>
<header>
  <h1>WORLDSCOPE — Daily Briefing</h1>
  <div class="sub">{date_long} · prepared for Dr. Ian Helfrich</div>
</header>
"""

FOOT = """
<footer>WORLDSCOPE · sources cited inline · synthesis grounded in numbered items only</footer>
</body></html>
"""


def render_page(date_obj: date, sections_html: list[str], out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = HEAD.format(
        date_str=date_obj.isoformat(),
        date_long=date_obj.strftime("%A, %B %-d, %Y"),
    ) + "\n".join(sections_html) + FOOT
    out_path = out_dir / f"{date_obj.isoformat()}.html"
    out_path.write_text(page, encoding="utf-8")
    # Also write index.html → most recent
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    return out_path
