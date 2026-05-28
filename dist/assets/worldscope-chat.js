/**
 * worldscope-chat — in-browser chat over today's brief.
 *
 * The chat panel calls the Anthropic API directly from the browser
 * (anthropic-dangerous-direct-browser-access header) and runs grounded
 * tool calls against the static JSON exported daily to dist/data/:
 *
 *   data/today.json    — today's records, grouped by section
 *   data/entities.json — entities mentioned today
 *   data/signals.json  — cross-section recurrence signals
 *
 * Tools mirror the MCP server (worldscope.search_news / lookup_entity /
 * etc.) but query the pre-shaped JSON, so the page works on plain
 * GitHub Pages with no backend.
 *
 * The API key is stored in localStorage on the user's machine; nothing
 * is sent anywhere except api.anthropic.com.
 */
(() => {
  "use strict";

  const STATE = {
    apiKey: localStorage.getItem("ws.apiKey") || "",
    model:  localStorage.getItem("ws.model")  || "claude-sonnet-4-6",
    open:   false,
    busy:   false,
    history: [],   // [{role: "user"|"assistant", content: <Anthropic content blocks>}]
    cache:  { today: null, entities: null, signals: null },
  };

  const MODELS = [
    { id: "claude-opus-4-7",   label: "Opus 4.7 — best for deep grounding" },
    { id: "claude-sonnet-4-6", label: "Sonnet 4.6 — balanced (default)" },
    { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5 — fastest" },
  ];

  // ----- Data loading ------------------------------------------------------

  async function loadLake() {
    if (STATE.cache.today) return STATE.cache;
    const [t, e, s] = await Promise.all([
      fetch("./data/today.json").then(r => r.ok ? r.json() : null),
      fetch("./data/entities.json").then(r => r.ok ? r.json() : null),
      fetch("./data/signals.json").then(r => r.ok ? r.json() : null),
    ]);
    STATE.cache = { today: t || {sections:{}}, entities: e || {entities:[]}, signals: s || {by_confidence:{}} };
    return STATE.cache;
  }

  // ----- Tool definitions (sent to Claude) --------------------------------

  const TOOLS = [
    {
      name: "list_sections",
      description: "List today's sections with record counts. Use this first when the user asks a broad question to discover what data is available.",
      input_schema: { type: "object", properties: {}, required: [] },
    },
    {
      name: "get_section",
      description: "Fetch today's records for one section. Returns up to N records with id, text, url, source, date.",
      input_schema: {
        type: "object",
        properties: {
          section_id: { type: "string", description: "Section id from list_sections (e.g. 'federal_register')." },
          limit:      { type: "integer", description: "Max records (default 20, max 50).", default: 20 },
        },
        required: ["section_id"],
      },
    },
    {
      name: "search_today",
      description: "Substring search across today's record text. Use for keyword lookups across all sections.",
      input_schema: {
        type: "object",
        properties: {
          query:      { type: "string", description: "Case-insensitive substring to match." },
          section_id: { type: "string", description: "Optional: restrict to one section." },
          limit:      { type: "integer", description: "Max hits (default 25, max 50).", default: 25 },
        },
        required: ["query"],
      },
    },
    {
      name: "cross_section_signals",
      description: "Today's converging entities — entities that appeared in 3+ sections. The analytical signal of the day.",
      input_schema: {
        type: "object",
        properties: {
          min_confidence: { type: "string", enum: ["high", "medium", "low"], default: "medium" },
        },
        required: [],
      },
    },
    {
      name: "lookup_entity",
      description: "Find a named entity (person/org/place) mentioned today. Returns the entity plus the list of sections that mentioned it.",
      input_schema: {
        type: "object",
        properties: {
          name: { type: "string", description: "Case-insensitive substring of canonical name." },
        },
        required: ["name"],
      },
    },
  ];

  // ----- Tool execution (runs in the browser against static JSON) ---------

  async function runTool(name, input) {
    const lake = await loadLake();
    switch (name) {
      case "list_sections": {
        const out = [];
        for (const [sid, recs] of Object.entries(lake.today.sections || {})) {
          out.push({ section_id: sid, exported: recs.length, total_today: (lake.today.section_counts || {})[sid] || recs.length });
        }
        out.sort((a, b) => b.total_today - a.total_today);
        return { date: lake.today.date, sections: out };
      }
      case "get_section": {
        const sid = input.section_id;
        const limit = Math.max(1, Math.min(input.limit || 20, 50));
        const recs = (lake.today.sections || {})[sid] || [];
        return { section_id: sid, count: Math.min(recs.length, limit), records: recs.slice(0, limit) };
      }
      case "search_today": {
        const q = (input.query || "").trim().toLowerCase();
        if (!q) {
          return { query: input.query || "", count: 0, records: [],
                   error: "query is required and must be non-empty" };
        }
        const limit = Math.max(1, Math.min(input.limit || 25, 50));
        const sectionFilter = input.section_id || null;
        const hits = [];
        for (const [sid, recs] of Object.entries(lake.today.sections || {})) {
          if (sectionFilter && sid !== sectionFilter) continue;
          for (const r of recs) {
            if ((r.text || "").toLowerCase().includes(q)) {
              hits.push(r);
              if (hits.length >= limit) break;
            }
          }
          if (hits.length >= limit) break;
        }
        return { query: input.query, count: hits.length, records: hits };
      }
      case "cross_section_signals": {
        const conf = (input.min_confidence || "medium").toLowerCase();
        const bands = ["high", "medium", "low"];
        const cut = bands.indexOf(conf);
        const keep = cut >= 0 ? bands.slice(0, cut + 1) : ["high", "medium"];
        const ents = [];
        const bc = lake.signals.by_confidence || {};
        for (const b of keep) for (const e of (bc[b] || [])) ents.push(e);
        return { date: lake.signals.day, recurrences_found: lake.signals.recurrences_found || 0, entities: ents };
      }
      case "lookup_entity": {
        const needle = (input.name || "").trim().toLowerCase();
        if (!needle) {
          return { query: input.name || "", count: 0, entities: [],
                   error: "name is required and must be non-empty" };
        }
        const hits = (lake.entities.entities || []).filter(e =>
          (e.name || "").toLowerCase().includes(needle)
        );
        return { query: input.name, count: hits.length, entities: hits.slice(0, 25) };
      }
      default:
        return { error: `unknown tool: ${name}` };
    }
  }

  // ----- Anthropic API call (direct from browser) -------------------------

  async function callClaude(messages) {
    const SYSTEM = `You are an analyst embedded in WORLDSCOPE, a daily political/economic/OSINT briefing.

You answer questions about TODAY's briefing using the tools provided.
Today's date is ${(STATE.cache.today && STATE.cache.today.date) || "the most recent ingestion date"}.

Style:
  - Be concise; lead with the answer, then evidence.
  - Cite records inline using [section_id:record_id] format. The UI converts
    these to clickable chips that link to the per-section page.
  - When a record id is long (a URL or hash), still use it — the UI handles it.
  - If a question can't be answered from today's data, say so plainly and
    suggest what tool / data might help.
  - Don't fabricate. If a tool returns nothing, say so.`;

    const body = {
      model: STATE.model,
      max_tokens: 2048,
      system: SYSTEM,
      tools: TOOLS,
      messages: messages,
    };
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": STATE.apiKey,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const errTxt = await resp.text();
      throw new Error(`API ${resp.status}: ${errTxt.slice(0, 300)}`);
    }
    return resp.json();
  }

  // ----- Conversation loop (tool use until model emits final text) --------

  async function ask(userText) {
    if (!STATE.apiKey) {
      openSettings();
      return;
    }
    setBusy(true);
    STATE.history.push({ role: "user", content: userText });
    renderMessage("user", userText);

    try {
      // Build the messages array in Anthropic format.
      let messages = STATE.history.map(m => {
        if (typeof m.content === "string") return { role: m.role, content: m.content };
        return { role: m.role, content: m.content };
      });

      for (let hop = 0; hop < 6; hop++) {
        const result = await callClaude(messages);
        // Capture assistant turn (full content blocks for next iteration).
        STATE.history.push({ role: "assistant", content: result.content });
        messages.push({ role: "assistant", content: result.content });

        // Render any text blocks for the user.
        const textBlocks = result.content.filter(b => b.type === "text");
        const toolUses   = result.content.filter(b => b.type === "tool_use");
        if (textBlocks.length) {
          renderMessage("assistant", textBlocks.map(t => t.text).join("\n\n"));
        }

        if (toolUses.length === 0 || result.stop_reason !== "tool_use") {
          break;
        }

        // Execute every tool call and append a single user message with the results.
        const toolResults = [];
        for (const tu of toolUses) {
          renderToolBadge(tu.name, tu.input);
          let out;
          try {
            out = await runTool(tu.name, tu.input || {});
          } catch (e) {
            out = { error: String(e) };
          }
          toolResults.push({
            type: "tool_result",
            tool_use_id: tu.id,
            content: JSON.stringify(out),
          });
        }
        const toolMsg = { role: "user", content: toolResults };
        STATE.history.push(toolMsg);
        messages.push(toolMsg);
      }
    } catch (e) {
      renderMessage("error", String(e));
    } finally {
      setBusy(false);
    }
  }

  // ----- Rendering --------------------------------------------------------

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  }

  function renderMarkdown(text) {
    // Minimal markdown: paragraphs, bold, italic, code, lists, [citations].
    let out = escapeHtml(text);
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(?<!\*)\*(?!\*)([^*\n]+)\*/g, '<em>$1</em>');
    out = out.replace(/`([^`]+)`/g, '<code class="bg-mist/60 px-1 rounded text-[12.5px]">$1</code>');
    // Citation chips: [section_id:record_id] => clickable chip linking to the section
    out = out.replace(/\[([a-z0-9_]+):([^\]]+)\]/gi, (m, sid, rid) => {
      const href = `./sections/${encodeURIComponent(sid)}/`;
      return `<a href="${href}" class="inline-block bg-gold/20 hover:bg-gold/40 transition-colors text-navy font-sans text-[11px] font-semibold px-1.5 py-0.5 rounded mx-0.5 no-underline" title="${escapeHtml(rid)}">${escapeHtml(sid)} ›</a>`;
    });
    // Paragraphs + line breaks
    out = out.split(/\n{2,}/).map(p => `<p class="mb-2 leading-snug">${p.replace(/\n/g, '<br>')}</p>`).join("");
    return out;
  }

  function renderMessage(role, text) {
    const list = document.getElementById("ws-chat-list");
    if (!list) return;
    const el = document.createElement("div");
    if (role === "user") {
      el.className = "self-end max-w-[88%] bg-navy text-white px-3.5 py-2 rounded-lg rounded-tr-sm shadow-card font-sans text-[13.5px] leading-snug";
      el.textContent = text;
    } else if (role === "assistant") {
      el.className = "self-start max-w-[92%] bg-panel border border-mist px-3.5 py-2.5 rounded-lg rounded-tl-sm shadow-card font-serif text-[14px] text-ink";
      el.innerHTML = renderMarkdown(text);
    } else {
      el.className = "self-start max-w-[92%] bg-red-50 border border-red-200 text-red-900 px-3 py-2 rounded font-sans text-[12.5px]";
      el.textContent = text;
    }
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
  }

  function renderToolBadge(name, input) {
    const list = document.getElementById("ws-chat-list");
    if (!list) return;
    const el = document.createElement("div");
    el.className = "self-start font-sans text-[10.5px] uppercase tracking-[0.10em] text-slate-dim bg-mist/40 border border-mist px-2 py-1 rounded";
    const params = Object.entries(input || {}).slice(0, 3).map(([k,v]) => `${k}=${JSON.stringify(v).slice(0,32)}`).join(" ");
    el.textContent = `▸ ${name}${params ? "  " + params : ""}`;
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
  }

  function setBusy(b) {
    STATE.busy = b;
    const btn = document.getElementById("ws-chat-send");
    const inp = document.getElementById("ws-chat-input");
    const ind = document.getElementById("ws-chat-busy");
    if (btn) btn.disabled = b;
    if (inp) inp.disabled = b;
    if (ind) ind.style.display = b ? "inline-flex" : "none";
  }

  // ----- Settings modal (API key) ----------------------------------------

  function openSettings() {
    const modal = document.getElementById("ws-chat-settings");
    if (!modal) return;
    document.getElementById("ws-key-input").value = STATE.apiKey || "";
    const sel = document.getElementById("ws-model-select");
    sel.innerHTML = "";
    for (const m of MODELS) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      if (m.id === STATE.model) opt.selected = true;
      sel.appendChild(opt);
    }
    modal.style.display = "flex";
  }

  function saveSettings() {
    const k = document.getElementById("ws-key-input").value.trim();
    const m = document.getElementById("ws-model-select").value;
    if (k) { STATE.apiKey = k; localStorage.setItem("ws.apiKey", k); }
    if (m) { STATE.model  = m; localStorage.setItem("ws.model", m); }
    document.getElementById("ws-chat-settings").style.display = "none";
    if (STATE.apiKey && STATE.history.length === 0) {
      renderMessage("assistant",
        "Hi. I can search today's brief, drill into any section, look up entities, and surface cross-section signals. Try: *what's converging today?* or *show me today's federal register items*.");
    }
  }

  // ----- Wiring ----------------------------------------------------------

  function togglePanel(force) {
    const panel = document.getElementById("ws-chat-panel");
    if (!panel) return;
    STATE.open = (typeof force === "boolean") ? force : !STATE.open;
    panel.classList.toggle("ws-chat-open", STATE.open);
    if (STATE.open && !STATE.apiKey) openSettings();
    if (STATE.open) loadLake();
  }

  function init() {
    document.getElementById("ws-chat-toggle").addEventListener("click", () => togglePanel());
    document.getElementById("ws-chat-close").addEventListener("click", () => togglePanel(false));
    document.getElementById("ws-chat-settings-btn").addEventListener("click", openSettings);
    document.getElementById("ws-chat-clear").addEventListener("click", () => {
      STATE.history = [];
      document.getElementById("ws-chat-list").innerHTML = "";
    });
    document.getElementById("ws-key-save").addEventListener("click", saveSettings);
    document.getElementById("ws-key-cancel").addEventListener("click", () => {
      document.getElementById("ws-chat-settings").style.display = "none";
    });

    const form = document.getElementById("ws-chat-form");
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const inp = document.getElementById("ws-chat-input");
      const v = inp.value.trim();
      if (!v || STATE.busy) return;
      inp.value = "";
      ask(v);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
