"""render_reasoned.py — render the 'Reasoned' design from REAL lake data.

A first real-data pass of the Reasoned layout (judgment -> epistemic status ->
evidence -> ledger), filled from the claim graph, the integrity report, and the
signals engine. Reuses the committed prototype's styles verbatim so the look is
identical; only the content is now real. Writes a standalone preview file for
review before anything touches the live homepage.

    python tools/render_reasoned.py --date 2026-06-01
    -> dist/mockups/next/reasoned-live.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from worldscope import claims as cl          # noqa: E402
from worldscope import signals as sg          # noqa: E402
from worldscope import integrity as ig        # noqa: E402
from worldscope.lake import Lake              # noqa: E402

PROTOTYPE = REPO / "dist" / "mockups" / "next" / "reasoned.html"
OUT = REPO / "dist" / "mockups" / "next" / "reasoned-live.html"
HOME = REPO / "dist" / "index.html"

# Non-Latin scripts we can flag deterministically (Latin-script languages like
# Spanish/French aren't reliably detectable cheaply, so we leave those).
_SCRIPTS = [("RU", (0x0400, 0x04FF)), ("ZH", (0x4E00, 0x9FFF)),
            ("AR", (0x0600, 0x06FF)), ("UK", (0x0400, 0x04FF))]


def _lang_tag(text: str) -> str:
    for ch in text or "":
        o = ord(ch)
        for code, (lo, hi) in _SCRIPTS:
            if lo <= o <= hi:
                return code
    return ""

# claim status -> ledger pill class + assessment label (intelligence nomenclature)
_STAT = {
    "primary_confirmed": ("hit", "CONFIRMED"),
    "multi_source": ("open", "CORROBORATED"),
    "single_source": ("open", "SINGLE-SOURCE"),
    "contradicted": ("miss", "DISPUTED"),
    "not_enough_info": ("open", "UNCONFIRMED"),
}

# Drop clusters that are real multi-source but not intelligence: sport,
# entertainment, lifestyle, holidays, and explainer/clickbait pieces.
_NOISE = re.compile(
    r"\b(vs\.?|game \d|world cup|nba|playoffs?|finals?|french open|roland garros|"
    r"atp|wta|grand slam|premier league|la liga|serie a|bundesliga|champions league|"
    r"fifa|uefa|olympic|super bowl|formula 1|grand prix|knocked out|"
    r"quarter-?final|semi-?final|transfer window|box office|grammy|oscar|"
    # holidays / faith observances / lifestyle / soft features
    r"eid mubarak|eid al-?|ramadan|hajj|diwali|hanukkah|christmas|easter|"
    r"mother'?s day|father'?s day|valentine|horoscope|zodiac|recipe|"
    r"celebrit|royal family|met gala|red carpet|netflix|spotify|box-office|"
    r"things to know|what to know|everything you need|guide to|tips for|"
    r"best (?:movies|shows|books|restaurants))\b", re.I)

# Explainer/question-style headlines aren't assessments.
_EXPLAINER = re.compile(r"^\s*(what is|what are|why do|why does|why is|how to|"
                        r"how do|how does|who is|who are|when is|when does|"
                        r"everything you|here'?s (?:what|why|how)|a guide)\b", re.I)


def _relevant(c) -> bool:
    t = (c.claim_text or "").strip()
    if not t or _NOISE.search(t) or _EXPLAINER.search(t):
        return False
    if t.endswith("?"):           # clickbait questions / explainers
        return False
    return True


def _headline(text: str, max_words: int = 14) -> str:
    """First clause of the lead claim, as a tight headline."""
    t = re.split(r"(?<=[a-z])\.\s|[;:]| — ", (text or "").strip())[0]
    t = re.split(r",\s", t)[0] if len(t.split()) > max_words else t
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words])
    return t.rstrip(" .,;:–—-")


def _oneline(text: str, max_chars: int = 150) -> str:
    """First sentence of a claim, capped — no run-ons in the lists."""
    t = re.split(r"(?<=[a-z])\.\s", (text or "").strip())[0].strip()
    return (t[:max_chars].rstrip() + "…") if len(t) > max_chars else t


def _css() -> str:
    try:
        m = re.search(r"<style>(.*?)</style>", PROTOTYPE.read_text(encoding="utf-8"), re.S)
        return m.group(1) if m else ""
    except OSError:
        return ""   # missing prototype -> unstyled but correct, not a lost render


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def _translate(texts: list) -> dict:
    """Translate non-English headlines to English via Haiku, batched into one
    call. No-op (returns {}) without ANTHROPIC_API_KEY — so it degrades to the
    original text + a language tag. Any failure falls back silently."""
    uniq = [t for t in dict.fromkeys(texts) if t][:12]
    if not uniq or not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    try:
        import anthropic
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(uniq))
        prompt = ("Translate each numbered news headline into concise English. "
                  "Output ONLY the translations with the same numbering, one per "
                  "line, nothing else.\n\n" + numbered)
        resp = anthropic.Anthropic(timeout=40).messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1200,
            messages=[{"role": "user", "content": prompt}])
        body = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        out = {}
        for line in body.splitlines():
            m = re.match(r"\s*(\d+)\.\s*(.+)", line)
            if m:
                i = int(m.group(1)) - 1
                if 0 <= i < len(uniq):
                    out[uniq[i]] = m.group(2).strip()
        return out
    except Exception:
        return {}


def _gather(today: date):
    lake = Lake.open()
    conn = lake._ensure_open()
    recs = sg.load_records_from_jsonl(today=today, days=2)
    tiers = cl._tier_map(conn)
    claims = (cl.build_claims(recs, today=today, tier_by_source=tiers) if recs
              else cl.build_from_lake(today=today, conn=conn))
    sids = ig.section_ids_from_registry()
    if not sids:
        sids = sorted({r[0] for r in conn.execute("SELECT DISTINCT section_id FROM records")})
    from worldscope.store import SnapshotStore
    reports = ig.assess(conn, sids, today=today, store=SnapshotStore())
    fresh = sum(1 for r in reports if r.status == "FRESH")
    sigs = sg.build_signals(today=today, conn=conn)
    preds = sg.signals_to_predictions(sigs, today=today)
    lake.close()
    return claims, reports, fresh, preds, recs


# ── rich-section builders (charts / markets / theater / outlook) ────────────

CHART_SPECS = [
    ("gdelt_tone_heatmap", "Global media tone (geographic)"),
    ("yield_curve", "US Treasury yield curve"),
    ("fx_oil", "FX &amp; oil"),
    ("conflict_fatalities", "Conflict fatalities"),
    ("anomaly_screen", "Cross-section anomaly screen"),
    ("watchareas_volume", "Watch-area volume"),
]


def _latest_chart_date(today: date) -> Optional[str]:
    bdir = REPO / "dist" / "briefings"
    for d in range(0, 9):
        ds = (today - timedelta(days=d)).isoformat()
        if (bdir / f"{ds}-yield_curve.png").exists():
            return ds
    return None


def _section(title: str, inner: str, *, cap: str = "", sid: str = "") -> str:
    if not inner:
        return ""
    cap_html = f"<div class='ws-cap'>{cap}</div>" if cap else ""
    attr = f" data-ws-section='{sid}' data-ws-label='{_esc(title)}'" if sid else ""
    return (f"<section class='ws-sec'{attr}><h2 class='ws-h'>{title}</h2>"
            f"{inner}{cap_html}</section>")


def _charts_html(today: date) -> str:
    cd = _latest_chart_date(today)
    if not cd:
        return ""
    tiles = []
    for name, label in CHART_SPECS:
        if (REPO / "dist" / "briefings" / f"{cd}-{name}.png").exists():
            tiles.append(
                f"<figure class='ws-chart'><img src='./briefings/{cd}-{name}.png' "
                f"alt='{label}' loading='lazy'><figcaption>{label}</figcaption></figure>")
    if not tiles:
        return ""
    return _section("Indicators &amp; maps", f"<div class='ws-charts'>{''.join(tiles)}</div>",
                    cap=f"Generated from lake data · {cd}", sid="indicators")


_MKT_ORDER = ["equit", "commod", "rate", "treasur", "bond", "credit", "crypto",
              "vol", "fx", "currenc"]


def _markets_html(recs: list) -> str:
    # Lake records carry text in original_text (not title); change% in extra.
    # Bucket by the leading [group] tag and take a cross-asset spread, not the
    # first 12 (which are all one asset class).
    buckets: dict = {}
    for r in recs:
        if r.get("section_id") != "markets_global":
            continue
        line = (r.get("title") or r.get("original_text") or "").split(" — ")[0].strip()
        if not line:
            continue
        gm = re.match(r"^\[([^\]]+)\]", line)
        grp = (gm.group(1).lower() if gm else "other")
        buckets.setdefault(grp, [])
        if line not in [x[0] for x in buckets[grp]]:
            buckets[grp].append((line, r))

    def _rank(g):
        for i, key in enumerate(_MKT_ORDER):
            if key in g:
                return i
        return len(_MKT_ORDER)

    rows = []
    for grp in sorted(buckets, key=_rank):
        for line, r in buckets[grp][:2]:        # up to 2 per asset class
            lbl = re.sub(r"^\[[^\]]+\]\s*", "", line)
            dp = (r.get("extra") or {}).get("change_pct")
            cls = "up" if (isinstance(dp, (int, float)) and dp >= 0) else "dn"
            rows.append(f"<div class='row {cls}'><span>{_esc(lbl)}</span></div>")
            if len(rows) >= 12:
                break
        if len(rows) >= 12:
            break
    if not rows:
        return ""
    return _section("Markets", f"<div class='ws-mkt'>{''.join(rows)}</div>",
                    cap="Cross-asset levels · markets_global", sid="markets")


def _claims_chart_data(ranked: list, tmap: dict) -> list:
    """[{label, sources, status, conf}] for the interactive corroboration chart —
    the day's reporting ranked by how many independent sources carry it."""
    out = []
    for c in ranked[:12]:
        _, label = _STAT.get(c.status, ("open", c.status))
        out.append({"label": _oneline(tmap.get(c.claim_text, c.claim_text), 44),
                    "sources": c.n_sources, "status": label,
                    "conf": int(c.confidence * 100)})
    return out


def _theater_html(recs: list, today: date) -> str:
    th = [r for r in recs if r.get("section_id") == "ukraine_theater"]
    if not th:
        return ""
    seen, items = set(), []
    for r in th:
        t = sg._clean_text(r.get("title") or r.get("original_text") or "")
        if t and t not in seen and len(t) > 12:
            seen.add(t)
            items.append(t)
        if len(items) >= 6:
            break
    lis = "".join(f"<li><div class='t'>{_esc(_oneline(t, 150))}</div></li>" for t in items)
    # embed the most recent theater map if one exists (hourly feed is under repair)
    bdir = REPO / "dist" / "briefings"
    map_html = ""
    for d in range(0, 14):
        ds = (today - timedelta(days=d)).isoformat()
        mp = bdir / f"{ds}-ukraine_theater_overview.png"
        if mp.exists():
            note = "" if d <= 1 else f" (latest available · {ds})"
            map_html = (f"<figure class='ws-chart' style='margin-bottom:14px'>"
                        f"<img src='./briefings/{ds}-ukraine_theater_overview.png' "
                        f"alt='Ukraine theater'><figcaption>Theater overview{note}</figcaption></figure>")
            break
    return _section("Ukraine theater", f"{map_html}<ul class='ws-list'>{lis}</ul>",
                    cap=f"{len(th)} theater records today", sid="theater")


_KEY_RE = re.compile(r"'([^']+)' \(key '([^']+)'\)")


def _outlook_html(preds: list) -> str:
    if not preds:
        return ""
    lis = []
    for p in preds[:6]:
        crit = p.get("resolution_criteria", "")
        m = _KEY_RE.search(crit)
        label = m.group(1) if m else (p.get("_key") or "signal")
        conf = int(float(p.get("confidence", 0)) * 100)
        tgt = p.get("target_date", "")
        lis.append(f"<li><div class='t'>{_esc(label)} stays cross-source-salient</div>"
                   f"<div class='m'>{conf}% · resolves {tgt}</div></li>")
    return _section("Outlook — what we're watching",
                    f"<ul class='ws-list'>{''.join(lis)}</ul>",
                    cap="Falsifiable calls from cross-source signals · auto-graded", sid="outlook")


EXTRA_CSS = """
.ws-sec{max-width:1180px;margin:0 auto;padding:34px 44px 0}
.ws-sec h2.ws-h{font-family:var(--serif);font-weight:700;font-size:24px;letter-spacing:-.01em;
  border-top:2px solid var(--ink);padding-top:16px;margin:0 0 16px}
.ws-charts{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
@media(max-width:880px){.ws-charts{grid-template-columns:1fr}.ws-mkt{grid-template-columns:1fr!important}}
.ws-chart{border:1px solid var(--hair);border-radius:10px;overflow:hidden;background:var(--paper);margin:0}
.ws-chart img{width:100%;height:auto;display:block}
.ws-chart figcaption{font-family:var(--mono);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--soft);padding:8px 12px;border-top:1px solid var(--hair)}
.ws-cap{font-family:var(--mono);font-size:10px;color:var(--faint);margin-top:10px;letter-spacing:.04em}
.ws-mkt{display:grid;grid-template-columns:1fr 1fr;gap:0 36px}
.ws-mkt .row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--hair2);
  font-family:var(--sans);font-size:14px}
.ws-mkt .row.up{color:var(--ok)} .ws-mkt .row.dn{color:var(--ox)}
.ws-list{list-style:none;padding:0;margin:0}
.ws-list li{padding:11px 0;border-bottom:1px solid var(--hair2)}
.ws-list .t{font-family:var(--serif);font-size:16px;color:var(--ink)}
.ws-list .m{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--soft);margin-top:3px}
"""


CUSTOM_CSS = """
.ws-plot{margin:0 0 18px}
.ws-plot svg{max-width:100%;height:auto;font-family:var(--sans)}
#ws-cog{position:fixed;top:14px;right:14px;z-index:60;width:40px;height:40px;border-radius:50%;
  border:1px solid var(--hair);background:var(--paper);color:var(--ink);font-size:18px;cursor:pointer;
  box-shadow:0 1px 5px rgba(0,0,0,.10);line-height:1}
#ws-customize{position:fixed;top:62px;right:14px;z-index:60;background:var(--paper);
  border:1px solid var(--hair);border-radius:11px;padding:14px 16px;display:none;min-width:190px;
  box-shadow:0 6px 22px rgba(0,0,0,.14)}
#ws-customize.open{display:block}
#ws-customize .ws-cust-h{font-family:var(--mono);font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--soft);margin-bottom:10px}
#ws-customize label{display:flex;align-items:center;gap:8px;font-family:var(--sans);
  font-size:13.5px;color:var(--ink);padding:5px 0;cursor:pointer}
"""

CONTROLLER_JS = r"""
(function(){
  var KEY="ws-hidden-sections", hidden=[];
  try{hidden=JSON.parse(localStorage.getItem(KEY)||"[]");}catch(e){}
  var secs=[].slice.call(document.querySelectorAll("[data-ws-section]"));
  function apply(){secs.forEach(function(s){
    s.style.display=hidden.indexOf(s.getAttribute("data-ws-section"))>=0?"none":"";});}
  apply();
  var list=document.getElementById("ws-cust-list");
  if(list){secs.forEach(function(s){
    var id=s.getAttribute("data-ws-section"), label=s.getAttribute("data-ws-label")||id;
    var row=document.createElement("label"),
        cb=document.createElement("input"); cb.type="checkbox"; cb.checked=hidden.indexOf(id)<0;
    cb.addEventListener("change",function(){
      if(cb.checked){hidden=hidden.filter(function(x){return x!==id;});}
      else if(hidden.indexOf(id)<0){hidden.push(id);}
      try{localStorage.setItem(KEY,JSON.stringify(hidden));}catch(e){}
      apply();
    });
    row.appendChild(cb); row.appendChild(document.createTextNode(" "+label)); list.appendChild(row);
  });}
  var cog=document.getElementById("ws-cog"), panel=document.getElementById("ws-customize");
  if(cog&&panel){cog.addEventListener("click",function(){panel.classList.toggle("open");});}
  try{
    var raw=document.getElementById("ws-data");
    var cl=(raw?JSON.parse(raw.textContent||"{}"):{}).claims||[];
    var el=document.getElementById("ws-plot-claims");
    if(el&&cl.length&&window.Plot){
      var col={CONFIRMED:"#2F6B3A",CORROBORATED:"#2B4257",DISPUTED:"#990000",
               "SINGLE-SOURCE":"#9A6B00",UNCONFIRMED:"#6F695C"};
      el.appendChild(Plot.plot({
        height:Math.max(190,cl.length*26), marginLeft:250, marginRight:28,
        style:{background:"transparent",fontSize:"11px"},
        x:{label:"independent sources",grid:true},
        y:{label:null},
        marks:[
          Plot.barX(cl,{y:"label",x:"sources",
            fill:function(d){return col[d.status]||"#888";},
            sort:{y:"x",reverse:true}, tip:true,
            title:function(d){return d.label+"\n"+d.status+" · "+d.conf+"% confidence · "+d.sources+" sources";}})
        ]
      }));
    }
  }catch(e){if(window.console)console.error(e);}
})();
"""


def render(today: date, *, homepage: bool = False) -> Path:
    try:
        claims, reports, fresh, preds, recs = _gather(today)
    except Exception as exc:        # never let homepage generation crash a deploy
        claims, reports, fresh, preds, recs = [], [], 0, [], []
        print(f"[reasoned] gather failed: {type(exc).__name__}: {exc}")
    claims = [c for c in claims if _relevant(c)]
    n = len(reports)
    n_preds = len(preds)
    # rich sections from real assets (each degrades to '' on missing data)
    charts_html = _charts_html(today)
    markets_html = _markets_html(recs)
    theater_html = _theater_html(recs, today)
    outlook_html = _outlook_html(preds)

    # hero = the most-corroborated non-contradicted claim; fall back to the first
    ranked = sorted(claims, key=lambda c: (c.status != "contradicted", c.n_sources,
                                           c.confidence), reverse=True)
    hero = ranked[0] if ranked else None
    # Translate the foreign (non-Latin-script) headlines among what we show.
    tmap = _translate([c.claim_text for c in ranked[:14] if _lang_tag(c.claim_text)])

    def _tx(c):
        return tmap.get(c.claim_text, c.claim_text)

    n_contra = sum(1 for c in claims if c.status == "contradicted")
    n_primary = sum(1 for c in claims if c.status == "primary_confirmed")

    # ---- ribbon (real accountability numbers) ----
    ribbon = "".join([
        f"<div class='ks'><div class='k'>Claims today</div><div class='v'>{len(claims)}</div></div>",
        f"<div class='ks'><div class='k'>Primary-confirmed</div><div class='v ok'>{n_primary}</div></div>",
        f"<div class='ks'><div class='k'>Contradicted</div><div class='v ox'>{n_contra}</div></div>",
        f"<div class='ks'><div class='k'>Sources fresh</div><div class='v'>{fresh}/{n}</div></div>",
        f"<div class='ks'><div class='k'>Open signals</div><div class='v'>{n_preds}</div></div>",
    ])

    # ---- judgment hero ----
    if hero:
        _, hlabel = _STAT.get(hero.status, ("open", hero.status))
        h1 = _esc(_headline(_tx(hero)))
        conf_word = ("high" if hero.confidence >= 0.75 else
                     "moderate" if hero.confidence >= 0.55 else "low")
        stand = (f"We assess with {conf_word} confidence; {hero.n_sources} sources "
                 f"across {hero.n_sections} independent sections. "
                 + (_esc(hero.contradiction_note) if hero.contradiction_note else ""))
        status_bar = "".join([
            f"<div class='seg'><div class='k'>Confidence</div>"
            f"<div class='conf'>{int(hero.confidence*100)}% <span class='cbar'>"
            f"<b style='width:{int(hero.confidence*100)}%'></b></span></div></div>",
            f"<div class='seg'><div class='k'>Status</div><div class='cont'>{hlabel}</div></div>",
            f"<div class='seg'><div class='k'>Corroboration</div>"
            f"<div class='conf'>{hero.n_sources} sources · {hero.n_sections} sections</div></div>",
            f"<div class='seg'><div class='k'>Type</div>"
            f"<div class='conf'>{_esc(hero.claim_type.replace('_',' '))}</div></div>",
        ])
    else:
        h1, stand, status_bar = "No cross-source claims today.", "", ""

    # ---- evidence / reading: top claims with provenance ----
    rows = []
    for c in ranked[1:7]:
        pill, label = _STAT.get(c.status, ("open", c.status))
        srcs = " ".join(f"<span class='src'><i></i>{_esc(s)}</span>" for s in c.topics[:5])
        trn = (f" <span style='font-family:var(--mono);font-size:9px;color:var(--gold)'>"
               f"· translated from {_lang_tag(c.claim_text)}</span>"
               if c.claim_text in tmap else "")
        rows.append(
            f"<div class='note'><div class='h'>{label} · {int(c.confidence*100)}%"
            f" · {c.n_sources} sources</div>"
            f"<p style='font-family:var(--serif);font-size:16px;color:var(--ink)'>{_esc(_oneline(_tx(c)))}{trn}</p>"
            f"<div class='srcbar' style='margin-top:8px'>{srcs}</div></div>")
    reading = "".join(rows)

    # ---- the ledger: claims as scored calls ----
    calls = []
    for c in ranked[:10]:
        pill, label = _STAT.get(c.status, ("open", c.status))
        lt = _lang_tag(c.claim_text)
        badge = (f"{lt}→EN" if c.claim_text in tmap else lt) if lt else ""
        tag = (f"<span style='font-family:var(--mono);font-size:9px;color:var(--gold);"
               f"border:1px solid #E2C9B5;border-radius:3px;padding:0 4px;margin-right:6px'>{badge}</span>"
               if badge else "")
        calls.append(
            "<div class='call'>"
            f"<div class='q'>{tag}{_esc(_oneline(_tx(c), 130))}"
            f"<div class='meta'>{_esc(', '.join(c.topics[:4]))} · {c.claim_type.replace('_',' ')}</div></div>"
            f"<div class='prob'><span class='m'><b style='width:{int(c.confidence*100)}%'></b></span>"
            f"<span class='pct'>{int(c.confidence*100)}%</span></div>"
            f"<div class='res'>{c.n_sources} src</div>"
            f"<div class='stat {pill}'>{label}</div></div>")
    ledger = "".join(calls)

    nav_links = " · ".join(
        f"<a href='{h}' style='color:inherit;text-decoration:none;"
        f"border-bottom:1px solid var(--hair)'>{t}</a>"
        for t, h in (("Sections", "./sections/"), ("Archive", "./briefings/"),
                     ("Bundle", f"./zips/{today.isoformat()}.zip")))
    subbar_right = ((nav_links + " · OSINT · FOR DR. I. HELFRICH") if homepage
                    else "OSINT · ALL-SOURCE · PREPARED FOR DR. I. HELFRICH")

    body = f"""<div class="wrap">
  <header class="standing">
    <div class="brand">
      <svg class="mk" viewBox="0 0 40 40" fill="none">
        <circle cx="20" cy="20" r="18" stroke="#7A2A20" stroke-width="1.5"/>
        <circle cx="20" cy="20" r="11" stroke="#9A6B00" stroke-width="1"/>
        <path d="M20 3 L20 37 M3 20 L37 20" stroke="#9A6B00" stroke-width=".6" opacity=".5"/>
        <circle cx="20" cy="20" r="3.2" fill="#7A2A20"/></svg>
      <div class="nm"><b>WORLDSCOPE</b><span>DAILY INTELLIGENCE BRIEF</span></div>
    </div>
    <div class="ledgerstrip">{ribbon}</div>
  </header>
  <div class="subbar"><span class="live"><i></i>{today.strftime('%A %d %B %Y').upper()}</span>
    <span>{subbar_right}</span></div>

  <section class="judgment">
    <div class="eyebrow">Key judgment</div>
    <h1>{h1}</h1>
    <p class="stand">{stand}</p>
    <div class="status">{status_bar}</div>
  </section>

  <div class="reading" data-ws-section="developments" data-ws-label="Key developments">
    <div class="essay">
      <p style="font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--gold)">Key developments</p>
      {reading}
    </div>
    <aside>
      <div class="note"><div class="h">Sourcing &amp; confidence</div>
        <p>Each item is a cluster of independent reports of the same event.
        Confidence reflects the number of independent sources and their tier;
        items a source denies are marked DISPUTED. Assessments are derived, not
        asserted, and every item links to its underlying records.</p></div>
    </aside>
  </div>
  {charts_html}
  {markets_html}
  {theater_html}
  {outlook_html}

  <section class="ledger" data-ws-section="reporting" data-ws-label="Current reporting">
    <div class="head"><h2>Current reporting</h2>
      <span class="sub">{len(claims)} items · by assessed confidence</span></div>
    <div id="ws-plot-claims" class="ws-plot"></div>
    <div class="calls">{ledger}</div>
  </section>

  <div class="prov">SOURCING · all items drawn from records ingested for {today.isoformat()}; confidence and
    status are derived from cross-source corroboration and source tier. Times UTC.</div>
</div>"""

    data_json = json.dumps({"claims": _claims_chart_data(ranked, tmap)})
    controls = (
        "<button id='ws-cog' aria-label='Customize sections' "
        "title='Customize sections'>⚙</button>"
        "<div id='ws-customize'><div class='ws-cust-h'>Show sections</div>"
        "<div id='ws-cust-list'></div></div>"
        f"<script id='ws-data' type='application/json'>{data_json}</script>"
        "<script src='./assets/vendor/d3.min.js'></script>"
        "<script src='./assets/vendor/plot.umd.min.js'></script>"
        f"<script>{CONTROLLER_JS}</script>")

    css = _css()
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WORLDSCOPE · Reasoned (live) · {today.isoformat()}</title>"
            f"<style>{css}{EXTRA_CSS}{CUSTOM_CSS}</style></head>"
            f"<body>{body}{controls}</body></html>")
    out = HOME if homepage else OUT
    out.write_text(page, encoding="utf-8")
    return out


def _effective_date(today: date) -> date:
    """The MOST RECENT day with enough data — not the day with the most records.
    Recency wins so the brief never looks stale; we only fall back further when a
    day is genuinely thin (ingest still in progress).

    The lookback spans two weeks so a multi-day ingestion gap (a stalled cron,
    or data commits blocked by repo size) doesn't blank or pin the homepage to a
    near-empty 'today'; it degrades gracefully to the freshest substantive day
    instead. On a healthy daily run the first iteration (today) clears the floor,
    so this stays a no-op."""
    FLOOR = 1500
    LOOKBACK = 14
    fallback, fb_n = today, -1
    for d in range(0, LOOKBACK):
        dt = today - timedelta(days=d)
        try:
            nrec = len(sg.load_records_from_jsonl(today=dt, days=1))
        except Exception:
            nrec = 0
        if nrec >= FLOOR:
            return dt                       # most recent adequately-covered day
        if nrec > fb_n:
            fallback, fb_n = dt, nrec
    return fallback


def render_homepage(today: date, out_root: Path) -> Path:
    """Write the Reasoned brief as the site homepage (dist/index.html)."""
    global HOME
    HOME = Path(out_root) / "index.html"
    return render(_effective_date(today), homepage=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the Reasoned design from real data.")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--home", action="store_true",
                    help="write dist/index.html (the homepage) instead of the preview")
    args = ap.parse_args(argv)
    out = render(date.fromisoformat(args.date), homepage=args.home)
    print(f"[reasoned] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
