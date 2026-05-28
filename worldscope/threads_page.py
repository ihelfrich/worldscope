"""threads_page — render /threads/ index + per-thread timelines.

Reads threads.json (written by worldscope.threads) and emits:

  dist/threads/index.html       — list of active threads, ranked by heat
  dist/threads/<slug>/index.html — timeline view of one thread

Both use the shared page_shell so chrome matches the homepage + graph
view.
"""
from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

from .lib.page_chrome import page_shell


def _heat_pill(heat: float, active: bool) -> str:
    if not active:
        return '<span class="font-sans uppercase tracking-[0.10em] text-[10px] font-bold text-slate-dim px-2 py-0.5 rounded bg-mist border border-mist-strong">cooling</span>'
    if heat >= 1000:
        return '<span class="font-sans uppercase tracking-[0.10em] text-[10px] font-bold text-white px-2 py-0.5 rounded bg-crimson">running hot</span>'
    if heat >= 100:
        return '<span class="font-sans uppercase tracking-[0.10em] text-[10px] font-bold text-ink px-2 py-0.5 rounded bg-gold">active</span>'
    return '<span class="font-sans uppercase tracking-[0.10em] text-[10px] font-bold text-navy px-2 py-0.5 rounded bg-mist border border-mist-strong">tracking</span>'


def render_threads_index(out_dir: Path, threads_doc: dict,
                          today: date | None = None) -> Path:
    out_dir = Path(out_dir)
    target_dir = out_dir / "threads"
    target_dir.mkdir(parents=True, exist_ok=True)
    iso = (today or date.today()).isoformat()
    threads = threads_doc.get("threads") or []

    if not threads:
        body = """
<main class="px-7 max-w-[1200px] mx-auto pt-12 pb-16">
  <header class="mb-7">
    <div class="font-sans text-kicker text-gold uppercase mb-3">STORY THREADS</div>
    <h1 class="font-serif text-editorial text-ink mb-2">No multi-day arcs today</h1>
    <p class="font-serif text-lede text-slate max-w-2xl">
      Threads emerge when an entity persists across ≥3 days with ≥5 total items.
      Today's signal hasn't accumulated long enough.
    </p>
  </header>
</main>
"""
    else:
        cards = []
        for t in threads:
            slug   = html.escape(t["slug"], quote=True)
            title  = html.escape(t["title"])
            synth  = html.escape(t["synth"])
            etype  = html.escape((t.get("entity_type") or "topic").split(":")[0])
            secs   = ", ".join(html.escape(s) for s in (t.get("sections_touched") or [])[:4])
            heat   = _heat_pill(t.get("heat_score", 0), t.get("is_active_today", False))
            sparkline = _spark_html(t.get("items_by_day", {}), iso)
            cards.append(f"""
<a href="./{slug}/" class="lift-card block bg-panel border border-mist rounded-xl p-5 shadow-card border-l-[3px] border-l-navy animate-fade-rise no-underline">
  <div class="flex items-baseline gap-3 mb-2 flex-wrap">
    <span class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim">{etype}</span>
    {heat}
  </div>
  <h3 class="font-serif text-[20px] font-bold text-ink leading-tight mb-1.5 tracking-[-0.012em]">{title}</h3>
  <p class="text-slate text-[13.5px] font-sans leading-snug mb-3">{synth}</p>
  <div class="flex items-baseline justify-between gap-3">
    <div class="font-sans text-[12px] text-slate-dim">
      <span class="font-bold text-navy tabular-nums">{t["days_active"]}</span> days &middot;
      <span class="font-bold text-navy tabular-nums">{t["items_total"]:,}</span> items &middot;
      {secs}
    </div>
    <div>{sparkline}</div>
  </div>
</a>""")

        body = f"""
<main class="px-7 max-w-[1200px] mx-auto pt-10 pb-16">
  <header class="mb-7">
    <div class="font-sans text-kicker text-gold uppercase mb-3">STORY THREADS &middot; {iso}</div>
    <h1 class="font-serif text-editorial text-ink mb-2">What's been running</h1>
    <p class="font-serif text-lede text-slate max-w-3xl">
      Multi-day arcs auto-detected from entity persistence across the last {threads_doc.get('lookback_days', 14)} days.
      Each thread is an entity whose mentions span ≥3 days with ≥5 total items;
      heat ranks recency, volume, and section breadth together.
    </p>
  </header>
  <hr class="editorial-rule mb-6">
  <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
    {"".join(cards)}
  </div>
</main>
"""
    page = page_shell(
        title=f"WORLDSCOPE · Threads · {iso}",
        body_html=body,
        description="Multi-day story arcs auto-detected from entity persistence.",
        canonical="https://ihelfrich.github.io/worldscope/threads/",
        base="../",
        include_chat=False,
    )
    path = target_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def _spark_html(items_by_day: dict[str, list[dict]], today_iso: str) -> str:
    """Tiny inline sparkline showing item counts per day, last 7 days."""
    today_d = date.fromisoformat(today_iso)
    bars = []
    counts = []
    for i in range(6, -1, -1):
        d = (today_d.fromordinal(today_d.toordinal() - i)).isoformat()
        n = len(items_by_day.get(d, []))
        counts.append(n)
    if not any(counts):
        return ""
    cmax = max(counts) or 1
    for n in counts:
        h = int(2 + (n / cmax) * 18)
        # today bar is gold, rest are navy
        is_today = counts.index(n) == len(counts) - 1
        color = "#D4A017" if is_today else "#1F3D6E"
        bars.append(
            f'<span class="inline-block align-baseline mx-[0.5px] rounded-sm" '
            f'style="width:5px;height:{h}px;background:{color};opacity:{0.85 if n else 0.18}"></span>'
        )
    return f'<span class="inline-flex items-end" title="last 7 days">{"".join(bars)}</span>'


def render_thread_detail(out_dir: Path, thread: dict,
                          today: date | None = None) -> Path:
    out_dir = Path(out_dir)
    iso = (today or date.today()).isoformat()
    slug = thread["slug"]
    target_dir = out_dir / "threads" / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    title = html.escape(thread["title"])
    etype = html.escape((thread.get("entity_type") or "topic").split(":")[0])
    secs  = ", ".join(html.escape(s) for s in (thread.get("sections_touched") or []))

    # Timeline: days in reverse chronological order, each with item cards
    days_html = []
    days_sorted = sorted((thread.get("items_by_day") or {}).keys(), reverse=True)
    for day in days_sorted:
        items = thread["items_by_day"].get(day) or []
        if not items: continue
        item_lis = []
        for it in items:
            url = html.escape(it.get("url") or "#", quote=True)
            t   = html.escape(it.get("title") or "")
            sm  = html.escape((it.get("summary") or "")[:240])
            sec = html.escape(it.get("section_id") or "")
            link_open = url != html.escape("#", quote=True)
            href_attr = (f'href="{url}" target="_blank" rel="noopener noreferrer" '
                         'class="font-serif font-medium text-[15px] text-navy hover:text-gold leading-snug"') if link_open else (
                'class="font-serif font-medium text-[15px] text-ink leading-snug"')
            item_lis.append(f"""
<li class="py-2.5 border-b border-mist last:border-b-0">
  <div class="flex items-baseline gap-2 flex-wrap">
    <span class="font-sans uppercase tracking-[0.10em] text-[10px] font-bold text-slate-dim">{sec}</span>
    <a {href_attr}>{t}</a>
  </div>
  {f'<div class="text-slate text-[13px] leading-snug mt-1 font-sans">{sm}</div>' if sm else ''}
</li>""")
        is_today = day == iso
        day_kicker = "TODAY" if is_today else day
        days_html.append(f"""
<section class="mb-7">
  <h2 class="font-sans uppercase tracking-[0.18em] text-[11px] font-bold text-{('gold' if is_today else 'slate-dim')} mb-3 flex items-baseline gap-3">
    <span class="tabular-nums">{day_kicker}</span>
    <span class="text-slate-dim font-normal normal-case tracking-normal text-[12px]">{len(items)} item{'s' if len(items)!=1 else ''}</span>
  </h2>
  <ul class="list-none p-0 m-0">{"".join(item_lis)}</ul>
</section>""")

    heat_pill = _heat_pill(thread.get("heat_score", 0), thread.get("is_active_today", False))
    body = f"""
<main class="px-7 max-w-[1100px] mx-auto pt-10 pb-16">
  <div class="font-sans text-[12px] mb-4">
    <a href="../" class="text-carolina hover:text-navy">← All threads</a>
  </div>
  <header class="mb-6">
    <div class="flex items-baseline gap-3 mb-3 flex-wrap">
      <div class="font-sans text-kicker text-gold uppercase">{etype} &middot; THREAD</div>
      {heat_pill}
    </div>
    <h1 class="font-serif text-editorial text-ink mb-3">{title}</h1>
    <p class="font-serif text-lede text-slate max-w-3xl mb-5">{html.escape(thread.get('synth') or '')}</p>
    <div class="font-sans text-[12.5px] text-slate flex flex-wrap gap-x-5 gap-y-1">
      <span><span class="text-slate-dim">Days active:</span> <strong class="text-navy tabular-nums">{thread["days_active"]}</strong></span>
      <span><span class="text-slate-dim">Total items:</span> <strong class="text-navy tabular-nums">{thread["items_total"]:,}</strong></span>
      <span><span class="text-slate-dim">Sections:</span> <strong class="text-navy">{secs}</strong></span>
      <span><span class="text-slate-dim">Heat:</span> <strong class="text-navy tabular-nums">{thread.get("heat_score", 0):,.1f}</strong></span>
    </div>
  </header>
  <hr class="editorial-rule mb-6">
  {"".join(days_html)}
</main>
"""
    page = page_shell(
        title=f"WORLDSCOPE · Thread · {thread['title']}",
        body_html=body,
        description=f"Multi-day thread for {thread['title']}.",
        canonical=f"https://ihelfrich.github.io/worldscope/threads/{slug}/",
        base="../../",
        include_chat=False,
    )
    path = target_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path


def render_all_threads(out_dir: Path, threads_doc: dict,
                       today: date | None = None) -> list[Path]:
    """Render the index page + every per-thread detail page."""
    written: list[Path] = []
    written.append(render_threads_index(out_dir, threads_doc, today=today))
    for t in threads_doc.get("threads") or []:
        written.append(render_thread_detail(out_dir, t, today=today))
    return written
