/**
 * worldscope-globe — the interactive intelligence globe at /globe/.
 *
 * Frosty-white 3D Earth, thin country borders, soft population
 * shading, realtime alert markers from today's brief, click-to-drill
 * into the Evidence Drawer. globe.gl + three.js, loaded lazily from
 * jsdelivr.
 *
 * Layers (toggleable):
 *   - basemap     — composited frosty-white texture + thin borders
 *   - alerts      — points for every entity mentioned in today's brief
 *                   that resolves to a country, sized + colored by
 *                   mention count
 *   - signals     — cross-section recurrence entities glow gold + pulse
 *   - satellites  — TLE-driven satellite paths (future; placeholder for now)
 *   - maritime    — AIS ship positions (future; data-dependency)
 *
 * Data sources (all loaded from data/*.json that the daily brief writes):
 *   - today.json     — records with section attribution
 *   - entities.json  — entities mentioned today + their sections
 *   - signals.json   — cross-section recurrence signals
 *
 * The page uses ./data/ resolution per WS_BASE injected by page_shell.
 */
(() => {
  "use strict";

  const GLOBE_GL_URL  = "https://cdn.jsdelivr.net/npm/globe.gl";
  const COUNTRIES_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";
  const TOPOJSON_URL  = "https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js";

  const COUNTRY_NAMES = {
    // ISO numeric → display name + ISO alpha-3 (used to match entity names)
    "840":"United States","124":"Canada","484":"Mexico","76":"Brazil","32":"Argentina",
    "826":"United Kingdom","250":"France","276":"Germany","380":"Italy","724":"Spain",
    "643":"Russia","804":"Ukraine","112":"Belarus","616":"Poland","792":"Turkey",
    "300":"Greece","364":"Iran","368":"Iraq","376":"Israel","760":"Syria",
    "422":"Lebanon","400":"Jordan","682":"Saudi Arabia","818":"Egypt","887":"Yemen",
    "356":"India","586":"Pakistan","4":"Afghanistan","50":"Bangladesh",
    "156":"China","158":"Taiwan","392":"Japan","410":"South Korea","408":"North Korea",
    "704":"Vietnam","608":"Philippines","360":"Indonesia","458":"Malaysia","764":"Thailand",
    "36":"Australia","554":"New Zealand","710":"South Africa","566":"Nigeria","404":"Kenya",
    "231":"Ethiopia","729":"Sudan","434":"Libya","12":"Algeria","504":"Morocco",
    "180":"DRC","178":"Congo","800":"Uganda","646":"Rwanda","862":"Venezuela",
    "170":"Colombia","152":"Chile","604":"Peru","68":"Bolivia",
  };
  const NAME_TO_ID = Object.fromEntries(
    Object.entries(COUNTRY_NAMES).map(([id, name]) => [name.toLowerCase(), id])
  );

  const STATE = {
    globe: null,
    countries: null,
    today: null,
    entities: null,
    signals: null,
    layers: {
      alerts:     true,
      signals:    true,
      satellites: false,    // not yet wired
      maritime:   false,    // not yet wired
    },
    selectedISO: null,
  };

  // ---- CDN loaders (sequential — globe.gl needs three.js, topojson is small)

  async function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = false;
      s.onload = resolve;
      s.onerror = () => reject(new Error("failed to load " + src));
      document.head.appendChild(s);
    });
  }
  async function loadDeps() {
    await loadScript(TOPOJSON_URL);
    await loadScript(GLOBE_GL_URL);
  }

  // ---- data loading -----------------------------------------------------

  function dataUrl(file) {
    if (typeof window.WS_BASE === "string" && window.WS_BASE.length) {
      return window.WS_BASE.replace(/\/?$/, "/") + "data/" + file;
    }
    const m = window.location.pathname.match(/^(.*?\/worldscope\/)/);
    if (m) return m[1] + "data/" + file;
    return "../data/" + file;
  }

  async function loadData() {
    const [countriesTopo, today, entities, signals] = await Promise.all([
      fetch(COUNTRIES_URL).then(r => r.json()),
      fetch(dataUrl("today.json")).then(r => r.ok ? r.json() : {sections: {}}),
      fetch(dataUrl("entities.json")).then(r => r.ok ? r.json() : {entities: []}),
      fetch(dataUrl("signals.json")).then(r => r.ok ? r.json() : {by_confidence: {}}),
    ]);
    const countries = window.topojson.feature(countriesTopo, countriesTopo.objects.countries).features;
    STATE.countries = countries;
    STATE.today     = today;
    STATE.entities  = entities;
    STATE.signals   = signals;
  }

  // ---- aggregation -----------------------------------------------------

  function mentionsPerCountry() {
    // For each known country, count entities (with type=place) whose name
    // matches. Returns {iso: {name, mentions, sections, entities[]}}.
    const out = {};
    for (const e of (STATE.entities.entities || [])) {
      if (!e.name) continue;
      const id = NAME_TO_ID[e.name.toLowerCase()];
      if (!id) continue;
      const bucket = out[id] || {
        iso: id, name: e.name, mentions: 0, sections: new Set(), entities: [],
      };
      bucket.mentions += (e.n_mentions || 1);
      for (const s of (e.sections || [])) bucket.sections.add(s);
      bucket.entities.push(e);
      out[id] = bucket;
    }
    for (const k of Object.keys(out)) {
      out[k].sectionList = [...out[k].sections];
      delete out[k].sections;
    }
    return out;
  }

  function signalsByCountry() {
    // Cross-section recurrence entities (pinned signals) that match countries.
    const out = {};
    const bc = STATE.signals.by_confidence || {};
    for (const band of ["high", "medium", "low"]) {
      for (const s of (bc[band] || [])) {
        const id = NAME_TO_ID[(s.canonical_name || "").toLowerCase()];
        if (!id) continue;
        out[id] = { iso: id, name: s.canonical_name, n_sections: s.n_sections, band };
      }
    }
    return out;
  }

  // ---- rendering -------------------------------------------------------

  function buildGlobe() {
    const root = document.getElementById("globe-root");
    if (!root) return;
    const mentions = mentionsPerCountry();
    const signals  = signalsByCountry();
    const maxMentions = Math.max(1, ...Object.values(mentions).map(m => m.mentions));

    // globe.gl supports both default + named imports across versions.
    const G = window.Globe.default ? window.Globe.default() : window.Globe();
    STATE.globe = G(root)
      .backgroundColor("rgba(0,0,0,0)")
      // Frosty-white look: muted texture, no atmospheric blue, soft white glow.
      .globeImageUrl("//unpkg.com/three-globe/example/img/earth-day.jpg")
      .bumpImageUrl("//unpkg.com/three-globe/example/img/earth-topology.png")
      .atmosphereColor("#FFFFFF")
      .atmosphereAltitude(0.18)
      .showGraticules(false)
      .polygonsData(STATE.countries)
      .polygonAltitude(d => signals[d.id] ? 0.012 : 0.005)
      .polygonCapColor(d => {
        const sig = signals[d.id];
        if (sig) return "rgba(212,160,23,0.42)";   // pinned signal — gold
        const m = mentions[d.id];
        if (!m) return "rgba(255,255,255,0.04)";
        // Soft cyan fill proportional to mention count.
        const t = Math.min(1, m.mentions / maxMentions);
        return `rgba(75,156,211,${0.10 + 0.55 * t})`;
      })
      .polygonSideColor(() => "rgba(19,41,75,0.18)")
      .polygonStrokeColor(() => "rgba(19,41,75,0.55)")
      .polygonLabel(d => {
        const id = d.id;
        const m = mentions[id];
        const s = signals[id];
        const name = COUNTRY_NAMES[id] || (d.properties || {}).name || id;
        const lines = [`<div style="font-family:'Source Serif 4',serif;font-weight:700;font-size:15px;color:#0B1220;margin-bottom:4px">${escapeHtml(name)}</div>`];
        if (s) lines.push(`<div style="font-family:Inter,sans-serif;font-size:12px;color:#13294B"><strong>cross-section signal</strong> · ${s.n_sections} sections</div>`);
        if (m) {
          lines.push(`<div style="font-family:Inter,sans-serif;font-size:12px;color:#4E5667">${m.mentions} mention${m.mentions !== 1 ? "s" : ""} · ${m.sectionList.length} section${m.sectionList.length !== 1 ? "s" : ""}</div>`);
          if (m.sectionList.length) lines.push(`<div style="font-family:Inter,sans-serif;font-size:11px;color:#6B7180;margin-top:3px">${m.sectionList.slice(0,3).join(" · ")}</div>`);
        }
        if (!m && !s) lines.push('<div style="font-family:Inter,sans-serif;font-size:12px;color:#6B7180;font-style:italic">no records today</div>');
        return `<div style="background:rgba(250,248,243,0.95);padding:10px 12px;border-radius:6px;border:1px solid #E8E2D5;box-shadow:0 4px 12px rgba(11,18,32,0.12);max-width:280px">${lines.join("")}</div>`;
      })
      .onPolygonClick(d => openCountryDrawer(d, mentions, signals))
      .onPolygonHover(hov => {
        root.style.cursor = hov ? "pointer" : "grab";
      });

    // Pulse alert dots for the heaviest-mentioned countries.
    const pulses = Object.values(mentions)
      .sort((a, b) => b.mentions - a.mentions)
      .slice(0, 25)
      .map(m => {
        const lat = countryCentroid(m.iso, "lat");
        const lng = countryCentroid(m.iso, "lng");
        return lat != null && lng != null
          ? { lat, lng, name: m.name, mentions: m.mentions }
          : null;
      })
      .filter(Boolean);

    STATE.globe
      .ringsData(pulses)
      .ringColor(() => () => "rgba(212,160,23,0.45)")
      .ringMaxRadius(d => Math.min(7, 1 + Math.log2(d.mentions)))
      .ringPropagationSpeed(2.0)
      .ringRepeatPeriod(2200)
      .ringAltitude(0.012);

    // Initial framing — center on Atlantic with a gentle tilt.
    STATE.globe.pointOfView({ lat: 18, lng: -10, altitude: 2.4 }, 1200);

    // Auto-rotate slowly until the user interacts.
    if (STATE.globe.controls()) {
      STATE.globe.controls().autoRotate = true;
      STATE.globe.controls().autoRotateSpeed = 0.35;
      STATE.globe.controls().addEventListener("start",
        () => { STATE.globe.controls().autoRotate = false; });
    }

    // Resize observer so the globe adapts to layout changes.
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        STATE.globe.width(width).height(height);
      }
    });
    ro.observe(root);

    // Stats line
    const stats = document.getElementById("globe-stats");
    if (stats) {
      const countriesActive = Object.keys(mentions).length;
      const signalsActive = Object.keys(signals).length;
      stats.innerHTML =
        `<strong class="tabular-nums">${countriesActive}</strong> ${countriesActive === 1 ? "country" : "countries"} with records today · ` +
        `<strong class="tabular-nums">${signalsActive}</strong> cross-section ${signalsActive === 1 ? "signal" : "signals"} · ` +
        `<strong class="tabular-nums">${pulses.length}</strong> pulsed`;
    }
  }

  // Cheap country centroid table (roughly, for dropping alert dots).
  function countryCentroid(iso, dim) {
    const C = {
      "840":[39.5,-98.5],"124":[60,-95],"484":[23,-102],"76":[-14,-51],"32":[-38,-63],
      "826":[55,-3],"250":[46,2],"276":[51,9],"380":[42,12],"724":[40,-3],
      "643":[60,90],"804":[49,32],"112":[53,28],"616":[52,19],"792":[39,35],
      "300":[39,22],"364":[32,53],"368":[33,44],"376":[31,35],"760":[35,38],
      "422":[34,36],"400":[31,36],"682":[24,45],"818":[26,30],"887":[15,48],
      "356":[20,77],"586":[30,70],"4":[33,66],"50":[24,90],
      "156":[35,103],"158":[24,121],"392":[36,138],"410":[37,127],"408":[40,127],
      "704":[16,108],"608":[13,122],"360":[-0.8,113],"458":[2.5,112],"764":[15,101],
      "36":[-25,134],"554":[-41,174],"710":[-30,25],"566":[10,8],"404":[-0.5,38],
      "231":[8,38],"729":[15,30],"434":[27,17],"12":[28,2],"504":[31,-7],
      "180":[-2,23],"178":[-1,15],"800":[1,32],"646":[-2,30],"862":[8,-66],
      "170":[4,-72],"152":[-30,-71],"604":[-10,-76],"68":[-17,-65],
    };
    const c = C[iso];
    if (!c) return null;
    return dim === "lat" ? c[0] : c[1];
  }

  // ---- click handler: open the evidence drawer for the country -------

  function openCountryDrawer(polygon, mentionsMap, signalsMap) {
    const id = polygon.id;
    const m = mentionsMap[id];
    const s = signalsMap[id];
    const name = COUNTRY_NAMES[id] || (polygon.properties || {}).name || id;
    // Defer to worldscope-evidence.js's existing drawer if loaded; otherwise
    // render an inline panel.
    const drawer = document.getElementById("ws-evidence-drawer");
    if (drawer) {
      drawer.classList.add("ws-evid-open");
      drawer.querySelector("#ws-evid-body").innerHTML = countryDrawerBody(name, m, s);
    } else {
      // Inline fallback panel.
      let panel = document.getElementById("globe-detail");
      if (panel) panel.innerHTML = countryDrawerBody(name, m, s);
    }
    STATE.selectedISO = id;
  }

  function countryDrawerBody(name, m, s) {
    let body = `<div style="font-family:Inter,sans-serif;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;font-weight:700;color:#D4A017;margin-bottom:6px">COUNTRY</div>`;
    body += `<div style="font-family:'Source Serif 4',Georgia,serif;font-size:24px;font-weight:700;color:#0B1220;margin-bottom:12px;line-height:1.1">${escapeHtml(name)}</div>`;
    if (s) {
      body += `<div style="background:rgba(212,160,23,0.16);border:1px solid #D4A017;color:#0B1220;padding:6px 10px;border-radius:4px;font-family:Inter,sans-serif;font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;display:inline-block;margin-bottom:14px">cross-section signal · ${s.n_sections} sections</div>`;
    }
    if (m) {
      body += `<dl style="display:grid;grid-template-columns:110px 1fr;gap:6px 14px;font-family:Inter,sans-serif;font-size:13px;margin:0 0 18px;color:#0B1220">`;
      body += `<dt style="font-weight:600;color:#6B7180;text-transform:uppercase;letter-spacing:.08em;font-size:10.5px;align-self:center">Mentions</dt><dd style="margin:0">${m.mentions}</dd>`;
      body += `<dt style="font-weight:600;color:#6B7180;text-transform:uppercase;letter-spacing:.08em;font-size:10.5px;align-self:center">Sections</dt><dd style="margin:0">${m.sectionList.join(", ")}</dd>`;
      body += `<dt style="font-weight:600;color:#6B7180;text-transform:uppercase;letter-spacing:.08em;font-size:10.5px;align-self:center">Entities</dt><dd style="margin:0">${m.entities.slice(0,5).map(e => escapeHtml(e.name)).join(" · ")}</dd>`;
      body += `</dl>`;
      const slug = name.toLowerCase().replace(/\s+/g, "-");
      body += `<a href="${dataUrl("").replace(/\/data\/$/, "/threads/" + slug + "/")}" style="display:inline-block;font-family:Inter,sans-serif;font-weight:600;font-size:12.5px;color:#13294B;text-decoration:none;border-bottom:1px solid #D4A017">View thread →</a>`;
    } else {
      body += `<div style="font-family:Inter,sans-serif;font-size:13px;color:#6B7180;font-style:italic">No records mention this country in today's brief.</div>`;
    }
    return body;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  // ---- view controls ---------------------------------------------------

  function bindControls() {
    const btnRotate = document.getElementById("g-rotate");
    if (btnRotate) {
      btnRotate.addEventListener("click", () => {
        if (!STATE.globe || !STATE.globe.controls) return;
        const ctrl = STATE.globe.controls();
        ctrl.autoRotate = !ctrl.autoRotate;
        btnRotate.classList.toggle("active", ctrl.autoRotate);
        btnRotate.textContent = ctrl.autoRotate ? "Pause rotation" : "Resume rotation";
      });
    }
    const btnReset = document.getElementById("g-reset");
    if (btnReset) {
      btnReset.addEventListener("click", () => {
        if (!STATE.globe) return;
        STATE.globe.pointOfView({ lat: 18, lng: -10, altitude: 2.4 }, 900);
      });
    }
  }

  // ---- init ------------------------------------------------------------

  async function buildHeroGlobe() {
    // Hero embed: smaller, control-less, just the planet spinning quietly
    // as a teaser. Click goes to /globe/. Loaded after the page settles
    // so it doesn't block the homepage's first paint.
    const root = document.getElementById("ws-hero-globe");
    if (!root) return;
    try {
      await loadDeps();
      await loadData();
      const G = window.Globe.default ? window.Globe.default() : window.Globe();
      const hero = G(root)
        .backgroundColor("rgba(0,0,0,0)")
        .globeImageUrl("//unpkg.com/three-globe/example/img/earth-day.jpg")
        .atmosphereColor("#FFFFFF")
        .atmosphereAltitude(0.16)
        .showGraticules(false)
        .polygonsData(STATE.countries)
        .polygonAltitude(0.003)
        .polygonCapColor(() => "rgba(75,156,211,0.18)")
        .polygonSideColor(() => "rgba(19,41,75,0.10)")
        .polygonStrokeColor(() => "rgba(19,41,75,0.42)");
      hero.pointOfView({ lat: 18, lng: -10, altitude: 2.6 }, 0);
      if (hero.controls()) {
        hero.controls().autoRotate = true;
        hero.controls().autoRotateSpeed = 0.4;
        hero.controls().enableZoom = false;
        hero.controls().enablePan = false;
      }
      // Hide the cursor change — the hero globe is a link to /globe/,
      // not draggable in place.
      root.style.cursor = "pointer";
      const ro = new ResizeObserver(entries => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect;
          hero.width(width).height(height);
        }
      });
      ro.observe(root);
    } catch (e) {
      // Quiet failure — the decorative gradient placeholder stays.
      console.warn("hero globe failed:", e);
    }
  }

  async function init() {
    // Two entry points: /globe/ page (full interactive) or homepage hero embed.
    const fullRoot = document.getElementById("globe-root");
    const heroRoot = document.getElementById("ws-hero-globe");
    if (fullRoot) {
      fullRoot.style.cursor = "grab";
      try {
        await loadDeps();
        await loadData();
        buildGlobe();
        bindControls();
      } catch (e) {
        fullRoot.innerHTML = `<div style="padding:32px;color:#990000;font-family:Inter,sans-serif;font-size:13px">Globe failed to load: ${escapeHtml(String(e))}</div>`;
        console.error(e);
      }
    } else if (heroRoot) {
      // Defer until window load so the homepage's critical content paints first.
      if (document.readyState === "complete") buildHeroGlobe();
      else window.addEventListener("load", buildHeroGlobe);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
