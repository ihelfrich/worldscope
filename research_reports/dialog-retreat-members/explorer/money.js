/* Dialog money flows — interactive Sankey.
   Public-record / reported figures only; a money tie is a transaction record,
   not an allegation of wrongdoing. */
(function () {
"use strict";
const MD = window.MONEY;
if (!MD || !window.d3 || !d3.sankey) {
  document.body.innerHTML = "<p style='padding:20px'>libraries failed to load.</p>"; return;
}
const KIND_COLOR = {donor:"#f0883e", pac:"#f85149", candidate:"#58a6ff", lp:"#d29922",
  fund:"#d29922", company:"#3fb950", trust:"#bc8cff", grantee:"#39c5cf",
  regulator:"#909dab", market:"#909dab", person:"#db61a2"};
const POL_COLOR = {neutral:"#7d8590", support:"#3fb950", oppose:"#f85149"};
const CATS = Object.keys(MD.meta.categories);
const state = { cats: new Set(CATS), selected: null };

const usd = v => v>=1e9 ? "$"+(v/1e9).toFixed(v>=1e10?0:1)+"B"
  : v>=1e6 ? "$"+(v/1e6).toFixed(0)+"M" : "$"+d3.format(",")(v);
document.getElementById("meta").textContent =
  `${MD.meta.n_nodes} actors · ${MD.meta.n_flows} flows · ` +
  CATS.map(c=>`${c} ${usd(MD.meta.categories[c].known_usd)}`).join(" · ");

const nodeById = new Map(MD.nodes.map(n=>[n.id,n]));
const svg = d3.select("#sankey");
const gLink = svg.append("g"), gNode = svg.append("g");
const tip = document.getElementById("tip");

/* category chips */
const cf = document.getElementById("catFilters");
CATS.forEach(c => {
  const el = document.createElement("div");
  el.className = "chip";
  el.innerHTML = `<span class="dot" style="background:#bc8cff"></span>${c} <span class="muted">(${MD.meta.categories[c].flows})</span>`;
  el.onclick = () => { state.cats.has(c)?state.cats.delete(c):state.cats.add(c);
    el.classList.toggle("off", !state.cats.has(c)); draw(); };
  cf.appendChild(el);
});

function activeFlows(){ return MD.flows.filter(f=>state.cats.has(f.category)); }

function draw(){
  const flows = activeFlows();
  const ids = new Set(); flows.forEach(f=>{ids.add(f.source);ids.add(f.target);});
  const nodes = [...ids].map(id=>({id, ...nodeById.get(id)}));
  const links = flows.map(f=>({source:f.source, target:f.target, value:Math.max(f.amount,1),
    f}));
  const panel = document.getElementById("sankeyPanel").getBoundingClientRect();
  const W = panel.width-2, H = panel.height-40;
  svg.attr("width",W).attr("height",H);
  if(!nodes.length){ gLink.selectAll("*").remove(); gNode.selectAll("*").remove(); return; }

  const sankey = d3.sankey().nodeId(d=>d.id).nodeWidth(13).nodePadding(11)
    .nodeAlign(d3.sankeyJustify).extent([[6,6],[W-6,H-6]]);
  let graph;
  try { graph = sankey({nodes:nodes.map(d=>({...d})), links:links.map(d=>({...d}))}); }
  catch(e){ gNode.selectAll("*").remove(); gLink.selectAll("*").remove();
    gNode.append("text").attr("x",12).attr("y",24).attr("fill","#f85149")
      .text("Sankey layout error (cyclic flow in selection): "+e.message); return; }

  gLink.selectAll("path").data(graph.links, d=>d.f.source+"|"+d.f.target).join("path")
    .attr("class", d=>"slink"+(d.f.amount_known?"":" undisc"))
    .attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke", d=>POL_COLOR[d.f.polarity]||"#7d8590")
    .attr("stroke-width", d=>Math.max(1.2, d.width))
    .on("mousemove", (e,d)=>showTip(e, flowTip(d.f)))
    .on("mouseleave", hideTip)
    .on("click", (e,d)=>{ e.stopPropagation(); selectNode(d.f.source); });

  const nsel = gNode.selectAll("g.snode").data(graph.nodes, d=>d.id).join(en=>{
    const g=en.append("g").attr("class","snode");
    g.append("rect"); g.append("text"); return g;
  });
  nsel.attr("transform", d=>`translate(${d.x0},${d.y0})`);
  nsel.select("rect").attr("width", d=>d.x1-d.x0).attr("height", d=>Math.max(1,d.y1-d.y0))
    .attr("fill", d=>KIND_COLOR[d.kind]||"#888")
    .on("click",(e,d)=>{ e.stopPropagation(); selectNode(d.id); })
    .on("mousemove",(e,d)=>showTip(e, nodeTip(d)))
    .on("mouseleave", hideTip);
  nsel.select("text")
    .attr("x", d=> d.x0 < W/2 ? (d.x1-d.x0)+5 : -5)
    .attr("y", d=>(d.y1-d.y0)/2).attr("dy","0.32em")
    .attr("text-anchor", d=> d.x0 < W/2 ? "start" : "end")
    .text(d=>d.label.length>34?d.label.slice(0,33)+"…":d.label);

  applySelect();
}

function flowTip(f){
  return `<b>${nodeById.get(f.source).label}</b> → <b>${nodeById.get(f.target).label}</b><br>`+
    `${f.amount_known?usd(f.amount):"amount undisclosed"} · <span style="color:${POL_COLOR[f.polarity]}">${f.polarity}</span> · ${f.category}<br>`+
    `<span class="muted">${f.basis} · confidence: ${f.confidence}</span>`;
}
function nodeTip(d){
  const inn=d3.sum(d.targetLinks||[],l=>l.f.amount_known?l.f.amount:0);
  const out=d3.sum(d.sourceLinks||[],l=>l.f.amount_known?l.f.amount:0);
  return `<b>${d.label}</b> <span class="muted">(${d.kind})</span><br>`+
    `in ${usd(inn)} · out ${usd(out)}`;
}
function showTip(e,html){ tip.innerHTML=html; tip.style.opacity=1;
  tip.style.left=(e.clientX+14)+"px"; tip.style.top=(e.clientY+12)+"px"; }
function hideTip(){ tip.style.opacity=0; }

function selectNode(id){ state.selected = id===state.selected?null:id; applySelect(); renderDetail(); }
function applySelect(){
  const sel=state.selected;
  gLink.selectAll("path").classed("sankey-dim", d=> sel && d.f.source!==sel && d.f.target!==sel);
  gNode.selectAll("g.snode").classed("sankey-dim", d=>{
    if(!sel) return false;
    if(d.id===sel) return false;
    return !MD.flows.some(f=>(f.source===sel&&f.target===d.id)||(f.target===sel&&f.source===d.id));
  });
}
function renderDetail(){
  const box=document.getElementById("detailBody"); const id=state.selected;
  if(!id){ box.innerHTML=`<p class="muted">Hover a flow for amount &amp; source. Click a node to isolate its flows.</p>`; return; }
  const n=nodeById.get(id);
  const outF=MD.flows.filter(f=>f.source===id), inF=MD.flows.filter(f=>f.target===id);
  const row=f=>{const other=nodeById.get(f.source===id?f.target:f.source).label;
    return `<div class="flowrow"><span class="amt">${f.amount_known?usd(f.amount):"n/a"}</span>
      <span><span style="color:${POL_COLOR[f.polarity]}">●</span> ${other}
      ${f.url?`<a href="${f.url}" target="_blank" rel="noopener">↗</a>`:""}
      <br><span class="muted small">${f.basis} · ${f.confidence}</span></span></div>`;};
  box.innerHTML=`<div class="dtitle">${n.label}</div>
    <div class="dsub"><span class="badge" style="border-color:${KIND_COLOR[n.kind]}">${n.kind}</span></div>
    ${outF.length?`<div class="sec-h">Outflows (${outF.length})</div>`+outF.map(row).join(""):""}
    ${inF.length?`<div class="sec-h">Inflows (${inF.length})</div>`+inF.map(row).join(""):""}`;
}

document.getElementById("reset").onclick=()=>{ state.selected=null;
  state.cats=new Set(CATS); document.querySelectorAll("#catFilters .chip").forEach(c=>c.classList.remove("off"));
  draw(); renderDetail(); };
svg.on("click", ()=>{ state.selected=null; applySelect(); renderDetail(); });
window.addEventListener("resize", draw);
draw();
})();
