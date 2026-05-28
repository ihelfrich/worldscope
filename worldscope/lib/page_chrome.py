"""Shared page chrome: Tailwind-driven theme + heritage palette + custom utilities.

Used by both render.py (daily brief homepage) and site_builder.py (per-section
pages) so design changes ship everywhere at once. Tailwind via Play CDN with
an inline config so the heritage palette + custom typography + custom
utilities are first-class theme tokens (not hand-rolled CSS).

Heritage palette tokens (used throughout):
  ink         #0B1220   - body copy, headline
  parchment   #FAF8F3   - canvas/paper background
  panel       #FFFFFF   - card surfaces
  mist        #E8E2D5   - hairline rules
  slate       #4E5667   - secondary text
  navy        #13294B   - primary brand
  navy-soft   #1F3D6E   - gradient stop
  gold        #D4A017   - accent + NEW badge
  carolina    #4B9CD3   - link accent
  crimson     #990000   - warning, stale-failed
  teal        #1A8A87   - secondary accent

All custom utilities defined in @layer utilities so they cohabit cleanly
with Tailwind's stock classes.
"""
from __future__ import annotations

import html
from datetime import date as _date


# Tailwind CDN config: extends theme with heritage palette + serif/sans
# fonts + editorial typography scale. The Play CDN compiles JIT in the
# browser so any class we write (including arbitrary values) resolves
# without a build step.
TAILWIND_HEAD = """<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700;8..60,800&display=swap">
<script src="https://cdn.tailwindcss.com?plugins=typography,forms,aspect-ratio"></script>
<script>
tailwind.config = {
  theme: {
    extend: {
      colors: {
        ink: '#0B1220',
        parchment: '#FAF8F3',
        panel: '#FFFFFF',
        mist: '#E8E2D5',
        'mist-strong': '#C9C1B2',
        slate: { DEFAULT: '#4E5667', dim: '#6B7180' },
        navy: { DEFAULT: '#13294B', soft: '#1F3D6E', deep: '#091428' },
        gold: { DEFAULT: '#D4A017', soft: '#E8BC42' },
        carolina: '#4B9CD3',
        crimson: '#990000',
        teal: '#1A8A87'
      },
      fontFamily: {
        serif: ['"Source Serif 4"', 'Source Serif Pro', 'Georgia', 'Iowan Old Style', 'serif'],
        sans:  ['Inter', '-apple-system', 'BlinkMacSystemFont', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        mono:  ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace']
      },
      fontSize: {
        'kicker':    ['11px',  { letterSpacing: '0.18em', lineHeight: '1.2', fontWeight: '700' }],
        'editorial': ['clamp(36px, 5.5vw, 64px)', { lineHeight: '1.04', letterSpacing: '-0.025em', fontWeight: '800' }],
        'lede':      ['clamp(17px, 1.4vw, 19px)', { lineHeight: '1.55', fontWeight: '400' }]
      },
      boxShadow: {
        'card':  '0 1px 2px rgba(11,18,32,0.04), 0 4px 12px rgba(11,18,32,0.05)',
        'lift':  '0 2px 6px rgba(11,18,32,0.06), 0 14px 32px rgba(11,18,32,0.11)',
        'press': 'inset 0 1px 2px rgba(11,18,32,0.08)'
      },
      transitionTimingFunction: {
        'editorial': 'cubic-bezier(0.2, 0.7, 0.2, 1)'
      },
      keyframes: {
        'fade-rise':  { '0%': { opacity: '0', transform: 'translateY(8px)' },
                        '100%': { opacity: '1', transform: 'translateY(0)' } },
        'pulse-soft': { '0%,100%': { opacity: '0.65' }, '50%': { opacity: '1' } }
      },
      animation: {
        'fade-rise': 'fade-rise 0.5s cubic-bezier(0.2,0.7,0.2,1) both',
        'pulse-soft': 'pulse-soft 3s ease-in-out infinite'
      }
    }
  }
};
</script>
<style type="text/tailwindcss">
  @layer base {
    html { scroll-behavior: smooth; }
    body {
      font-family: 'Source Serif 4','Georgia',serif;
      color: #0B1220;
      background: #FAF8F3;
      font-size: 16.5px;
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
      -webkit-text-size-adjust: 100%;
      font-feature-settings: 'kern' 1, 'liga' 1, 'calt' 1, 'onum' 1;
      text-rendering: optimizeLegibility;
    }
    /* Tabular figures for numbers everywhere — keeps counts column-aligned */
    .tabular-nums, [data-figure-target] text, .count, time, .stat {
      font-feature-settings: 'tnum' 1, 'kern' 1;
      font-variant-numeric: tabular-nums;
    }
    ::selection { background: #D4A01755; }
    /* Headings: refined editorial rhythm */
    h1, h2, h3 { font-feature-settings: 'kern' 1, 'liga' 1, 'calt' 1, 'lnum' 1; }
  }
  @layer components {
    /* Editorial drop-cap on first letter of the lede paragraph */
    .drop-cap::first-letter {
      font-family: 'Source Serif 4', serif;
      float: left;
      font-size: 4.2em;
      line-height: 0.85;
      padding: 0.05em 0.12em 0 0;
      color: #13294B;
      font-weight: 800;
    }
    /* Gold-bar accent rule between sections */
    .editorial-rule {
      height: 2px;
      background: linear-gradient(90deg, #D4A017 0%, #D4A01755 30%, transparent 100%);
      border: 0;
    }
    /* Recurrence chip: shown in the hero "signals converging" block */
    .signal-chip {
      display: inline-flex;
      align-items: baseline;
      gap: 0.45rem;
      padding: 0.55rem 0.85rem 0.55rem 0.9rem;
      border-radius: 9999px;
      background: rgba(255,255,255,0.86);
      border: 1px solid #E8E2D5;
      backdrop-filter: blur(6px);
      font-family: 'Inter', sans-serif;
      font-size: 13px;
      font-weight: 600;
      color: #13294B;
      transition: transform 0.18s cubic-bezier(0.2,0.7,0.2,1), box-shadow 0.18s, border-color 0.18s;
      cursor: default;
    }
    .signal-chip:hover {
      transform: translateY(-1px);
      border-color: #D4A017;
      box-shadow: 0 2px 6px rgba(11,18,32,0.06), 0 8px 20px rgba(11,18,32,0.08);
    }
    .signal-chip .count {
      font-family: 'Inter', sans-serif;
      font-weight: 700;
      font-size: 11px;
      color: #4E5667;
      letter-spacing: 0.05em;
    }
    .signal-chip .conf-dot {
      width: 6px; height: 6px; border-radius: 9999px;
      background: #D4A017;
      box-shadow: 0 0 0 2px rgba(212,160,23,0.18);
    }
    .signal-chip[data-conf="high"]   .conf-dot { background: #13294B; box-shadow: 0 0 0 2px rgba(19,41,75,0.18); }
    .signal-chip[data-conf="medium"] .conf-dot { background: #D4A017; }
    .signal-chip[data-conf="low"]    .conf-dot { background: #C9C1B2; box-shadow: none; }
    /* NEW marker -- intentionally loud */
    .new-pill {
      display: inline-block;
      background: #D4A017;
      color: #0B1220;
      font-family: 'Inter', sans-serif;
      font-weight: 800;
      font-size: 9.5px;
      padding: 2px 6px 1.5px;
      letter-spacing: 0.12em;
      border-radius: 3px;
      vertical-align: 0.18em;
      margin-right: 6px;
    }
    /* Stale-state badges */
    .stale-pill {
      display: inline-block;
      font-family: 'Inter', sans-serif;
      font-size: 10.5px; font-weight: 600;
      padding: 2px 7px; border-radius: 3px;
      letter-spacing: 0.06em;
      margin-left: 8px;
      vertical-align: 0.18em;
    }
    .stale-pill.carry  { background: #FFF2CC; color: #856404; border: 1px solid #E6C75A; }
    .stale-pill.failed { background: #FCE4D6; color: #8B3A0E; border: 1px solid #D27F5A; }
    .stale-pill.none   { background: #E5E7EB; color: #4B5563; border: 1px solid #C9C1B2; }
    /* Section emoji as oversized background flourish in card header */
    .section-glyph {
      font-size: 38px;
      line-height: 1;
      filter: saturate(0.85);
    }
    /* Editorial pull-quote: large serif italic, gold-rule accent.
       Used in the hero "story of the day" treatment. */
    .pull-quote {
      font-family: 'Source Serif 4', Georgia, serif;
      font-style: italic;
      font-weight: 500;
      font-size: clamp(20px, 2.2vw, 26px);
      line-height: 1.32;
      color: #0B1220;
      padding-left: 1.05rem;
      border-left: 3px solid #D4A017;
      letter-spacing: -0.005em;
    }
    /* Figure-card hover: gold accent bar slides in from left.
       Subtle but unmistakably-editorial cue. */
    .figure-card {
      position: relative;
      overflow: hidden;
    }
    .figure-card::before {
      content: '';
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 3px;
      background: #D4A017;
      transform: scaleY(0);
      transform-origin: top;
      transition: transform 0.28s cubic-bezier(0.2,0.7,0.2,1);
    }
    .figure-card:hover::before { transform: scaleY(1); }
    /* "Stat block" — oversized number with caption beneath. */
    .stat-block .stat {
      font-family: 'Source Serif 4', Georgia, serif;
      font-weight: 800;
      font-size: clamp(40px, 4.4vw, 56px);
      line-height: 0.96;
      letter-spacing: -0.025em;
      color: #13294B;
    }
    .stat-block .stat-label {
      font-family: Inter, sans-serif;
      font-size: 11px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      font-weight: 700;
      color: #4E5667;
      margin-top: 6px;
    }
    /* Vega chart frame — restrained look that matches the card chrome */
    [data-vega-target] .vega-embed {
      width: 100%;
      display: block;
    }
    [data-vega-target] svg { display: block; max-width: 100%; }
  }
  @layer utilities {
    /* Glass strip used by sticky topnav over the canvas background */
    .glass-nav {
      background: linear-gradient(180deg, rgba(19,41,75,0.96) 0%, rgba(31,61,110,0.94) 100%);
      backdrop-filter: blur(8px);
    }
    /* Card hover lift -- used across hero and section cards */
    .lift-card {
      transition: transform 0.22s cubic-bezier(0.2,0.7,0.2,1),
                  box-shadow 0.22s cubic-bezier(0.2,0.7,0.2,1),
                  border-color 0.22s;
    }
    .lift-card:hover {
      transform: translateY(-2px);
      box-shadow: 0 2px 6px rgba(11,18,32,0.06), 0 14px 32px rgba(11,18,32,0.11);
    }
    /* Respect reduced motion */
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: 0.001ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
        scroll-behavior: auto !important;
      }
    }
    /* Print stylesheet -- strip navigation, expand details, ink-on-paper */
    @media print {
      .glass-nav, footer .actions, .ws-bg, details > summary::-webkit-details-marker { display: none !important; }
      details { open: ''; }
      details > div { display: block !important; }
      body { background: #fff; }
    }
  }
</style>
"""


def topnav(base: str = "") -> str:
    """Sticky navy-on-parchment top navigation with gold accent rule."""
    return f"""<nav class="glass-nav sticky top-0 z-50 text-white border-b-2 border-gold shadow-md" role="navigation" aria-label="Primary">
  <div class="max-w-[1200px] mx-auto px-7 py-3 flex flex-wrap items-center gap-5 font-sans text-[13px]">
    <a href="{base}index.html" class="font-extrabold tracking-[0.10em] uppercase text-white text-[13.5px]">
      <span class="text-gold mr-1">◆</span>WORLDSCOPE
    </a>
    <a href="{base}index.html" class="text-mist hover:text-white transition-colors">Today</a>
    <a href="{base}sections/" class="text-mist hover:text-white transition-colors">Sections</a>
    <a href="{base}briefings/" class="text-mist hover:text-white transition-colors">Archive</a>
    <span class="flex-1"></span>
    <span class="text-mist/85 text-[12px] pl-3.5 ml-1 border-l border-white/20">Dr. Ian Helfrich</span>
  </div>
</nav>"""


def footer_block() -> str:
    return """<footer class="mt-20 pt-8 border-t border-mist text-slate font-sans text-[12.5px] text-center" role="contentinfo">
  <div>WORLDSCOPE · sources cited inline · synthesis grounded in numbered items only</div>
  <div class="mt-1 text-slate-dim">all data primary-sourced · &copy; Dr. Ian Helfrich</div>
</footer>"""


# Slide-in chat panel: floating toggle button bottom-right, panel slides
# in from the right edge. Pure HTML + a few utility classes; logic lives
# in dist/assets/worldscope-chat.js. Loaded by page_shell() on the
# homepage; section pages don't include it (they don't have the lake
# export adjacent).
def chat_panel(base: str = "") -> str:
    return f"""
<style>
  #ws-chat-panel {{
    position: fixed; top: 0; right: 0; height: 100vh;
    width: min(440px, 96vw); max-width: 460px;
    background: #FAF8F3;
    box-shadow: -6px 0 24px rgba(11,18,32,0.18);
    transform: translateX(102%);
    transition: transform 0.32s cubic-bezier(0.2,0.7,0.2,1);
    z-index: 80;
    display: flex; flex-direction: column;
  }}
  #ws-chat-panel.ws-chat-open {{ transform: translateX(0); }}
  #ws-chat-toggle {{
    position: fixed; bottom: 22px; right: 22px;
    background: #13294B; color: #FAF8F3;
    border: 0; padding: 12px 16px;
    border-radius: 9999px;
    font-family: Inter, sans-serif; font-weight: 700; font-size: 13px;
    letter-spacing: 0.06em; text-transform: uppercase;
    cursor: pointer; z-index: 70;
    box-shadow: 0 2px 6px rgba(11,18,32,0.20), 0 12px 28px rgba(11,18,32,0.18);
    transition: transform 0.18s, box-shadow 0.18s, background 0.18s;
  }}
  #ws-chat-toggle:hover {{ background: #1F3D6E; transform: translateY(-1px); }}
  #ws-chat-toggle::before {{ content: '◆'; color: #D4A017; margin-right: 6px; }}
  @media print {{ #ws-chat-toggle, #ws-chat-panel, #ws-chat-settings {{ display: none !important; }} }}
</style>

<button id="ws-chat-toggle" aria-label="Ask the brief">Ask the brief</button>

<aside id="ws-chat-panel" role="dialog" aria-label="Chat with today's brief">
  <header class="bg-navy text-white px-4 py-3 flex items-center gap-3 border-b-2 border-gold shrink-0">
    <span class="font-sans font-extrabold tracking-[0.10em] text-[13px] uppercase">
      <span class="text-gold mr-1">◆</span>Ask the brief
    </span>
    <span class="flex-1"></span>
    <button id="ws-chat-settings-btn" class="text-mist hover:text-white text-[12px] font-sans" title="Settings">⚙</button>
    <button id="ws-chat-clear" class="text-mist hover:text-white text-[12px] font-sans" title="Clear conversation">⟲</button>
    <button id="ws-chat-close" class="text-mist hover:text-white text-[18px] leading-none" title="Close">×</button>
  </header>

  <div id="ws-chat-list" class="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-2.5">
    <div class="self-center font-sans text-[12.5px] text-slate-dim text-center max-w-sm py-4">
      <div class="font-serif font-semibold text-navy text-[14.5px] mb-1">Grounded in today's briefing</div>
      <div>Ask anything about today's records. Answers cite sections inline. Click <span class="text-gold">⚙</span> to set your Anthropic API key (stored locally only).</div>
    </div>
  </div>

  <div id="ws-chat-busy" class="px-4 pb-1 text-slate-dim font-sans text-[11.5px]" style="display:none">
    <span class="inline-block w-2 h-2 rounded-full bg-gold animate-pulse-soft mr-1.5"></span>thinking…
  </div>

  <form id="ws-chat-form" class="border-t border-mist p-3 flex gap-2 shrink-0 bg-panel">
    <input id="ws-chat-input"
           type="text"
           autocomplete="off"
           placeholder="Ask about today's brief…"
           class="flex-1 font-sans text-[14px] bg-parchment border border-mist rounded px-3 py-2
                  focus:border-navy focus:outline-none focus:ring-2 focus:ring-navy/15">
    <button id="ws-chat-send" type="submit"
            class="font-sans text-[13px] font-semibold bg-navy text-white px-4 rounded hover:bg-navy-soft transition-colors disabled:opacity-50">Ask</button>
  </form>
</aside>

<div id="ws-chat-settings" style="display:none"
     class="fixed inset-0 z-[90] items-center justify-center bg-ink/55 backdrop-blur-sm p-4">
  <div class="bg-parchment border border-mist-strong rounded-xl shadow-lift max-w-md w-full p-5 font-sans">
    <h3 class="font-serif text-navy text-[18px] font-bold mb-1">Connect your Anthropic key</h3>
    <p class="text-slate text-[12.5px] mb-3">Stored only in this browser's localStorage. Used directly with api.anthropic.com via the browser-access header. Nothing is sent through any WORLDSCOPE backend.</p>
    <label class="block text-[11px] font-bold uppercase tracking-[0.10em] text-slate-dim mb-1">API key</label>
    <input id="ws-key-input" type="password" placeholder="sk-ant-..."
           class="w-full font-mono text-[13px] bg-panel border border-mist rounded px-3 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-navy/15">
    <label class="block text-[11px] font-bold uppercase tracking-[0.10em] text-slate-dim mb-1">Model</label>
    <select id="ws-model-select" class="w-full text-[13px] bg-panel border border-mist rounded px-3 py-2 mb-4 focus:outline-none focus:ring-2 focus:ring-navy/15"></select>
    <div class="flex gap-2 justify-end">
      <button id="ws-key-cancel" class="text-slate text-[13px] px-3 py-1.5 hover:text-navy">Cancel</button>
      <button id="ws-key-save" class="bg-navy text-white text-[13px] font-semibold px-3.5 py-1.5 rounded hover:bg-navy-soft">Save</button>
    </div>
  </div>
</div>
<script src="{base}assets/worldscope-chat.js" defer></script>
"""


def page_shell(
    *,
    title: str,
    body_html: str,
    description: str = "",
    canonical: str = "https://ihelfrich.github.io/worldscope/",
    base: str = "",
    network_seed_json: str = "{}",
    network_assets_path: str = "assets/network.js",
    include_chat: bool = False,
) -> str:
    """Wrap body content in the full Tailwind-themed page chrome.

    Includes the ambient ws-network canvas + heritage chrome. `base` is the
    relative prefix for assets (use "" from the dist root, "../" one deep).
    """
    desc = html.escape(description or f"WORLDSCOPE: {title}", quote=True)
    can  = html.escape(canonical, quote=True)
    ttl  = html.escape(title)
    return f"""<!doctype html>
<html lang="en" class="antialiased">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{ttl}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{can}">
<meta property="og:type" content="article">
<meta property="og:title" content="{ttl}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{can}">
<meta property="og:site_name" content="WORLDSCOPE">
<meta name="theme-color" content="#13294B">
{TAILWIND_HEAD}
<style>
  .ws-bg {{ position: fixed; inset: 0; z-index: -1; pointer-events: auto; opacity: 0.55; }}
  .ws-bg canvas {{ display: block; width: 100%; height: 100%; }}
</style>
</head>
<body class="font-serif text-ink bg-parchment">
<div class="ws-bg" aria-hidden="true"><canvas id="ws-network"></canvas></div>
<script type="application/json" id="ws-network-seed">{network_seed_json}</script>
<script src="{base}{network_assets_path}" defer></script>
{topnav(base=base)}
{body_html}
{chat_panel(base=base) if include_chat else ""}
</body>
</html>
"""
