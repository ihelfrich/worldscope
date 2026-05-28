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
import json
from datetime import date as _date


# Built CSS pipeline:
#   npm run build:css  →  dist/assets/worldscope.css
#
# That bundle contains Tailwind preflight + design tokens + self-hosted
# fonts + component styles. ~30KB minified after tree-shake.  No more
# CDN, no more FOUC, no more "should not be used in production" warning.
#
# The design token + Tailwind config lives in worldscope/design/ — edit
# tokens there, run `npm run build:css`, ship the new worldscope.css.

TAILWIND_HEAD = """<link rel="stylesheet" href="{base}assets/worldscope.css">
<link rel="preload" href="{base}assets/fonts/inter-variable.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{base}assets/fonts/ss4-latin.woff2" as="font" type="font/woff2" crossorigin>
<meta name="color-scheme" content="light dark">
"""


def _json_script_safe(s: str) -> str:
    """Escape JSON for safe embedding inside <script>...</script>.

    json.dumps does NOT escape:
      - "</" — an entity name containing "</script>" would break out
        of the script block
      - "<!--" — could open an HTML comment
      - U+2028 / U+2029 — JS line terminators inside string literals
    """
    return (s
            .replace("</",   "<\\/")
            .replace("<!--", "<\\!--")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029"))


def topnav(base: str = "") -> str:
    """Frosty-glass sticky top nav. Light-on-light over the canvas
    (rather than navy bar) so it composes with the rest of the design
    system. ⌘K command palette trigger on the right."""
    return f"""<nav class="glass-nav sticky top-0 z-50 text-ink" role="navigation" aria-label="Primary">
  <div class="max-w-[1400px] mx-auto px-6 py-2.5 flex flex-wrap items-center gap-x-5 gap-y-1 font-sans text-[13px]">
    <a href="{base}index.html" class="font-extrabold tracking-[0.10em] uppercase text-ink text-[13.5px] mr-2">
      <span class="text-gold mr-1">◆</span>WORLDSCOPE
    </a>
    <a href="{base}index.html" class="text-slate hover:text-ink transition-colors">Today</a>
    <a href="{base}globe/" class="text-slate hover:text-ink transition-colors">Globe</a>
    <a href="{base}threads/" class="text-slate hover:text-ink transition-colors">Threads</a>
    <a href="{base}graph/" class="text-slate hover:text-ink transition-colors">Graph</a>
    <a href="{base}reproducibility/" class="text-slate hover:text-ink transition-colors">Reproducibility</a>
    <a href="{base}health/" class="text-slate hover:text-ink transition-colors">Health</a>
    <a href="{base}sections/" class="text-slate hover:text-ink transition-colors">Sections</a>
    <a href="{base}briefings/" class="text-slate hover:text-ink transition-colors">Archive</a>
    <span class="flex-1"></span>
    <button type="button" data-palette-trigger class="ws-palette-trigger" aria-label="Open command palette">
      <span class="text-slate-dim">Jump to anywhere</span><kbd>⌘K</kbd>
    </button>
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
{TAILWIND_HEAD.format(base=base)}
<style>
  .ws-bg {{ position: fixed; inset: 0; z-index: -1; pointer-events: auto; opacity: 0.55; }}
  .ws-bg canvas {{ display: block; width: 100%; height: 100%; }}
</style>
</head>
<body class="font-serif text-ink bg-parchment">
<div class="ws-bg" aria-hidden="true"><canvas id="ws-network"></canvas></div>
<script>window.WS_BASE = {json.dumps(base or "./")};</script>
<script type="application/json" id="ws-network-seed">{_json_script_safe(network_seed_json)}</script>
<script src="{base}{network_assets_path}" defer></script>
<script src="{base}assets/worldscope-evidence.js" defer></script>
<script src="{base}assets/worldscope-ui.js" defer></script>
{topnav(base=base)}
{body_html}
{chat_panel(base=base) if include_chat else ""}
</body>
</html>
"""
