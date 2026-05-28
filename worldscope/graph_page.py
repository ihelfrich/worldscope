"""graph_page — render the /graph/ entity-network view.

Writes dist/graph/index.html. Uses the same page_chrome (topnav,
heritage palette, fonts, canvas background) as the homepage for visual
consistency, but the main body is a full-viewport D3 force-directed
graph + a detail sidebar + a search box.

The actual interactive view lives in dist/assets/worldscope-graph.js,
which loads ../data/graph.json (written by worldscope.graph_export).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .lib.page_chrome import page_shell


def render_graph_page(out_dir: Path, today: date | None = None) -> Path:
    """Render dist/graph/index.html. Returns the path written."""
    out_dir = Path(out_dir)
    target_dir = out_dir / "graph"
    target_dir.mkdir(parents=True, exist_ok=True)
    iso = (today or date.today()).isoformat()

    body = f"""
<main class="px-7 max-w-[1400px] mx-auto pt-10 pb-16">
  <header class="mb-7">
    <div class="font-sans text-kicker text-gold uppercase mb-3">ENTITY NETWORK · {iso}</div>
    <h1 class="font-serif text-editorial text-ink mb-2">Today, mapped as a graph</h1>
    <p class="font-serif text-lede text-slate max-w-3xl">
      Every node is an entity mentioned today. Every edge is a co-occurrence in the
      same record. Cross-section recurrence signals glow gold. Click any node to drill
      into its neighborhood; click <em>Ask the brief</em> to query that entity via chat.
    </p>
  </header>

  <hr class="editorial-rule mb-6">

  <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
    <!-- Graph canvas -->
    <section class="lg:col-span-9 bg-panel border border-mist rounded-xl shadow-card overflow-hidden">
      <header class="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-mist">
        <div>
          <div class="font-sans uppercase tracking-[0.18em] text-[10.5px] font-bold text-slate-dim">
            Force-directed network
          </div>
          <div id="g-meta" class="font-sans text-[12.5px] text-slate tabular-nums mt-0.5">loading…</div>
        </div>
        <div class="flex items-center gap-2">
          <input id="g-search"
                 type="search"
                 placeholder="search entities…"
                 class="font-sans text-[13px] bg-parchment border border-mist rounded px-3 py-1.5
                        w-64 focus:border-navy focus:outline-none focus:ring-2 focus:ring-navy/15">
        </div>
      </header>
      <div id="graph-root" class="w-full" style="min-height: 720px"></div>
      <footer id="g-pinned" class="px-5 py-3 border-t border-mist font-sans text-[12px] text-slate"></footer>
    </section>

    <!-- Detail sidebar -->
    <aside class="lg:col-span-3 bg-panel border border-mist rounded-xl shadow-card p-5">
      <div id="g-detail" class="font-sans text-[13.5px] text-ink">
        <div class="font-sans text-kicker text-gold uppercase mb-2">DETAIL</div>
        <h3 class="font-serif text-[19px] font-bold text-ink mb-2 leading-tight">Click any node to inspect</h3>
        <p class="text-slate text-[13px] leading-relaxed mb-4">
          Hover over a node to highlight its co-mentioned neighborhood. Drag to reposition.
          Pinch / scroll to zoom. Use the search box to filter by name.
        </p>
        <div class="border-t border-mist mt-4 pt-4">
          <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-2">Legend</div>
          <ul class="space-y-1.5 text-[12.5px] font-sans text-slate">
            <li class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#13294B"></span> person</li>
            <li class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#1A8A87"></span> organization</li>
            <li class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#D4A017"></span> place / country</li>
            <li class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#990000"></span> policy / statute</li>
            <li class="flex items-center gap-2"><span class="inline-block w-2.5 h-2.5 rounded-full" style="background:#4B9CD3"></span> topic</li>
            <li class="flex items-center gap-2 mt-2"><span class="inline-block w-3 h-3 rounded-full" style="background:rgba(212,160,23,0.3);border:1.5px solid #D4A017"></span> pinned (cross-section signal)</li>
          </ul>
        </div>
      </div>
    </aside>
  </div>

  <p class="text-slate-dim font-sans text-[11.5px] mt-6 italic">
    Edges drawn between nodes that co-occur in ≥2 records today. Capped at 150 nodes and 600 edges
    for performance. Cross-section recurrence entities are pinned regardless of mention count.
  </p>
</main>
<style>
  .pinned-link {{
    background: none; border: 0; padding: 0;
    color: #13294B; text-decoration: underline;
    text-underline-offset: 2px; text-decoration-color: rgba(19,41,75,0.25);
    cursor: pointer; font-family: 'Inter', sans-serif; font-weight: 600;
  }}
  .pinned-link:hover {{ color: #D4A017; text-decoration-color: #D4A017; }}
  #graph-root svg {{ display: block; width: 100%; height: 100%; }}
</style>
<script src="../assets/worldscope-graph.js" defer></script>
"""

    html = page_shell(
        title=f"WORLDSCOPE · Entity graph · {iso}",
        body_html=body,
        description=f"Interactive entity co-occurrence graph for {iso}.",
        canonical=f"https://ihelfrich.github.io/worldscope/graph/",
        base="../",
        network_seed_json="{}",
        network_assets_path="assets/network.js",
        include_chat=False,  # chat lives on the homepage; graph defers to it
    )

    path = target_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
