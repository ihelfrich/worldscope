"""theater_map.py — build-time extractor that turns the lake's ukraine_theater
records into compact GeoJSON for the client-side d3 map. No matplotlib / GDAL /
fiona: a direct SQLite read, so it runs anywhere the lake file exists.

Returns a dict the board embeds verbatim:
  {points:[{lon,lat,kind,fatalities,text}], frontline:[[[lon,lat],...]],
   alerts:[oblast,...], counts:{...}, bbox:[w,s,e,n]}

kinds: 'conflict' (ACLED conflict-events, sized by fatalities) and
'fire' (FIRMS thermal). Frontline polygons come from DeepStateMap
features_json. Everything is clipped to the theater bbox.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LAKE = REPO / "lake" / "db" / "worldscope.sqlite"

# Theater bbox: west, south, east, north (matches the matplotlib overview).
BBOX = (22.0, 44.0, 40.5, 53.0)

# Oblast → (lat, lon) centroids for the air-alert layer (approximate).
OBLAST_CENTROIDS = {
    "Kyiv": (50.45, 30.52), "Kharkiv": (49.99, 36.23), "Odesa": (46.48, 30.73),
    "Dnipropetrovsk": (48.46, 35.04), "Donetsk": (48.02, 37.80),
    "Zaporizhzhia": (47.84, 35.14), "Lviv": (49.84, 24.03),
    "Mykolaiv": (46.97, 31.99), "Kherson": (46.64, 32.61), "Sumy": (50.91, 34.80),
    "Chernihiv": (51.49, 31.29), "Poltava": (49.59, 34.55), "Vinnytsia": (49.23, 28.47),
    "Luhansk": (48.57, 39.31), "Zhytomyr": (50.25, 28.66), "Cherkasy": (49.44, 32.06),
    "Rivne": (50.62, 26.25), "Ivano-Frankivsk": (48.92, 24.71), "Ternopil": (49.55, 25.59),
    "Khmelnytskyi": (49.42, 26.99), "Volyn": (50.75, 25.32), "Chernivtsi": (48.29, 25.94),
    "Kropyvnytskyi": (48.51, 32.26), "Kirovohrad": (48.51, 32.26),
}

UKRAINE_CITIES = {
    "Kyiv": (50.45, 30.52), "Kharkiv": (49.99, 36.23), "Odesa": (46.48, 30.73),
    "Dnipro": (48.46, 35.04), "Lviv": (49.84, 24.03), "Zaporizhzhia": (47.84, 35.14),
    "Donetsk": (48.02, 37.80), "Kherson": (46.64, 32.61), "Mariupol": (47.10, 37.55),
    "Bakhmut": (48.60, 38.00),
}


FRONTLINE_URL = "https://deepstatemap.live/api/history/last"


def _flt(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _in_bbox(lon, lat) -> bool:
    w, s, e, n = BBOX
    return lon is not None and lat is not None and w <= lon <= e and s <= lat <= n


def _ring_in_bbox(ring) -> bool:
    return any(_in_bbox(p[0], p[1]) for p in ring)


def fetch_frontline_live(timeout: int = 15, max_rings: int = 1200) -> list[list]:
    """Pull the current DeepStateMap frontline directly (not the lake's truncated
    copy) and return simplified outer rings clipped to the theater. Rounds to ~1km
    and decimates points to keep the embedded payload light. Degrades to [] on any
    network/parse failure so the map still renders borders + events.

    Uses stdlib urllib (no `requests`) so it works in any CI job regardless of
    which extras are installed before the renderer runs."""
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(FRONTLINE_URL,
                                     headers={"User-Agent": "worldscope-map/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[theater_map] live frontline fetch failed: {type(exc).__name__}: {exc}")
        return []
    mp = data.get("map")
    feats = (mp.get("features") if isinstance(mp, dict) else None) or data.get("features") or []
    rings: list[list] = []
    for f in feats:
        geom = (f or {}).get("geometry") or {}
        gtype, coords = geom.get("type"), geom.get("coordinates") or []
        polys = [coords] if gtype == "Polygon" else (coords if gtype == "MultiPolygon" else [])
        for poly in polys:
            outer = poly[0] if poly else []
            ring = [[round(float(p[0]), 2), round(float(p[1]), 2)]
                    for i, p in enumerate(outer) if len(p) >= 2 and i % 2 == 0]
            if len(ring) >= 4 and _ring_in_bbox(ring):
                rings.append(ring)
                if len(rings) >= max_rings:
                    return rings
    return rings


def _fetch(lake_path: Path, since_iso: str) -> list[dict]:
    if not lake_path.exists():
        return []
    out: list[dict] = []
    try:
        conn = sqlite3.connect(str(lake_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT extra_json FROM records WHERE section_id='ukraine_theater' "
                "AND ingested_at >= ?", (since_iso,))
            for row in rows:
                try:
                    out.append(json.loads(row["extra_json"]) if row["extra_json"] else {})
                except Exception:
                    continue
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"[theater_map] lake read failed: {exc}")
    return out


def theater_geojson(date_iso: str | None = None, *, lake_path: Path | None = None,
                    days_back: int = 3, max_fires: int = 1200,
                    live_frontline: bool = True) -> dict:
    lake_path = lake_path or DEFAULT_LAKE
    date_iso = date_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    since = (d - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")

    recs = _fetch(lake_path, since)
    conflict, fires, frontline, alerts = [], [], [], set()

    for r in recs:
        kind = r.get("source_kind")
        if kind == "conflict-events":
            lon, lat = _flt(r.get("longitude")), _flt(r.get("latitude"))
            if _in_bbox(lon, lat):
                conflict.append({
                    "lon": round(lon, 3), "lat": round(lat, 3), "kind": "conflict",
                    "fatalities": int(_flt(r.get("fatalities")) or 0),
                    "text": (r.get("_original_text") or r.get("notes") or "")[:140],
                })
        elif kind == "thermal":
            lon, lat = _flt(r.get("longitude")), _flt(r.get("latitude"))
            if _in_bbox(lon, lat):
                fires.append({"lon": round(lon, 3), "lat": round(lat, 3), "kind": "fire"})
        elif kind == "air-alert":
            ob = (r.get("oblast") or "").replace("’", "'").strip()
            if ob:
                alerts.add(ob)
        elif kind == "frontline":
            fj = r.get("features_json") or ""
            if not fj:
                continue
            try:
                feats = json.loads(fj)
            except json.JSONDecodeError:
                continue
            for feat in feats[:80]:
                geom = (feat or {}).get("geometry") or {}
                if geom.get("type") != "Polygon":
                    continue
                ring = (geom.get("coordinates") or [[]])[0]
                # decimate long rings; round to keep payload small
                pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                       for i, p in enumerate(ring) if len(p) >= 2 and i % 2 == 0]
                if len(pts) >= 4:
                    frontline.append(pts)

    # Prefer the live DeepStateMap frontline (full geometry) over the lake's
    # truncated copy; fall back to whatever survived ingestion.
    if live_frontline:
        live = fetch_frontline_live()
        if live:
            frontline = live

    # cap fire points (FIRMS can be thousands) — keep the densest by simple stride
    if len(fires) > max_fires:
        stride = len(fires) // max_fires + 1
        fires = fires[::stride]

    # alert markers → centroids
    alert_pts = []
    for ob in sorted(alerts):
        for key, (lat, lon) in OBLAST_CENTROIDS.items():
            if key.lower() in ob.lower() or ob.lower() in key.lower():
                alert_pts.append({"name": key, "lon": lon, "lat": lat})
                break

    cities = [{"name": n, "lon": lon, "lat": lat} for n, (lat, lon) in UKRAINE_CITIES.items()]

    return {
        "points": conflict + fires,
        "frontline": frontline,
        "alerts": alert_pts,
        "cities": cities,
        "bbox": list(BBOX),
        "counts": {"conflict": len(conflict), "fire": len(fires),
                   "frontline": len(frontline), "alerts": len(alert_pts)},
        "date": date_iso,
    }


if __name__ == "__main__":
    import sys
    g = theater_geojson(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(g["counts"]), "bbox", g["bbox"])
