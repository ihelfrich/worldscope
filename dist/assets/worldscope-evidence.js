/**
 * worldscope-evidence — Evidence Drawer for the Claim Ledger.
 *
 * Loads dist/data/claims.json (produced by worldscope.fact_check's
 * --write-ledger flag) and dist/data/today.json (record lookup), then:
 *
 *   1. Scans the page DOM for each claim's raw_text and wraps it in a
 *      clickable badge whose color encodes verification status:
 *        verified   → small navy ✓ pill
 *        divergent  → crimson ⚠ pill (loud — this is the headline case)
 *        unverified → mist ? pill
 *        skipped    → no badge (skip reasons: forecast_context, etc.)
 *
 *   2. Clicking any badge slides in a panel from the right containing:
 *        - The claim text + status
 *        - For verified/divergent: claimed vs actual, tolerance,
 *          divergence percentage
 *        - The evidence records (resolved from today.json by id)
 *        - The validator name (e.g. "asset_price/coingecko/sameday")
 *
 *   3. Browser localStorage keeps the drawer's last query so it
 *      survives reload.
 *
 * The script gracefully no-ops when claims.json is missing — useful
 * when an older build is served before the Claim Ledger has produced
 * its first output. Re-deploys will activate it.
 */
(() => {
  "use strict";

  const STATE = {
    claims: null,
    records: null,
    open: false,
  };

  // ---- data loading -----------------------------------------------------

  async function loadClaims() {
    if (STATE.claims !== null) return STATE.claims;
    try {
      const r = await fetch("./data/claims.json", { cache: "no-cache" });
      if (!r.ok) { STATE.claims = []; return STATE.claims; }
      const doc = await r.json();
      STATE.claims = Array.isArray(doc.claims) ? doc.claims : [];
    } catch (e) {
      STATE.claims = [];
    }
    return STATE.claims;
  }

  async function loadRecords() {
    if (STATE.records !== null) return STATE.records;
    try {
      const r = await fetch("./data/today.json", { cache: "no-cache" });
      if (!r.ok) { STATE.records = {}; return STATE.records; }
      const doc = await r.json();
      // Flatten {sections: {sid: [recs]}} into {record_id: record}.
      const flat = {};
      for (const recs of Object.values(doc.sections || {})) {
        for (const r of recs) {
          if (r.id) flat[r.id] = r;
        }
      }
      STATE.records = flat;
    } catch (e) {
      STATE.records = {};
    }
    return STATE.records;
  }

  // ---- DOM scanning + badge injection ----------------------------------

  // Find every text node whose content contains the claim's raw_text and
  // wrap the matching substring in a clickable badge. Skips nodes that
  // already live inside a badge to keep the scan idempotent.
  function injectBadges(claims) {
    if (!claims.length) return;
    // Sort by raw_text length descending so the longest claim wraps first
    // and short claims don't fragment a longer one.
    const sorted = [...claims].sort(
      (a, b) => (b.raw_text || "").length - (a.raw_text || "").length
    );
    for (const c of sorted) {
      if (!c.raw_text || c.status === "skipped") continue;
      const escaped = c.raw_text
        .replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      // Treat the matched text as a literal; don't bind to word
      // boundaries because dollar-prefixed numbers don't start at \b.
      const pat = new RegExp(escaped, "g");
      wrapTextMatching(document.querySelector("main") || document.body,
                        pat, c);
    }
  }

  function wrapTextMatching(root, pat, claim) {
    const walker = document.createTreeWalker(
      root, NodeFilter.SHOW_TEXT,
      {
        acceptNode(node) {
          // Skip if any ancestor is already a claim badge, a script, or
          // a chart's text node.
          let p = node.parentElement;
          while (p) {
            if (p.dataset && p.dataset.claimId) return NodeFilter.FILTER_REJECT;
            const tn = (p.tagName || "").toLowerCase();
            if (tn === "script" || tn === "style" || tn === "svg" ||
                tn === "input" || tn === "textarea" || tn === "code") {
              return NodeFilter.FILTER_REJECT;
            }
            p = p.parentElement;
          }
          return pat.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        },
      }
    );
    const toWrap = [];
    let n;
    while ((n = walker.nextNode())) toWrap.push(n);
    for (const node of toWrap) {
      pat.lastIndex = 0;
      const text = node.nodeValue;
      const m = pat.exec(text);
      if (!m) continue;
      const before = text.slice(0, m.index);
      const match  = text.slice(m.index, m.index + m[0].length);
      const after  = text.slice(m.index + m[0].length);
      const frag   = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));
      const span = document.createElement("span");
      span.className = "claim-badge claim-" + (claim.status || "unverified");
      span.dataset.claimId = claim.id;
      span.title = "Click for evidence";
      span.textContent = match;
      span.appendChild(badgeMark(claim));
      frag.appendChild(span);
      if (after) frag.appendChild(document.createTextNode(after));
      node.parentNode.replaceChild(frag, node);
    }
  }

  function badgeMark(claim) {
    const span = document.createElement("span");
    span.className = "claim-mark";
    span.setAttribute("aria-hidden", "true");
    span.textContent = ({
      verified:   "✓",   // ✓
      divergent:  "⚠",   // ⚠
      unverified: "?",
    }[claim.status]) || "?";
    return span;
  }

  // ---- drawer rendering -------------------------------------------------

  function ensureDrawer() {
    let drawer = document.getElementById("ws-evidence-drawer");
    if (drawer) return drawer;
    drawer = document.createElement("aside");
    drawer.id = "ws-evidence-drawer";
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-label", "Evidence");
    drawer.innerHTML = `
      <header class="ws-evid-head">
        <span class="ws-evid-kicker">EVIDENCE</span>
        <button id="ws-evid-close" aria-label="Close">×</button>
      </header>
      <div id="ws-evid-body"></div>
    `;
    document.body.appendChild(drawer);
    drawer.querySelector("#ws-evid-close")
          .addEventListener("click", () => closeDrawer());
    return drawer;
  }

  function openDrawer(claim, records) {
    const drawer = ensureDrawer();
    drawer.classList.add("ws-evid-open");
    STATE.open = true;
    const body = drawer.querySelector("#ws-evid-body");
    body.innerHTML = renderClaim(claim, records);
  }

  function closeDrawer() {
    const drawer = document.getElementById("ws-evidence-drawer");
    if (drawer) drawer.classList.remove("ws-evid-open");
    STATE.open = false;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  function fmtPct(p) {
    if (p == null) return "—";
    const sign = p >= 0 ? "+" : "";
    return `${sign}${(p * 100).toFixed(1)}%`;
  }

  function fmtNum(n, unit) {
    if (n == null) return "—";
    if (unit === "USD") return `$${Number(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    if (unit === "%")   return `${Number(n).toFixed(2)}%`;
    return Number(n).toLocaleString();
  }

  function renderClaim(c, records) {
    const statusLabel = {
      verified:   "Verified against same-day source",
      divergent:  "Diverges from same-day source",
      unverified: "Not verified (no validator yet)",
      skipped:    "Skipped (forecast context, etc.)",
    }[c.status] || c.status;

    const stat = c.status === "divergent" ? "divergent" : (c.status || "unverified");
    let body = `
      <div class="ws-evid-status ws-evid-${escapeHtml(stat)}">${escapeHtml(statusLabel)}</div>
      <div class="ws-evid-claim">${escapeHtml(c.raw_text || "")}</div>
      <dl class="ws-evid-meta">
        <dt>Type</dt><dd>${escapeHtml(c.claim_type || "?")}</dd>
        <dt>Subject</dt><dd>${escapeHtml(c.subject || "?")}</dd>
        <dt>Claimed</dt><dd class="tabular-nums">${escapeHtml(fmtNum(c.claimed_value, c.unit))}</dd>
    `;
    if (c.actual_value != null) {
      body += `
        <dt>Actual</dt><dd class="tabular-nums">${escapeHtml(fmtNum(c.actual_value, c.unit))}</dd>
        <dt>Divergence</dt><dd class="tabular-nums">${escapeHtml(fmtPct(c.divergence_pct))}</dd>
      `;
    }
    if (c.tolerance != null) {
      body += `<dt>Tolerance</dt><dd class="tabular-nums">±${(c.tolerance * 100).toFixed(1)}%</dd>`;
    }
    if (c.validator) {
      body += `<dt>Validator</dt><dd><code>${escapeHtml(c.validator)}</code></dd>`;
    }
    if (c.skip_reason) {
      body += `<dt>Skip reason</dt><dd>${escapeHtml(c.skip_reason)}</dd>`;
    }
    body += "</dl>";

    const evRows = (c.evidence_record_ids || []).map(rid => {
      const r = records[rid];
      if (!r) return `<li class="ws-evid-rec missing"><code>${escapeHtml(rid)}</code> <span class="muted">(not in today's lake)</span></li>`;
      const url = r.url || "#";
      const safeUrl = /^https?:\/\//i.test(url) ? url : "#";
      const title = r.text || rid;
      return `<li class="ws-evid-rec">
        <div class="ws-evid-rec-title"><a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a></div>
        <div class="ws-evid-rec-meta"><span class="ws-evid-rec-section">${escapeHtml(r.section_id || "")}</span> &middot; <span class="tabular-nums">${escapeHtml(r.date || "")}</span></div>
      </li>`;
    }).join("");
    if (evRows) {
      body += `<h3 class="ws-evid-h">Evidence records</h3><ul class="ws-evid-recs">${evRows}</ul>`;
    } else if (c.status === "unverified" || c.status === "skipped") {
      body += `<p class="ws-evid-noev">No evidence records linked (validator not run, or skipped).</p>`;
    }
    return body;
  }

  // ---- record-only drawer (no claim — used by chat citations) ---------

  function renderRecord(rec) {
    if (!rec) {
      return '<div class="ws-evid-noev">Record not found in today\'s lake.</div>';
    }
    const sid = rec.section_id || "";
    const url = rec.url || "";
    const safeUrl = /^https?:\/\//i.test(url) ? url : "#";
    const sectionLink = sid
      ? `<a href="./sections/${encodeURIComponent(sid)}/" class="ws-evid-rec-section">${escapeHtml(sid)} →</a>`
      : "";
    return `
      <div class="ws-evid-status ws-evid-unverified">EVIDENCE RECORD</div>
      <div class="ws-evid-claim">${escapeHtml(rec.text || rec.title || "(no title)")}</div>
      <dl class="ws-evid-meta">
        <dt>Section</dt><dd>${sectionLink || escapeHtml(sid)}</dd>
        <dt>Source</dt><dd>${escapeHtml(rec.source_id || "?")}</dd>
        <dt>Date</dt><dd class="tabular-nums">${escapeHtml(rec.date || "")}</dd>
        ${rec.lang ? `<dt>Language</dt><dd>${escapeHtml(rec.lang)}</dd>` : ""}
      </dl>
      ${url ? `<a class="ws-evid-link" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">Open primary source →</a>` : ""}
    `;
  }

  function openRecordDrawer(recordId, records) {
    const drawer = ensureDrawer();
    drawer.classList.add("ws-evid-open");
    STATE.open = true;
    drawer.querySelector("#ws-evid-body").innerHTML = renderRecord(records[recordId]);
  }

  // ---- click handler ----------------------------------------------------

  function bindClicks(records) {
    document.addEventListener("click", (e) => {
      // Claim badge: open the full claim drawer.
      const claimEl = e.target.closest && e.target.closest("[data-claim-id]");
      if (claimEl) {
        e.preventDefault();
        const cid = claimEl.dataset.claimId;
        const claim = (STATE.claims || []).find(c => c.id === cid);
        if (claim) openDrawer(claim, records);
        return;
      }
      // Chat citation chip: open the record drawer instead of navigating
      // (chip still has an href as fallback for the no-JS case).
      const recEl = e.target.closest && e.target.closest("[data-record-id]");
      if (recEl) {
        e.preventDefault();
        const rid = recEl.dataset.recordId;
        openRecordDrawer(rid, records);
        return;
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && STATE.open) closeDrawer();
    });
  }

  // ---- init -------------------------------------------------------------

  async function init() {
    const [claims, records] = await Promise.all([loadClaims(), loadRecords()]);
    // Bind clicks unconditionally so chat citation chips (data-record-id)
    // open the record drawer even when the Claim Ledger isn't built yet.
    bindClicks(records);
    if (claims.length) injectBadges(claims);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
