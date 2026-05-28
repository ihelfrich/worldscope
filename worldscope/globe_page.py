"""globe_page — render the /globe/ interactive intelligence globe.

A frosty-white 3D Earth with thin country borders, soft mention-count
shading per country, gold cross-section signal highlights, and pulsing
alert rings. Click any country to inspect its records via the Evidence
Drawer.

The page is mostly a thin shell; the interactive layer lives in
dist/assets/worldscope-globe.js. Data sources are the same JSON files
the homepage already produces (today.json, entities.json, signals.json)
so no extra orchestration is required.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .lib.page_chrome import page_shell


def render_globe_page(out_dir: Path, today: date | None = None) -> Path:
    out_dir = Path(out_dir)
    target_dir = out_dir / "globe"
    target_dir.mkdir(parents=True, exist_ok=True)
    iso = (today or date.today()).isoformat()

    body = f"""
<main class="px-7 max-w-[1600px] mx-auto pt-8 pb-16">
  <header class="mb-6">
    <div class="font-sans text-kicker text-gold uppercase mb-3">GLOBE &middot; {iso}</div>
    <h1 class="font-serif text-editorial text-ink mb-3 leading-tight">Today, as a planet</h1>
    <p class="font-serif text-lede text-slate max-w-3xl mb-3">
      Every country shaded by today's mention count. Cross-section recurrence signals
      glow gold. Hover for the country's section coverage, click to inspect its records
      in the Evidence Drawer. Drag to spin, scroll to zoom.
    </p>
    <div id="globe-stats" class="font-sans text-[12.5px] text-slate-dim tabular-nums">loading…</div>
  </header>

  <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
    <!-- Globe canvas -->
    <section class="lg:col-span-9">
      <div class="bg-panel border border-mist rounded-xl shadow-card overflow-hidden">
        <header class="flex flex-wrap items-center gap-3 px-5 py-3 border-b border-mist">
          <div class="font-sans uppercase tracking-[0.18em] text-[10.5px] font-bold text-slate-dim">
            Interactive 3D Earth
          </div>
          <span class="flex-1"></span>
          <button id="g-rotate" type="button"
                  class="font-sans uppercase tracking-[0.10em] text-[10.5px] font-bold text-slate hover:text-navy border border-mist hover:border-gold rounded px-2.5 py-1 transition-colors">
            Pause rotation
          </button>
          <button id="g-reset" type="button"
                  class="font-sans uppercase tracking-[0.10em] text-[10.5px] font-bold text-slate hover:text-navy border border-mist hover:border-gold rounded px-2.5 py-1 transition-colors">
            Reset view
          </button>
        </header>
        <div id="globe-root" class="relative w-full" style="aspect-ratio: 16/11; min-height: 520px; background: radial-gradient(ellipse at 50% 60%, #FFFFFF 0%, #FAF8F3 70%, #F0EBE0 100%);"></div>
        <footer class="px-5 py-3 border-t border-mist font-sans text-[12px] text-slate-dim">
          <div class="flex flex-wrap items-center gap-x-5 gap-y-1.5">
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-3 h-3 rounded-sm" style="background:rgba(212,160,23,0.5);border:1px solid #D4A017"></span>
              cross-section signal
            </span>
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-3 h-3 rounded-sm" style="background:rgba(75,156,211,0.45)"></span>
              mentioned today (shade ∝ count)
            </span>
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-3 h-3 rounded-sm" style="background:rgba(255,255,255,0.04);border:1px solid rgba(19,41,75,0.4)"></span>
              no records today
            </span>
            <span class="flex items-center gap-1.5">
              <span class="inline-block w-2 h-2 rounded-full ring-2 ring-gold/40" style="background:#D4A017"></span>
              pulsing = top 25 most-mentioned
            </span>
          </div>
        </footer>
      </div>
    </section>

    <!-- Detail sidebar -->
    <aside class="lg:col-span-3 bg-panel border border-mist rounded-xl shadow-card p-5">
      <div id="globe-detail" class="font-sans text-[13.5px] text-ink">
        <div class="font-sans text-kicker text-gold uppercase mb-2">SELECT</div>
        <h3 class="font-serif text-[19px] font-bold text-ink mb-2 leading-tight">Click a country</h3>
        <p class="text-slate text-[13px] leading-relaxed mb-4">
          The interactive globe surfaces what today's brief is actually about,
          geographically. The frosty-white basemap is intentional — borders and signal
          should dominate the eye, not satellite imagery.
        </p>
        <div class="border-t border-mist pt-4">
          <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-2">Controls</div>
          <ul class="space-y-1.5 text-[12.5px] font-sans text-slate">
            <li>· <strong class="text-navy">Drag</strong> to rotate · <strong class="text-navy">Scroll</strong> to zoom</li>
            <li>· <strong class="text-navy">Hover</strong> a country to peek at its records</li>
            <li>· <strong class="text-navy">Click</strong> a country to open it in the Evidence Drawer</li>
          </ul>
        </div>
        <div class="border-t border-mist mt-5 pt-4">
          <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-2">Coming</div>
          <ul class="space-y-1.5 text-[12.5px] font-sans text-slate-dim">
            <li>· Satellite orbits with sensor swaths</li>
            <li>· Maritime AIS traffic layer</li>
            <li>· Flatten to 2D projection</li>
            <li>· Sunset light/dark transition</li>
          </ul>
        </div>
      </div>
    </aside>
  </div>

  <p class="text-slate-dim font-sans text-[11.5px] mt-6 italic">
    Country boundaries from Natural Earth (1:110m). Topojson by Mike Bostock.
    Globe rendered with <a href="https://github.com/vasturiano/globe.gl" class="text-carolina hover:text-navy">globe.gl</a>
    (three.js / WebGL). Auto-rotates until first interaction.
  </p>
</main>
<script src="../assets/worldscope-globe.js" defer></script>
"""

    page = page_shell(
        title=f"WORLDSCOPE · Globe · {iso}",
        body_html=body,
        description=f"Interactive intelligence globe for {iso}.",
        canonical=f"https://ihelfrich.github.io/worldscope/globe/",
        base="../",
        include_chat=False,
    )
    path = target_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
