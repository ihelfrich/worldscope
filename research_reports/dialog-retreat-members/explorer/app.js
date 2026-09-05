/* Dialog investigative explorer — linked network + map + timeline.
   Public-record data only; roster presence != membership or wrongdoing. */
(function () {
"use strict";
const D = window.DIALOG;
if (!D) { document.body.innerHTML = "<p style='padding:20px'>data.js failed to load.</p>"; return; }

const TYPE_COLOR = {person:"#58a6ff",company:"#3fb950",fund:"#d29922",pac:"#f85149",
  org:"#bc8cff",media:"#39c5cf",gov:"#909dab",family:"#db61a2",document:"#e3b341"};
const EV_COLOR = {sec:"#d29922",court:"#f85149",fedreg:"#bc8cff",form990:"#3fb950",
  "key:leak":"#ff7b72","key:legal":"#f85149","key:milestone":"#58a6ff"};
const EV_LABEL = {sec:"SEC filing",court:"Court",fedreg:"Fed. Register",form990:"Form 990",
  "key:leak":"LEAK",ev_key:"key"};

const byId = new Map(D.nodes.map(n => [n.id, n]));
const neighbors = new Map(D.nodes.map(n => [n.id, new Set()]));
const relOf = new Map(); // "a|b" -> [{rel,note,dir}]
D.edges.forEach(e => {
  neighbors.get(e.source)?.add(e.target);
  neighbors.get(e.target)?.add(e.source);
});
const eventsByNode = new Map(); const yearsByNode = new Map();
D.events.forEach(ev => {
  if (!ev.nodeId) return;
  (eventsByNode.get(ev.nodeId) || eventsByNode.set(ev.nodeId, []).get(ev.nodeId)).push(ev);
  (yearsByNode.get(ev.nodeId) || yearsByNode.set(ev.nodeId, new Set()).get(ev.nodeId)).add(ev.year);
});

const Y0 = +D.meta.date_min.slice(0,4), Y1 = +D.meta.date_max.slice(0,4);
const state = {
  selected: null,
  types: new Set(D.meta.node_types),
  events: new Set(D.meta.event_types),
  range: [Y0, Y1],
};

document.getElementById("meta").textContent =
  `${D.meta.n_nodes} nodes · ${D.meta.n_edges} edges · ${D.meta.n_events} dated records · ${D.meta.n_geo} located · ${Y0}–${Y1}`;

/* ---------- filter chips ---------- */
function buildChips(el, items, set, colorMap, onToggle) {
  el.innerHTML = "";
  items.forEach(t => {
    const c = document.createElement("div");
    c.className = "chip";
    c.innerHTML = `<span class="dot" style="background:${colorMap[t]||'#888'}"></span>${(EV_LABEL[t]||t)}`;
    c.onclick = () => { set.has(t) ? set.delete(t) : set.add(t); c.classList.toggle("off", !set.has(t)); onToggle(); };
    el.appendChild(c);
  });
}
buildChips(document.getElementById("typeFilters"), D.meta.node_types, state.types, TYPE_COLOR, render);
buildChips(document.getElementById("eventFilters"), D.meta.event_types, state.events, EV_COLOR, () => { drawTimeline(); });
document.getElementById("netLegend").innerHTML = D.meta.node_types
  .map(t => `<span><i style="background:${TYPE_COLOR[t]}"></i>${t}</span>`).join("");

/* ---------- network (SVG force) ---------- */
const netSvg = d3.select("#net");
let W = 0, H = 0;
const gZoom = netSvg.append("g");
const gLink = gZoom.append("g"), gNode = gZoom.append("g"), gLab = gZoom.append("g");
const nodesC = D.nodes.map(n => Object.assign({}, n));
const nodeC = new Map(nodesC.map(n => [n.id, n]));
const linksC = D.edges.map(e => ({source:e.source, target:e.target, rel:e.relation, note:e.note}));
const radius = n => 3 + Math.sqrt(n.degree||0) * 1.7;

const sim = d3.forceSimulation(nodesC)
  .force("link", d3.forceLink(linksC).id(d=>d.id).distance(46).strength(.25))
  .force("charge", d3.forceManyBody().strength(-95))
  .force("collide", d3.forceCollide().radius(d=>radius(d)+2))
  .force("center", d3.forceCenter())
  .force("x", d3.forceX().strength(.04)).force("y", d3.forceY().strength(.04));

let link = gLink.selectAll("line"), node = gNode.selectAll("circle"), lab = gLab.selectAll("text");
function drawNet() {
  link = gLink.selectAll("line").data(linksC).join("line").attr("class","link");
  node = gNode.selectAll("circle").data(nodesC).join("circle")
    .attr("class","node").attr("r", radius).attr("fill", d=>TYPE_COLOR[d.type]||"#888")
    .on("click", (e,d)=>{ e.stopPropagation(); select(d.id); })
    .call(d3.drag()
      .on("start",(e,d)=>{ if(!e.active)sim.alphaTarget(.2).restart(); d.fx=d.x; d.fy=d.y; })
      .on("drag",(e,d)=>{ d.fx=e.x; d.fy=e.y; })
      .on("end",(e,d)=>{ if(!e.active)sim.alphaTarget(0); d.fx=null; d.fy=null; }));
  node.append("title").text(d=>`${d.label} — ${d.type}${d.degree?` · deg ${d.degree}`:""}`);
  lab = gLab.selectAll("text").data(nodesC.filter(d=>(d.degree||0)>=6)).join("text")
    .attr("class","label").attr("dx",6).attr("dy",3).text(d=>d.label.length>26?d.label.slice(0,25)+"…":d.label);
}
drawNet();
sim.on("tick", () => {
  link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  node.attr("cx",d=>d.x).attr("cy",d=>d.y);
  lab.attr("x",d=>d.x).attr("y",d=>d.y);
});
const zoom = d3.zoom().scaleExtent([.2,6]).on("zoom", e => gZoom.attr("transform", e.transform));
netSvg.call(zoom).on("click", () => select(null));
function sizeNet() {
  const r = document.getElementById("netPanel").getBoundingClientRect();
  W = r.width; H = r.height - 34; netSvg.attr("width",W).attr("height",H);
  sim.force("center", d3.forceCenter(W/2, H/2)); sim.alpha(.3).restart();
}

/* ---------- map (Leaflet) ---------- */
L.Icon.Default.imagePath = "vendor/images/";
const map = L.map("map", {worldCopyJump:true, zoomControl:true}).setView([30,5], 2);
// dark basemap (needs internet; markers/interactions work offline if tiles fail)
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  {attribution:"© OpenStreetMap, © CARTO", subdomains:"abcd", maxZoom:19}).addTo(map);
const markers = new Map();
D.nodes.filter(n=>n.lat!=null).forEach(n => {
  const m = L.circleMarker([n.lat,n.lon], {radius:4+Math.sqrt(n.degree||0)*1.3,
    color:"#0d1117", weight:.6, fillColor:TYPE_COLOR[n.type]||"#888", fillOpacity:.85});
  m.bindTooltip(`${n.label}<br><span style="opacity:.7">${n.city||""}${n.country?", "+n.country:""}</span>`);
  m.on("click", () => select(n.id));
  m.addTo(map); markers.set(n.id, m);
});

/* ---------- timeline (SVG stacked bars + brush) ---------- */
const tlEl = document.getElementById("timeline");
const tlSvg = d3.select("#timeline").append("svg").attr("width","100%").attr("height",64);
const tlG = tlSvg.append("g").attr("transform","translate(0,2)");
const years = d3.range(Y0, Y1+1);
const x = d3.scaleBand().domain(years).padding(.18);
const y = d3.scaleLinear();
const tlAxis = tlSvg.append("g").attr("class","tl-axis");
const brush = d3.brushX().on("brush end", ev => {
  if (!ev.selection) { state.range=[Y0,Y1]; }
  else {
    const [a,b] = ev.selection;
    const lo = years.find(yr => x(yr)+x.bandwidth() >= a) ?? Y0;
    const hiArr = years.filter(yr => x(yr) <= b); const hi = hiArr.length?hiArr[hiArr.length-1]:Y1;
    state.range = [lo, hi];
  }
  document.getElementById("timeLabel").textContent = `(${state.range[0]}–${state.range[1]})`;
  render();
});
const tlBrushG = tlSvg.append("g").attr("class","tl-brush");
function drawTimeline() {
  const wob = tlEl.clientWidth || 600, h = 48;
  tlSvg.attr("width", wob);
  x.range([24, wob-6]);
  const active = [...state.events];
  const counts = years.map(yr => {
    const o = {year:yr, total:0};
    active.forEach(t => { o[t] = D.events.filter(e=>e.year===yr && e.type===t).length; o.total += o[t]; });
    return o;
  });
  y.domain([0, d3.max(counts, d=>d.total)||1]).range([h, 2]);
  const stack = d3.stack().keys(active)(counts);
  tlG.selectAll("g.layer").data(stack, d=>d.key).join(
    en => en.append("g").attr("class","layer"),
    up => up, ex => ex.remove())
    .attr("fill", d=>EV_COLOR[d.key]||"#888")
    .selectAll("rect").data(d=>d).join("rect").attr("class","tl-bar")
      .attr("x", d=>x(d.data.year)).attr("width", x.bandwidth())
      .attr("y", d=>y(d[1])).attr("height", d=>Math.max(0,y(d[0])-y(d[1])));
  tlAxis.attr("transform",`translate(0,${h})`)
    .call(d3.axisBottom(x).tickValues(years.filter((d,i)=>i%2===0)).tickSizeOuter(0));
  brush.extent([[24,0],[wob-6,h]]);
  tlBrushG.call(brush);
}

/* ---------- selection / filters / render ---------- */
function passType(n){ return state.types.has(n.type); }
function passTime(id){
  if (state.range[0]<=Y0 && state.range[1]>=Y1) return true;
  const ys = yearsByNode.get(id);
  if (!ys) return true;                       // structural node w/ no dated records: unaffected
  for (const yr of ys) if (yr>=state.range[0] && yr<=state.range[1]) return true;
  return false;
}
function baseVisible(n){ return passType(n) && passTime(n.id); }

function select(id){ state.selected = (id===state.selected)?null:id; render(); renderDetail(); if(id) flyTo(id); }
function flyTo(id){ const n=byId.get(id); if(n&&n.lat!=null){ map.flyTo([n.lat,n.lon], Math.max(map.getZoom(),4), {duration:.6}); markers.get(id)?.openTooltip(); } }

function render(){
  const sel = state.selected;
  const focus = sel ? new Set([sel, ...neighbors.get(sel)]) : null;
  node.classed("dim", d => !baseVisible(d) || (focus && !focus.has(d.id)));
  node.attr("stroke", d=> d.id===sel ? "#fff" : "#0d1117").attr("stroke-width", d=> d.id===sel?2:.6);
  link.classed("dim", l => {
    const s=l.source.id||l.source, t=l.target.id||l.target;
    if(!baseVisible(nodeC.get(s))||!baseVisible(nodeC.get(t))) return true;
    return focus ? !(s===sel||t===sel) : false;
  });
  lab.classed("dim", d => !baseVisible(d) || (focus && !focus.has(d.id)));
  markers.forEach((m,id) => {
    const n=byId.get(id); const vis=baseVisible(n);
    const hi = !focus || focus.has(id);
    m.setStyle({fillOpacity: vis?(hi?.9:.15):.04, opacity: vis?(hi?1:.2):.05});
  });
}

function pill(t){ return `<span class="pill" style="background:${EV_COLOR[t]||'#888'}">${(EV_LABEL[t]||t)}</span>`; }
function renderDetail(){
  const box = document.getElementById("detailBody");
  const id = state.selected;
  if(!id){ box.innerHTML = `<p class="muted">Select a node, brush the timeline, or search. Everything is linked.</p>
    <p class="muted small">Public-record data only. Roster presence does <b>not</b> imply membership or wrongdoing.</p>`; return; }
  const n = byId.get(id);
  const nbrs = [...neighbors.get(id)].map(x=>byId.get(x)).filter(Boolean)
    .sort((a,b)=>(b.degree||0)-(a.degree||0));
  const rels = D.edges.filter(e=>e.source===id||e.target===id);
  const relTxt = t => { const e=rels.find(e=>(e.source===id&&e.target===t)||(e.target===id&&e.source===t));
    return e ? (e.source===id? e.relation : "← "+e.relation) : ""; };
  const evs = (eventsByNode.get(id)||[]).slice().sort((a,b)=>b.date.localeCompare(a.date));
  box.innerHTML =
    `<div class="dtitle">${n.label}</div>
     <div class="dsub"><span class="badge" style="border-color:${TYPE_COLOR[n.type]}">${n.type}</span>
       ${n.degree?`<span class="badge">degree ${n.degree}</span>`:""}
       ${n.city?`<span class="badge">${n.city}${n.country?", "+n.country:""}</span>`:""}</div>
     <div class="sec-h">Connections (${nbrs.length})</div>
     ${nbrs.slice(0,40).map(x=>`<div class="nbr" data-id="${x.id}">
        <span style="color:${TYPE_COLOR[x.type]}">●</span> ${x.label}
        <span class="muted small">${relTxt(x.id)}</span></div>`).join("")}
     <div class="sec-h">Public records (${evs.length})</div>
     ${evs.length? evs.slice(0,60).map(e=>`<div class="evrow">
        <span class="dt">${e.date}</span><span>${pill(e.type)}
        ${e.url?`<a href="${e.url}" target="_blank" rel="noopener">${(e.title||e.label||"record")}</a>`:(e.title||e.label||"")}
        ${e.value?` <span class="muted">rev $${(e.value/1e6).toFixed(1)}M</span>`:""}</span></div>`).join("")
        : `<p class="muted small">No dated public records linked to this node (structural/relationship node, or records under a different name).</p>`}`;
  box.querySelectorAll(".nbr").forEach(el=>el.onclick=()=>select(el.dataset.id));
}

/* ---------- search ---------- */
const search = document.getElementById("search");
search.addEventListener("keydown", e => {
  if(e.key!=="Enter") return;
  const q = search.value.trim().toLowerCase(); if(!q) return;
  const hit = D.nodes.find(n=>n.label.toLowerCase().includes(q)) ;
  if(hit){ select(hit.id); netSvg.transition().call(zoom.transform, d3.zoomIdentity); }
});
document.getElementById("reset").onclick = () => {
  state.selected=null; state.range=[Y0,Y1];
  state.types=new Set(D.meta.node_types); state.events=new Set(D.meta.event_types);
  document.querySelectorAll(".chip").forEach(c=>c.classList.remove("off"));
  document.getElementById("timeLabel").textContent="";
  tlBrushG.call(brush.move,null);
  search.value=""; drawTimeline(); render(); renderDetail();
};

/* ---------- boot ---------- */
function boot(){ sizeNet(); drawTimeline(); render(); setTimeout(()=>map.invalidateSize(),200); }
window.addEventListener("resize", () => { sizeNet(); drawTimeline(); render(); map.invalidateSize(); });
boot();
})();
