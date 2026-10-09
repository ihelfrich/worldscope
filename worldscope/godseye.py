"""worldscope.godseye — build-time generator for the static "god's-eye" live map.

Reads geo-bearing records from the lake (last --days days), optionally adds a
handful of free, keyless live feeds, and writes static GeoJSON layers plus a
self-contained Leaflet page under <out>/ (normally dist/live/). Nothing here
runs in the browser; the page only fetches files published next to it.

    python -m worldscope.godseye --out dist/live [--days 7] [--lake lake] [--no-network]
"""
from __future__ import annotations

import argparse
import email.utils
import html as _html
import json
import logging
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

import requests
import yaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "templates" / "godseye.html"
DEFAULT_LAKE = REPO / "lake"
DEFAULT_WATCH = REPO / "watchareas.yaml"

TIMEOUT = 15
HEADERS = {"User-Agent": "worldscope-godseye/1.0 (+https://ihelfrich.github.io/worldscope/)",
           "Accept": "application/json, application/geo+json, application/xml;q=0.9, */*;q=0.8"}

LAYERS = ("firms", "quakes", "gdacs", "conflict", "ukraine", "flights",
          "weather_alerts", "watch_areas")

FIRMS_CAP_WATCH = 5000
FIRMS_CAP_GLOBAL = 1000
SUMMARY_MAX = 240

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
GDACS_RSS_URL = "https://www.gdacs.org/xml/rss.xml"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?days=3&status=open"
GDELT_GEO_URL = "https://api.gdeltproject.org/api/v2/geo/geo"
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?status=actual&severity=Extreme,Severe"

GDELT_QUERIES = ("hezbollah", "taiwan strait", "sahel", "rare earth", "shadow fleet",
                 "houthi", "north korea missile", "south china sea")

SEVERITY_NOTES = {
    "firms": "FRP (MW): <5 → 2, <20 → 4, <50 → 6, <150 → 8, else 10; low confidence −1. EONET wildfire events fixed at 5.",
    "quakes": "Magnitude: <4.5 → 2, <5 → 4, <5.5 → 5, <6 → 6, <6.5 → 7, <7 → 8, <7.5 → 9, else 10; +1 if tsunami flag or PAGER orange/red (capped at 10).",
    "gdacs": "GDACS alert level: Green → 3, Orange → 6, Red → 9. EONET open events: 5 (volcanoes/severe storms 6).",
    "conflict": "ACLED fatalities: 0 → 3, 1–4 → 5, 5–9 → 6, 10–24 → 7, 25–49 → 8, 50+ → 9. GDELT GEO article count: log2 scale 2–8.",
    "ukraine": "FIRMS thermal as firms layer; ACLED conflict-events as conflict layer; air alerts 6; frontline polygons 5.",
    "flights": "Tracked aircraft: 3 baseline, +2 if inside a watch area, +1 if airborne above 9 km (cap 7).",
    "weather_alerts": "NWS severity: Extreme → 9, Severe → 7, Moderate → 5, Minor → 3, Unknown → 2.",
    "watch_areas": "Watch-area priority: high → 7, normal → 5, low → 3.",
}

log = logging.getLogger("godseye")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _flt(x) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _lonlat(d: dict) -> Optional[tuple[float, float]]:
    """Return (lon, lat) from the many spellings adapters use, or None."""
    if not isinstance(d, dict):
        return None
    lat = _flt(d.get("lat", d.get("latitude")))
    lon = _flt(d.get("lon", d.get("longitude", d.get("lng"))))
    if lat is None or lon is None:
        coords = d.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            lon, lat = _flt(coords[0]), _flt(coords[1])
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lon, lat


def _clamp_sev(v) -> int:
    try:
        return max(1, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return 1


def _clean_text(s) -> str:
    if s is None:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _summary(s) -> str:
    s = _clean_text(s)
    return s if len(s) <= SUMMARY_MAX else s[:SUMMARY_MAX - 1].rstrip() + "…"


def _http_url(u) -> str:
    u = str(u or "").strip()
    return u if u.startswith(("http://", "https://")) else ""


def _parse_dt(s) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return (s if s.tzinfo else s.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if isinstance(s, (int, float)):
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(s).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            m = re.match(r"(\d{4}-\d{2}-\d{2})[T ]?(\d{2})?:?(\d{2})?", s)
            if not m:
                return None
            try:
                dt = datetime.fromisoformat(m.group(1))
            except ValueError:
                return None
            dt = dt.replace(hour=int(m.group(2) or 12), minute=int(m.group(3) or 0))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _record_time(rec: dict, explicit=None) -> tuple[str, int]:
    """(date ISO, unix ts). Explicit timestamps win; else a record dated the
    same day it was ingested takes the ingest time, otherwise noon UTC of its
    record_date."""
    dt = _parse_dt(explicit) if explicit is not None else None
    if dt is None:
        rd = _parse_dt(rec.get("record_date") or rec.get("date"))
        ing = _parse_dt(rec.get("ingested_at_utc") or rec.get("ingested_at"))
        if rd is not None and ing is not None and rd.date() == ing.date():
            dt = ing
        elif rd is not None:
            dt = rd
        elif ing is not None:
            dt = ing
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), int(dt.timestamp())


def _feature(lonlat, *, title, summary, date_iso, ts, severity, source, url, layer,
             geometry=None, **extra) -> Optional[dict]:
    if geometry is None:
        if lonlat is None:
            return None
        geometry = {"type": "Point", "coordinates": [round(lonlat[0], 5), round(lonlat[1], 5)]}
    title = _clean_text(title)[:200] or "(untitled)"
    props = {
        "title": title,
        "summary": _summary(summary),
        "date": date_iso,
        "ts": int(ts),
        "severity": _clamp_sev(severity),
        "source": str(source),
        "url": _http_url(url),
        "layer": layer,
    }
    for k, v in extra.items():
        if v is not None:
            props[k] = v
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _feature_key(f: dict) -> tuple:
    g = f.get("geometry") or {}
    p = f.get("properties") or {}
    if g.get("type") == "Point":
        lon, lat = g["coordinates"][:2]
        loc = (round(float(lat), 4), round(float(lon), 4))
    else:
        loc = (g.get("type"), json.dumps(g.get("coordinates"))[:200])
    return loc + (p.get("date", "")[:10], p.get("title", ""))


def dedupe(features: list[dict]) -> list[dict]:
    seen: set = set()
    out = []
    for f in features:
        if f is None:
            continue
        k = _feature_key(f)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def _in_bbox(lon: float, lat: float, bbox) -> bool:
    try:
        w, s, e, n = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return False
    return s <= lat <= n and w <= lon <= e


# ---------------------------------------------------------------------------
# lake access
# ---------------------------------------------------------------------------

def iter_lake_records(lake: Path, section: str, days: int, today: date) -> Iterator[dict]:
    base = Path(lake) / "sections" / section
    if not base.is_dir():
        return
    cutoff = today - timedelta(days=days)
    for d in sorted(base.iterdir()):
        try:
            dd = date.fromisoformat(d.name)
        except ValueError:
            continue
        if dd < cutoff:
            continue
        raw = d / "raw.jsonl"
        if not raw.is_file():
            continue
        try:
            text = raw.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def _latest_meta_file(lake: Path, name: str) -> Optional[Path]:
    base = Path(lake) / "sections" / "_meta"
    if not base.is_dir():
        return None
    for d in sorted(base.iterdir(), reverse=True):
        p = d / name
        if p.is_file():
            return p
    return None


def load_watch_areas(path: Path = DEFAULT_WATCH) -> list[dict]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("watchareas.yaml unreadable: %s", exc)
        return []
    areas = data.get("watch_areas") if isinstance(data, dict) else data
    return [a for a in (areas or []) if isinstance(a, dict) and a.get("name")]


# ---------------------------------------------------------------------------
# build context
# ---------------------------------------------------------------------------

class Build:
    def __init__(self, *, lake: Path, out: Path, days: int, network: bool,
                 watch_path: Path = DEFAULT_WATCH, now: Optional[datetime] = None):
        self.lake = Path(lake)
        self.out = Path(out)
        self.days = max(1, int(days))
        self.network = bool(network)
        self.now = now or datetime.now(timezone.utc)
        self.today = self.now.date()
        self.watch_areas = load_watch_areas(watch_path)
        self.bboxes = [a["bbox"] for a in self.watch_areas
                       if isinstance(a.get("bbox"), (list, tuple)) and len(a["bbox"]) == 4]
        self.inputs: list[dict] = []
        self.layers: dict[str, list[dict]] = {}

    # -- bookkeeping -------------------------------------------------------
    def record_input(self, name: str, layer: str, *, ok: bool, count: int = 0,
                     error: Optional[str] = None, url: Optional[str] = None) -> None:
        entry = {"name": name, "layer": layer, "ok": bool(ok), "count": int(count),
                 "error": (str(error)[:200] if error else None)}
        if url:
            entry["url"] = url
        self.inputs.append(entry)
        (log.info if ok else log.warning)("input %-24s layer=%-14s ok=%s count=%d %s",
                                          name, layer, ok, count, error or "")

    def run_input(self, name: str, layer: str, fn: Callable[[], list[dict]],
                  *, url: Optional[str] = None) -> list[dict]:
        try:
            feats = [f for f in fn() if f is not None]
        except Exception as exc:  # any failure only logs
            self.record_input(name, layer, ok=False, error=f"{type(exc).__name__}: {exc}", url=url)
            return []
        self.record_input(name, layer, ok=True, count=len(feats), url=url)
        return feats

    def in_watch(self, lon: float, lat: float) -> bool:
        return any(_in_bbox(lon, lat, b) for b in self.bboxes)

    def records(self, section: str) -> Iterator[dict]:
        return iter_lake_records(self.lake, section, self.days, self.today)

    def get(self, url: str, **params) -> requests.Response:
        resp = requests.get(url, params=params or None, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp

    # -- lake extractors ---------------------------------------------------
    def lake_firms(self) -> list[dict]:
        out = []
        for rec in self.records("firms"):
            ex = rec.get("extra") or {}
            ll = _lonlat(ex)
            if ll is None:
                continue
            frp = _flt(ex.get("frp")) or 0.0
            conf = str(ex.get("confidence") or "").lower()
            sev = _frp_severity(frp) - (1 if conf in ("l", "low") else 0)
            d, ts = _record_time(rec, ex.get("acq_datetime"))
            out.append(_feature(ll, title=rec.get("original_text", "").split(" — ")[0] or f"Thermal anomaly {ll[1]:.2f}, {ll[0]:.2f}",
                                summary=rec.get("original_text"), date_iso=d, ts=ts, severity=sev,
                                source="firms", url=rec.get("original_url"), layer="firms",
                                frp=round(frp, 2), confidence=conf or None, zone=ex.get("zone")))
        return out

    def lake_quakes(self) -> list[dict]:
        out, seen = [], set()
        for rec in self.records("usgs_quakes"):
            ex = rec.get("extra") or {}
            ll = _lonlat(ex)
            if ll is None:
                continue
            qid = str(rec.get("id") or "")
            seen.add(qid)
            mag = _flt(ex.get("mag"))
            d, ts = _record_time(rec, ex.get("time"))
            out.append(_feature(ll, title=rec.get("original_text", "").split(" — ")[0] or ex.get("place"),
                                summary=rec.get("original_text"), date_iso=d, ts=ts,
                                severity=_quake_severity(mag, ex.get("tsunami"), ex.get("alert")),
                                source="usgs_quakes", url=rec.get("original_url"), layer="quakes",
                                mag=mag, depth_km=_flt(ex.get("depth_km")), event_id=qid or None))
        for rec in self.records("weather"):
            ex = rec.get("extra") or {}
            if ex.get("subsection") != "earthquake":
                continue
            qid = str(rec.get("id") or "").replace("earthquake-", "", 1)
            if qid in seen:
                continue
            ll = _lonlat(ex)
            if ll is None:
                continue
            seen.add(qid)
            mag = _flt(ex.get("magnitude"))
            coords = ex.get("coordinates") or []
            d, ts = _record_time(rec)
            out.append(_feature(ll, title=rec.get("original_text", "").split(" — ")[0] or ex.get("place"),
                                summary=rec.get("original_text"), date_iso=d, ts=ts,
                                severity=_quake_severity(mag, ex.get("tsunami"), ex.get("alert_level")),
                                source="weather", url=rec.get("original_url"), layer="quakes",
                                mag=mag, depth_km=_flt(coords[2]) if len(coords) > 2 else None,
                                event_id=qid or None))
        return out

    def lake_gdacs(self) -> list[dict]:
        out = []
        for rec in self.records("gdacs"):
            ex = rec.get("extra") or {}
            ll = _lonlat(ex)
            if ll is None:
                continue
            d, ts = _record_time(rec)
            level = str(ex.get("alert_level") or "")
            out.append(_feature(ll, title=rec.get("original_text", "").split(" — ")[0],
                                summary=rec.get("original_text"), date_iso=d, ts=ts,
                                severity=_gdacs_severity(level), source="gdacs",
                                url=rec.get("original_url"), layer="gdacs",
                                alert_level=level or None, event_type=ex.get("event_type"),
                                country=ex.get("country")))
        return out

    def lake_acled(self) -> list[dict]:
        out = []
        for rec in self.records("acled"):
            ex = rec.get("extra") or {}
            ll = _lonlat(ex)
            if ll is None:
                continue
            fat = int(_flt(ex.get("fatalities")) or 0)
            d, ts = _record_time(rec)
            out.append(_feature(ll, title=rec.get("original_text", "").split(" — ")[0],
                                summary=rec.get("original_text"), date_iso=d, ts=ts,
                                severity=_fatality_severity(fat), source="acled",
                                url=rec.get("original_url"), layer="conflict",
                                fatalities=fat, event_type=ex.get("event_type"),
                                country=ex.get("country")))
        return out

    def lake_ukraine(self) -> list[dict]:
        from .theater_map import OBLAST_CENTROIDS
        out = []
        for rec in self.records("ukraine_theater"):
            ex = rec.get("extra") or {}
            kind = str(ex.get("source_kind") or "")
            src = str(rec.get("source_id") or "ukraine_theater")
            text = rec.get("original_text") or ""
            title = text.split(" — ")[0]
            if kind == "thermal":
                ll = _lonlat(ex)
                if ll is None:
                    continue
                frp = _flt(ex.get("frp")) or 0.0
                conf = str(ex.get("confidence") or "").lower()
                m = re.search(r"acquired (\d{4}-\d{2}-\d{2}) (\d{2})(\d{2})Z", text)
                explicit = f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00Z" if m else None
                d, ts = _record_time(rec, explicit)
                out.append(_feature(ll, title=title, summary=text, date_iso=d, ts=ts,
                                    severity=_frp_severity(frp) - (1 if conf in ("l", "low") else 0),
                                    source=src, url=rec.get("original_url"), layer="ukraine",
                                    kind="thermal", frp=round(frp, 2)))
            elif kind in ("conflict-events", "conflict"):
                ll = _lonlat(ex)
                if ll is None:
                    continue
                fat = int(_flt(ex.get("fatalities")) or 0)
                d, ts = _record_time(rec)
                out.append(_feature(ll, title=title, summary=text, date_iso=d, ts=ts,
                                    severity=_fatality_severity(fat), source=src,
                                    url=rec.get("original_url"), layer="ukraine",
                                    kind="conflict", fatalities=fat))
            elif kind == "air-alert":
                if ex.get("_error"):
                    continue
                ob = str(ex.get("oblast") or "")
                key = next((k for k in OBLAST_CENTROIDS if k.lower() in ob.lower()), None)
                if key is None:
                    continue
                lat, lon = OBLAST_CENTROIDS[key]
                d, ts = _record_time(rec, ex.get("started_at"))
                out.append(_feature((lon, lat), title=title, summary=text, date_iso=d, ts=ts,
                                    severity=6, source=src, url=rec.get("original_url"),
                                    layer="ukraine", kind="air-alert", oblast=key))
            elif kind == "frontline":
                d, ts = _record_time(rec)
                for ring in _salvage_rings(ex.get("features_json") or ""):
                    out.append(_feature(None, title="DeepStateMap frontline", summary=text,
                                        date_iso=d, ts=ts, severity=5, source=src,
                                        url=rec.get("original_url"), layer="ukraine", kind="frontline",
                                        geometry={"type": "Polygon", "coordinates": [ring]}))
        return out

    def lake_flights(self) -> list[dict]:
        latest: dict[str, dict] = {}
        for rec in self.records("vip_flights"):
            ex = rec.get("extra") or {}
            ll = _lonlat(ex)
            if ll is None:
                continue
            wa = ex.get("watch_areas") or []
            alt = _flt(ex.get("altitude_m")) or 0.0
            sev = 3 + (2 if wa else 0) + (1 if alt > 9000 else 0)
            d, ts = _record_time(rec)
            f = _feature(ll, title=rec.get("original_text", "").split(" — ")[0],
                         summary=rec.get("original_text"), date_iso=d, ts=ts, severity=min(sev, 7),
                         source="vip_flights", url=rec.get("original_url"), layer="flights",
                         callsign=ex.get("callsign"), country=ex.get("country"),
                         altitude_m=alt, watch_areas=wa or None)
            key = str(ex.get("icao24") or rec.get("id") or "")
            if f and (key not in latest or latest[key]["properties"]["ts"] <= ts):
                latest[key] = f
        return list(latest.values())

    def lake_weather_alerts(self) -> list[dict]:
        out = []
        for rec in self.records("weather"):
            ex = rec.get("extra") or {}
            if ex.get("subsection") != "active_alert":
                continue
            ll = _lonlat(ex) or _lonlat(ex.get("geometry") or {})
            if ll is None:
                continue
            d, ts = _record_time(rec, ex.get("sent") or ex.get("effective"))
            out.append(_feature(ll, title=rec.get("original_text", "").split(" — ")[0],
                                summary=rec.get("original_text"), date_iso=d, ts=ts,
                                severity=_nws_severity(ex.get("severity")), source="weather",
                                url=rec.get("original_url"), layer="weather_alerts",
                                event=ex.get("event"), nws_severity=ex.get("severity")))
        return out

    def watch_area_polygons(self) -> list[dict]:
        out = []
        d, ts = self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), int(self.now.timestamp())
        pr_sev = {"high": 7, "normal": 5, "low": 3}
        for a in self.watch_areas:
            bbox = a.get("bbox")
            if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                continue
            try:
                w, s, e, n = (float(v) for v in bbox)
            except (TypeError, ValueError):
                continue
            ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
            prio = str(a.get("priority") or "normal").lower()
            kws = ", ".join(str(k) for k in (a.get("keywords") or [])[:12])
            out.append(_feature(None, title=a["name"], summary=f"{prio} priority · {kws}",
                                date_iso=d, ts=ts, severity=pr_sev.get(prio, 5),
                                source="watchareas", url="", layer="watch_areas",
                                geometry={"type": "Polygon", "coordinates": [ring]},
                                name=a["name"], priority=prio, bbox=[w, s, e, n]))
        return out

    # -- live feeds --------------------------------------------------------
    def live_usgs(self) -> list[dict]:
        data = self.get(USGS_URL).json()
        out = []
        for f in data.get("features") or []:
            geom = f.get("geometry") or {}
            ll = _lonlat({"coordinates": geom.get("coordinates")})
            p = f.get("properties") or {}
            if ll is None:
                continue
            d, ts = _record_time({}, p.get("time"))
            mag = _flt(p.get("mag"))
            coords = geom.get("coordinates") or []
            out.append(_feature(ll, title=p.get("title") or p.get("place"),
                                summary=f"M{mag} · {p.get('place', '')} · depth {coords[2] if len(coords) > 2 else '?'} km",
                                date_iso=d, ts=ts, severity=_quake_severity(mag, p.get("tsunami"), p.get("alert")),
                                source="usgs_live", url=p.get("url"), layer="quakes",
                                mag=mag, depth_km=_flt(coords[2]) if len(coords) > 2 else None,
                                event_id=f.get("id")))
        return out

    def live_gdacs(self) -> list[dict]:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.get(GDACS_RSS_URL).content)
        out = []
        for item in root.iter():
            if _local(item.tag) != "item":
                continue
            fields: dict[str, str] = {}
            for el in item.iter():
                name = _local(el.tag)
                if el.text and el.text.strip() and name not in fields:
                    fields[name] = el.text.strip()
            ll = _lonlat({"lat": fields.get("lat"), "lon": fields.get("long")})
            if ll is None:
                continue
            level = fields.get("alertlevel") or fields.get("episodealertlevel") or ""
            d, ts = _record_time({}, fields.get("fromdate") or fields.get("pubDate"))
            out.append(_feature(ll, title=fields.get("title"), summary=fields.get("description"),
                                date_iso=d, ts=ts, severity=_gdacs_severity(level),
                                source="gdacs_live", url=fields.get("link"), layer="gdacs",
                                alert_level=level or None, event_type=fields.get("eventtype"),
                                country=fields.get("country")))
        return out

    def live_eonet(self) -> tuple[list[dict], list[dict]]:
        data = self.get(EONET_URL).json()
        fires, hazards = [], []
        for ev in data.get("events") or []:
            cats = [c.get("title") or c.get("id") or "" for c in (ev.get("categories") or [])]
            cat = (cats[0] if cats else "").lower()
            geoms = ev.get("geometry") or []
            if not geoms:
                continue
            g = geoms[-1]
            ll = _lonlat({"coordinates": g.get("coordinates")}) if g.get("type") == "Point" else None
            if ll is None:
                continue
            d, ts = _record_time({}, g.get("date"))
            srcs = ev.get("sources") or []
            url = _http_url(srcs[0].get("url")) if srcs else ""
            url = url or _http_url(ev.get("link"))
            is_fire = "wildfire" in cat
            sev = 5 if is_fire else (6 if ("volcano" in cat or "storm" in cat) else 5)
            f = _feature(ll, title=ev.get("title"), summary=f"{', '.join(cats)} · NASA EONET open event",
                         date_iso=d, ts=ts, severity=sev, source="eonet", url=url,
                         layer="firms" if is_fire else "gdacs", event_type=cats[0] if cats else None)
            (fires if is_fire else hazards).append(f)
        return fires, hazards

    def live_gdelt_geo(self, query: str) -> list[dict]:
        q = f'"{query}"' if " " in query else query
        resp = self.get(GDELT_GEO_URL, query=q, format="geojson", timespan="24h", maxpoints=250)
        data = resp.json()
        out = []
        d, ts = _record_time({}, self.now)
        for f in data.get("features") or []:
            geom = f.get("geometry") or {}
            ll = _lonlat({"coordinates": geom.get("coordinates")})
            if ll is None:
                continue
            p = f.get("properties") or {}
            count = int(_flt(p.get("count")) or 1)
            html_blob = str(p.get("html") or "")
            m = re.search(r'href=["\']([^"\']+)', html_blob)
            first_title = _clean_text(re.sub(r"<br\s*/?>", " | ", html_blob)).split(" | ")[0]
            out.append(_feature(ll, title=f"{p.get('name') or 'location'}: {count} article(s) on “{query}”",
                                summary=first_title or f"GDELT GEO 2.0 · last 24h · query {query}",
                                date_iso=d, ts=ts, severity=_count_severity(count), source="gdelt_geo",
                                url=m.group(1) if m else "", layer="conflict", query=query, count=count))
        return out

    def live_nws(self) -> list[dict]:
        data = self.get(NWS_ALERTS_URL).json()
        out = []
        for f in data.get("features") or []:
            geom = f.get("geometry") or {}
            p = f.get("properties") or {}
            ll = _centroid(geom)
            if ll is None:
                continue
            d, ts = _record_time({}, p.get("sent") or p.get("effective"))
            out.append(_feature(ll, title=p.get("headline") or p.get("event"),
                                summary=p.get("description") or p.get("areaDesc"),
                                date_iso=d, ts=ts, severity=_nws_severity(p.get("severity")),
                                source="nws_live", url=p.get("@id") or p.get("id"),
                                layer="weather_alerts", event=p.get("event"),
                                nws_severity=p.get("severity"), area=_summary(p.get("areaDesc"))))
        return out

    def live_frontline(self) -> list[dict]:
        from .theater_map import fetch_frontline_live
        d, ts = _record_time({}, self.now)
        out = []
        for ring in fetch_frontline_live(timeout=TIMEOUT):
            pts = [[float(p[0]), float(p[1])] for p in ring if len(p) >= 2]
            if len(pts) < 4:
                continue
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            out.append(_feature(None, title="DeepStateMap frontline", summary="Live DeepStateMap frontline polygon",
                                date_iso=d, ts=ts, severity=5, source="deepstatemap_live",
                                url="https://deepstatemap.live/", layer="ukraine", kind="frontline",
                                geometry={"type": "Polygon", "coordinates": [pts]}))
        return out

    # -- orchestration -----------------------------------------------------
    def build_layers(self) -> dict[str, list[dict]]:
        L: dict[str, list[dict]] = {k: [] for k in LAYERS}
        L["firms"] += self.run_input("lake:firms", "firms", self.lake_firms)
        L["quakes"] += self.run_input("lake:usgs_quakes+weather", "quakes", self.lake_quakes)
        L["gdacs"] += self.run_input("lake:gdacs", "gdacs", self.lake_gdacs)
        L["conflict"] += self.run_input("lake:acled", "conflict", self.lake_acled)
        L["ukraine"] += self.run_input("lake:ukraine_theater", "ukraine", self.lake_ukraine)
        L["flights"] += self.run_input("lake:vip_flights", "flights", self.lake_flights)
        L["weather_alerts"] += self.run_input("lake:weather", "weather_alerts", self.lake_weather_alerts)
        L["watch_areas"] += self.run_input("watchareas.yaml", "watch_areas", self.watch_area_polygons)

        if self.network:
            L["quakes"] += self.run_input("live:usgs", "quakes", self.live_usgs, url=USGS_URL)
            L["gdacs"] += self.run_input("live:gdacs_rss", "gdacs", self.live_gdacs, url=GDACS_RSS_URL)
            eonet: dict[str, list[dict]] = {}

            def _eonet():
                fires, hazards = self.live_eonet()
                eonet["fires"], eonet["hazards"] = fires, hazards
                return fires + hazards
            self.run_input("live:eonet", "firms+gdacs", _eonet, url=EONET_URL)
            L["firms"] += eonet.get("fires", [])
            L["gdacs"] += eonet.get("hazards", [])
            for q in GDELT_QUERIES:
                L["conflict"] += self.run_input(f"live:gdelt_geo:{q}", "conflict",
                                                lambda q=q: self.live_gdelt_geo(q), url=GDELT_GEO_URL)
            L["weather_alerts"] += self.run_input("live:nws_alerts", "weather_alerts", self.live_nws,
                                                  url=NWS_ALERTS_URL)
            L["ukraine"] += self.run_input("live:deepstatemap", "ukraine", self.live_frontline)

        for k in LAYERS:
            L[k] = dedupe(L[k])
        if self.network:
            L["ukraine"] = _prefer_live_frontline(L["ukraine"])
        L["firms"] = self.cap_firms(L["firms"])
        self.layers = L
        return L

    def cap_firms(self, feats: list[dict]) -> list[dict]:
        def frp(f):
            return float((f.get("properties") or {}).get("frp") or 0.0)
        inside, outside = [], []
        for f in feats:
            lon, lat = f["geometry"]["coordinates"][:2]
            (inside if self.in_watch(lon, lat) else outside).append(f)
        inside.sort(key=frp, reverse=True)
        chosen = inside[:FIRMS_CAP_WATCH]
        chosen_ids = {id(f) for f in chosen}
        everything = sorted(feats, key=frp, reverse=True)
        extra = [f for f in everything if id(f) not in chosen_ids][:FIRMS_CAP_GLOBAL]
        return chosen + extra

    def signals(self) -> dict:
        out: dict = {"generated_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": None,
                     "entities": [], "stories": [], "watch_area_counts": {}, "latest_brief": None}
        cs = _latest_meta_file(self.lake, "cross_section.json")
        if cs:
            try:
                data = json.loads(cs.read_text(encoding="utf-8"))
                out["day"] = data.get("day")
                byc = data.get("by_confidence") or {}
                ents = []
                for conf in ("high", "medium"):
                    for e in byc.get(conf) or []:
                        if not isinstance(e, dict) or not e.get("canonical_name"):
                            continue
                        secs = e.get("section_counts") or {}
                        ents.append({"name": _clean_text(e.get("canonical_name"))[:80],
                                     "type": e.get("entity_type"),
                                     "confidence": conf,
                                     "sections": list(e.get("sections") or secs.keys()),
                                     "section_counts": {str(k): int(v) for k, v in secs.items()},
                                     "count": int(e.get("total_mentions") or sum(secs.values()))})
                ents.sort(key=lambda e: (len(e["sections"]), e["count"]), reverse=True)
                out["entities"] = ents[:40]
            except (OSError, ValueError, TypeError) as exc:
                log.warning("cross_section.json unreadable: %s", exc)
        ts = _latest_meta_file(self.lake, "top_stories.json")
        if ts:
            try:
                data = json.loads(ts.read_text(encoding="utf-8"))
                stories = []
                for s in data.get("stories") or []:
                    if not isinstance(s, dict):
                        continue
                    stories.append({"headline": _clean_text(s.get("headline"))[:160],
                                    "n_records": int(s.get("n_records") or 0),
                                    "n_outlets": int(s.get("n_outlets") or 0),
                                    "n_sections": int(s.get("n_sections") or 0),
                                    "sections": list(s.get("sections") or []),
                                    "score": _flt(s.get("score")),
                                    "url": _http_url(s.get("representative_url")),
                                    "source": s.get("representative_source")})
                out["stories"] = stories[:12]
                out["stories_date"] = data.get("date")
            except (OSError, ValueError, TypeError) as exc:
                log.warning("top_stories.json unreadable: %s", exc)
        out["watch_area_counts"] = self.watch_area_counts()
        out["latest_brief"] = self.latest_brief()
        return out

    def watch_area_counts(self) -> dict:
        counts: dict[str, int] = {a["name"]: 0 for a in self.watch_areas}
        base = self.lake / "sections"
        day = None
        if not base.is_dir():
            return {"day": day, "counts": counts}
        for sec in base.iterdir():
            if not sec.is_dir() or sec.name.startswith("_"):
                continue
            days = sorted(d.name for d in sec.iterdir() if _is_date(d.name))
            if not days:
                continue
            latest = days[-1]
            if date.fromisoformat(latest) < self.today - timedelta(days=1):
                continue
            day = max(day or latest, latest)
            raw = sec / latest / "raw.jsonl"
            if not raw.is_file():
                continue
            try:
                for line in raw.read_text(encoding="utf-8", errors="replace").splitlines():
                    if '"watch_areas"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for name in ((rec.get("extra") or {}).get("watch_areas") or []):
                        if name in counts:
                            counts[name] += 1
            except OSError:
                continue
        return {"day": day, "counts": counts}

    def latest_brief(self) -> Optional[dict]:
        candidates = []
        for folder in (self.out.parent / "briefings", REPO / "briefings"):
            if folder.is_dir():
                for p in folder.iterdir():
                    if p.suffix in (".html", ".md") and _is_date(p.stem):
                        candidates.append(p.stem)
        if not candidates:
            return None
        d = max(candidates)
        return {"date": d, "url": f"../briefings/{d}.html"}

    def write(self) -> dict:
        layers_dir = self.out / "layers"
        layers_dir.mkdir(parents=True, exist_ok=True)
        manifest_layers = []
        for name in LAYERS:
            feats = self.layers.get(name, [])
            fc = {"type": "FeatureCollection", "features": feats}
            (layers_dir / f"{name}.geojson").write_text(json.dumps(fc, separators=(",", ":"), ensure_ascii=False),
                                                       encoding="utf-8")
            freshest = max((f["properties"]["ts"] for f in feats), default=None)
            manifest_layers.append({
                "layer": name,
                "file": f"layers/{name}.geojson",
                "count": len(feats),
                "freshest_ts": freshest,
                "freshest_at": (datetime.fromtimestamp(freshest, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                                if freshest else None),
                "inputs": [i["name"] for i in self.inputs if name in i["layer"].split("+")],
                "severity": SEVERITY_NOTES.get(name, ""),
            })
        manifest = {
            "generated_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_ts": int(self.now.timestamp()),
            "days": self.days,
            "network": self.network,
            "layers": manifest_layers,
            "inputs": self.inputs,
            "counts": {m["layer"]: m["count"] for m in manifest_layers},
            "note": "Analytic context only. Not safety or navigation guidance.",
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        (self.out / "signals.json").write_text(json.dumps(self.signals(), indent=1, ensure_ascii=False),
                                               encoding="utf-8")
        page = TEMPLATE.read_text(encoding="utf-8")
        page = page.replace("__GENERATED_AT__", manifest["generated_at"]).replace("__DAYS__", str(self.days))
        (self.out / "index.html").write_text(page, encoding="utf-8")
        return manifest


# ---------------------------------------------------------------------------
# severity heuristics (documented in SEVERITY_NOTES)
# ---------------------------------------------------------------------------

def _frp_severity(frp: float) -> int:
    frp = frp or 0.0
    if frp < 5:
        return 2
    if frp < 20:
        return 4
    if frp < 50:
        return 6
    if frp < 150:
        return 8
    return 10


def _quake_severity(mag, tsunami=None, alert=None) -> int:
    mag = _flt(mag)
    if mag is None:
        return 3
    steps = [(4.5, 2), (5.0, 4), (5.5, 5), (6.0, 6), (6.5, 7), (7.0, 8), (7.5, 9)]
    sev = 10
    for cut, s in steps:
        if mag < cut:
            sev = s
            break
    bump = bool(_flt(tsunami)) or str(alert or "").lower() in ("orange", "red")
    return min(10, sev + (1 if bump else 0))


def _gdacs_severity(level) -> int:
    return {"green": 3, "orange": 6, "red": 9}.get(str(level or "").lower(), 4)


def _fatality_severity(fat: int) -> int:
    if fat <= 0:
        return 3
    if fat < 5:
        return 5
    if fat < 10:
        return 6
    if fat < 25:
        return 7
    if fat < 50:
        return 8
    return 9


def _count_severity(count: int) -> int:
    return max(2, min(8, 1 + int(math.log2(max(1, count)) + 1)))


def _nws_severity(level) -> int:
    return {"extreme": 9, "severe": 7, "moderate": 5, "minor": 3}.get(str(level or "").lower(), 2)


# ---------------------------------------------------------------------------
# misc parsing
# ---------------------------------------------------------------------------

def _local(tag) -> str:
    tag = str(tag)
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _is_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _centroid(geom: dict) -> Optional[tuple[float, float]]:
    if not isinstance(geom, dict):
        return None
    t, coords = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        return _lonlat({"coordinates": coords})
    if t == "Polygon":
        rings = [coords[0]] if coords else []
    elif t == "MultiPolygon":
        rings = [poly[0] for poly in (coords or []) if poly]
    else:
        return None
    pts = [p for ring in rings for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return None
    lon = sum(_flt(p[0]) or 0.0 for p in pts) / len(pts)
    lat = sum(_flt(p[1]) or 0.0 for p in pts) / len(pts)
    return _lonlat({"lat": lat, "lon": lon})


def _salvage_rings(features_json: str, max_rings: int = 400) -> list[list]:
    """The lake stores DeepStateMap's features_json truncated at 30k chars.
    Parse what is parseable; otherwise cut back to the last complete feature."""
    if not features_json:
        return []
    try:
        feats = json.loads(features_json)
    except json.JSONDecodeError:
        cut = features_json.rfind('{"type": "Feature"')
        if cut <= 0:
            return []
        try:
            feats = json.loads(features_json[:cut].rstrip().rstrip(",") + "]")
        except json.JSONDecodeError:
            return []
    rings = []
    for feat in feats or []:
        geom = (feat or {}).get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        ring = (geom.get("coordinates") or [[]])[0]
        pts = []
        for p in ring:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                lon, lat = _flt(p[0]), _flt(p[1])
                if lon is not None and lat is not None:
                    pts.append([round(lon, 4), round(lat, 4)])
        if len(pts) >= 4:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            rings.append(pts)
        if len(rings) >= max_rings:
            break
    return rings


def _prefer_live_frontline(feats: list[dict]) -> list[dict]:
    live = [f for f in feats if f["properties"].get("kind") == "frontline"
            and f["properties"].get("source") == "deepstatemap_live"]
    if not live:
        return feats
    return [f for f in feats if not (f["properties"].get("kind") == "frontline"
                                     and f["properties"].get("source") != "deepstatemap_live")]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build(out: Path, *, lake: Path = DEFAULT_LAKE, days: int = 7, network: bool = True,
          watch_path: Path = DEFAULT_WATCH, now: Optional[datetime] = None) -> dict:
    b = Build(lake=lake, out=out, days=days, network=network, watch_path=watch_path, now=now)
    b.build_layers()
    manifest = b.write()
    log.info("wrote %s · %s", out, json.dumps(manifest["counts"]))
    return manifest


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worldscope.godseye", description=__doc__)
    ap.add_argument("--out", default="dist/live", help="output directory (default dist/live)")
    ap.add_argument("--days", type=int, default=7, help="lake look-back window in days")
    ap.add_argument("--lake", default=str(DEFAULT_LAKE), help="lake root (default lake/)")
    ap.add_argument("--watchareas", default=str(DEFAULT_WATCH), help="watchareas.yaml path")
    ap.add_argument("--no-network", action="store_true", help="skip live feeds; lake only")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="[godseye] %(levelname)s %(message)s", stream=sys.stderr)
    manifest = build(Path(args.out), lake=Path(args.lake), days=args.days,
                     network=not args.no_network, watch_path=Path(args.watchareas))
    failed = [i["name"] for i in manifest["inputs"] if not i["ok"]]
    print(f"[godseye] layers: {json.dumps(manifest['counts'])}")
    print(f"[godseye] inputs ok={len(manifest['inputs']) - len(failed)} failed={len(failed)}"
          + (f" ({', '.join(failed)})" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
