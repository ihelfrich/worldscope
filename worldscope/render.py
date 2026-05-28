"""HTML render layer for the daily briefing homepage.

Writes dist/YYYY-MM-DD.html plus an index.html alias to the most recent.
The page is composed in three movements:

  1. Editorial hero: kicker + date headline + download action.
  2. Signal block: cross-section recurrence chips (drawn from
     lake/sections/_meta/<date>/cross_section.json) + a top-3 NEW-items
     hero column from the highest-significance items across all sections.
  3. Synthesis overview (drop-cap'd lede from overview.py).
  4. Adaptive section-card grid: every section renders as a card with a
     synth paragraph, top NEW items, and a native <details> disclosure
     for the remainder. Cards re-flow 1 / 2 / 3 columns by viewport.

Chrome (Tailwind + heritage palette + canvas background) lives in
lib/page_chrome.py so this module owns layout/composition only.
"""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .lib.page_chrome import footer_block, page_shell

# -----------------------------------------------------------------------------
# Markdown -> HTML for the overview block (minimal, no external dep)
# -----------------------------------------------------------------------------

def _md_to_html(md: str) -> str:
    out: list[str] = []
    in_list = False
    paragraph_count = 0
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("")
            continue
        # Strip the prefix BEFORE escaping so we don't escape "# ".
        if line.startswith("# "):
            out.append(
                f'<h2 class="font-serif text-[26px] font-extrabold text-navy mt-7 mb-3 tracking-tight">{html.escape(line[2:])}</h2>'
            )
        elif line.startswith("## "):
            out.append(
                f'<h3 class="font-sans uppercase tracking-[0.10em] text-[11px] font-bold text-slate-dim mt-6 mb-2">{html.escape(line[3:])}</h3>'
            )
        elif line.startswith("### "):
            out.append(
                f'<h4 class="font-serif text-[17px] font-bold text-navy mt-4 mb-1.5">{html.escape(line[4:])}</h4>'
            )
        elif line.startswith("- "):
            if not in_list:
                out.append('<ul class="list-disc pl-6 space-y-1 my-2 marker:text-gold">')
                in_list = True
            out.append(f'<li class="leading-snug">{html.escape(line[2:])}</li>')
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            paragraph_count += 1
            klass = "leading-relaxed text-[16.5px] my-3"
            if paragraph_count == 1:
                klass += " drop-cap"
            out.append(f'<p class="{klass}">{html.escape(line)}</p>')
    if in_list:
        out.append("</ul>")
    htmlout = "\n".join(out)
    # Apply bold/italic AFTER escaping: the raw asterisks survive html.escape()
    # so the regex still matches the escaped text. Inline tags inserted here
    # are intentional and trusted.
    htmlout = re.sub(r"\*\*(.+?)\*\*", r'<strong class="text-navy font-bold">\1</strong>', htmlout)
    htmlout = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"<em>\1</em>", htmlout)
    return htmlout


# -----------------------------------------------------------------------------
# Hero block: editorial title + signal chips + top-NEW column
# -----------------------------------------------------------------------------

def _signal_chips(cross_section: dict) -> str:
    """Render the cross-section recurrence chips for the hero.

    Reads cross_section.json shape: by_confidence: {high, medium, low}.
    Shows up to 8 entities sorted by (confidence rank, n_sections desc).
    """
    if not cross_section:
        return ""
    by_conf = cross_section.get("by_confidence") or {}
    ranked: list[tuple[int, dict]] = []
    for rank, key in enumerate(("high", "medium", "low")):
        for ent in by_conf.get(key, []) or []:
            ranked.append((rank, ent))
    ranked.sort(key=lambda t: (t[0], -t[1].get("n_sections", 0)))
    if not ranked:
        return ""
    chips = []
    for _, ent in ranked[:8]:
        name = html.escape(ent.get("canonical_name", "?"))
        nsec = int(ent.get("n_sections") or 0)
        conf = html.escape(ent.get("confidence") or "low", quote=True)
        chips.append(
            f'<span class="signal-chip animate-fade-rise" data-conf="{conf}">'
            f'<span class="conf-dot" aria-hidden="true"></span>'
            f'{name}<span class="count">· {nsec} sections</span>'
            f'</span>'
        )
    return (
        '<div class="flex flex-wrap gap-2">'
        + "\n".join(chips)
        + "</div>"
    )


def _top_new_items(states: dict, limit: int = 3) -> list[tuple[str, str, dict]]:
    """Return the top-N NEW items across all sections.

    Sorted by: source_tier (primary > mainstream > other), then by section
    name (deterministic). Returns list of (section_id, section_title, item).
    """
    tier_rank = {
        "primary_document": 0,
        "mainstream_independent": 1,
        "regional_independent": 2,
        "ngo_independent": 2,
        "academic": 3,
    }
    candidates: list[tuple[int, str, dict, Any]] = []
    for sid, st in states.items():
        for it in (st.new or []):
            tier = tier_rank.get(getattr(st, "source_tier", "") or "other", 5)
            candidates.append((tier, sid, it, st))
    candidates.sort(key=lambda t: (t[0], t[1]))
    out = []
    for _, sid, it, st in candidates[:limit]:
        out.append((sid, getattr(st, "title", sid), it))
    return out


def _hero_block(date_obj: date, cross_section: dict, states: dict,
                 threads: Optional[dict] = None) -> str:
    kicker = date_obj.strftime("%A · %B %-d, %Y").upper()
    chips_html = _signal_chips(cross_section)
    chips_count = (cross_section or {}).get("recurrences_found", 0)

    # Compute the headline stat for the hero: total NEW items today + the
    # leading converging entity. Falls back gracefully when data's thin.
    total_new = sum(len(getattr(s, "new", []) or []) for s in states.values()) if states else 0
    top_entity = None
    for band in ("high", "medium", "low"):
        for e in (cross_section or {}).get("by_confidence", {}).get(band, []) or []:
            top_entity = e
            break
        if top_entity:
            break

    # If we have story threads, the top active one becomes the lead
    # for the pull-quote (more narrative than the bare cross-section signal).
    top_thread = None
    if threads and threads.get("threads"):
        for t in threads["threads"]:
            if t.get("is_active_today"):
                top_thread = t
                break

    top_news = _top_new_items(states, limit=3)
    news_cards = []
    for sid, title, it in top_news:
        emoji = html.escape(getattr(states.get(sid), "emoji", "📌"))
        item_title = html.escape(it.get("title") or "(no title)")
        url = it.get("url") or "#"
        url_attr = html.escape(url, quote=True)
        summary = html.escape((it.get("summary") or "")[:140])
        section_label = html.escape(title)
        news_cards.append(f"""
        <a href="{url_attr}" target="_blank" rel="noopener noreferrer"
           class="lift-card block bg-panel border border-mist rounded-lg p-4 shadow-card border-l-4 border-l-gold animate-fade-rise">
          <div class="font-sans uppercase tracking-[0.10em] text-[10px] font-bold text-slate-dim mb-1.5 flex items-center gap-1.5">
            <span class="section-glyph text-[14px]" aria-hidden="true">{emoji}</span>
            <span>{section_label}</span>
            <span class="new-pill ml-auto" aria-label="new today">NEW</span>
          </div>
          <div class="font-serif font-semibold text-[15.5px] leading-snug text-ink group-hover:text-navy mb-1">{item_title}</div>
          <div class="text-slate text-[12.5px] font-sans leading-snug">{summary}</div>
        </a>""")

    news_col = (
        '<div class="space-y-3">' + "\n".join(news_cards) + "</div>"
    ) if news_cards else (
        '<div class="text-slate-dim font-sans text-[13px] italic">No new items today.</div>'
    )

    signals_intro = (
        f'Today {chips_count} '
        f'{"entity is" if chips_count == 1 else "entities are"} '
        f'recurring across 3+ sections.'
    ) if chips_count else 'No cross-section recurrence today.'

    # The "stat block" — pulled into the hero on the right side. Shows
    # total NEW today, the leading thread (if any), and the cross-section
    # signal as fallback. The thread treatment makes the brief
    # longitudinal: instead of "China appeared in 3 sections today" we
    # say "China — 382 items across 13 sections over 4 days." A link
    # takes the reader straight to the thread page.
    if top_thread:
        slug = html.escape(top_thread["slug"], quote=True)
        story_quote = (
            f"<a href='./threads/{slug}/' class='text-navy hover:text-gold no-underline border-b border-gold/40 hover:border-gold transition-colors'>"
            f"<strong>{html.escape(top_thread['title'])}</strong></a> — "
            f"<strong class='text-navy tabular-nums'>{top_thread['items_total']:,}</strong> items across "
            f"<strong class='text-navy tabular-nums'>{len(top_thread['sections_touched'])}</strong> sections "
            f"over <strong class='text-navy tabular-nums'>{top_thread['days_active']}</strong> days. "
            f"<span class='text-slate text-[0.85em]'>The dominant thread.</span>"
        )
    elif top_entity:
        story_quote = (
            f"<strong class='text-navy'>{html.escape(top_entity.get('canonical_name','?'))}</strong> "
            f"appeared in <strong class='text-navy'>{int(top_entity.get('n_sections') or 0)}</strong> "
            f"sections today — "
            f"<span class='text-slate'>"
            + ", ".join(html.escape(s) for s in (top_entity.get('sections') or [])[:4])
            + "</span>."
        )
    elif total_new:
        story_quote = (
            f"<strong class='text-navy'>{total_new}</strong> records new since yesterday. "
            f"No single thread is converging across sections — today's signal is dispersed."
        )
    else:
        story_quote = "A quiet day across the watch list."

    return f"""
<header class="relative pt-16 lg:pt-20 pb-8 px-7 max-w-[1200px] mx-auto">
  <div class="font-sans text-kicker text-gold uppercase mb-4">{html.escape(kicker)}</div>
  <div class="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12 items-end">
    <div class="lg:col-span-7">
      <h1 class="font-serif text-editorial text-ink mb-3">WORLDSCOPE</h1>
      <p class="font-serif text-lede text-slate max-w-2xl mb-6">
        Daily political, economic, and OSINT briefing &mdash; primary sources only,
        synthesized into one page.
      </p>
      <div class="flex flex-wrap items-center gap-3">
        <a href="./zips/{date_obj.isoformat()}.zip" download
           class="inline-flex items-center gap-2 font-sans text-[13px] font-semibold
                  bg-navy text-white px-4 py-2.5 rounded-md shadow-card
                  hover:bg-navy-soft hover:shadow-lift transition-all">
          <span aria-hidden="true">⬇</span> Today's package (.zip)
        </a>
        <a href="./sections/"
           class="inline-flex items-center gap-2 font-sans text-[13px] font-semibold
                  text-navy hover:text-gold transition-colors px-1">
          Drill into sections →
        </a>
      </div>
    </div>
    <div class="lg:col-span-5 stat-block">
      <div class="stat tabular-nums">{total_new:,}</div>
      <div class="stat-label">records new since yesterday</div>
      <div class="pull-quote mt-5">{story_quote}</div>
    </div>
  </div>
</header>

<section class="px-7 max-w-[1200px] mx-auto mb-10" aria-labelledby="signals-h">
  <hr class="editorial-rule mb-7">
  <div class="grid grid-cols-1 lg:grid-cols-12 gap-7 lg:gap-10">
    <div class="lg:col-span-7">
      <h2 id="signals-h" class="font-sans uppercase tracking-[0.18em] text-[11px] font-bold text-slate-dim mb-3">
        Signals converging today
      </h2>
      <p class="font-serif text-[16px] leading-snug text-ink mb-4 max-w-xl">{signals_intro}</p>
      {chips_html or '<div class="text-slate-dim font-sans text-[13px] italic">No recurring entities reached the 3-section threshold.</div>'}
    </div>
    <div class="lg:col-span-5">
      <h2 class="font-sans uppercase tracking-[0.18em] text-[11px] font-bold text-slate-dim mb-3">
        Top of the brief
      </h2>
      {news_col}
    </div>
  </div>
</section>
"""


# -----------------------------------------------------------------------------
# Threads band: multi-day arcs surfaced as a horizontal scroller of cards
# -----------------------------------------------------------------------------

def _threads_band(threads_doc: Optional[dict]) -> str:
    """Render the active-threads strip. Shows up to 6 threads ranked by
    heat, each a clickable card linking to /threads/<slug>/. Omitted
    entirely when no threads exist (no empty skeleton)."""
    threads = (threads_doc or {}).get("threads") or []
    threads = [t for t in threads if t.get("is_active_today")]
    threads = threads[:6]
    if not threads:
        return ""
    cards: list[str] = []
    for t in threads:
        slug   = html.escape(t["slug"], quote=True)
        title  = html.escape(t["title"])
        etype  = html.escape((t.get("entity_type") or "topic").split(":")[0])
        n_secs = len(t.get("sections_touched") or [])
        cards.append(f"""
<a href="./threads/{slug}/"
   class="lift-card shrink-0 w-[260px] bg-panel border border-mist rounded-lg p-4 shadow-card border-l-[3px] border-l-gold animate-fade-rise no-underline">
  <div class="font-sans uppercase tracking-[0.16em] text-[10px] font-bold text-slate-dim mb-1.5">{etype} &middot; {t["days_active"]}d</div>
  <div class="font-serif text-[16.5px] font-bold text-ink leading-tight mb-1.5 tracking-[-0.012em]">{title}</div>
  <div class="font-sans text-[11.5px] text-slate tabular-nums">
    <span class="text-navy font-bold">{t["items_total"]:,}</span> items &middot;
    <span class="text-navy font-bold">{n_secs}</span> sections
  </div>
</a>""")
    return f"""
<section class="px-7 max-w-[1200px] mx-auto mb-10" aria-labelledby="threads-h">
  <hr class="editorial-rule mb-6">
  <div class="flex items-baseline justify-between mb-4 flex-wrap gap-3">
    <h2 id="threads-h" class="font-sans uppercase tracking-[0.18em] text-[11px] font-bold text-slate-dim">
      Story threads &middot; running this week
    </h2>
    <a href="./threads/" class="font-sans text-[12px] font-semibold text-navy hover:text-gold transition-colors">
      All threads →
    </a>
  </div>
  <div class="flex gap-4 overflow-x-auto pb-3 -mx-1 px-1" style="scrollbar-width: thin">
    {"".join(cards)}
  </div>
</section>"""


# -----------------------------------------------------------------------------
# Figures band: interactive Vega-Lite charts driven by figures.json
# -----------------------------------------------------------------------------

def _figures_block(figures_doc: Optional[dict]) -> str:
    """Render the interactive figure cards. Each card is a placeholder the
    client-side worldscope-figures.js script hydrates with the Vega-Lite
    spec, kicker, title, and caption from data/figures.json.

    When figures_doc is None or empty the band is omitted (no empty card
    skeletons left behind).
    """
    figs = (figures_doc or {}).get("figures") or []
    if not figs:
        return ""
    cards: list[str] = []
    for f in figs:
        fid    = html.escape(str(f.get("id") or ""), quote=True)
        kicker = html.escape(str(f.get("kicker") or "FIGURE"))
        title  = html.escape(str(f.get("title") or ""))
        # Caption is server-trusted; rendered as HTML so <strong> survives.
        caption = str(f.get("caption") or "")
        # Wide cards (map, cross-section recurrence) span 2 cols on lg+;
        # everything else is 1 col. World map gets even more height.
        spans = {"world-map": "lg:col-span-2", "cross-section": "lg:col-span-2"}
        span_class = spans.get(f.get("id") or "", "")
        h = {"world-map": "min-h-[420px]"}.get(f.get("id") or "", "min-h-[320px]")
        cards.append(f"""
<article class="figure-card lift-card bg-panel border border-mist rounded-xl shadow-card animate-fade-rise {span_class}"
         data-figure="{fid}">
  <header class="px-6 pt-5 pb-3">
    <div class="font-sans uppercase tracking-[0.18em] text-[10.5px] font-bold text-gold mb-1.5" data-figure-kicker>{kicker}</div>
    <h3 class="font-serif text-[19px] font-bold text-ink leading-tight tracking-[-0.012em]" data-figure-title>{title}</h3>
  </header>
  <div data-vega-target class="{h} px-3 pb-2"></div>
  <footer class="px-6 pb-5 pt-2.5 text-slate text-[12.5px] font-sans leading-snug border-t border-mist tabular-nums" data-figure-caption>{caption}</footer>
</article>""")
    return f"""
<section class="px-7 max-w-[1200px] mx-auto mb-10" aria-labelledby="figures-h">
  <hr class="editorial-rule mb-7">
  <div class="flex items-baseline justify-between mb-4 flex-wrap gap-3">
    <h2 id="figures-h" class="font-sans uppercase tracking-[0.16em] text-[11px] font-bold text-slate-dim">
      Today in charts
    </h2>
    <div class="font-sans text-[10.5px] uppercase tracking-[0.10em] text-slate-dim" data-figures-meta></div>
  </div>
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
    {"".join(cards)}
  </div>
</section>
<script src="assets/worldscope-figures.js" defer></script>
"""


# -----------------------------------------------------------------------------
# Section cards: each section becomes one Tailwind card in an adaptive grid
# -----------------------------------------------------------------------------

def _staleness_pill(state) -> str:
    today = date.today()
    st = getattr(state, "state", "")
    src = getattr(state, "source_date", None)
    if st == "carry_forward" and src:
        try:
            days_ago = (today - date.fromisoformat(src)).days
        except Exception:
            days_ago = 0
        label = f"carried · {src}" + (f" ({days_ago}d ago)" if days_ago else "")
        return f'<span class="stale-pill carry">{html.escape(label)}</span>'
    if st == "stale_after_failure" and src:
        try:
            days_ago = (today - date.fromisoformat(src)).days
        except Exception:
            days_ago = 0
        return f'<span class="stale-pill failed">stale · last good {html.escape(src)} ({days_ago}d ago)</span>'
    if st == "no_data":
        return '<span class="stale-pill none">unavailable today</span>'
    return ""


TIER_LABEL = {
    "primary_document":      ("primary", "bg-navy text-white"),
    "mainstream_independent":("mainstream", "bg-mist text-navy border border-mist-strong"),
    "regional_independent":  ("regional", "bg-mist text-slate border border-mist-strong"),
    "ngo_independent":       ("ngo", "bg-mist text-teal border border-mist-strong"),
    "academic":              ("academic", "bg-mist text-carolina border border-mist-strong"),
    "social":                ("social", "bg-mist text-slate-dim border border-mist-strong"),
}


def _source_tier_pill(state) -> str:
    tier = getattr(state, "source_tier", "") or ""
    label, klass = TIER_LABEL.get(tier, ("", ""))
    if not label:
        return ""
    src_name = html.escape(getattr(state, "source_name", "") or "")
    title_attr = f' title="{src_name}"' if src_name else ""
    return (f'<span class="inline-block font-sans uppercase tracking-[0.10em] '
            f'text-[9.5px] font-bold {klass} px-1.5 py-0.5 rounded mr-1"'
            f'{title_attr}>{label}</span>')


def _volume_anomaly_pill(state, store_db_path: Optional[Path] = None) -> str:
    """Show a small +Nσ or -Nσ marker when today's volume is significantly
    off the trailing 7-day mean. Computed quietly from the snapshot store
    so it never blocks rendering on slow IO."""
    if not store_db_path or not Path(store_db_path).exists():
        return ""
    try:
        import sqlite3, json as _json, statistics as _stats
        today_n = len(getattr(state, "items", []) or [])
        if today_n == 0:
            return ""
        conn = sqlite3.connect(f"file:{store_db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT payload FROM snapshots WHERE section_id = ? "
                "  AND snapshot_date < ? "
                "  AND snapshot_date >= date(?, '-13 days') "
                "ORDER BY snapshot_date DESC LIMIT 7",
                (state.section_id, state.source_date or date.today().isoformat(),
                 state.source_date or date.today().isoformat()),
            )
            counts = []
            for (p,) in cur.fetchall():
                try:
                    counts.append(len(_json.loads(p).get("items") or []))
                except Exception:
                    pass
        finally:
            conn.close()
        if len(counts) < 3:
            return ""
        mean = _stats.mean(counts)
        sd   = _stats.pstdev(counts) or 1.0
        z    = (today_n - mean) / sd
        if abs(z) < 1.5:
            return ""
        sign = "+" if z > 0 else ""
        klass = "bg-teal text-white" if z > 0 else "bg-crimson text-white"
        return (f'<span class="inline-block font-sans uppercase tracking-[0.10em] '
                f'text-[9.5px] font-bold {klass} px-1.5 py-0.5 rounded ml-1.5" '
                f'title="today {today_n} vs trailing-7 mean {mean:.1f} (σ={sd:.1f})">'
                f'{sign}{z:.1f}σ</span>')
    except Exception:
        return ""


def _section_card(state, synth_text: Optional[str] = None,
                   store_db_path: Optional[Path] = None) -> str:
    title = html.escape(getattr(state, "title", "") or state.section_id)
    emoji = html.escape(getattr(state, "emoji", "📌") or "📌")
    items = list(getattr(state, "items", []) or [])
    new_ids = {it.get("_id") for it in (getattr(state, "new", []) or [])}
    total = len(items)
    n_new = len(new_ids)
    stale = _staleness_pill(state)
    tier  = _source_tier_pill(state)
    anomaly = _volume_anomaly_pill(state, store_db_path=store_db_path)

    # Bring NEW items to the top, then keep relative order
    new_first: list[dict] = []
    rest: list[dict] = []
    for it in items:
        (new_first if it.get("_id") in new_ids else rest).append(it)
    visible = (new_first + rest)[:6]
    remaining = (new_first + rest)[6:30]

    def _li(it: dict) -> str:
        is_new = it.get("_id") in new_ids
        url = it.get("url") or "#"
        title_html = html.escape(it.get("title") or "(no title)")
        date_s = html.escape(it.get("date") or "")
        summary = html.escape((it.get("summary") or "")[:200])
        pill = '<span class="new-pill" aria-label="new today">NEW</span>' if is_new else ""
        return (
            f'<li class="py-2.5 border-b border-mist last:border-b-0">'
            f'  <div class="flex items-baseline gap-2">'
            f'    {pill}<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
            f'        class="font-serif font-medium text-[14.5px] text-navy hover:text-gold leading-snug">{title_html}</a>'
            f'    <span class="ml-auto font-sans text-[11px] text-slate-dim tabular-nums shrink-0">{date_s}</span>'
            f'  </div>'
            + (f'  <div class="text-slate text-[13px] leading-snug mt-1 font-sans">{summary}</div>' if summary else "")
            + '</li>'
        )

    visible_html = "\n".join(_li(it) for it in visible)
    remaining_html = "\n".join(_li(it) for it in remaining)

    synth_html = ""
    if synth_text:
        synth_html = (
            f'<p class="font-serif text-[14.5px] leading-relaxed text-ink mb-3 '
            f'bg-mist/40 border-l-2 border-l-gold pl-3 pr-2 py-2.5 rounded-r">'
            f'{html.escape(synth_text)}'
            f'</p>'
        )

    details_html = ""
    if remaining:
        details_html = f"""
<details class="mt-2 group">
  <summary class="cursor-pointer font-sans text-[12px] font-semibold uppercase tracking-[0.10em] text-slate hover:text-navy transition-colors py-1.5 select-none list-none">
    <span class="inline-block group-open:rotate-90 transition-transform mr-1">›</span>show {len(remaining)} more
  </summary>
  <ul class="list-none p-0 m-0 mt-1">{remaining_html}</ul>
</details>"""

    # Distinguish "pull succeeded with zero items" from "pull failed":
    #   fresh_empty   → clean pull, no signal today (italicized note)
    #   stale_after_failure / carry_forward / no_data → handled by
    #                   _staleness_pill in the header
    empty_html = ""
    if total == 0:
        st = getattr(state, "state", "")
        if st == "fresh_empty":
            empty_html = ('<div class="font-sans text-[13px] text-slate-dim italic py-2 '
                          'flex items-center gap-1.5">'
                          '<span class="inline-block w-1.5 h-1.5 rounded-full bg-teal" '
                          'aria-hidden="true" title="upstream API answered cleanly with zero items"></span>'
                          'clean pull · no signal in watch areas today</div>')
        elif st == "stale_after_failure":
            err = html.escape(getattr(state, "error", "") or "pull failed", quote=True)
            empty_html = ('<div class="font-sans text-[13px] text-crimson italic py-2 '
                          f'flex items-center gap-1.5" title="{err}">'
                          '<span class="inline-block w-1.5 h-1.5 rounded-full bg-crimson"></span>'
                          'pull failed · showing nothing</div>')
        else:
            empty_html = ('<div class="font-sans text-[13px] text-slate-dim italic py-2">'
                          'no items in this section today.</div>')

    return f"""
<article class="lift-card bg-panel border border-mist rounded-xl p-5 shadow-card border-l-[3px] border-l-navy break-inside-avoid mb-5 animate-fade-rise"
         aria-labelledby="sec-{html.escape(state.section_id, quote=True)}">
  <header class="flex items-start gap-3 mb-3">
    <span class="section-glyph shrink-0" aria-hidden="true">{emoji}</span>
    <div class="flex-1 min-w-0">
      <h2 id="sec-{html.escape(state.section_id, quote=True)}"
          class="font-serif text-[19px] font-bold text-navy leading-tight mb-0.5">
        {title}{stale}
      </h2>
      <div class="font-sans text-[11.5px] uppercase tracking-[0.10em] text-slate-dim flex items-center flex-wrap gap-y-1">
        {tier}<span class="text-navy font-bold">{n_new} new</span> &middot; {total} total{anomaly}
      </div>
    </div>
  </header>
  {synth_html}
  {empty_html}
  <ul class="list-none p-0 m-0">{visible_html}</ul>
  {details_html}
</article>"""


# -----------------------------------------------------------------------------
# Overview synth block + archive nav
# -----------------------------------------------------------------------------

def _overview_block(overview_md: Optional[str]) -> str:
    if not overview_md:
        return ""
    return f"""
<section class="px-7 max-w-[1200px] mx-auto mb-10" aria-label="Synthesis overview">
  <hr class="editorial-rule mb-7">
  <div class="font-sans uppercase tracking-[0.16em] text-[11px] font-bold text-slate-dim mb-4">
    Synthesis &middot; cross-section overview
  </div>
  <div class="font-serif text-[16.5px] leading-[1.65] text-ink prose-editorial max-w-3xl">
    {_md_to_html(overview_md)}
  </div>
</section>"""


def _archive_nav(archive_dates: list[date] | None) -> str:
    if not archive_dates:
        return ""
    links = " ".join(
        f'<a href="./{d.isoformat()}.html" class="text-carolina hover:text-navy underline-offset-2 hover:underline transition-colors">{d.isoformat()}</a>'
        for d in archive_dates[-21:]
    )
    return f"""
<nav class="px-7 max-w-[1200px] mx-auto mt-12 mb-2" aria-label="Recent briefings">
  <hr class="editorial-rule mb-4">
  <div class="font-sans uppercase tracking-[0.16em] text-[11px] font-bold text-slate-dim mb-2">
    Recent briefings
  </div>
  <div class="flex flex-wrap gap-x-4 gap-y-1 font-sans text-[13px]">{links}</div>
</nav>"""


# -----------------------------------------------------------------------------
# Public entrypoint
# -----------------------------------------------------------------------------

def render_page(
    date_obj: date,
    sections_html: list[str],          # legacy: ignored when `states` is provided
    out_dir: Path,
    *,
    overview_md: Optional[str] = None,
    archive_dates: list[date] | None = None,
    states: Optional[dict] = None,
    synth_by_section: Optional[dict[str, str]] = None,
    cross_section: Optional[dict] = None,
    figures: Optional[dict] = None,
    threads: Optional[dict] = None,
    store_db_path: Optional[Path] = None,
    network_seed_json: str = "{}",
) -> Path:
    """Render today's brief. When `states` is provided, builds the modern
    hero + adaptive card grid. The legacy `sections_html` argument is kept
    for backwards compatibility -- if `states` is None, the function falls
    back to rendering whatever sections_html the caller passed in."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    hero = _hero_block(date_obj, cross_section or {}, states or {}, threads=threads)
    figures_band = _figures_block(figures)
    threads_band = _threads_band(threads)
    overview = _overview_block(overview_md)

    # Section cards. If we have states (the modern path), render every
    # section as a card. Otherwise fall back to the pre-rendered HTML the
    # caller produced.
    if states:
        synths = synth_by_section or {}
        cards = [_section_card(st, synth_text=synths.get(sid),
                                store_db_path=store_db_path)
                 for sid, st in states.items()]
        sections_block = (
            '<section class="px-7 max-w-[1200px] mx-auto pb-12" aria-label="Sections">'
            '<hr class="editorial-rule mb-7">'
            '<div class="font-sans uppercase tracking-[0.16em] text-[11px] font-bold text-slate-dim mb-5">All sections</div>'
            '<div class="columns-1 md:columns-2 xl:columns-3 gap-6 [column-fill:_balance]">'
            + "\n".join(cards)
            + "</div></section>"
        )
    else:
        sections_block = (
            '<section class="px-7 max-w-[1200px] mx-auto pb-12 space-y-5">'
            + "\n".join(sections_html)
            + "</section>"
        )

    archive_html = _archive_nav(archive_dates)
    main_body = f"<main>{hero}{threads_band}{figures_band}{overview}{sections_block}{archive_html}</main>{footer_block()}"

    page = page_shell(
        title=f"WORLDSCOPE · {date_obj.isoformat()}",
        body_html=main_body,
        description=f"Daily political, economic, and OSINT briefing for {date_obj.isoformat()}.",
        canonical=f"https://ihelfrich.github.io/worldscope/{date_obj.isoformat()}.html",
        base="",
        network_seed_json=network_seed_json,
        network_assets_path="assets/network.js",
        include_chat=True,
    )

    out_path = out_dir / f"{date_obj.isoformat()}.html"
    out_path.write_text(page, encoding="utf-8")
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    return out_path
