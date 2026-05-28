/**
 * worldscope-figures — interactive chart + map rendering for the homepage.
 *
 * Loads data/figures.json (produced daily by worldscope.figures_engine)
 * and renders each figure into its placeholder card. Charts are
 * Vega-Lite (drawn via the vega/vega-lite/vega-embed UMD bundles on the
 * official jsdelivr CDN). Maps use Vega-Lite's geoshape layer (no
 * separate map lib needed; topojson loads on demand).
 *
 * Each figure renders into <div data-figure="<id>"> placeholders that
 * render.py emits inside the hero band. If a placeholder is missing for
 * an id, that figure is silently skipped (so server-side adds new
 * figures don't break older deployed HTML).
 *
 * Loaders are lazy: we don't pull Vega until at least one figure exists
 * for today.
 */
(() => {
  "use strict";

  const VEGA_BUNDLE = [
    "https://cdn.jsdelivr.net/npm/vega@5",
    "https://cdn.jsdelivr.net/npm/vega-lite@5",
    "https://cdn.jsdelivr.net/npm/vega-embed@6",
  ];

  let loadedVega = null;

  async function loadVega() {
    if (loadedVega) return loadedVega;
    loadedVega = (async () => {
      for (const url of VEGA_BUNDLE) {
        await new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = url;
          s.async = false;
          s.onload = resolve;
          s.onerror = () => reject(new Error("failed to load " + url));
          document.head.appendChild(s);
        });
      }
      return window.vegaEmbed;
    })();
    return loadedVega;
  }

  async function renderFigure(card, fig) {
    const target = card.querySelector("[data-vega-target]");
    if (!target) return;
    if (fig.spec_type !== "vega-lite") {
      target.innerHTML = '<div class="text-slate-dim font-sans text-[12px] italic p-3">unsupported figure type</div>';
      return;
    }
    try {
      const vegaEmbed = await loadVega();
      await vegaEmbed(target, fig.spec, {
        actions:    false,
        renderer:   "svg",
        tooltip:    { theme: "light" },
      });
    } catch (e) {
      target.innerHTML = `<div class="text-crimson font-sans text-[12px] p-3">chart failed to render: ${escapeHtml(String(e))}</div>`;
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  // Strict caption sanitizer: escapes everything, then unescapes the
  // single allowed inline tag <strong>...</strong>. No attributes ever
  // pass through. Anything outside that allowlist remains visibly escaped.
  function sanitizeCaption(s) {
    let out = escapeHtml(String(s));
    // Re-allow only the bare <strong> tag (no attributes).
    out = out.replace(/&lt;strong&gt;/g, "<strong>")
             .replace(/&lt;\/strong&gt;/g, "</strong>");
    return out;
  }

  async function init() {
    let doc;
    try {
      const r = await fetch("./data/figures.json", { cache: "no-cache" });
      if (!r.ok) return;
      doc = await r.json();
    } catch (e) {
      return;
    }
    if (!doc || !Array.isArray(doc.figures)) return;

    // Render into each placeholder, IDs matching figure.id
    for (const fig of doc.figures) {
      const card = document.querySelector(`[data-figure="${CSS.escape(fig.id)}"]`);
      if (!card) continue;
      // Captions may include <strong> for emphasis but nothing else.
      // Server-side already escapes data, but we re-tighten here as
      // defense-in-depth: any other tag (incl. <script>, <img onerror=>,
      // attributes) is stripped.
      const cap = card.querySelector("[data-figure-caption]");
      if (cap && fig.caption) cap.innerHTML = sanitizeCaption(fig.caption);
      const kicker = card.querySelector("[data-figure-kicker]");
      if (kicker && fig.kicker) kicker.textContent = fig.kicker;
      const title = card.querySelector("[data-figure-title]");
      if (title && fig.title) title.textContent = fig.title;
      // Lazy render when the card enters the viewport.
      if ("IntersectionObserver" in window) {
        const obs = new IntersectionObserver((entries, o) => {
          for (const e of entries) {
            if (e.isIntersecting) {
              o.disconnect();
              renderFigure(card, fig);
            }
          }
        }, { rootMargin: "120px" });
        obs.observe(card);
      } else {
        renderFigure(card, fig);
      }
    }

    // Footnote (generator info) on the figures section, if present
    const meta = document.querySelector("[data-figures-meta]");
    if (meta && doc.date) {
      const gen = doc.generator || "deterministic";
      const flag = gen === "llm" ? "LLM-curated" : gen === "mixed" ? "LLM + defaults" : "auto-generated";
      meta.textContent = `${flag} · ${doc.date}`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
