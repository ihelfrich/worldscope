"""
watchareas.py — user-configurable intelligence watch areas.

Every WORLDSCOPE section, every adapter, every spider passes its candidate
items through `match_item()`. The matcher tags each item with the watch
areas it falls into (by topic, theme, location name, bounding box, keyword,
entity, or actor). The routine prompt reads the resulting tags and surfaces
matched items in a dedicated "Watch areas" section at the top of the brief.

Config schema (watchareas.yaml at repo root):

  - name: "Russia oil sanctions perimeter"
    priority: high            # high | normal | low
    topics: [sanctions, oil, energy]
    themes: [ECON_SANCTIONS, ENV_OIL_GAS]   # GDELT GKG themes
    keywords:                 # substring match on title/summary (lowercase)
      - sberbank
      - rosneft
      - lukoil
      - gazprom
      - shadow fleet
      - g7 price cap
    entities:                 # exact-match against item entity lists
      - Q649       # Moscow (wikidata)
      - Q159       # Russia
    countries: [Russia, Belarus]
    actors: [Rosneft, Lukoil]
    bbox: [36.0, 46.0, 60.0, 60.0]   # west,south,east,north  (or null)
    locations:                # named locations (case-insensitive substring)
      - Novorossiysk
      - Primorsk
      - Ust-Luga
    sources: [acled, sanctions, gdelt_gkg, mediacloud, firms, markets]
    alert:                    # surface in headline if any fire
      min_items: 3
      min_fatalities: 5
      anomaly_zscore: 2.0

Matcher returns a list of watch-area names per item plus a global rollup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # graceful fallback for envs without PyYAML
    yaml = None  # type: ignore

DEFAULT_PATH = Path(__file__).parent.parent.parent / "watchareas.yaml"


@dataclass
class WatchArea:
    name: str
    priority: str = "normal"
    topics: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    bbox: list[float] | None = None
    locations: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    alert: dict = field(default_factory=dict)

    def matches(self, item: dict, source_id: str = "") -> bool:
        # Source filter: if `sources` is set, item must come from one of them
        if self.sources and source_id and source_id not in self.sources:
            return False
        # Country match
        ic = (item.get("country") or "").lower()
        if self.countries:
            if any(c.lower() in ic or ic in c.lower() for c in self.countries if c):
                return True
        # Actor match
        ia = (item.get("actors") or "") + " " + (item.get("actor1") or "") + " " + (item.get("actor2") or "")
        ia = ia.lower()
        if self.actors and any(a.lower() in ia for a in self.actors if a):
            return True
        # Theme match (GDELT GKG)
        item_themes = set(t.upper() for t in (item.get("themes") or []))
        if self.themes and item_themes & set(t.upper() for t in self.themes):
            return True
        # Entity match
        item_entities = set(item.get("entities") or [])
        if self.entities and item_entities & set(self.entities):
            return True
        # Bounding box on lat/lon
        if self.bbox and len(self.bbox) == 4:
            try:
                lat = float(item.get("latitude"))
                lon = float(item.get("longitude"))
                w, s, e, n = self.bbox
                if w <= lon <= e and s <= lat <= n:
                    return True
            except (TypeError, ValueError):
                pass
        # Location-name match (case-insensitive substring on title/summary/location)
        haystack = " ".join(str(item.get(k, "")) for k in ("title", "summary", "location", "place"))
        haystack_l = haystack.lower()
        if self.locations and any(loc.lower() in haystack_l for loc in self.locations if loc):
            return True
        # Keyword match (substring on title/summary)
        if self.keywords and any(kw.lower() in haystack_l for kw in self.keywords if kw):
            return True
        # Topic match — items can carry an explicit `topics` list
        item_topics = set(t.lower() for t in (item.get("topics") or []))
        if self.topics and item_topics & set(t.lower() for t in self.topics):
            return True
        return False


def load_watch_areas(path: Path | str | None = None) -> list[WatchArea]:
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists() or yaml is None:
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if isinstance(raw, dict):
        raw = raw.get("watch_areas", [])
    areas: list[WatchArea] = []
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        areas.append(WatchArea(**{k: v for k, v in entry.items() if k in WatchArea.__dataclass_fields__}))
    return areas


def match_item(item: dict, areas: Iterable[WatchArea], source_id: str = "") -> list[str]:
    """Return the list of watch-area names this item falls into."""
    return [a.name for a in areas if a.matches(item, source_id=source_id)]


def tag_items(items: list[dict], areas: Iterable[WatchArea], source_id: str = "") -> list[dict]:
    """Mutate items in place by adding a `watch_areas` list. Returns items."""
    areas = list(areas)
    for it in items:
        tags = match_item(it, areas, source_id=source_id)
        if tags:
            existing = it.get("watch_areas") or []
            it["watch_areas"] = list(dict.fromkeys(existing + tags))
    return items


def rollup(items: list[dict], areas: list[WatchArea]) -> dict[str, dict[str, Any]]:
    """Aggregate per watch-area: count, sample titles, top countries, fatalities sum."""
    out: dict[str, dict[str, Any]] = {a.name: {
        "priority": a.priority,
        "count": 0,
        "fatalities": 0,
        "countries": {},
        "sources": {},
        "sample_titles": [],
        "alert_fired": False,
        "alert_reasons": [],
    } for a in areas}
    by_name = {a.name: a for a in areas}
    for it in items:
        for name in it.get("watch_areas") or []:
            if name not in out:
                continue
            row = out[name]
            row["count"] += 1
            try:
                row["fatalities"] += int(it.get("fatalities") or 0)
            except (TypeError, ValueError):
                pass
            c = it.get("country") or ""
            if c:
                row["countries"][c] = row["countries"].get(c, 0) + 1
            s = it.get("_source") or ""
            if s:
                row["sources"][s] = row["sources"].get(s, 0) + 1
            if len(row["sample_titles"]) < 5:
                row["sample_titles"].append(it.get("title", "")[:200])
    # Alert evaluation
    for name, row in out.items():
        a = by_name.get(name)
        if not a or not a.alert:
            continue
        if (mi := a.alert.get("min_items")) and row["count"] >= mi:
            row["alert_fired"] = True
            row["alert_reasons"].append(f"count {row['count']} >= {mi}")
        if (mf := a.alert.get("min_fatalities")) and row["fatalities"] >= mf:
            row["alert_fired"] = True
            row["alert_reasons"].append(f"fatalities {row['fatalities']} >= {mf}")
    return out
