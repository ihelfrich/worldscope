"""Render /reproducibility/<date>/index.html."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .lib.page_chrome import footer_block, page_shell


STATE_CLASS = {
    "fresh": "bg-teal/10 text-teal border-teal/30",
    "empty_ok": "bg-carolina/10 text-navy border-carolina/30",
    "failed": "bg-crimson/10 text-crimson border-crimson/30",
    "carried": "bg-gold/15 text-[#856404] border-gold/35",
    "no_data": "bg-mist text-slate border-mist-strong",
}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def _state_badge(state: str) -> str:
    cls = STATE_CLASS.get(state, "bg-mist text-slate border-mist-strong")
    return (
        f'<span class="inline-flex rounded border px-2 py-0.5 font-sans text-[11px] '
        f'font-semibold uppercase tracking-[0.08em] {cls}">{_e(state)}</span>'
    )


def _source_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="7" class="px-3 py-4 text-slate">No snapshot rows found.</td></tr>'
    out = []
    for row in rows:
        error = row.get("error") or ""
        out.append(f"""
<tr class="border-b border-mist align-top">
  <td class="px-3 py-2 font-mono text-[12px] text-navy">{_e(row.get('section_id'))}</td>
  <td class="px-3 py-2">{_state_badge(str(row.get('state') or 'no_data'))}</td>
  <td class="px-3 py-2 text-right tabular-nums">{_num(row.get('items_today'))}</td>
  <td class="px-3 py-2 text-right tabular-nums">{_num(row.get('items_yesterday'))}</td>
  <td class="px-3 py-2 font-mono text-[11px] text-slate">{_e(row.get('pulled_at'))}</td>
  <td class="px-3 py-2 max-w-[280px] text-[12px] text-crimson">{_e(error)}</td>
  <td class="px-3 py-2 font-sans text-[11px] uppercase tracking-[0.08em] text-slate">{_e(row.get('source_tier'))}</td>
</tr>""")
    return "\n".join(out)


def _artifact_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="4" class="px-3 py-4 text-slate">No artifacts found.</td></tr>'
    out = []
    for row in rows:
        status = "present" if row.get("exists") else "expected"
        status_cls = "text-teal" if row.get("exists") else "text-slate-dim"
        href = _e(row.get("href"))
        out.append(f"""
<tr class="border-b border-mist align-top">
  <td class="px-3 py-2">
    <a class="font-mono text-[12px] text-navy hover:text-gold" href="{href}">{_e(row.get('path'))}</a>
    <span class="ml-2 font-sans text-[10px] uppercase tracking-[0.10em] {status_cls}">{status}</span>
  </td>
  <td class="px-3 py-2 text-right tabular-nums">{_num(row.get('bytes'))}</td>
  <td class="px-3 py-2 font-mono text-[12px] text-slate">{_e(row.get('sha256'))}</td>
</tr>""")
    return "\n".join(out)


def render_reproducibility_page(out_dir: Path, doc: dict[str, Any]) -> Path:
    """Write the dated reproducibility page and return its path."""
    out_dir = Path(out_dir)
    iso = str(doc.get("brief_date") or "")
    hub_dir = out_dir / "reproducibility"
    target_dir = out_dir / "reproducibility" / iso
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "index.html"

    commit = doc.get("commit") or {}
    env = doc.get("environment") or {}
    fact = doc.get("fact_check") or {}
    lake = doc.get("lake_stats") or {}
    body = f"""
<main class="px-7 max-w-[1200px] mx-auto">
  <header class="pt-14 pb-8 border-b border-mist">
    <div class="font-sans text-kicker text-gold uppercase mb-3">Reproducibility · {_e(iso)}</div>
    <h1 class="font-serif text-editorial text-ink mb-4">How this brief was built</h1>
    <p class="font-sans text-slate text-[14px]">
      Build time <span class="font-mono">{_e(commit.get('time'))}</span>
      · commit <span class="font-mono text-navy">{_e(commit.get('short'))}</span>
    </p>
  </header>

  <section class="py-8 border-b border-mist">
    <h2 class="font-serif text-[28px] font-bold text-navy mb-4">Build Provenance</h2>
    <dl class="grid md:grid-cols-2 gap-x-8 gap-y-3 font-sans text-[13px]">
      <div><dt class="text-slate-dim uppercase tracking-[0.10em] text-[10px]">Full commit</dt><dd class="font-mono break-all">{_e(commit.get('sha'))}</dd></div>
      <div><dt class="text-slate-dim uppercase tracking-[0.10em] text-[10px]">Claim generator</dt><dd class="font-mono">{_e(doc.get('generator_version'))}</dd></div>
      <div><dt class="text-slate-dim uppercase tracking-[0.10em] text-[10px]">WORLDSCOPE version</dt><dd>{_e(env.get('worldscope_version'))}</dd></div>
      <div><dt class="text-slate-dim uppercase tracking-[0.10em] text-[10px]">Python</dt><dd class="font-mono">{_e(env.get('python_version'))}</dd></div>
      <div><dt class="text-slate-dim uppercase tracking-[0.10em] text-[10px]">OS hash</dt><dd class="font-mono">{_e(env.get('os_hash'))}</dd></div>
      <div><dt class="text-slate-dim uppercase tracking-[0.10em] text-[10px]">Platform</dt><dd class="font-mono break-all">{_e(env.get('platform'))}</dd></div>
    </dl>
  </section>

  <section class="py-8 border-b border-mist">
    <div class="flex flex-wrap items-end gap-4 mb-4">
      <h2 class="font-serif text-[28px] font-bold text-navy">Source Pulls</h2>
      <p class="font-sans text-[12px] text-slate">One row per section snapshot.</p>
    </div>
    <div class="overflow-x-auto bg-panel border border-mist shadow-card">
      <table class="min-w-full font-sans text-[12.5px]">
        <thead class="bg-parchment text-slate-dim uppercase tracking-[0.10em] text-[10px]">
          <tr>
            <th class="px-3 py-2 text-left">section_id</th>
            <th class="px-3 py-2 text-left">state</th>
            <th class="px-3 py-2 text-right">items_today</th>
            <th class="px-3 py-2 text-right">items_yesterday</th>
            <th class="px-3 py-2 text-left">pulled_at</th>
            <th class="px-3 py-2 text-left">error?</th>
            <th class="px-3 py-2 text-left">source_tier</th>
          </tr>
        </thead>
        <tbody>{_source_rows(doc.get('source_pulls') or [])}</tbody>
      </table>
    </div>
  </section>

  <section class="py-8 border-b border-mist grid lg:grid-cols-2 gap-8">
    <div>
      <h2 class="font-serif text-[28px] font-bold text-navy mb-4">Fact-Check Summary</h2>
      <div class="grid grid-cols-2 sm:grid-cols-5 gap-3 font-sans">
        <div><div class="stat text-[24px] font-bold text-navy">{_num(fact.get('verified'))}</div><div class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">verified</div></div>
        <div><div class="stat text-[24px] font-bold text-crimson">{_num(fact.get('divergent'))}</div><div class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">divergent</div></div>
        <div><div class="stat text-[24px] font-bold text-gold">{_num(fact.get('unverified'))}</div><div class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">unverified</div></div>
        <div><div class="stat text-[24px] font-bold text-slate">{_num(fact.get('skipped'))}</div><div class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">skipped</div></div>
        <div><div class="stat text-[24px] font-bold text-ink">{_num(fact.get('total'))}</div><div class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">total</div></div>
      </div>
      <a class="inline-block mt-4 font-sans text-[13px] font-semibold text-carolina hover:text-navy" href="../../data/claims.json">claims.json</a>
    </div>
    <div>
      <h2 class="font-serif text-[28px] font-bold text-navy mb-4">Lake Stats</h2>
      <dl class="grid grid-cols-2 gap-3 font-sans">
        <div><dt class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">records</dt><dd class="stat text-[24px] font-bold">{_num(lake.get('records'))}</dd></div>
        <div><dt class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">entities</dt><dd class="stat text-[24px] font-bold">{_num(lake.get('entities'))}</dd></div>
        <div><dt class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">relationships</dt><dd class="stat text-[24px] font-bold">{_num(lake.get('relationships'))}</dd></div>
        <div><dt class="text-[11px] uppercase tracking-[0.10em] text-slate-dim">cross-section signals</dt><dd class="stat text-[24px] font-bold">{_num(lake.get('cross_section_signals'))}</dd></div>
      </dl>
    </div>
  </section>

  <section class="py-8">
    <h2 class="font-serif text-[28px] font-bold text-navy mb-4">Artifacts</h2>
    <div class="overflow-x-auto bg-panel border border-mist shadow-card">
      <table class="min-w-full font-sans text-[12.5px]">
        <thead class="bg-parchment text-slate-dim uppercase tracking-[0.10em] text-[10px]">
          <tr>
            <th class="px-3 py-2 text-left">path</th>
            <th class="px-3 py-2 text-right">bytes</th>
            <th class="px-3 py-2 text-left">sha256</th>
          </tr>
        </thead>
        <tbody>{_artifact_rows(doc.get('artifacts') or [])}</tbody>
      </table>
    </div>
  </section>
</main>
{footer_block()}
"""
    page = page_shell(
        title=f"WORLDSCOPE · Reproducibility · {iso}",
        body_html=body,
        description=f"Build provenance and source-pull ledger for the {iso} WORLDSCOPE brief.",
        canonical=f"https://ihelfrich.github.io/worldscope/reproducibility/{iso}/",
        base="../../",
        network_assets_path="assets/network.js",
        include_chat=False,
    )
    target.write_text(page, encoding="utf-8")
    hub_dir.mkdir(parents=True, exist_ok=True)
    hub_body = f"""
<main class="px-7 max-w-[960px] mx-auto py-14">
  <div class="font-sans text-kicker text-gold uppercase mb-3">Reproducibility</div>
  <h1 class="font-serif text-editorial text-ink mb-4">Build proof sheets</h1>
  <p class="font-sans text-slate text-[15px] mb-6">Dated provenance pages for WORLDSCOPE daily briefs.</p>
  <a class="font-sans text-[14px] font-semibold text-carolina hover:text-navy"
     href="./{_e(iso)}/">{_e(iso)} proof sheet</a>
</main>
{footer_block()}
"""
    hub_page = page_shell(
        title="WORLDSCOPE · Reproducibility",
        body_html=hub_body,
        description="Dated build provenance pages for WORLDSCOPE daily briefs.",
        canonical="https://ihelfrich.github.io/worldscope/reproducibility/",
        base="../",
        network_assets_path="assets/network.js",
        include_chat=False,
    )
    (hub_dir / "index.html").write_text(hub_page, encoding="utf-8")
    return target
