"""figures_engine — generate today's interactive chart + map specs.

Produces a `dist/data/figures.json` document consumed by the homepage's
worldscope-figures.js renderer. Two paths feed it:

  1. Deterministic defaults: 5 high-quality charts computed directly
     from the lake (cross-section signals, section volume + deltas vs
     yesterday, top anomalies, regional intensity world map). These
     guarantee the homepage always has charts, even on cold-start.
  2. LLM-driven overrides (when ANTHROPIC_API_KEY is set in env): we
     pass the lake summary + signals to Claude and ask for 3-5 Vega-Lite
     specs the model picks as most important to surface today. These
     replace or augment the defaults.

Output shape (figures.json):

  {
    "date": "2026-05-28",
    "generated_at": "...",
    "generator": "deterministic" | "llm" | "mixed",
    "figures": [
      {
        "id": "cross-section-signals",
        "title": "Signals converging today",
        "kicker": "CROSS-SECTION RECURRENCE",
        "caption": "Three entities recurred in 3+ sections today...",
        "spec_type": "vega-lite",      // "vega-lite" | "maplibre" | "html"
        "spec": { ... vega-lite JSON ... },
        "links": [ { "label": "...", "href": "..." }, ... ]
      },
      ...
    ]
  }

Charts share a heritage-palette Vega-Lite config (CONFIG below) so they
look like one product rather than five.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Vega-Lite theme: heritage palette + editorial type + minimal axes
# ---------------------------------------------------------------------------
# Loaded into every chart's `config` block so they share visual language.

VEGA_CONFIG: dict[str, Any] = {
    "background": "transparent",
    "padding": {"top": 8, "left": 4, "right": 4, "bottom": 4},
    "font": "Inter, -apple-system, sans-serif",
    "title": {
        "font":       "'Source Serif 4', Georgia, serif",
        "fontSize":   16,
        "fontWeight": 700,
        "color":      "#0B1220",
        "anchor":     "start",
        "offset":     6,
        "subtitleFont":     "Inter, sans-serif",
        "subtitleFontSize": 12,
        "subtitleColor":    "#4E5667",
    },
    "axis": {
        "labelFont":      "Inter, sans-serif",
        "labelFontSize":  11,
        "labelColor":     "#4E5667",
        "labelPadding":   6,
        "titleFont":      "Inter, sans-serif",
        "titleFontSize":  11,
        "titleColor":     "#4E5667",
        "titleFontWeight": 500,
        "titlePadding":   8,
        "domainColor":    "#E8E2D5",
        "tickColor":      "#E8E2D5",
        "gridColor":      "#F0EBE0",
        "gridDash":       [],
        "gridOpacity":    0.6,
        "labelOverlap":   "greedy",
    },
    "axisY": { "ticks": False, "domain": False },
    "axisX": { "grid": False },
    "legend": {
        "labelFont":     "Inter, sans-serif",
        "labelFontSize": 11,
        "labelColor":    "#4E5667",
        "titleFont":     "Inter, sans-serif",
        "titleFontSize": 11,
        "titleColor":    "#0B1220",
        "titleFontWeight": 600,
        "symbolType":    "circle",
    },
    "view": { "stroke": "transparent" },
    "range": {
        "category": ["#13294B", "#D4A017", "#990000", "#4B9CD3", "#1A8A87",
                     "#1F3D6E", "#E8BC42", "#4E5667"],
        "ordinal":  {"scheme": "blues"},
        "ramp":     {"scheme": "blues"},
        "diverging": {"scheme": "redblue"},
    },
    "bar":  { "color": "#13294B", "cornerRadiusEnd": 2 },
    "line": { "color": "#13294B", "strokeWidth": 2, "interpolate": "monotone" },
    "area": { "color": "#13294B", "opacity": 0.18 },
    "point": { "filled": True, "color": "#13294B", "size": 36 },
    "rule": { "color": "#E8E2D5", "strokeWidth": 1 },
    "mark": { "tooltip": True },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_lake_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _vega_lite(spec_inner: dict) -> dict:
    """Wrap a chart spec with the shared config so all figures share theme."""
    spec = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "config": VEGA_CONFIG}
    spec.update(spec_inner)
    return spec


# ---------------------------------------------------------------------------
# Deterministic figure generators
# ---------------------------------------------------------------------------

def _fig_cross_section(cross_section: dict) -> dict | None:
    """Recurrence chart: entities × number of sections, sorted desc."""
    bands = ("high", "medium", "low")
    entities: list[dict] = []
    for band in bands:
        for ent in (cross_section.get("by_confidence", {}).get(band) or []):
            entities.append(ent)
    if not entities:
        return None
    entities.sort(key=lambda e: (-int(e.get("n_sections") or 0),
                                  -int(e.get("total_mentions") or 0)))
    data = [{
        "name":       e.get("canonical_name", "?"),
        "n_sections": int(e.get("n_sections") or 0),
        "mentions":   int(e.get("total_mentions") or 0),
        "confidence": e.get("confidence", "low"),
        "sections":   ", ".join(e.get("sections") or []),
    } for e in entities[:12]]

    caption = (
        f"{len(entities)} {'entity' if len(entities)==1 else 'entities'} "
        f"recurred in 3+ sections today. "
        f"Most converged: {entities[0].get('canonical_name','?')} "
        f"({int(entities[0].get('n_sections') or 0)} sections)."
    )

    spec = _vega_lite({
        "data":  {"values": data},
        "mark":  {"type": "bar", "cornerRadiusEnd": 3},
        "encoding": {
            "y": {"field": "name", "type": "nominal",
                  "sort": "-x", "title": None,
                  "axis": {"labelLimit": 140, "labelFontSize": 12}},
            "x": {"field": "n_sections", "type": "quantitative",
                  "title": "Sections mentioning",
                  "axis": {"tickMinStep": 1, "format": "d"}},
            "color": {
                "field": "confidence", "type": "ordinal",
                "scale": {
                    "domain": ["high", "medium", "low"],
                    "range":  ["#13294B", "#D4A017", "#C9C1B2"],
                },
                "legend": {"title": "Confidence", "orient": "bottom"},
            },
            "tooltip": [
                {"field": "name", "title": "Entity"},
                {"field": "n_sections", "title": "Sections", "format": "d"},
                {"field": "mentions",   "title": "Mentions", "format": "d"},
                {"field": "sections",   "title": "In"},
                {"field": "confidence", "title": "Confidence"},
            ],
        },
        "height": {"step": 24},
    })
    return {
        "id": "cross-section",
        "kicker":  "RECURRENCE",
        "title":   "Signals converging today",
        "caption": caption,
        "spec_type": "vega-lite",
        "spec": spec,
    }


def _fig_section_volume(today_doc: dict) -> dict | None:
    """Horizontal bar of record counts per section today."""
    counts = today_doc.get("section_counts") or {}
    if not counts:
        return None
    data = [{"section": s.replace("_", " "), "count": n}
            for s, n in sorted(counts.items(), key=lambda kv: -kv[1])[:18]]
    total = sum(counts.values())
    top = data[0]
    caption = (
        f"{total:,} records ingested across {len(counts)} sections today. "
        f"Highest volume: <strong>{top['section']}</strong> ({top['count']:,})."
    )
    spec = _vega_lite({
        "data":  {"values": data},
        "mark":  {"type": "bar", "color": "#13294B", "cornerRadiusEnd": 3},
        "encoding": {
            "y": {"field": "section", "type": "nominal", "sort": "-x",
                  "title": None, "axis": {"labelLimit": 200, "labelFontSize": 12}},
            "x": {"field": "count", "type": "quantitative",
                  "title": "Records",
                  "axis": {"format": ",d"}},
            "tooltip": [
                {"field": "section", "title": "Section"},
                {"field": "count",   "title": "Records today", "format": ",d"},
            ],
        },
        "height": {"step": 22},
    })
    return {
        "id": "section-volume",
        "kicker":  "TODAY",
        "title":   "Record volume by section",
        "caption": caption,
        "spec_type": "vega-lite",
        "spec": spec,
    }


def _fig_section_deltas(today_doc: dict, store_db: Path) -> dict | None:
    """How much each section changed vs yesterday (absolute Δ records)."""
    if not store_db.exists():
        return None
    today_iso = today_doc.get("date") or _date.today().isoformat()
    counts_today = today_doc.get("section_counts") or {}
    yest_counts: dict[str, int] = {}
    conn = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        # The snapshot store has per-day per-section payload JSONs;
        # extract yesterday's item count.
        cur = conn.execute(
            "SELECT section_id, payload FROM snapshots "
            "WHERE snapshot_date = date(?, '-1 day')",
            (today_iso,)
        )
        for sid, payload in cur.fetchall():
            try:
                p = json.loads(payload)
                yest_counts[sid] = len(p.get("items") or [])
            except Exception:
                pass
    finally:
        conn.close()

    deltas = []
    for sid, today_n in counts_today.items():
        y = yest_counts.get(sid, 0)
        deltas.append({"section": sid.replace("_", " "), "today": today_n,
                       "yesterday": y, "delta": today_n - y})
    if not any(d["delta"] for d in deltas):
        return None
    deltas.sort(key=lambda d: abs(d["delta"]), reverse=True)
    data = deltas[:14]
    gainers = [d for d in deltas if d["delta"] > 0]
    losers  = [d for d in deltas if d["delta"] < 0]
    caption = (
        f"{len(gainers)} sections up vs yesterday, "
        f"{len(losers)} down. "
        f"Largest mover: <strong>{data[0]['section']}</strong> "
        f"({'+' if data[0]['delta']>=0 else ''}{data[0]['delta']:+,d})."
    )
    spec = _vega_lite({
        "data": {"values": data},
        "mark": {"type": "bar", "cornerRadiusEnd": 3},
        "encoding": {
            "y": {"field": "section", "type": "nominal",
                  "sort": {"field": "delta", "op": "min", "order": "descending"},
                  "title": None, "axis": {"labelLimit": 200, "labelFontSize": 12}},
            "x": {"field": "delta", "type": "quantitative",
                  "title": "Δ records vs yesterday",
                  "axis": {"format": "+,d"}},
            "color": {
                "condition": {"test": "datum.delta >= 0", "value": "#1A8A87"},
                "value": "#990000",
            },
            "tooltip": [
                {"field": "section",   "title": "Section"},
                {"field": "today",     "title": "Today",     "format": ",d"},
                {"field": "yesterday", "title": "Yesterday", "format": ",d"},
                {"field": "delta",     "title": "Δ",         "format": "+,d"},
            ],
        },
        "height": {"step": 22},
    })
    return {
        "id": "section-deltas",
        "kicker":  "MOVEMENT",
        "title":   "What changed vs yesterday",
        "caption": caption,
        "spec_type": "vega-lite",
        "spec": spec,
    }


def _fig_entity_types(entities_doc: dict) -> dict | None:
    """Composition of today's entity mentions by type (person/place/org/etc)."""
    ents = entities_doc.get("entities") or []
    if not ents:
        return None
    counts = Counter((e.get("type") or "other").split(":")[0] for e in ents)
    data = [{"type": t, "count": n}
            for t, n in counts.most_common() if t and n > 1]
    if not data:
        return None
    total = sum(c["count"] for c in data)
    top = data[0]
    caption = (
        f"{total:,} unique entity references in today's brief. "
        f"Most common type: <strong>{top['type']}</strong> "
        f"({top['count']:,}, {top['count']*100//total}% of total)."
    )
    spec = _vega_lite({
        "data": {"values": data},
        "mark": {"type": "arc", "innerRadius": 64, "outerRadius": 108,
                 "cornerRadius": 2, "stroke": "#FAF8F3", "strokeWidth": 3},
        "encoding": {
            "theta": {"field": "count", "type": "quantitative", "stack": True},
            "color": {"field": "type",  "type": "nominal",
                      "legend": {"title": "Entity type", "orient": "right"},
                      "scale": {"range": ["#13294B", "#D4A017", "#990000",
                                          "#4B9CD3", "#1A8A87", "#1F3D6E",
                                          "#E8BC42", "#4E5667"]}},
            "tooltip": [
                {"field": "type",  "title": "Type"},
                {"field": "count", "title": "Mentions", "format": ",d"},
            ],
        },
        "height": 240,
    })
    return {
        "id": "entity-types",
        "kicker":  "ENTITIES",
        "title":   "What's being talked about",
        "caption": caption,
        "spec_type": "vega-lite",
        "spec": spec,
    }


def _fig_world_map(entities_doc: dict, lake_db: Path) -> dict | None:
    """World map: country-level intensity, colored by record count today.

    Derived from entities of type 'country' or 'place' that match
    well-known country names. Uses Vega-Lite's geoshape with the
    world-110m topojson from the official vega-datasets CDN, so no
    pre-shipped GeoJSON is needed.
    """
    # Map common canonical names to ISO numeric codes (matching
    # topojson world-110m country id field).
    COUNTRY_ID = {
        "United States": 840, "USA": 840, "U.S.": 840, "US": 840,
        "Canada": 124, "Mexico": 484, "Brazil": 76, "Argentina": 32,
        "United Kingdom": 826, "UK": 826, "Britain": 826,
        "France": 250, "Germany": 276, "Italy": 380, "Spain": 724,
        "Russia": 643, "Ukraine": 804, "Belarus": 112, "Poland": 616,
        "Turkey": 792, "Greece": 300, "Iran": 364, "Iraq": 368,
        "Israel": 376, "Syria": 760, "Lebanon": 422, "Jordan": 400,
        "Saudi Arabia": 682, "Egypt": 818, "Yemen": 887,
        "India": 356, "Pakistan": 586, "Afghanistan": 4, "Bangladesh": 50,
        "China": 156, "Taiwan": 158, "Japan": 392, "South Korea": 410,
        "North Korea": 408, "Vietnam": 704, "Philippines": 608,
        "Indonesia": 360, "Malaysia": 458, "Thailand": 764,
        "Australia": 36, "New Zealand": 554,
        "South Africa": 710, "Nigeria": 566, "Kenya": 404, "Ethiopia": 231,
        "Sudan": 729, "Libya": 434, "Algeria": 12, "Morocco": 504,
        "DRC": 180, "Congo": 178, "Uganda": 800, "Rwanda": 646,
        "Venezuela": 862, "Colombia": 170, "Chile": 152, "Peru": 604,
        "Bolivia": 68,
    }
    ents = entities_doc.get("entities") or []
    # Count mentions for each country canonical name we recognize
    intensity: dict[int, dict] = {}
    for e in ents:
        cid = COUNTRY_ID.get((e.get("name") or "").strip())
        if cid is None:
            continue
        d = intensity.setdefault(cid, {"id": cid, "name": e["name"],
                                       "mentions": 0, "sections": 0})
        d["mentions"] += int(e.get("n_mentions") or 0)
        d["sections"] = max(d["sections"], int(e.get("n_sections") or 0))
    if not intensity:
        return None

    values = list(intensity.values())
    values.sort(key=lambda v: -v["mentions"])
    top = values[0]
    caption = (
        f"{len(values)} countries appear in today's brief. "
        f"Heaviest coverage: <strong>{top['name']}</strong> "
        f"({top['mentions']:,} mention{'s' if top['mentions'] != 1 else ''})."
    )
    spec = _vega_lite({
        "width": "container",
        "height": 360,
        "projection": {"type": "naturalEarth1"},
        "layer": [
            {  # Basemap: every country shaded as a soft fill
                "data": {
                    "url": "https://cdn.jsdelivr.net/npm/vega-datasets@2/data/world-110m.json",
                    "format": {"type": "topojson", "feature": "countries"},
                },
                "mark": {"type": "geoshape", "fill": "#F0EBE0",
                         "stroke": "#E8E2D5", "strokeWidth": 0.5},
            },
            {  # Choropleth overlay for countries with mentions
                "data": {"values": values},
                "transform": [{
                    "lookup": "id",
                    "from": {
                        "data": {
                            "url": "https://cdn.jsdelivr.net/npm/vega-datasets@2/data/world-110m.json",
                            "format": {"type": "topojson", "feature": "countries"},
                        },
                        "key": "id",
                    },
                    "as": "geo",
                }],
                "mark": {"type": "geoshape", "stroke": "#FAF8F3",
                         "strokeWidth": 0.6},
                "encoding": {
                    "shape": {"field": "geo", "type": "geojson"},
                    "color": {
                        "field": "mentions", "type": "quantitative",
                        "scale": {"scheme": "blues", "type": "sqrt"},
                        "legend": {"title": "Mentions today", "orient": "bottom-right"},
                    },
                    "tooltip": [
                        {"field": "name",     "title": "Country"},
                        {"field": "mentions", "title": "Mentions", "format": ",d"},
                        {"field": "sections", "title": "Sections", "format": "d"},
                    ],
                },
            },
        ],
    })
    return {
        "id": "world-map",
        "kicker":  "GEOGRAPHY",
        "title":   "Today's news, mapped",
        "caption": caption,
        "spec_type": "vega-lite",
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# LLM hook (no-op when ANTHROPIC_API_KEY not set)
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """You are a data-visualization editor for WORLDSCOPE,
a daily political/economic/OSINT briefing.

You will receive today's lake summary (cross-section signals, section
volumes, deltas, entity composition, regional intensity). Your job is to
propose 3 to 5 interactive Vega-Lite chart specs that best convey today's
*specific* story — not generic dashboards.

Hard constraints:

  - Output strict JSON: {"figures": [{...}, ...]} only. No prose.
  - Each figure: { "id", "kicker" (ALLCAPS short label), "title" (≤8 words),
    "caption" (≤140 chars; you may use <strong> for emphasis),
    "spec_type": "vega-lite", "spec": <complete Vega-Lite v5 spec> }.
  - Specs must reference data inline via "data": {"values": [...]} (no
    URL fetches except the official vega-datasets CDN for topojson maps).
  - Reuse the shared "config" block: assume it's set globally. Only
    override fields that diverge.
  - One chart, one story. Avoid combo dashboards.
  - Be opinionated: surface the surprising, not the predictable. If
    today's data is mundane, say so in the caption rather than padding.
"""


def _llm_figures(lake_summary: dict, api_key: str) -> list[dict] | None:
    """Ask Claude for visualization specs. Returns None on any error so the
    caller falls back to deterministic defaults."""
    try:
        import urllib.request
        body = json.dumps({
            "model": os.environ.get("WORLDSCOPE_FIGURES_MODEL", "claude-sonnet-4-6"),
            "max_tokens": 4096,
            "system": LLM_SYSTEM_PROMPT,
            "messages": [{
                "role": "user",
                "content": "Lake summary for today:\n\n```json\n"
                           + json.dumps(lake_summary, indent=2)[:18000]
                           + "\n```\n\nReturn 3-5 figures.",
            }],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
        # Concatenate text blocks
        text = "".join(b.get("text", "") for b in payload.get("content", [])
                       if b.get("type") == "text")
        # Strip code fences if present
        if "```" in text:
            parts = text.split("```")
            for p in parts:
                stripped = p.strip()
                if stripped.startswith("{") and stripped.endswith("}"):
                    text = stripped
                    break
                if stripped.startswith("json\n"):
                    text = stripped[5:]
                    break
        parsed = json.loads(text)
        figs = parsed.get("figures") or []
        return [f for f in figs if isinstance(f, dict) and f.get("spec")]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def build_figures(
    *,
    out_dir: Path,
    lake_db: Path,
    store_db: Path,
    today: _date | None = None,
    today_doc: dict | None = None,
    entities_doc: dict | None = None,
    cross_section: dict | None = None,
    use_llm: bool = True,
) -> Path:
    """Build dist/data/figures.json. Returns the path written."""
    today = today or _date.today()
    out_dir = Path(out_dir)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)

    # Hydrate from previously-exported JSON if not provided
    data_dir = out_dir / "data"
    if today_doc is None and (data_dir / "today.json").exists():
        today_doc = json.loads((data_dir / "today.json").read_text())
    if entities_doc is None and (data_dir / "entities.json").exists():
        entities_doc = json.loads((data_dir / "entities.json").read_text())
    if cross_section is None and (data_dir / "signals.json").exists():
        cross_section = json.loads((data_dir / "signals.json").read_text())

    today_doc     = today_doc or {"date": today.isoformat(), "sections": {}, "section_counts": {}}
    entities_doc  = entities_doc or {"entities": []}
    cross_section = cross_section or {"by_confidence": {}, "recurrences_found": 0}

    # Deterministic defaults
    figs: list[dict] = []
    for builder in (_fig_cross_section,):
        f = builder(cross_section)
        if f: figs.append(f)
    for builder in (_fig_section_volume, _fig_entity_types):
        f = builder(today_doc if builder is _fig_section_volume else entities_doc)
        if f: figs.append(f)
    f = _fig_section_deltas(today_doc, store_db)
    if f: figs.append(f)
    f = _fig_world_map(entities_doc, lake_db)
    if f: figs.append(f)

    generator = "deterministic"
    # LLM override hook
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if use_llm and api_key:
        summary = {
            "date": today.isoformat(),
            "section_counts": today_doc.get("section_counts"),
            "cross_section": {
                "recurrences_found": cross_section.get("recurrences_found"),
                "by_confidence": {
                    band: [{k: v for k, v in e.items()
                            if k in ("canonical_name","n_sections","total_mentions","sections","confidence")}
                           for e in (cross_section.get("by_confidence", {}).get(band) or [])[:8]]
                    for band in ("high","medium","low")
                },
            },
            "top_entities": [
                {"name": e.get("name"), "type": e.get("type"),
                 "n_sections": e.get("n_sections"), "n_mentions": e.get("n_mentions")}
                for e in (entities_doc.get("entities") or [])[:25]
            ],
        }
        llm_figs = _llm_figures(summary, api_key)
        if llm_figs:
            # Ensure the shared config is attached
            for f in llm_figs:
                if isinstance(f.get("spec"), dict):
                    f["spec"].setdefault("config", VEGA_CONFIG)
            figs = llm_figs + figs[-2:]  # LLM gets priority, plus map for richness
            generator = "mixed" if any(f["id"].startswith("llm") for f in llm_figs) else "llm"

    doc = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": generator,
        "figures": figs,
    }
    path = out_dir / "data" / "figures.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return path


def build_from_repo(repo: Path, out_dir: Path, today: _date | None = None,
                    use_llm: bool = True) -> Path:
    return build_figures(
        out_dir=Path(out_dir),
        lake_db=repo / "lake" / "db" / "worldscope.sqlite",
        store_db=repo / "data" / "store.sqlite",
        today=today,
        use_llm=use_llm,
    )
