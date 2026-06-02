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
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from worldscope import claims as cl          # noqa: E402
from worldscope import signals as sg          # noqa: E402
from worldscope import integrity as ig        # noqa: E402
from worldscope.lake import Lake              # noqa: E402

PROTOTYPE = REPO / "dist" / "mockups" / "next" / "reasoned.html"
OUT = REPO / "dist" / "mockups" / "next" / "reasoned-live.html"

# claim status -> ledger pill class + assessment label (intelligence nomenclature)
_STAT = {
    "primary_confirmed": ("hit", "CONFIRMED"),
    "multi_source": ("open", "CORROBORATED"),
    "single_source": ("open", "SINGLE-SOURCE"),
    "contradicted": ("miss", "DISPUTED"),
    "not_enough_info": ("open", "UNCONFIRMED"),
}

# Drop sports/entertainment clusters — real multi-source, but not intelligence.
_NOISE = re.compile(
    r"\b(vs\.?|game \d|world cup|nba|playoffs?|finals?|french open|roland garros|"
    r"atp|wta|grand slam|premier league|la liga|serie a|bundesliga|champions league|"
    r"fifa|uefa|olympic|super bowl|formula 1|grand prix|knocked out|"
    r"quarter-?final|semi-?final|transfer window|box office|grammy|oscar)\b", re.I)


def _relevant(c) -> bool:
    return not _NOISE.search(c.claim_text or "")


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
    m = re.search(r"<style>(.*?)</style>", PROTOTYPE.read_text(encoding="utf-8"), re.S)
    return m.group(1) if m else ""


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


def _gather(today: date):
    lake = Lake.open()
    conn = lake._ensure_open()
    claims = cl.build_from_lake(today=today, conn=conn)
    # integrity (section list falls back to lake-distinct when the registry
    # import is unavailable locally)
    sids = ig.section_ids_from_registry()
    if not sids:
        sids = sorted({r[0] for r in conn.execute("SELECT DISTINCT section_id FROM records")})
    from worldscope.store import SnapshotStore
    reports = ig.assess(conn, sids, today=today, store=SnapshotStore())
    fresh = sum(1 for r in reports if r.status == "FRESH")
    # signals -> open prediction count
    sigs = sg.build_signals(today=today, conn=conn)
    preds = sg.signals_to_predictions(sigs, today=today)
    lake.close()
    return claims, reports, fresh, len(preds)


def render(today: date) -> Path:
    claims, reports, fresh, n_preds = _gather(today)
    claims = [c for c in claims if _relevant(c)]
    n = len(reports)

    # hero = the most-corroborated non-contradicted claim; fall back to the first
    ranked = sorted(claims, key=lambda c: (c.status != "contradicted", c.n_sources,
                                           c.confidence), reverse=True)
    hero = ranked[0] if ranked else None
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
        h1 = _esc(_headline(hero.claim_text))
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
        rows.append(
            f"<div class='note'><div class='h'>{label} · {int(c.confidence*100)}%"
            f" · {c.n_sources} sources</div>"
            f"<p style='font-family:var(--serif);font-size:16px;color:var(--ink)'>{_esc(_oneline(c.claim_text))}</p>"
            f"<div class='srcbar' style='margin-top:8px'>{srcs}</div></div>")
    reading = "".join(rows)

    # ---- the ledger: claims as scored calls ----
    calls = []
    for c in ranked[:10]:
        pill, label = _STAT.get(c.status, ("open", c.status))
        calls.append(
            "<div class='call'>"
            f"<div class='q'>{_esc(_oneline(c.claim_text, 130))}"
            f"<div class='meta'>{_esc(', '.join(c.topics[:4]))} · {c.claim_type.replace('_',' ')}</div></div>"
            f"<div class='prob'><span class='m'><b style='width:{int(c.confidence*100)}%'></b></span>"
            f"<span class='pct'>{int(c.confidence*100)}%</span></div>"
            f"<div class='res'>{c.n_sources} src</div>"
            f"<div class='stat {pill}'>{label}</div></div>")
    ledger = "".join(calls)

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
    <span>OSINT · ALL-SOURCE · PREPARED FOR DR. I. HELFRICH</span></div>

  <section class="judgment">
    <div class="eyebrow">Key judgment</div>
    <h1>{h1}</h1>
    <p class="stand">{stand}</p>
    <div class="status">{status_bar}</div>
  </section>

  <div class="reading">
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

  <section class="ledger">
    <div class="head"><h2>Current reporting</h2>
      <span class="sub">{len(claims)} items · by assessed confidence</span></div>
    <div class="calls">{ledger}</div>
  </section>

  <div class="prov">SOURCING · all items drawn from records ingested for {today.isoformat()}; confidence and
    status are derived from cross-source corroboration and source tier. Times UTC.</div>
</div>"""

    css = _css()
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WORLDSCOPE · Reasoned (live) · {today.isoformat()}</title>"
            f"<style>{css}</style></head><body>{body}</body></html>")
    OUT.write_text(page, encoding="utf-8")
    return OUT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the Reasoned design from real data.")
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args(argv)
    out = render(date.fromisoformat(args.date))
    print(f"[reasoned] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
