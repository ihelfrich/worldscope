/**
 * worldscope-graph — interactive entity network for today's brief.
 *
 * D3 v7 force-directed layout rendered to SVG. Nodes are entities,
 * edges are co-occurrences in the same record today. Cross-section
 * recurrence signals are pinned (gold ring + soft pulse). Clicking a
 * node opens the chat panel with that entity pre-loaded.
 *
 * Loads d3 from the official jsdelivr CDN on demand. Data source is
 * ../data/graph.json (one level up because this lives at /graph/).
 */
(() => {
  "use strict";

  const D3_SRC = "https://cdn.jsdelivr.net/npm/d3@7";
  const TYPE_COLOR = {
    person:  "#13294B",
    place:   "#D4A017",
    org:     "#1A8A87",
    company: "#1A8A87",
    agency:  "#1F3D6E",
    country: "#D4A017",
    statute: "#990000",
    policy:  "#990000",
    topic:   "#4B9CD3",
    other:   "#4E5667",
  };

  let graphData = null;
  let svg, simulation, linkSel, nodeSel, labelSel;
  let highlightedId = null;
  let zoom;
  const VIEWPORT = { w: 1200, h: 720 };

  async function loadD3() {
    if (window.d3) return window.d3;
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = D3_SRC;
      s.onload  = () => resolve(window.d3);
      s.onerror = () => reject(new Error("d3 failed to load"));
      document.head.appendChild(s);
    });
  }

  async function loadGraph() {
    if (graphData) return graphData;
    const r = await fetch("../data/graph.json", { cache: "no-cache" });
    if (!r.ok) throw new Error("graph.json " + r.status);
    graphData = await r.json();
    return graphData;
  }

  // ---- rendering --------------------------------------------------------

  function renderMeta(doc) {
    document.getElementById("g-meta").textContent =
      `${doc.node_count} entities · ${doc.edge_count} co-occurrences · ${doc.date}`;
    const pinned = doc.nodes.filter(n => n.pinned);
    const note = document.getElementById("g-pinned");
    if (pinned.length) {
      note.innerHTML = "Pinned signals: " +
        pinned.map(n => `<button class="pinned-link" data-id="${escapeAttr(n.id)}">${escapeHtml(n.name)}</button>`)
              .join(" · ");
      note.querySelectorAll(".pinned-link").forEach(btn => {
        btn.addEventListener("click", () => focusNode(btn.dataset.id));
      });
    }
  }

  function nodeRadius(n) {
    const base = Math.sqrt(Math.max(1, n.mentions || 1)) * 2.1;
    return Math.min(18, Math.max(4, n.pinned ? base + 4 : base));
  }

  function buildForce(d3, doc) {
    const root = document.getElementById("graph-root");
    root.innerHTML = "";
    svg = d3.select(root).append("svg")
      .attr("viewBox", `0 0 ${VIEWPORT.w} ${VIEWPORT.h}`)
      .attr("preserveAspectRatio", "xMidYMid meet")
      .attr("aria-label", "Entity co-occurrence graph");

    // defs: pulse for pinned nodes
    const defs = svg.append("defs");
    defs.append("filter").attr("id", "soft-glow").html(`
      <feGaussianBlur stdDeviation="1.8" result="blur"></feGaussianBlur>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    `);

    // pan/zoom wrapper
    const g = svg.append("g");
    zoom = d3.zoom()
      .scaleExtent([0.35, 5])
      .on("zoom", e => g.attr("transform", e.transform));
    svg.call(zoom);

    // node lookup
    const nodes = doc.nodes.map(n => ({...n}));
    const idMap = new Map(nodes.map(n => [n.id, n]));
    const edges = doc.edges
      .map(e => ({ source: idMap.get(e.source), target: idMap.get(e.target),
                   weight: e.weight, sections: e.sections }))
      .filter(e => e.source && e.target);

    // force simulation
    simulation = d3.forceSimulation(nodes)
      .force("link",    d3.forceLink(edges).id(d => d.id)
        .distance(d => 30 + 80 / Math.max(1, d.weight))
        .strength(d => Math.min(1, 0.18 + d.weight * 0.04)))
      .force("charge",  d3.forceManyBody().strength(-160))
      .force("center",  d3.forceCenter(VIEWPORT.w / 2, VIEWPORT.h / 2))
      .force("collide", d3.forceCollide(d => nodeRadius(d) + 3.5))
      .alphaDecay(0.04);

    linkSel = g.append("g").attr("class", "links")
      .selectAll("line").data(edges).join("line")
      .attr("stroke",         "#13294B")
      .attr("stroke-opacity", d => Math.min(0.55, 0.10 + Math.log2(d.weight + 1) * 0.06))
      .attr("stroke-width",   d => Math.min(3, 0.5 + Math.sqrt(d.weight)));

    nodeSel = g.append("g").attr("class", "nodes")
      .selectAll("g").data(nodes).join("g")
      .attr("class", "node")
      .style("cursor", "pointer")
      .call(dragHandler(d3));

    // pinned halo (drawn behind)
    nodeSel.filter(d => d.pinned).append("circle")
      .attr("class", "halo")
      .attr("r",      d => nodeRadius(d) + 7)
      .attr("fill",   "rgba(212,160,23,0.18)")
      .attr("stroke", "#D4A017")
      .attr("stroke-width", 1.5);

    nodeSel.append("circle")
      .attr("class", "core")
      .attr("r",      d => nodeRadius(d))
      .attr("fill",   d => TYPE_COLOR[d.type] || TYPE_COLOR.other)
      .attr("stroke", "#FAF8F3")
      .attr("stroke-width", 1.3);

    nodeSel.append("title")
      .text(d => `${d.name}  (${d.type}, ${d.mentions} mentions, ${d.sections} section${d.sections !== 1 ? "s" : ""})`);

    nodeSel.on("mouseenter", (e, d) => highlightNeighborhood(d.id))
           .on("mouseleave", () => highlightNeighborhood(null))
           .on("click",      (e, d) => onNodeClick(d));

    // labels: top 20 by mentions + all pinned
    const showLabels = new Set([
      ...nodes.filter(n => n.pinned).map(n => n.id),
      ...[...nodes].sort((a, b) => (b.mentions || 0) - (a.mentions || 0)).slice(0, 20).map(n => n.id),
    ]);
    labelSel = g.append("g").attr("class", "labels")
      .selectAll("text").data(nodes).join("text")
      .attr("font-family", "Inter, sans-serif")
      .attr("font-size",   11)
      .attr("font-weight", d => d.pinned ? 700 : 500)
      .attr("fill",        d => d.pinned ? "#0B1220" : "#4E5667")
      .attr("pointer-events", "none")
      .attr("text-anchor", "middle")
      .attr("dy",          d => -nodeRadius(d) - 5)
      .text(d => showLabels.has(d.id) ? d.name : "");

    simulation.on("tick", () => {
      linkSel
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      nodeSel.attr("transform", d => `translate(${d.x},${d.y})`);
      labelSel.attr("transform", d => `translate(${d.x},${d.y})`);
    });

    // First-time fit: gentle zoom out after settling
    setTimeout(() => fitToBox(d3, nodes), 1200);
  }

  function dragHandler(d3) {
    return d3.drag()
      .on("start", (e, d) => { if (!e.active) simulation.alphaTarget(0.25).restart();
                                d.fx = d.x; d.fy = d.y; })
      .on("drag",  (e, d) => { d.fx = e.x;  d.fy = e.y; })
      .on("end",   (e, d) => { if (!e.active) simulation.alphaTarget(0);
                                d.fx = null; d.fy = null; });
  }

  function fitToBox(d3, nodes) {
    if (!nodes.length) return;
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = x1 - x0, h = y1 - y0;
    if (!w || !h) return;
    const scale = Math.min(0.95, Math.min(VIEWPORT.w / (w + 80), VIEWPORT.h / (h + 80)));
    const tx = (VIEWPORT.w - (x0 + x1) * scale) / 2;
    const ty = (VIEWPORT.h - (y0 + y1) * scale) / 2;
    svg.transition().duration(600)
       .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }

  function highlightNeighborhood(id) {
    highlightedId = id;
    if (!nodeSel) return;
    if (!id) {
      nodeSel.style("opacity", 1);
      linkSel.style("stroke-opacity", d => Math.min(0.55, 0.10 + Math.log2(d.weight + 1) * 0.06));
      return;
    }
    const neighbors = new Set([id]);
    linkSel.each(d => {
      if (d.source.id === id) neighbors.add(d.target.id);
      if (d.target.id === id) neighbors.add(d.source.id);
    });
    nodeSel.style("opacity", d => neighbors.has(d.id) ? 1 : 0.18);
    linkSel.style("stroke-opacity",
      d => (d.source.id === id || d.target.id === id) ? 0.85 : 0.05);
  }

  function focusNode(id) {
    if (!nodeSel) return;
    const node = nodeSel.data().find(n => n.id === id);
    if (!node) return;
    highlightNeighborhood(id);
    // Pan to the node
    if (zoom && svg) {
      const scale = 1.4;
      svg.transition().duration(500).call(zoom.transform,
        window.d3.zoomIdentity
          .translate(VIEWPORT.w / 2 - node.x * scale, VIEWPORT.h / 2 - node.y * scale)
          .scale(scale));
    }
    document.getElementById("g-detail").innerHTML = renderDetail(node);
  }

  function onNodeClick(node) {
    document.getElementById("g-detail").innerHTML = renderDetail(node);
    highlightNeighborhood(node.id);
  }

  function renderDetail(n) {
    const neighbors = [];
    if (graphData) {
      const nameById = Object.fromEntries(graphData.nodes.map(x => [x.id, x.name]));
      for (const e of graphData.edges) {
        if (e.source === n.id || e.source.id === n.id) {
          neighbors.push({ name: nameById[e.target.id || e.target], weight: e.weight, sections: e.sections });
        } else if (e.target === n.id || e.target.id === n.id) {
          neighbors.push({ name: nameById[e.source.id || e.source], weight: e.weight, sections: e.sections });
        }
      }
      neighbors.sort((a, b) => b.weight - a.weight);
    }
    const top = neighbors.slice(0, 10).map(x =>
      `<li class="flex items-baseline gap-2 text-[13px]">
         <span class="text-navy font-medium">${escapeHtml(x.name)}</span>
         <span class="text-slate-dim text-[11px] tabular-nums">×${x.weight}</span>
       </li>`).join("");
    return `
      <div class="font-sans text-kicker text-gold uppercase mb-1.5">${escapeHtml(n.type)}${n.pinned ? " · PINNED" : ""}</div>
      <h3 class="font-serif text-[22px] font-bold text-ink leading-tight mb-2">${escapeHtml(n.name)}</h3>
      <div class="text-slate text-[12.5px] font-sans mb-3">
        ${n.mentions} mention${n.mentions === 1 ? "" : "s"} ·
        ${n.sections} section${n.sections === 1 ? "" : "s"} ·
        ${neighbors.length} co-occurrence${neighbors.length === 1 ? "" : "s"}
      </div>
      ${neighbors.length ? `
        <div class="font-sans uppercase tracking-[0.16em] text-[10.5px] font-bold text-slate-dim mb-2">Top co-mentioned</div>
        <ul class="list-none p-0 m-0 space-y-1.5 mb-4">${top}</ul>` : ""}
      <button id="g-ask-btn" class="font-sans text-[13px] font-semibold bg-navy text-white px-3.5 py-2 rounded hover:bg-navy-soft transition-colors w-full">
        Ask the brief about ${escapeHtml(n.name)} →
      </button>
    `;
  }

  // ---- search/filter ----------------------------------------------------

  function bindSearch() {
    const input = document.getElementById("g-search");
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      if (!q) { highlightNeighborhood(null); return; }
      const matches = new Set();
      nodeSel.each(d => { if ((d.name || "").toLowerCase().includes(q)) matches.add(d.id); });
      nodeSel.style("opacity", d => matches.has(d.id) ? 1 : 0.16);
      linkSel.style("stroke-opacity",
        d => (matches.has(d.source.id) || matches.has(d.target.id)) ? 0.35 : 0.04);
    });
  }

  // ---- chat bridge ------------------------------------------------------

  function bindAskButton() {
    document.addEventListener("click", (e) => {
      if (e.target && e.target.id === "g-ask-btn") {
        const detail = document.getElementById("g-detail");
        const titleEl = detail.querySelector("h3");
        if (!titleEl) return;
        const name = titleEl.textContent.trim();
        // Persist the prefill query in sessionStorage so the homepage chat
        // can pick it up on load.
        sessionStorage.setItem("ws.chat.prefill",
          `Tell me about ${name} in today's brief — which sections mention it and what's the story?`);
        // Navigate back to homepage; the chat panel auto-opens via the prefill hook.
        window.location.href = "../index.html#chat";
      }
    });
  }

  // ---- utils ------------------------------------------------------------

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }

  // ---- init -------------------------------------------------------------

  async function init() {
    try {
      const [d3, doc] = await Promise.all([loadD3(), loadGraph()]);
      renderMeta(doc);
      if (!doc.nodes.length) {
        document.getElementById("graph-root").innerHTML =
          '<div class="text-slate-dim font-sans text-[13px] italic p-8 text-center">No entity graph data available for this date.</div>';
        return;
      }
      buildForce(d3, doc);
      bindSearch();
      bindAskButton();
    } catch (e) {
      document.getElementById("graph-root").innerHTML =
        `<div class="text-crimson font-sans text-[12px] p-6">graph failed to load: ${escapeHtml(String(e))}</div>`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
