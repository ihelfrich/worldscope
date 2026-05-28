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
    }
    ::selection { background: #D4A01755; }
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


def page_shell(
    *,
    title: str,
    body_html: str,
    description: str = "",
    canonical: str = "https://ihelfrich.github.io/worldscope/",
    base: str = "",
    network_seed_json: str = "{}",
    network_assets_path: str = "assets/network.js",
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
</body>
</html>
"""
