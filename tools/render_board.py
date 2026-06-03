"""render_board.py — WORLDSCOPE 'situation board': a scannable, dense, ADHD-
friendly layout whose hero is the *cross-domain threads* (themes lighting up
across multiple domains at once — the polymath 'what's connecting underneath'),
backed by color-coded, glanceable per-domain modules with depth-on-demand.

Different on purpose from the linear brief: modular, color-anchored, hop-around.
Each thread expands to its full claim, assessment, and provenance; each domain
is a fixed color; real maps/indicators/markets/outlook ride underneath. The
user controls which blocks show (cog, top-right). Bolder, livelier register.

Standalone prototype -> dist/mockups/next/board.html.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import render_reasoned as rr  # noqa: E402  (reuse gather + helpers + rich sections)

OUT = REPO / "dist" / "mockups" / "next" / "board.html"

# section -> domain, and domain -> (label, accent). Accents pushed bolder /
# more saturated than the reasoned palette — the user asked for "more alive".
DOMAINS = {
    "war":    ("Conflict & security", "#C0392B",
               ["conflict", "acled", "ukraine_theater", "firms", "vip_flights"]),
    "geo":    ("Geopolitics", "#2C6FA6",
               ["foreign_news", "people", "political_figures", "commentary",
                "gdelt_regions", "gdelt_gkg", "mediacloud"]),
    "mkt":    ("Markets & macro", "#1E8A4C",
               ["markets_global", "macro", "forecasts", "paper_bets",
                "billionaires", "congressional_trades", "form4"]),
    "cyber":  ("Cyber", "#7B3FBF", ["cisa_kev", "epss"]),
    "health": ("Health & humanitarian", "#C77800", ["who_don", "reliefweb", "promed"]),
    "earth":  ("Earth & disaster", "#0F8B7E", ["usgs_quakes", "weather", "gdacs"]),
    "usgov":  ("US governance", "#6B5B4A",
               ["federal_register", "state_bills", "state_news", "local_news",
                "courtlistener", "fec", "sanctions", "sanctions_procurement"]),
    "state":  ("State media", "#B5602B",
               ["chinese_internal", "russian_internal", "ukrainian_internal"]),
}
SECTION_DOMAIN = {s: k for k, (_, _, secs) in DOMAINS.items() for s in secs}
DOM_LABEL = {k: v[0] for k, v in DOMAINS.items()}
DOM_COLOR = {k: v[1] for k, v in DOMAINS.items()}
ORDER = ["war", "geo", "mkt", "cyber", "health", "earth", "usgov", "state"]
_STATUS_DOT = {"primary_confirmed": "#2F6B3A", "multi_source": "#2C6FA6",
               "single_source": "#C77800", "contradicted": "#C0392B",
               "not_enough_info": "#8A8276"}


def _domains_of(topics):
    out = []
    for t in topics:
        d = SECTION_DOMAIN.get(t, "other")
        if d != "other" and d not in out:
            out.append(d)
    return out


def _sec_label(sid: str) -> str:
    """Friendly section name for provenance pills."""
    return sid.replace("_", " ").replace("gdelt", "GDELT").replace("acled", "ACLED")\
             .replace("cisa kev", "CISA KEV").replace("epss", "EPSS").replace("who don", "WHO DON")\
             .replace("usgs", "USGS").replace("firms", "FIRMS").replace("fec", "FEC").title()


def _provenance(c, esc) -> str:
    """Colored source pills — every claim's evidence, always visible on expand."""
    pills = []
    for sid in c.topics[:8]:
        d = SECTION_DOMAIN.get(sid, "other")
        col = DOM_COLOR.get(d, "#8A8276")
        pills.append(f"<span class='prov' style='--c:{col}'>{esc(_sec_label(sid))}</span>")
    return "".join(pills)


def render(today: date) -> Path:
    try:
        claims, reports, fresh, preds, recs = rr._gather(today)
    except Exception as exc:
        claims, reports, fresh, preds, recs = [], [], 0, [], []
        print("gather failed:", exc)
    claims = [c for c in claims if rr._relevant(c)]
    n_total = len(reports)
    n_contra = sum(1 for c in claims if c.status == "contradicted")
    n_primary = sum(1 for c in claims if c.status == "primary_confirmed")

    # translate the foreign-script headlines we're most likely to surface
    pool = [c.claim_text for c in claims[:40] if rr._lang_tag(c.claim_text)]
    tmap = rr._translate(pool)

    def tx(c):
        return tmap.get(c.claim_text, c.claim_text)

    esc = rr._esc

    # ── cross-domain threads ────────────────────────────────────────────────
    threads = []
    for c in claims:
        doms = _domains_of(c.topics)
        if len(doms) >= 2:
            threads.append((c, doms))
    threads.sort(key=lambda cd: (len(cd[1]), cd[0].n_sources), reverse=True)

    # ── per-domain buckets (each claim -> its dominant domain) ───────────────
    buckets = defaultdict(list)
    for c in claims:
        ds = [SECTION_DOMAIN.get(t) for t in c.topics if t in SECTION_DOMAIN]
        if not ds:
            continue
        buckets[Counter(ds).most_common(1)[0][0]].append(c)

    # ── threads hero (expandable: headline -> claim + assessment + provenance)
    trows = []
    for i, (c, doms) in enumerate(threads[:9]):
        chips = "".join(
            f"<span class='chip' style='background:{DOM_COLOR[d]}'>{esc(DOM_LABEL[d].split(' ')[0])}</span>"
            for d in doms)
        _, label = rr._STAT.get(c.status, ("open", c.status))
        lt = rr._lang_tag(c.claim_text)
        tnote = (f" <span class='tnote'>· {lt}→EN</span>" if c.claim_text in tmap else "")
        conf = int(c.confidence * 100)
        contra = (f"<div class='contra'>{esc(c.contradiction_note)}</div>"
                  if getattr(c, 'contradiction_note', '') else "")
        trows.append(
            f"<div class='thread' data-i='{i}'>"
            f"<div class='thead'>"
            f"<div class='tchips'>{chips}<span class='span'>{len(doms)} domains</span></div>"
            f"<div class='ttext'>{esc(rr._oneline(tx(c), 160))}{tnote}</div>"
            f"<div class='tmeta'><span class='tn'>{c.n_sources} src</span>"
            f"<span class='caret'>+</span></div>"
            f"</div>"
            f"<div class='body'>"
            f"<div class='assess'>"
            f"<span class='alabel a-{c.status}'>{esc(label)}</span>"
            f"<span class='abar'><b style='width:{conf}%'></b></span>"
            f"<span class='aconf'>{conf}% confidence</span>"
            f"<span class='asec'>{c.n_sources} sources · {c.n_sections} sections</span>"
            f"</div>"
            f"{contra}"
            f"<div class='provwrap'><span class='provk'>Evidence</span>{_provenance(c, esc)}</div>"
            f"</div></div>")
    threads_html = ("".join(trows) or
                    "<div class='empty'>No cross-domain threads cleared the bar today.</div>")

    # ── domain grid ─────────────────────────────────────────────────────────
    cards = []
    for d in ORDER:
        items = buckets.get(d, [])
        if not items:
            continue
        items = sorted(items, key=lambda c: c.n_sources, reverse=True)
        lis = "".join(
            f"<li><span class='dot' style='background:{_STATUS_DOT.get(c.status,'#888')}'></span>"
            f"<span class='it'>{esc(rr._oneline(tx(c), 100))}</span>"
            f"<span class='src'>{c.n_sources}</span></li>" for c in items[:6])
        more = (f"<li class='more'>+{len(items)-6} more in {esc(DOM_LABEL[d])}</li>"
                if len(items) > 6 else "")
        cards.append(
            f"<section class='card' data-dom='{d}' style='--c:{DOM_COLOR[d]}'>"
            f"<header><span class='cdot'></span>{esc(DOM_LABEL[d])}"
            f"<span class='cn'>{len(items)}</span></header>"
            f"<ul>{lis}{more}</ul></section>")
    grid_html = "".join(cards)

    # ── filter chips ────────────────────────────────────────────────────────
    fchips = "".join(
        f"<button class='fchip' data-dom='{d}' style='--c:{DOM_COLOR[d]}'>"
        f"<i style='background:{DOM_COLOR[d]}'></i>{esc(DOM_LABEL[d])}</button>"
        for d in ORDER if buckets.get(d))
    fchips = f"<button class='fchip on' data-dom='all'>All domains</button>{fchips}"

    # ── interactive chart payloads ──────────────────────────────────────────
    th_chart = [{"label": rr._oneline(tx(c), 42), "domains": len(doms),
                 "sources": c.n_sources} for c, doms in threads[:10]]
    ranked = sorted(claims, key=lambda c: (c.status != "contradicted", c.n_sources,
                                           c.confidence), reverse=True)
    cl_chart = rr._claims_chart_data(ranked, tmap)
    data_json = json.dumps({"threads": th_chart, "claims": cl_chart})

    # ── reused rich sections (real assets) — rewrite paths for depth-2 page ──
    def _fix(html):                       # ./briefings/ -> ../../briefings/
        return html.replace("./briefings/", "../../briefings/") if html else ""

    charts_html = _fix(rr._charts_html(today))
    theater_html = _fix(rr._theater_html(recs, today))
    markets_html = rr._markets_html(recs)
    outlook_html = rr._outlook_html(preds)

    n_threads = len(threads)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORLDSCOPE · Situation Board · {today.isoformat()}</title>
<style>{_CSS}{rr.EXTRA_CSS}{rr.CUSTOM_CSS}{_CSS_TAIL}</style></head><body>
<header class="top">
  <div class="brand"><span class="mk"></span>WORLDSCOPE<span class="sub">SITUATION BOARD</span></div>
  <div class="meta"><span class="live"><i></i>LIVE</span> · {today.strftime('%A %d %B %Y').upper()}</div>
</header>

<div class="ribbon">
  <div class="ks"><div class="v">{len(claims)}</div><div class="k">claims today</div></div>
  <div class="ks"><div class="v ok">{n_primary}</div><div class="k">primary-confirmed</div></div>
  <div class="ks"><div class="v ox">{n_contra}</div><div class="k">contradicted</div></div>
  <div class="ks"><div class="v">{fresh}/{n_total}</div><div class="k">sources fresh</div></div>
  <div class="ks"><div class="v">{n_threads}</div><div class="k">cross-domain threads</div></div>
</div>

<section class="threads" data-ws-section="threads" data-ws-label="Threads underneath">
  <h2>Threads underneath <span class="h2sub">— what's connecting across domains · click to open</span></h2>
  {threads_html}
</section>

<div class="filters">{fchips}</div>
<div class="grid" data-ws-section="grid" data-ws-label="Domain grid">{grid_html}</div>

<section class="chartwrap" data-ws-section="threadchart" data-ws-label="Thread chart">
  <h2>Strongest threads <span class="h2sub">— ranked by domains spanned · color = sources</span></h2>
  <div id="ws-plot" class="plot"></div>
</section>

<section class="chartwrap" data-ws-section="corrob" data-ws-label="Corroboration chart">
  <h2>Today's reporting <span class="h2sub">— ranked by independent corroboration</span></h2>
  <div id="ws-plot-claims" class="ws-plot"></div>
</section>

{charts_html}
{theater_html}
{markets_html}
{outlook_html}

<div class="foot">Threads = claims whose evidence spans ≥2 domains · color = domain · dot = assessment ·
all figures built from the lake · {today.isoformat()} · foreign headlines translated by Haiku</div>

<button id="ws-cog" title="Customize what's on screen">⚙</button>
<div id="ws-customize"><div class="ws-cust-h">Show on board</div><div id="ws-cust-list"></div></div>

<script id="ws-data" type="application/json">{data_json}</script>
<script src="../../assets/vendor/d3.min.js"></script>
<script src="../../assets/vendor/plot.umd.min.js"></script>
<script>{_JS}</script>
<script>{rr.CONTROLLER_JS}</script>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    return OUT


_CSS = """
:root{--bg:#F4F1E8;--ink:#1C1A14;--soft:#6E685C;--faint:#9A9384;--hair:#E2DACA;
 --hair2:#ECE5D6;--paper:#FCFAF3;--ok:#2F6B3A;--ox:#C0392B;--gold:#9A6B00;
 --serif:"Iowan Old Style",Palatino,Georgia,serif;
 --sans:"Inter",system-ui,Helvetica,Arial,sans-serif;
 --mono:ui-monospace,"DejaVu Sans Mono",Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.4;
 -webkit-font-smoothing:antialiased;padding-bottom:56px}
/* masthead */
.top{display:flex;align-items:center;justify-content:space-between;gap:20px;
 padding:16px 30px;background:var(--ink);color:#F4F1E8;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:11px;font-weight:800;letter-spacing:.22em;font-size:17px}
.brand .mk{width:13px;height:13px;background:#C0392B;transform:rotate(45deg);
 animation:spin 14s linear infinite}
@keyframes spin{to{transform:rotate(405deg)}}
.brand .sub{font-family:var(--mono);font-weight:400;font-size:10px;letter-spacing:.2em;
 color:#A9A293;border-left:1px solid #3A362C;padding-left:11px}
.meta{font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;color:#C9C2B1;display:flex;
 align-items:center;gap:6px}
.live{display:inline-flex;align-items:center;gap:5px;color:#E8B4AC}
.live i{width:7px;height:7px;border-radius:50%;background:#C0392B;animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(192,57,43,.5)}
 50%{opacity:.5;box-shadow:0 0 0 5px rgba(192,57,43,0)}}
/* ribbon */
.ribbon{display:flex;gap:0;border-bottom:1px solid var(--hair);background:var(--paper)}
.ribbon .ks{flex:1;padding:14px 18px;border-right:1px solid var(--hair2)}
.ribbon .ks:last-child{border-right:0}
.ribbon .v{font-family:var(--serif);font-size:28px;font-weight:700;line-height:1}
.ribbon .v.ok{color:var(--ok)} .ribbon .v.ox{color:var(--ox)}
.ribbon .k{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;
 color:var(--soft);margin-top:5px}
h2{font-family:var(--serif);font-size:22px;letter-spacing:-.01em;margin:0 0 14px;font-weight:700}
.h2sub{font-family:var(--sans);font-size:12.5px;font-weight:400;color:var(--soft);letter-spacing:0}
/* threads hero */
.threads{padding:26px 30px 8px;max-width:1180px;margin:0 auto}
.thread{border-bottom:1px solid var(--hair);animation:rise .5s ease both}
.thread:nth-child(2){animation-delay:.04s}.thread:nth-child(3){animation-delay:.08s}
.thread:nth-child(4){animation-delay:.12s}.thread:nth-child(5){animation-delay:.16s}
.thread:nth-child(6){animation-delay:.2s}.thread:nth-child(n+7){animation-delay:.24s}
@keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.thead{display:flex;align-items:baseline;gap:16px;padding:12px 6px;cursor:pointer;
 transition:background .15s}
.thead:hover{background:#FBF7EC}
.tchips{display:flex;flex-wrap:wrap;gap:5px;align-items:center;min-width:230px;max-width:230px}
.chip{font-family:var(--mono);font-size:9px;letter-spacing:.04em;text-transform:uppercase;color:#fff;
 padding:2px 7px;border-radius:3px;font-weight:600}
.span{font-family:var(--mono);font-size:9px;color:var(--soft);margin-left:2px}
.ttext{font-family:var(--serif);font-size:16.5px;flex:1;line-height:1.35}
.tnote{font-family:var(--mono);font-size:9px;color:var(--gold)}
.tmeta{display:flex;align-items:center;gap:10px;white-space:nowrap}
.tn{font-family:var(--mono);font-size:10px;color:var(--soft)}
.caret{font-family:var(--mono);font-size:16px;color:var(--faint);width:14px;text-align:center;
 transition:transform .25s}
.thread.open .caret{transform:rotate(45deg)}
.body{max-height:0;overflow:hidden;opacity:0;transition:max-height .35s ease,opacity .3s,padding .3s;
 padding:0 6px}
.thread.open .body{max-height:520px;opacity:1;padding:2px 6px 18px}
.assess{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:4px 0 10px}
.alabel{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;
 padding:3px 9px;border-radius:4px;color:#fff;font-weight:600}
.a-primary_confirmed{background:#2F6B3A}.a-multi_source{background:#2C6FA6}
.a-single_source{background:#C77800}.a-contradicted{background:#C0392B}
.a-not_enough_info{background:#8A8276}
.abar{display:inline-block;width:120px;height:6px;background:var(--hair);border-radius:3px;overflow:hidden}
.abar b{display:block;height:100%;background:var(--ink);width:0;animation:fill .8s .15s ease both}
@keyframes fill{from{width:0}}
.aconf,.asec{font-family:var(--mono);font-size:10px;color:var(--soft);letter-spacing:.04em}
.contra{font-family:var(--sans);font-size:13px;color:var(--ox);margin:0 0 10px;
 border-left:2px solid var(--ox);padding-left:10px}
.provwrap{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.provk{font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;
 color:var(--faint);margin-right:4px}
.prov{font-family:var(--mono);font-size:9.5px;color:var(--c);border:1px solid var(--c);
 border-radius:3px;padding:2px 7px;background:color-mix(in srgb,var(--c) 8%,transparent)}
.empty{font-family:var(--serif);font-size:16px;color:var(--soft);padding:14px 0}
/* filters */
.filters{display:flex;flex-wrap:wrap;gap:8px;padding:18px 30px 4px;max-width:1180px;margin:0 auto}
.fchip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10px;
 letter-spacing:.06em;border:1px solid var(--hair);background:var(--paper);color:var(--soft);
 padding:6px 12px;border-radius:999px;cursor:pointer;transition:all .15s}
.fchip i{width:7px;height:7px;border-radius:50%}
.fchip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.fchip:not(.on):hover{border-color:var(--c);color:var(--ink);transform:translateY(-1px)}
/* domain grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:16px;
 padding:14px 30px;max-width:1180px;margin:0 auto}
.card{background:var(--paper);border:1px solid var(--hair);border-radius:12px;overflow:hidden;
 border-top:3px solid var(--c);transition:transform .18s,box-shadow .18s;animation:rise .5s both}
.card:hover{transform:translateY(-3px);box-shadow:0 8px 22px rgba(40,34,20,.10)}
.card header{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:11px;
 letter-spacing:.1em;text-transform:uppercase;padding:12px 15px;background:#fff;color:var(--ink)}
.card header .cdot{width:10px;height:10px;border-radius:50%;background:var(--c)}
.card header .cn{margin-left:auto;color:var(--c);font-weight:700}
.card ul{list-style:none}
.card li{display:flex;align-items:flex-start;gap:10px;padding:10px 15px;border-top:1px solid var(--hair2);
 font-size:13.5px}
.card li.more{font-family:var(--mono);font-size:10px;color:var(--faint);letter-spacing:.04em}
.card li .dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex-shrink:0}
.card li .it{flex:1;font-family:var(--serif);font-size:14px;line-height:1.36}
.card li .src{font-family:var(--mono);font-size:10px;color:var(--soft)}
.chartwrap{padding:26px 30px 0;max-width:1180px;margin:0 auto}
.plot svg{max-width:100%;height:auto;font-family:var(--sans)}
.foot{font-family:var(--mono);font-size:10px;color:var(--faint);padding:26px 30px;letter-spacing:.04em;
 max-width:1180px;margin:0 auto}
@media(max-width:680px){.tchips{min-width:0;max-width:none}.thead{flex-wrap:wrap;gap:6px}
 .ribbon{flex-wrap:wrap}.ribbon .ks{flex:1 1 33%}}
"""

# tail overrides so the reused reasoned sections sit inside the board grid width
_CSS_TAIL = """
.ws-sec{max-width:1180px}
.ws-sec h2.ws-h{border-top:2px solid var(--ink)}
"""

_JS = r"""
(function(){
  // expand/collapse threads
  document.querySelectorAll(".thread .thead").forEach(function(h){
    h.addEventListener("click",function(){h.parentNode.classList.toggle("open");});
  });
  // domain filter: isolate a domain's card (and dim non-matching threads)
  var chips=[].slice.call(document.querySelectorAll(".fchip"));
  var cards=[].slice.call(document.querySelectorAll(".card[data-dom]"));
  chips.forEach(function(ch){ch.addEventListener("click",function(){
    chips.forEach(function(x){x.classList.remove("on");}); ch.classList.add("on");
    var d=ch.getAttribute("data-dom");
    cards.forEach(function(c){
      c.style.display=(d==="all"||c.getAttribute("data-dom")===d)?"":"none";});
  });});
  // threads-by-domain chart
  try{
    var raw=document.getElementById("ws-data");
    var th=(raw?JSON.parse(raw.textContent||"{}"):{}).threads||[];
    var el=document.getElementById("ws-plot");
    if(el&&th.length&&window.Plot){
      el.appendChild(Plot.plot({
        height:Math.max(200,th.length*28),marginLeft:250,marginRight:30,
        style:{background:"transparent",fontSize:"11px",color:"#1C1A14"},
        x:{label:"domains spanned →",grid:true,ticks:5},
        y:{label:null},
        color:{type:"linear",scheme:"YlOrRd",legend:true,label:"sources"},
        marks:[
          Plot.barX(th,{y:"label",x:"domains",fill:"sources",sort:{y:"x",reverse:true},
            tip:true,rx:2,
            title:function(d){return d.label+"\n"+d.domains+" domains · "+d.sources+" sources";}})
        ]
      }));
    }
  }catch(e){if(window.console)console.error(e);}
})();
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args(argv)
    out = render(rr._effective_date(date.fromisoformat(args.date)))
    print(f"[board] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
