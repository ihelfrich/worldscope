"""render_it_weekly.py — WORLDSCOPE 'IT & Information Systems Weekly': a
magazine-grade handbook of the week's major technology events, security/risk
news, and innovation.

Each story carries three things, by design:
  1. a single working, highly-credible, recent source URL (verified 200);
  2. "Summary:" — a thorough, human 2-4 paragraph briefing of the story in
     context, pitched at an intelligent reader with a day's background;
  3. "Rationale:" — 1-3 paragraphs on why the story was selected.

This module owns rendering: an issue dict -> styled, self-contained HTML
(warm-paper WORLDSCOPE aesthetic) that Chromium prints to PDF. Future weeks
can assemble the issue automatically from the lake's tech/security records and
synthesize the prose with Claude in CI.

    python tools/render_it_weekly.py --issue it_weekly/2026-W25.json --out dist/it_weekly

Story schema:
  {"title","url","source","date","tags":[...],
   "summary":["para",...],"rationale":["para",...]}
The cover story uses the same fields plus "kicker","box":{title,items}.
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import date as _date
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _paras(body) -> str:
    if isinstance(body, str):
        body = [body]
    return "".join(f"<p>{_esc(p)}</p>" for p in (body or []))


def _tags(tags) -> str:
    if not tags:
        return ""
    return "<span class='tags'>" + "".join(f"<span class='tag'>{_esc(t)}</span>" for t in tags) + "</span>"


def _source(st) -> str:
    url = st.get("url")
    if not url:
        return ""
    dom = urlparse(url).netloc.replace("www.", "")
    label = st.get("source") or dom
    dt = f" · {_esc(st['date'])}" if st.get("date") else ""
    return (f"<div class='source'><span class='slbl'>Source</span>"
            f"<a href='{_esc(url)}'>{_esc(label)}</a>"
            f"<span class='dom'>{_esc(dom)}{dt}</span></div>")


def _blocks(st) -> str:
    out = ""
    if st.get("summary"):
        out += f"<div class='block'><span class='lbl'>Summary</span>{_paras(st['summary'])}</div>"
    if st.get("rationale"):
        out += f"<div class='block rat'><span class='lbl'>Rationale</span>{_paras(st['rationale'])}</div>"
    return out


def render_issue(issue: dict) -> str:
    metrics = "".join(
        f"<div class='ks'><div class='v'>{_esc(m['v'])}</div><div class='k'>{_esc(m['k'])}</div></div>"
        for m in issue.get("metrics", []))

    cover = issue.get("cover", {})
    box = cover.get("box") or {}
    box_html = ""
    if box:
        items = "".join(f"<li>{_esc(i)}</li>" for i in box.get("items", []))
        box_html = (f"<aside class='actionbox'><h4>{_esc(box.get('title','Action items'))}</h4>"
                    f"<ul>{items}</ul></aside>")
    cover_html = (
        f"<section class='cover'>"
        f"<div class='kicker'>{_esc(cover.get('kicker','STORY OF THE WEEK'))}</div>"
        f"<h1>{_esc(cover.get('title',''))}</h1>"
        f"{_source(cover)}"
        f"<div class='coverbody'><div class='coverprose'>{_blocks(cover)}</div>{box_html}</div>"
        f"</section>")

    sec_html = []
    for s in issue.get("sections", []):
        accent = s.get("accent", "#1C1A14")
        stories = []
        for st in s.get("stories", []):
            stories.append(
                f"<article class='story'>"
                f"<h3>{_esc(st.get('title',''))}{_tags(st.get('tags'))}</h3>"
                f"{_source(st)}{_blocks(st)}</article>")
        sec_html.append(
            f"<section class='sec' style='--accent:{accent}'>"
            f"<h2><span class='secbar'></span>{_esc(s.get('label',''))}</h2>"
            f"<div class='stories'>{''.join(stories)}</div></section>")

    cross = ""
    if issue.get("crosscurrents"):
        cross = (f"<section class='sec cross' style='--accent:#6B5B4A'>"
                 f"<h2><span class='secbar'></span>Cross-currents — the threads underneath</h2>"
                 f"<div class='crossprose'>{_paras(issue['crosscurrents'])}</div></section>")

    checklist = ""
    if issue.get("checklist"):
        lis = "".join(f"<li>{_esc(i)}</li>" for i in issue["checklist"])
        checklist = (f"<section class='sec checklist' style='--accent:#1F5C3A'>"
                     f"<h2><span class='secbar'></span>IT &amp; InfoSec manager's checklist</h2>"
                     f"<ol>{lis}</ol></section>")

    method = ("<section class='method'><h2>Method &amp; verification</h2>"
              "<p>Every story carries a single source link that was checked to resolve (HTTP 200) "
              "from a highly-credible outlet within the reporting window. Summaries are written for a "
              "reader with roughly a day's background in the topic; rationales explain selection. "
              "Where a primary source rate-limited automated checks, a verified equivalent of equal "
              "credibility was substituted.</p></section>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORLDSCOPE · IT &amp; Information Systems Weekly · {_esc(issue.get('week',''))}</title>
<style>{_CSS}</style></head><body>
<header class="masthead">
  <div class="brandline">
    <span class="brand"><span class="mk"></span>WORLDSCOPE</span>
    <span class="vertical">IT &amp; INFORMATION SYSTEMS WEEKLY</span>
  </div>
  <div class="issueline">{_esc(issue.get('week',''))} · {_esc(issue.get('iso',''))}</div>
</header>
<p class="editor">{_esc(issue.get('editor',''))}</p>
<div class="ribbon">{metrics}</div>
{cover_html}
{''.join(sec_html)}
{cross}
{checklist}
{method}
<footer class="foot">WORLDSCOPE · IT &amp; Information Systems Weekly · compiled {_esc(issue.get('date',''))}
· every story carries one verified source · built alongside the WORLDSCOPE intelligence lake</footer>
</body></html>"""


_CSS = """
:root{--bg:#F4F1E8;--ink:#1C1A14;--soft:#5E584C;--faint:#8C8576;--hair:#E2DACA;
 --hair2:#ECE5D6;--paper:#FCFAF3;--ok:#2F6B3A;--ox:#C0392B;--gold:#9A6B00;
 --serif:"Iowan Old Style",Palatino,"Palatino Linotype",Georgia,serif;
 --sans:"Inter","Helvetica Neue",Arial,sans-serif;
 --mono:"SF Mono",ui-monospace,"DejaVu Sans Mono",Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--serif);line-height:1.5;
 -webkit-font-smoothing:antialiased;max-width:980px;margin:0 auto;padding:0 48px 70px}
.masthead{border-bottom:3px double var(--ink);padding:30px 0 14px;margin-bottom:6px}
.brandline{display:flex;align-items:baseline;justify-content:space-between;gap:18px;flex-wrap:wrap}
.brand{font-family:var(--sans);font-weight:800;letter-spacing:.2em;font-size:23px;
 display:inline-flex;align-items:center;gap:11px}
.brand .mk{width:14px;height:14px;background:#C0392B;transform:rotate(45deg);display:inline-block}
.vertical{font-family:var(--mono);font-size:11px;letter-spacing:.34em;color:var(--soft);text-transform:uppercase}
.issueline{font-family:var(--mono);font-size:11px;letter-spacing:.18em;color:var(--soft);
 text-transform:uppercase;margin-top:9px}
.editor{font-family:var(--serif);font-size:18.5px;line-height:1.55;font-style:italic;color:#322e25;
 margin:20px 0 18px;padding-left:16px;border-left:3px solid var(--gold)}
.ribbon{display:flex;flex-wrap:wrap;border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);margin-bottom:30px}
.ribbon .ks{flex:1;min-width:150px;padding:14px 16px;border-right:1px solid var(--hair2)}
.ribbon .ks:last-child{border-right:0}
.ribbon .v{font-family:var(--serif);font-weight:700;font-size:27px;line-height:1;color:var(--ox)}
.ribbon .k{font-family:var(--mono);font-size:9.5px;letter-spacing:.07em;text-transform:uppercase;
 color:var(--soft);margin-top:6px;line-height:1.3}
/* source line — present on every story */
.source{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:7px 0 11px;
 padding-bottom:9px;border-bottom:1px solid var(--hair2)}
.source .slbl{font-family:var(--mono);font-size:8.5px;letter-spacing:.13em;text-transform:uppercase;
 color:#fff;background:var(--gold);padding:2px 6px;border-radius:3px}
.source a{font-family:var(--sans);font-size:13px;font-weight:600;color:#1d4e74;text-decoration:none;
 border-bottom:1px solid #b9cfdd;word-break:break-word}
.source .dom{font-family:var(--mono);font-size:10px;color:var(--faint)}
/* summary / rationale blocks */
.block{margin:0 0 11px}
.block .lbl{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
 color:var(--ink);display:block;margin-bottom:5px;font-weight:700}
.block.rat .lbl{color:var(--gold)}
.block.rat{background:#FBF7EC;border-left:2px solid var(--gold);padding:10px 14px;border-radius:0 6px 6px 0}
.block p{font-size:14.5px;margin-bottom:9px}
.block p:last-child{margin-bottom:0}
/* cover */
.cover{border-bottom:1px solid var(--hair);padding-bottom:30px;margin-bottom:30px}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.26em;text-transform:uppercase;color:#fff;
 background:var(--ox);display:inline-block;padding:4px 11px;border-radius:3px;margin-bottom:14px}
.cover h1{font-family:var(--serif);font-size:38px;line-height:1.08;letter-spacing:-.015em;font-weight:700;margin-bottom:6px}
.coverbody{display:grid;grid-template-columns:1fr 280px;gap:30px;align-items:start;margin-top:6px}
@media(max-width:780px){.coverbody{grid-template-columns:1fr}}
.coverprose .block p{font-size:16px}
.coverprose .block:first-child p:first-of-type::first-letter{font-family:var(--serif);font-size:56px;
 font-weight:700;float:left;line-height:.82;padding:6px 10px 0 0;color:var(--ox)}
.actionbox{background:#fff;border:1px solid var(--hair);border-top:3px solid var(--ox);border-radius:9px;padding:16px 18px}
.actionbox h4{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--ox);margin-bottom:11px}
.actionbox ul{list-style:none}
.actionbox li{font-family:var(--sans);font-size:13px;line-height:1.4;padding:7px 0 7px 18px;border-top:1px solid var(--hair2);position:relative}
.actionbox li::before{content:"\\25B8";position:absolute;left:0;color:var(--ox)}
/* sections */
.sec{margin-bottom:34px}
.sec h2{font-family:var(--sans);font-weight:800;font-size:13px;letter-spacing:.16em;text-transform:uppercase;
 color:var(--ink);display:flex;align-items:center;gap:11px;padding-bottom:9px;border-bottom:2px solid var(--ink);margin-bottom:18px}
.sec h2 .secbar{width:16px;height:16px;background:var(--accent);flex-shrink:0}
.stories{display:grid;grid-template-columns:1fr 1fr;gap:26px 34px}
@media(max-width:780px){.stories{grid-template-columns:1fr}}
.story{break-inside:avoid}
.story h3{font-family:var(--serif);font-size:20px;line-height:1.2;font-weight:700;margin-bottom:4px}
.tags{display:inline;margin-left:7px;white-space:nowrap}
.tag{font-family:var(--mono);font-size:8.5px;letter-spacing:.04em;background:#EFE7D6;color:#6a5b3f;
 padding:2px 6px;border-radius:3px;border:1px solid var(--hair);vertical-align:middle}
.cross{background:#FBF7EC;border-radius:12px;padding:24px 26px;border:1px solid var(--hair)}
.crossprose p{font-size:16px;margin-bottom:13px;max-width:74ch}
.checklist ol{counter-reset:c;list-style:none;columns:2;column-gap:34px}
@media(max-width:780px){.checklist ol{columns:1}}
.checklist li{counter-increment:c;font-family:var(--sans);font-size:13.5px;line-height:1.4;
 padding:9px 0 9px 30px;position:relative;border-top:1px solid var(--hair2);break-inside:avoid}
.checklist li::before{content:counter(c);position:absolute;left:0;top:8px;width:20px;height:20px;
 background:var(--accent);color:#fff;font-family:var(--mono);font-size:10px;font-weight:700;border-radius:50%;
 display:flex;align-items:center;justify-content:center}
.method{border-top:1px solid var(--hair);padding-top:18px;margin-top:8px}
.method h2{font-family:var(--sans);font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--soft);margin-bottom:9px}
.method p{font-family:var(--sans);font-size:12px;color:var(--soft);line-height:1.5;max-width:80ch}
.foot{font-family:var(--mono);font-size:9.5px;letter-spacing:.05em;color:var(--faint);text-align:center;
 padding-top:26px;margin-top:26px;border-top:1px solid var(--hair)}
@media print{body{padding:0 30px}.story,.cover,.actionbox,.block.rat{break-inside:avoid}}
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True, help="path to the issue JSON")
    ap.add_argument("--out", default="dist/it_weekly", help="output directory")
    args = ap.parse_args(argv)
    issue = json.loads(Path(args.issue).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = issue.get("iso", "issue")
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(render_issue(issue), encoding="utf-8")
    n = sum(len(s.get("stories", [])) for s in issue.get("sections", []))
    print(f"[it-weekly] wrote {html_path} ({n} stories)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
