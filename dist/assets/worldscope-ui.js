/**
 * worldscope-ui — global UI primitives for the homepage:
 *   - inline sparklines in the section TOC
 *   - ⌘K command palette (navigation + search)
 *   - keyboard nav (j/k between sections, ⌘K for palette)
 *   - count-up animation for hero stats (driven by data-count)
 *
 * No build step. No framework. ~12KB. All vanilla.
 */
(() => {
  "use strict";

  // ────────────────────────────────────────────────────────────────
  // SPARKLINES
  // For every [data-spark] element on the page, fetch section_history
  // once and render an inline SVG sparkline. Last value is the gold
  // accent; the rest is slate.
  // ────────────────────────────────────────────────────────────────

  async function loadHistory() {
    const url = (typeof window.WS_BASE === "string" ? window.WS_BASE : "./")
                 + "data/section_history.json";
    try {
      const r = await fetch(url, { cache: "no-cache" });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  function renderSpark(el, history, width = 64, height = 18) {
    if (!history || !history.length) return;
    const max = Math.max(1, ...history);
    const stepX = width / Math.max(1, history.length - 1);
    // Polyline points
    const pts = history.map((v, i) => {
      const x = (i * stepX).toFixed(1);
      const y = (height - (v / max) * (height - 2) - 1).toFixed(1);
      return `${x},${y}`;
    }).join(" ");
    // Last-point dot
    const lastX = ((history.length - 1) * stepX).toFixed(1);
    const lastY = (height - (history[history.length - 1] / max) * (height - 2) - 1).toFixed(1);
    el.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"
           aria-hidden="true" style="display:inline-block;vertical-align:middle">
        <polyline points="${pts}"
                  fill="none" stroke="currentColor" stroke-width="1.2"
                  stroke-linejoin="round" stroke-linecap="round" opacity="0.55"/>
        <circle cx="${lastX}" cy="${lastY}" r="2"
                fill="var(--gold, #C8961A)" stroke="var(--canvas, #FCFCFD)" stroke-width="1"/>
      </svg>`;
  }

  async function paintSparklines() {
    const targets = document.querySelectorAll("[data-spark]");
    if (!targets.length) return;
    const doc = await loadHistory();
    if (!doc) return;
    for (const el of targets) {
      const sid = el.dataset.spark;
      const sec = (doc.sections || {})[sid];
      if (sec && sec.history) renderSpark(el, sec.history);
    }
  }

  // ────────────────────────────────────────────────────────────────
  // COUNT-UP for hero stats
  // ────────────────────────────────────────────────────────────────

  function animateCount(el) {
    const target = parseInt(el.dataset.count || "0", 10);
    if (!target || target < 5) { el.textContent = target; return; }
    const dur = 700;
    const t0 = performance.now();
    function frame(now) {
      const t = Math.min(1, (now - t0) / dur);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - t, 3);
      const v = Math.floor(target * eased);
      el.textContent = v.toLocaleString();
      if (t < 1) requestAnimationFrame(frame);
      else el.textContent = target.toLocaleString();
    }
    requestAnimationFrame(frame);
  }

  function animateAllCounts() {
    const targets = document.querySelectorAll("[data-count]");
    if (!targets.length) return;
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries, o) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            animateCount(e.target);
            o.unobserve(e.target);
          }
        }
      }, { rootMargin: "0px 0px -10% 0px" });
      targets.forEach(t => io.observe(t));
    } else {
      targets.forEach(animateCount);
    }
  }

  // ────────────────────────────────────────────────────────────────
  // COMMAND PALETTE (⌘K / ctrl+K)
  // Fuzzy-searches over: every nav destination, every section, every
  // thread on this page. Picks one with Enter, navigates immediately.
  // ────────────────────────────────────────────────────────────────

  const PALETTE_TARGETS = [];
  let paletteRoot = null;
  let paletteIndex = 0;
  let paletteFiltered = [];

  function collectTargets() {
    const base = (typeof window.WS_BASE === "string" ? window.WS_BASE : "./");
    const fixed = [
      { label: "Home — today's brief",        href: base + "index.html",       hint: "↵" },
      { label: "Globe — interactive Earth",   href: base + "globe/",           hint: "G" },
      { label: "Threads — multi-day arcs",    href: base + "threads/",         hint: "T" },
      { label: "Graph — entity network",      href: base + "graph/",           hint: "N" },
      { label: "Sections — section archive",  href: base + "sections/",        hint: "S" },
      { label: "Reproducibility — show work", href: base + "reproducibility/", hint: "R" },
      { label: "Health — source-feed status", href: base + "health/",          hint: "H" },
      { label: "Archive — past briefs",       href: base + "briefings/",       hint: "A" },
    ];
    PALETTE_TARGETS.push(...fixed);
    // Sections + threads from this page's DOM.
    document.querySelectorAll("[data-palette-section]").forEach(el => {
      PALETTE_TARGETS.push({
        label: `Section · ${el.dataset.paletteSection}`,
        href:  el.getAttribute("href") || "#" + (el.id || ""),
        hint:  "§",
      });
    });
    document.querySelectorAll("[data-palette-thread]").forEach(el => {
      PALETTE_TARGETS.push({
        label: `Thread · ${el.dataset.paletteThread}`,
        href:  el.getAttribute("href") || "#",
        hint:  "▶",
      });
    });
  }

  function ensurePalette() {
    if (paletteRoot) return paletteRoot;
    paletteRoot = document.createElement("div");
    paletteRoot.id = "ws-palette";
    paletteRoot.innerHTML = `
      <div class="ws-palette-scrim"></div>
      <div class="ws-palette-card" role="dialog" aria-label="Command palette">
        <input type="search" id="ws-palette-input"
               placeholder="Jump to anywhere · type to filter"
               autocomplete="off" spellcheck="false">
        <ul id="ws-palette-list" role="listbox"></ul>
        <footer class="ws-palette-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </footer>
      </div>`;
    document.body.appendChild(paletteRoot);
    paletteRoot.querySelector(".ws-palette-scrim")
              .addEventListener("click", closePalette);
    paletteRoot.querySelector("#ws-palette-input")
              .addEventListener("input", refreshPalette);
    paletteRoot.querySelector("#ws-palette-input")
              .addEventListener("keydown", onPaletteKey);
    return paletteRoot;
  }

  function openPalette() {
    if (!PALETTE_TARGETS.length) collectTargets();
    const root = ensurePalette();
    root.classList.add("open");
    const input = root.querySelector("#ws-palette-input");
    input.value = "";
    paletteIndex = 0;
    refreshPalette();
    setTimeout(() => input.focus(), 50);
  }

  function closePalette() {
    if (paletteRoot) paletteRoot.classList.remove("open");
  }

  function refreshPalette() {
    if (!paletteRoot) return;
    const input = paletteRoot.querySelector("#ws-palette-input");
    const q = (input.value || "").toLowerCase().trim();
    paletteFiltered = q
      ? PALETTE_TARGETS.filter(t => t.label.toLowerCase().includes(q))
      : PALETTE_TARGETS.slice(0, 40);
    paletteIndex = 0;
    const list = paletteRoot.querySelector("#ws-palette-list");
    list.innerHTML = paletteFiltered.map((t, i) => `
      <li role="option" data-i="${i}" class="${i === paletteIndex ? 'active' : ''}"
          tabindex="-1">
        <span class="hint">${t.hint || "→"}</span>
        <span class="label">${escapeHtml(t.label)}</span>
      </li>`).join("");
    list.querySelectorAll("li").forEach(li => {
      li.addEventListener("click", () => navigateTo(parseInt(li.dataset.i, 10)));
      li.addEventListener("mouseenter", () => {
        paletteIndex = parseInt(li.dataset.i, 10);
        refreshActive();
      });
    });
  }

  function refreshActive() {
    if (!paletteRoot) return;
    paletteRoot.querySelectorAll("#ws-palette-list li").forEach((li, i) =>
      li.classList.toggle("active", i === paletteIndex));
    const active = paletteRoot.querySelector("#ws-palette-list li.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function navigateTo(i) {
    const t = paletteFiltered[i];
    if (!t) return;
    closePalette();
    if (t.href.startsWith("#")) {
      document.querySelector(t.href)?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      window.location.href = t.href;
    }
  }

  function onPaletteKey(e) {
    if (e.key === "Escape") { closePalette(); return; }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      paletteIndex = Math.min(paletteIndex + 1, paletteFiltered.length - 1);
      refreshActive();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      paletteIndex = Math.max(paletteIndex - 1, 0);
      refreshActive();
    } else if (e.key === "Enter") {
      e.preventDefault();
      navigateTo(paletteIndex);
    }
  }

  function bindGlobalKeys() {
    document.addEventListener("keydown", (e) => {
      // ⌘K / Ctrl+K — open palette
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openPalette();
      }
      // ? — open palette too (for keyboards without modifier)
      if (e.key === "?" && !e.target.matches("input, textarea")) {
        e.preventDefault();
        openPalette();
      }
    });
    // Also bind any [data-palette-trigger] element (the topnav hint)
    document.querySelectorAll("[data-palette-trigger]").forEach(el =>
      el.addEventListener("click", (e) => { e.preventDefault(); openPalette(); }));
  }

  // ────────────────────────────────────────────────────────────────
  // util
  // ────────────────────────────────────────────────────────────────

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  // ────────────────────────────────────────────────────────────────
  // init
  // ────────────────────────────────────────────────────────────────

  function init() {
    paintSparklines();
    animateAllCounts();
    bindGlobalKeys();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
