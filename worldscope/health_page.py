"""Render /health/index.html source-health heatmap."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .lib.page_chrome import footer_block, page_shell


STATE_COLORS = {
    "fresh": "#157F3B",
    "empty_ok": "#1A8A87",
    "carry_forward": "#D4A017",
    "stale_after_failure": "#990000",
    "no_data": "#C9C1B2",
}

STATE_LABELS = {
    "fresh": "fresh",
    "empty_ok": "empty ok",
    "carry_forward": "carried",
    "stale_after_failure": "stale after failure",
    "no_data": "no data",
}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _day_labels(doc: dict[str, Any]) -> list[str]:
    sections = doc.get("sections") or []
    if not sections:
        return []
    return [h.get("date") or "" for h in (sections[0].get("history") or [])]


def _row(section: dict[str, Any]) -> str:
    sid = str(section.get("section_id") or "")
    hist = section.get("history") or []
    cells = []
    for h in hist:
        state = str(h.get("state") or "no_data")
        color = STATE_COLORS.get(state, STATE_COLORS["no_data"])
        parts = [
            h.get("date") or "",
            STATE_LABELS.get(state, state),
            f"{h.get('items') or 0} items",
        ]
        if h.get("carried_from"):
            parts.append(f"carried from {h['carried_from']}")
        if h.get("error"):
            parts.append(str(h["error"]))
        title = " · ".join(parts)
        cells.append(
            f'<span class="ws-health-cell" style="background:{color}" '
            f'title="{_e(title)}" aria-label="{_e(title)}"></span>'
        )
    return f"""
<tr class="ws-health-row border-b border-mist"
    data-section="{_e(sid.lower())}"
    data-failures="{int(section.get('consecutive_failure_days') or 0)}"
    data-fresh="{int(section.get('consecutive_fresh_days') or 0)}">
  <th class="sticky left-0 bg-panel px-3 py-2 text-left align-middle z-10">
    <div class="font-mono text-[12px] text-navy">{_e(sid)}</div>
    <div class="font-sans text-[10px] uppercase tracking-[0.10em] text-slate-dim">{_e(section.get('source_tier'))}</div>
  </th>
  <td class="px-3 py-2">
    <div class="flex gap-[3px] min-w-max">{''.join(cells)}</div>
  </td>
  <td class="px-3 py-2 text-right font-sans tabular-nums text-[12px]">{int(section.get('consecutive_fresh_days') or 0)}</td>
  <td class="px-3 py-2 text-right font-sans tabular-nums text-[12px]">{int(section.get('consecutive_failure_days') or 0)}</td>
</tr>"""


def render_health_page(out_dir: Path, doc: dict[str, Any]) -> Path:
    out_dir = Path(out_dir)
    target_dir = out_dir / "health"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "index.html"
    days = _day_labels(doc)
    day_headers = "".join(
        f'<span class="inline-block w-3.5 text-center font-sans text-[9px] text-slate-dim" title="{_e(d)}">{_e(d[-2:])}</span>'
        for d in days
    )
    rows = "\n".join(_row(s) for s in (doc.get("sections") or []))
    if not rows:
        rows = '<tr><td colspan="4" class="px-3 py-5 text-slate">No source-health data found.</td></tr>'

    legend = "".join(
        f'<span class="inline-flex items-center gap-1.5"><span class="w-2.5 h-2.5 inline-block" style="background:{color}"></span>{_e(STATE_LABELS[state])}</span>'
        for state, color in STATE_COLORS.items()
    )
    body = f"""
<main class="px-7 max-w-[1200px] mx-auto">
  <header class="pt-14 pb-8 border-b border-mist">
    <div class="font-sans text-kicker text-gold uppercase mb-3">Source Health · {_e(doc.get('as_of'))}</div>
    <h1 class="font-serif text-editorial text-ink mb-4">Thirty-day pull reliability</h1>
    <p class="font-sans text-slate text-[15px] max-w-3xl">Each cell is one section-day from the snapshot store, with current run states overlaid when available.</p>
  </header>

  <section class="py-6">
    <div class="flex flex-wrap gap-3 items-center justify-between mb-4">
      <div class="flex flex-wrap gap-3 font-sans text-[11px] text-slate">{legend}</div>
      <label class="font-sans text-[12px] text-slate">Sort
        <select id="ws-health-sort" class="ml-2 bg-panel border border-mist rounded px-2 py-1 text-[12px]">
          <option value="alpha">alphabetical</option>
          <option value="failing">most-failing</option>
          <option value="stable">most-stable</option>
        </select>
      </label>
    </div>
    <div class="overflow-x-auto bg-panel border border-mist shadow-card">
      <table class="min-w-full">
        <thead class="bg-parchment border-b border-mist">
          <tr>
            <th class="sticky left-0 bg-parchment px-3 py-2 text-left font-sans text-[10px] uppercase tracking-[0.10em] text-slate-dim z-10">section</th>
            <th class="px-3 py-2 text-left"><div class="flex gap-[3px] min-w-max">{day_headers}</div></th>
            <th class="px-3 py-2 text-right font-sans text-[10px] uppercase tracking-[0.10em] text-slate-dim">fresh</th>
            <th class="px-3 py-2 text-right font-sans text-[10px] uppercase tracking-[0.10em] text-slate-dim">fail</th>
          </tr>
        </thead>
        <tbody id="ws-health-body">{rows}</tbody>
      </table>
    </div>
  </section>
</main>
<style>
  .ws-health-cell {{
    display: inline-block;
    width: 14px;
    height: 22px;
    border-radius: 2px;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.35);
  }}
</style>
<script>
(function() {{
  const select = document.getElementById('ws-health-sort');
  const body = document.getElementById('ws-health-body');
  if (!select || !body) return;
  function sortRows() {{
    const rows = Array.from(body.querySelectorAll('.ws-health-row'));
    const mode = select.value;
    rows.sort((a, b) => {{
      if (mode === 'failing') return Number(b.dataset.failures) - Number(a.dataset.failures) || a.dataset.section.localeCompare(b.dataset.section);
      if (mode === 'stable') return Number(b.dataset.fresh) - Number(a.dataset.fresh) || a.dataset.section.localeCompare(b.dataset.section);
      return a.dataset.section.localeCompare(b.dataset.section);
    }});
    rows.forEach(row => body.appendChild(row));
  }}
  select.addEventListener('change', sortRows);
}}());
</script>
{footer_block()}
"""
    page = page_shell(
        title=f"WORLDSCOPE · Source health · {doc.get('as_of')}",
        body_html=body,
        description=f"Thirty-day source health heatmap for WORLDSCOPE as of {doc.get('as_of')}.",
        canonical="https://ihelfrich.github.io/worldscope/health/",
        base="../",
        network_assets_path="assets/network.js",
        include_chat=False,
    )
    target.write_text(page, encoding="utf-8")
    return target
