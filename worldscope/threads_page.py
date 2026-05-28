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


_TIER_BY_SECTION = {
    "federal_register":     "primary_document",
    "sanctions":            "primary_document",
    "sanctions_procurement":"primary_document",
    "macro":                "primary_document",
    "markets":              "primary_document",
    "markets_global":       "primary_document",
    "congressional_trades": "primary_document",
    "form4":                "primary_document",
    "fec":                  "primary_document",
    "firms":                "primary_document",
    "cisa_kev":             "primary_document",
    "courtlistener":        "primary_document",
    "state_bills":          "primary_document",
    "political_figures":    "primary_document",
    "vip_flights":          "primary_document",
    "weather":              "primary_document",
    "foreign_news":         "mainstream_independent",
    "commentary":           "mainstream_independent",
    "forecasts":            "mainstream_independent",
    "conflict":             "mainstream_independent",
    "ukraine_theater":      "mainstream_independent",
    "people":               "mainstream_independent",
    "billionaires":         "mainstream_independent",
    "mediacloud":           "mainstream_independent",
    "local_news":           "regional_independent",
    "state_news":           "regional_independent",
    "russian_internal":     "regional_independent",
    "chinese_internal":     "regional_independent",
    "ukrainian_internal":   "regional_independent",
    "acled":                "ngo_independent",
    "reliefweb":            "ngo_independent",
    "promed":               "ngo_independent",
    "wikidata_changes":     "ngo_independent",
    "gdelt_gkg":            "academic",
    "gdelt_regions":        "academic",
    "paper_bets":           "primary_document",
    "paper_bet_placement":  "primary_document",
}
_TIER_PALETTE = {
    "primary_document":      "#13294B",
    "mainstream_independent":"#1F3D6E",
    "regional_independent":  "#4B9CD3",
    "ngo_independent":       "#1A8A87",
    "academic":              "#D4A017",
    "social":                "#990000",
}
_TIER_LABEL = {
    "primary_document":      "primary",
    "mainstream_independent":"mainstream",
    "regional_independent":  "regional",
    "ngo_independent":       "ngo",
    "academic":              "academic",
    "social":                "social",
}


def _classify_chapters(items_by_day: dict[str, list[dict]]) -> dict[str, str]:
    """For each day in the thread, decide its 'chapter' label based on
    day-over-day item volume:
      'opening'      — first day of the thread
      'turning'      — day with ≥ 2× the running median
      'continuing'   — within ±50% of running median
      'cooling'      — < 50% of running median
    Returns {date: chapter}."""
    days = sorted(items_by_day.keys())
    if not days:
        return {}
    counts = [len(items_by_day[d]) for d in days]
    labels: dict[str, str] = {}
    if counts:
        # Use a simple running median ignoring zeros.
        nonzero = [c for c in counts if c]
        if nonzero:
            sorted_nz = sorted(nonzero)
            median = sorted_nz[len(sorted_nz) // 2]
        else:
            median = 1
        for i, (d, c) in enumerate(zip(days, counts)):
            if i == 0:
                labels[d] = "opening"
            elif c >= 2 * median:
                labels[d] = "turning"
            elif c <= 0.5 * median:
                labels[d] = "cooling"
            else:
                labels[d] = "continuing"
    return labels


def _key_entities(items_by_day: dict[str, list[dict]],
                   thread_title: str, limit: int = 8) -> list[tuple[str, int]]:
    """Surface entities co-mentioned across this thread's records.

    Pure substring-match against capitalized multi-word tokens in the
    items' titles and summaries. Filters out the thread's own title
    (since by definition every record mentions it) and stopword-ish
    short tokens. Returns (entity, count) sorted by count desc."""
    import re
    from collections import Counter
    GENERIC = {"The", "United", "States", "U.S.", "US", "President",
                "Senator", "Congress", "House", "Senate", "American",
                "Today", "Yesterday", "Reuters", "AP", "AFP", "Bloomberg"}
    # Match capitalized multi-word names: e.g. "Kevin Warsh", "New York"
    pat = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)+\b")
    counter: Counter[str] = Counter()
    own = (thread_title or "").lower()
    for items in items_by_day.values():
        for it in items:
            blob = (it.get("title") or "") + " " + (it.get("summary") or "")
            for m in pat.finditer(blob):
                name = m.group(0)
                if name in GENERIC:
                    continue
                if name.lower() == own:
                    continue
                counter[name] += 1
    return counter.most_common(limit)


def _source_tier_breakdown(items_by_day: dict[str, list[dict]]) -> list[tuple[str, int]]:
    """Return [(tier_label, count), ...] for the thread's items."""
    from collections import Counter
    counter: Counter[str] = Counter()
    for items in items_by_day.values():
        for it in items:
            sid = it.get("section_id") or ""
            tier = _TIER_BY_SECTION.get(sid, "mainstream_independent")
            counter[tier] += 1
    # Stable order: highest count first
    return [(t, n) for t, n in counter.most_common()]


def _tier_breakdown_html(breakdown: list[tuple[str, int]]) -> str:
    if not breakdown:
        return ""
    total = sum(n for _, n in breakdown) or 1
    # Stacked horizontal bar.
    segments = []
    for tier, n in breakdown:
        pct = n / total * 100
        color = _TIER_PALETTE.get(tier, "#4E5667")
        label = _TIER_LABEL.get(tier, tier)
        segments.append(
            f'<span class="ws-tier-seg" style="background:{color};width:{pct:.1f}%" '
            f'title="{html.escape(label)}: {n} ({pct:.0f}%)"></span>'
        )
    legend_items = []
    for tier, n in breakdown:
        color = _TIER_PALETTE.get(tier, "#4E5667")
        label = _TIER_LABEL.get(tier, tier)
        legend_items.append(
            f'<li class="flex items-center gap-2 text-[12px]">'
            f'<span class="inline-block w-2.5 h-2.5 rounded-sm" style="background:{color}"></span>'
            f'<span class="text-slate">{html.escape(label)}</span>'
            f'<span class="tabular-nums text-slate-dim ml-auto">{n}</span></li>'
        )
    return f"""
<div class="bg-panel border border-mist rounded-lg p-4 mb-6">
  <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-3">
    Source-tier coverage
  </div>
  <div class="flex w-full h-2 rounded overflow-hidden bg-mist mb-3">{''.join(segments)}</div>
  <ul class="list-none p-0 m-0 space-y-1.5 font-sans">{''.join(legend_items)}</ul>
</div>"""


def _chapter_label_html(label: str) -> str:
    if label == "opening":
        return '<span class="ws-chapter ws-chapter-opening">opening</span>'
    if label == "turning":
        return '<span class="ws-chapter ws-chapter-turning">turning point</span>'
    if label == "cooling":
        return '<span class="ws-chapter ws-chapter-cooling">cooling</span>'
    return ""


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

    items_by_day = thread.get("items_by_day") or {}
    chapter_labels = _classify_chapters(items_by_day)
    key_ents = _key_entities(items_by_day, thread.get("title") or "")
    tier_breakdown = _source_tier_breakdown(items_by_day)

    # Day-over-day delta for each day (vs the previous chronological day).
    days_asc = sorted(items_by_day.keys())
    delta_by_day: dict[str, int | None] = {}
    for i, d in enumerate(days_asc):
        if i == 0:
            delta_by_day[d] = None
        else:
            delta_by_day[d] = len(items_by_day[d]) - len(items_by_day[days_asc[i-1]])

    # Timeline: days in reverse chronological order, each with item cards
    days_html = []
    days_sorted = sorted(items_by_day.keys(), reverse=True)
    for day in days_sorted:
        items = items_by_day.get(day) or []
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
        chapter = chapter_labels.get(day, "continuing")
        chapter_pill = _chapter_label_html(chapter)
        # Delta vs prior day in this thread
        delta = delta_by_day.get(day)
        if delta is None:
            delta_pill = ""
        elif delta > 0:
            delta_pill = f'<span class="ws-day-delta ws-day-up tabular-nums">+{delta}</span>'
        elif delta < 0:
            delta_pill = f'<span class="ws-day-delta ws-day-dn tabular-nums">{delta}</span>'
        else:
            delta_pill = '<span class="ws-day-delta ws-day-flat">—</span>'
        days_html.append(f"""
<section class="mb-7" data-chapter="{chapter}">
  <h2 class="font-sans uppercase tracking-[0.18em] text-[11px] font-bold mb-3 flex items-baseline gap-3 flex-wrap text-{('gold' if is_today else 'slate-dim')}">
    <span class="tabular-nums">{day_kicker}</span>
    <span class="text-slate-dim font-normal normal-case tracking-normal text-[12px]">{len(items)} item{'s' if len(items)!=1 else ''}</span>
    {delta_pill}
    {chapter_pill}
  </h2>
  <ul class="list-none p-0 m-0">{"".join(item_lis)}</ul>
</section>""")

    # Key entities sidebar block.
    entities_html = ""
    if key_ents:
        rows = []
        for name, n in key_ents:
            rows.append(
                f'<li class="flex items-baseline justify-between gap-3 py-1.5 border-b border-mist last:border-b-0">'
                f'<span class="font-serif text-[14.5px] text-navy">{html.escape(name)}</span>'
                f'<span class="text-slate-dim text-[11px] tabular-nums">×{n}</span></li>'
            )
        entities_html = f"""
<div class="bg-panel border border-mist rounded-lg p-4 mb-6">
  <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-3">
    Co-mentioned with {title}
  </div>
  <ul class="list-none p-0 m-0">{''.join(rows)}</ul>
</div>"""

    # "Ask the brief about this thread" — prefills the chat with a falsification prompt.
    falsify_text = (
        f"What would falsify the {thread.get('title')} thread tomorrow? "
        "Which specific record or upstream signal would I need to see to "
        "conclude this arc is over?"
    )
    falsify_payload = html.escape(falsify_text, quote=True)
    falsify_block = f"""
<div class="bg-mist/40 border border-mist-strong rounded-lg p-4 mb-6">
  <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-2">
    What would disconfirm this?
  </div>
  <p class="font-serif text-[14.5px] text-ink mb-3 leading-snug">
    Every active thread has an inverse-evidence pointer: the specific signal that
    would convince you the arc is over. Ask the brief.
  </p>
  <button id="ws-falsify-btn" type="button"
          data-prefill="{falsify_payload}"
          class="font-sans text-[12.5px] font-semibold bg-navy text-white px-3 py-1.5 rounded hover:bg-navy-soft transition-colors">
    Ask the brief →
  </button>
</div>
<script>
(() => {{
  const btn = document.getElementById('ws-falsify-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {{
    try {{
      sessionStorage.setItem('ws.chat.prefill', btn.dataset.prefill);
    }} catch (e) {{}}
    window.location.href = '../../index.html#chat';
  }});
}})();
</script>"""

    heat_pill = _heat_pill(thread.get("heat_score", 0), thread.get("is_active_today", False))
    body = f"""
<main class="px-7 max-w-[1200px] mx-auto pt-10 pb-16">
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
  <div class="grid grid-cols-1 lg:grid-cols-12 gap-7">
    <div class="lg:col-span-8">
      {"".join(days_html)}
    </div>
    <aside class="lg:col-span-4">
      {_tier_breakdown_html(tier_breakdown)}
      {entities_html}
      {falsify_block}
    </aside>
  </div>
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
