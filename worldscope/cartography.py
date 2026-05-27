"""
cartography.py — render PNG maps for the daily brief.

Reads geolocated rows from the lake (`records.extra_json`) and emits four
maps under `figures/daily/<YYYY-MM-DD>/maps/`:

    world_today.png           1600x900   global events (quakes, fires, conflict, NHC, VIP flights)
    us_today.png              1600x1000  CONUS focus (NWS alerts, SPC, state-bills, quakes)
    conflict_intensity_30d.png 1600x900  30-day ACLED kernel density
    ukraine_kyiv_focus.png    1200x1000  Ukraine, with Kyiv reporting badge

The module degrades gracefully: an empty section renders a base map with a
"no events today" label rather than crashing.

Hard constraints (enforced by the daily brief):
    * No external deps beyond matplotlib / numpy / PIL / stdlib.
    * Cartopy/geopandas/folium are NOT in the environment. We read the
      Natural Earth lowres shapefile shipped with geopandas via fiona,
      which is available even when pandas itself is broken.
    * Heritage palette: CAROLINA_NAVY borders, BSE_TEAL ocean (30% alpha),
      PARCHMENT land, OLD_GOLD for VIP, INDIANA_CRIMSON for conflict/alerts.
    * No em-dashes anywhere in titles / captions / labels.
"""
from __future__ import annotations

import json
import math
import sqlite3
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.patches import Polygon as MplPolygon, FancyBboxPatch
import numpy as np

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Palette + fonts
# ---------------------------------------------------------------------------

CAROLINA_NAVY = "#13294B"
OLD_GOLD = "#D4A017"
BSE_TEAL = "#1A8A87"
INDIANA_CRIMSON = "#990000"
CAROLINA_BLUE = "#4B9CD3"
PARCHMENT = "#FAF8F3"
SLATE = "#4E5667"
MIST = "#E8E2D5"


def _font(family: str, size: int, weight: str = "normal") -> dict:
    return {"family": family, "size": size, "weight": weight}


TITLE_FONT = _font("Georgia", 22, "bold")
SUBTITLE_FONT = _font("Georgia", 13)
CAPTION_FONT = _font("Helvetica", 9)
LABEL_FONT = _font("Helvetica", 10)


# ---------------------------------------------------------------------------
# Airport ICAO lookup (lat, lon)
# ---------------------------------------------------------------------------

AIRPORTS: dict[str, tuple[float, float]] = {
    "KLAX": (33.9425, -118.4081),   # Los Angeles
    "KJFK": (40.6413, -73.7781),    # New York JFK
    "KEWR": (40.6925, -74.1687),    # Newark
    "KIAD": (38.9531, -77.4565),    # Washington Dulles
    "KDCA": (38.8521, -77.0377),    # Washington National
    "KORD": (41.9742, -87.9073),    # Chicago O'Hare
    "KATL": (33.6407, -84.4277),    # Atlanta
    "KSFO": (37.6213, -122.3790),   # San Francisco
    "KDFW": (32.8998, -97.0403),    # Dallas Fort Worth
    "KMIA": (25.7959, -80.2870),    # Miami
    "KIAH": (29.9844, -95.3414),    # Houston
    "KBOS": (42.3656, -71.0096),    # Boston Logan
    "KLAS": (36.0840, -115.1537),   # Las Vegas
    "KSEA": (47.4502, -122.3088),   # Seattle
    "KDEN": (39.8561, -104.6737),   # Denver
    "KMSP": (44.8848, -93.2223),    # Minneapolis
    "KPHX": (33.4342, -112.0116),   # Phoenix
    "KCLT": (35.2140, -80.9431),    # Charlotte
    "KMCO": (28.4312, -81.3081),    # Orlando
    "KSLC": (40.7899, -111.9791),   # Salt Lake City
    "KLSV": (36.2360, -115.0344),   # Nellis (Las Vegas military)
    "EGLL": (51.4700, -0.4543),     # London Heathrow
    "LFPG": (49.0097, 2.5479),      # Paris CDG
    "EDDF": (50.0379, 8.5622),      # Frankfurt
    "RJTT": (35.5494, 139.7798),    # Tokyo Haneda
    "RJAA": (35.7720, 140.3929),    # Tokyo Narita
    "ZBAA": (40.0801, 116.5846),    # Beijing Capital
    "ZSPD": (31.1443, 121.8083),    # Shanghai Pudong
    "VHHH": (22.3080, 113.9185),    # Hong Kong
    "WSSS": (1.3644, 103.9915),     # Singapore Changi
    "OMDB": (25.2532, 55.3657),     # Dubai
    "UUEE": (55.9726, 37.4146),     # Moscow Sheremetyevo
    "UUDD": (55.4088, 37.9063),     # Moscow Domodedovo
    "LTBA": (40.9769, 28.8146),     # Istanbul Ataturk
    "LIMC": (45.6306, 8.7281),      # Milan Malpensa
    "EHAM": (52.3105, 4.7683),      # Amsterdam
    "LSZH": (47.4647, 8.5492),      # Zurich
    "VABB": (19.0896, 72.8656),     # Mumbai
    "VIDP": (28.5562, 77.1000),     # Delhi
    "GMMN": (33.3675, -7.5898),     # Casablanca
    "HECA": (30.1219, 31.4056),     # Cairo
    "DNMM": (6.5774, 3.3211),       # Lagos
    "HAAB": (8.9779, 38.7993),      # Addis Ababa
    "SBGR": (-23.4356, -46.4731),   # Sao Paulo Guarulhos
    "MMMX": (19.4361, -99.0719),    # Mexico City
    "SCEL": (-33.3928, -70.7858),   # Santiago
    "CYYZ": (43.6777, -79.6248),    # Toronto Pearson
    "UKBB": (50.3450, 30.8947),     # Kyiv Boryspil
    "UKKK": (50.4019, 30.4498),     # Kyiv Zhuliany
}


# ---------------------------------------------------------------------------
# US state centroids (approximate lon, lat)
# ---------------------------------------------------------------------------

STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "Alabama":        (-86.79, 32.81),
    "Alaska":         (-152.40, 64.20),
    "Arizona":        (-111.93, 34.17),
    "Arkansas":       (-92.44, 34.97),
    "California":     (-119.68, 37.18),
    "Colorado":       (-105.55, 38.99),
    "Connecticut":    (-72.76, 41.62),
    "Delaware":       (-75.51, 38.99),
    "Florida":        (-81.92, 28.63),
    "Georgia":        (-83.44, 32.65),
    "Hawaii":         (-156.36, 20.30),
    "Idaho":          (-114.61, 44.24),
    "Illinois":       (-89.20, 40.00),
    "Indiana":        (-86.28, 39.91),
    "Iowa":           (-93.21, 42.07),
    "Kansas":         (-98.38, 38.50),
    "Kentucky":       (-84.86, 37.53),
    "Louisiana":      (-91.87, 31.05),
    "Maine":          (-69.24, 45.37),
    "Maryland":       (-76.80, 39.06),
    "Massachusetts":  (-71.81, 42.26),
    "Michigan":       (-84.71, 44.35),
    "Minnesota":      (-94.31, 46.28),
    "Mississippi":    (-89.66, 32.74),
    "Missouri":       (-92.46, 38.36),
    "Montana":        (-109.63, 47.05),
    "Nebraska":       (-99.79, 41.53),
    "Nevada":         (-117.05, 39.33),
    "New Hampshire":  (-71.58, 43.69),
    "New Jersey":     (-74.66, 40.20),
    "New Mexico":     (-106.11, 34.41),
    "New York":       (-75.53, 42.95),
    "North Carolina": (-79.81, 35.55),
    "North Dakota":   (-100.31, 47.45),
    "Ohio":           (-82.79, 40.29),
    "Oklahoma":       (-97.49, 35.59),
    "Oregon":         (-122.07, 44.13),
    "Pennsylvania":   (-77.21, 40.88),
    "Rhode Island":   (-71.51, 41.68),
    "South Carolina": (-80.95, 33.86),
    "South Dakota":   (-100.23, 44.44),
    "Tennessee":      (-86.69, 35.86),
    "Texas":          (-99.34, 31.05),
    "Utah":           (-111.86, 39.32),
    "Vermont":        (-72.71, 44.07),
    "Virginia":       (-78.17, 37.52),
    "Washington":     (-120.45, 47.38),
    "West Virginia":  (-80.95, 38.64),
    "Wisconsin":      (-89.99, 44.27),
    "Wyoming":        (-107.55, 42.99),
    "District of Columbia": (-77.03, 38.91),
}

STATE_CODE_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}


# ---------------------------------------------------------------------------
# Country borders loader (Natural Earth lowres, via fiona)
# ---------------------------------------------------------------------------

@dataclass
class CountryPoly:
    name: str
    iso_a3: str
    rings: list[np.ndarray]   # each ring is (N, 2) lon/lat


def _find_naturalearth_shapefile() -> Path | None:
    """Look in known locations for the Natural Earth lowres shapefile that
    geopandas ships. We probe sys.path so we don't actually import geopandas
    (its import drags in pandas, which is broken under numpy 2.x here)."""
    import sys
    candidates = []
    for entry in sys.path:
        if not entry:
            continue
        candidates.append(Path(entry) / "geopandas" / "datasets" / "naturalearth_lowres" / "naturalearth_lowres.shp")
    # also try the cache dir under this module
    candidates.append(Path(__file__).parent / "cartography_data" / "naturalearth_lowres.shp")
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_world_polygons() -> list[CountryPoly]:
    """Read Natural Earth lowres polygons via fiona. Returns flat list of
    rings tagged by country name. Falls back to empty list if anything
    goes wrong; the caller draws a graticule-only base map in that case."""
    try:
        import fiona
        shp_path = _find_naturalearth_shapefile()
        if shp_path is None or not shp_path.exists():
            print("[cartography] no Natural Earth shapefile found; drawing graticule only")
            return []
        out: list[CountryPoly] = []
        with fiona.open(str(shp_path)) as src:
            for feat in src:
                props = dict(feat["properties"])
                geom = feat["geometry"]
                if geom is None:
                    continue
                rings: list[np.ndarray] = []
                if geom["type"] == "Polygon":
                    polys = [geom["coordinates"]]
                elif geom["type"] == "MultiPolygon":
                    polys = geom["coordinates"]
                else:
                    continue
                for poly in polys:
                    if not poly:
                        continue
                    outer = poly[0]
                    rings.append(np.asarray(outer, dtype=float))
                out.append(CountryPoly(
                    name=str(props.get("name", "")),
                    iso_a3=str(props.get("iso_a3", "")),
                    rings=rings,
                ))
        return out
    except Exception as exc:
        print(f"[cartography] world polygons load failed: {exc}")
        return []


_WORLD_CACHE: list[CountryPoly] | None = None


def world_polygons() -> list[CountryPoly]:
    global _WORLD_CACHE
    if _WORLD_CACHE is None:
        _WORLD_CACHE = _load_world_polygons()
    return _WORLD_CACHE


# ---------------------------------------------------------------------------
# Base map drawing
# ---------------------------------------------------------------------------

def _setup_axes(ax: plt.Axes, bbox: tuple[float, float, float, float],
                graticule: bool = True) -> None:
    """bbox = (lon_min, lon_max, lat_min, lat_max)"""
    lon_min, lon_max, lat_min, lat_max = bbox
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal")
    # Ocean fill (entire axes background)
    ax.set_facecolor(_rgba(BSE_TEAL, 0.30))
    if graticule:
        for lon in range(int(lon_min // 30) * 30, int(lon_max) + 30, 30):
            ax.axvline(lon, color=MIST, lw=0.4, zorder=0.5, alpha=0.6)
        for lat in range(int(lat_min // 15) * 15, int(lat_max) + 15, 15):
            ax.axhline(lat, color=MIST, lw=0.4, zorder=0.5, alpha=0.6)
    ax.tick_params(axis="both", which="both", length=0, labelsize=8, colors=SLATE)
    for spine in ax.spines.values():
        spine.set_color(SLATE)
        spine.set_linewidth(0.6)


def _rgba(hex_color: str, alpha: float) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return (r, g, b, alpha)


def _draw_world(ax: plt.Axes, bbox: tuple[float, float, float, float],
                highlight: Optional[set[str]] = None) -> None:
    """Fill country polygons with PARCHMENT, outline in CAROLINA_NAVY.
    Optional `highlight` set of country names gets a stronger border."""
    polys = world_polygons()
    lon_min, lon_max, lat_min, lat_max = bbox
    fill_polys = []
    border_segs = []
    hl_segs = []
    for cp in polys:
        for ring in cp.rings:
            if ring.size == 0:
                continue
            # quick reject by bbox
            r_lon_min, r_lon_max = ring[:, 0].min(), ring[:, 0].max()
            r_lat_min, r_lat_max = ring[:, 1].min(), ring[:, 1].max()
            if r_lon_max < lon_min or r_lon_min > lon_max:
                continue
            if r_lat_max < lat_min or r_lat_min > lat_max:
                continue
            fill_polys.append(ring)
            # border as polyline closed
            closed = np.vstack([ring, ring[:1]])
            segs = np.stack([closed[:-1], closed[1:]], axis=1)
            if highlight and cp.name in highlight:
                hl_segs.append(segs)
            else:
                border_segs.append(segs)
    if fill_polys:
        coll = PolyCollection(
            fill_polys, facecolor=PARCHMENT, edgecolor="none",
            zorder=1.0,
        )
        ax.add_collection(coll)
    if border_segs:
        all_segs = np.concatenate(border_segs, axis=0)
        lc = LineCollection(all_segs, colors=CAROLINA_NAVY, linewidths=0.7, zorder=1.5)
        ax.add_collection(lc)
    if hl_segs:
        all_hl = np.concatenate(hl_segs, axis=0)
        lc = LineCollection(all_hl, colors=CAROLINA_NAVY, linewidths=1.6, zorder=1.6)
        ax.add_collection(lc)


# ---------------------------------------------------------------------------
# Lake reader
# ---------------------------------------------------------------------------

class LakeReader:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path))
        c.row_factory = sqlite3.Row
        return c

    def fetch_section(self, section_id: str, since_iso: str | None = None) -> list[dict]:
        if not self.db_path.exists():
            return []
        sql = "SELECT extra_json, ingested_at, original_text, record_date FROM records WHERE section_id = ?"
        params: list[Any] = [section_id]
        if since_iso:
            sql += " AND ingested_at >= ?"
            params.append(since_iso)
        out: list[dict] = []
        try:
            with self._conn() as conn:
                for row in conn.execute(sql, params):
                    try:
                        extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
                    except Exception:
                        extra = {}
                    extra.setdefault("_ingested_at", row["ingested_at"])
                    extra.setdefault("_record_date", row["record_date"])
                    extra.setdefault("_original_text", row["original_text"])
                    out.append(extra)
        except sqlite3.Error as exc:
            print(f"[cartography] lake read failed for {section_id}: {exc}")
        return out


# ---------------------------------------------------------------------------
# Helpers: lat/lon parsing, fatality scaling
# ---------------------------------------------------------------------------

def _flt(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _quake_size(mag: float) -> float:
    # USGS magnitude → radius; mag 4 = 30, mag 7 = 220
    return max(10.0, 6 * (mag ** 2))


def _conflict_size(fatalities: float) -> float:
    if fatalities <= 0:
        return 20.0
    return min(400.0, 20.0 + 12.0 * math.sqrt(fatalities))


def _extract_state_from_area(area_desc: str) -> str | None:
    """NWS area descriptions often end with a state name or code."""
    if not area_desc:
        return None
    parts = [p.strip() for p in area_desc.replace(";", ",").split(",")]
    for p in reversed(parts):
        u = p.upper().strip()
        if u in STATE_CODE_TO_NAME:
            return STATE_CODE_TO_NAME[u]
        if p in STATE_CENTROIDS:
            return p
    return None


def _state_from_ugc(ugc_codes: list[str]) -> str | None:
    """NWS UGC codes start with a 2-letter state abbreviation, e.g. 'CAZ006'."""
    if not ugc_codes:
        return None
    for code in ugc_codes:
        if isinstance(code, str) and len(code) >= 2:
            ab = code[:2].upper()
            if ab in STATE_CODE_TO_NAME:
                return STATE_CODE_TO_NAME[ab]
    return None


# ---------------------------------------------------------------------------
# Title / caption rendering
# ---------------------------------------------------------------------------

def _draw_title(fig: plt.Figure, title: str, subtitle: str) -> None:
    fig.text(
        0.02, 0.965, title,
        family="Georgia", fontsize=22, fontweight="bold", color=CAROLINA_NAVY,
    )
    fig.text(
        0.02, 0.935, subtitle,
        family="Georgia", fontsize=12, color=SLATE, style="italic",
    )


def _draw_caption(fig: plt.Figure, sources: list[str], date_iso: str,
                  projection: str = "Plate Carree (equirectangular)") -> None:
    src = ", ".join(sources) if sources else "no sources"
    txt = f"Sources: {src}. Date: {date_iso}. Projection: {projection}."
    fig.text(
        0.02, 0.015, txt,
        family="Helvetica", fontsize=8.5, color=SLATE,
    )
    fig.text(
        0.98, 0.015, "WORLDSCOPE",
        family="Georgia", fontsize=8.5, color=CAROLINA_NAVY,
        ha="right", style="italic",
    )


def _no_data_label(ax: plt.Axes, text: str = "no events today") -> None:
    ax.text(
        0.5, 0.5, text,
        transform=ax.transAxes,
        ha="center", va="center",
        family="Georgia", fontsize=18, color=SLATE, style="italic",
        bbox=dict(facecolor=PARCHMENT, edgecolor=SLATE, boxstyle="round,pad=0.6", alpha=0.85),
        zorder=10,
    )


# ---------------------------------------------------------------------------
# Lightweight 2D Gaussian KDE on a grid (no scipy)
# ---------------------------------------------------------------------------

def _kde_grid(
    points: np.ndarray, weights: np.ndarray | None,
    bbox: tuple[float, float, float, float],
    nx: int = 320, ny: int = 180, bandwidth_deg: float = 1.8,
) -> np.ndarray:
    """Return ny x nx density grid in lat/lon space."""
    lon_min, lon_max, lat_min, lat_max = bbox
    grid = np.zeros((ny, nx), dtype=float)
    if points.size == 0:
        return grid
    xs = np.linspace(lon_min, lon_max, nx)
    ys = np.linspace(lat_min, lat_max, ny)
    inv2s2 = 1.0 / (2.0 * bandwidth_deg ** 2)
    w = weights if weights is not None else np.ones(len(points))
    # vectorize over grid X for each point — modest memory cost
    XX, YY = np.meshgrid(xs, ys)
    for (lon, lat), wi in zip(points, w):
        if not (lon_min - 5 <= lon <= lon_max + 5):
            continue
        if not (lat_min - 5 <= lat <= lat_max + 5):
            continue
        d2 = (XX - lon) ** 2 + (YY - lat) ** 2
        grid += wi * np.exp(-d2 * inv2s2)
    return grid


# ---------------------------------------------------------------------------
# DailyMaps — public API
# ---------------------------------------------------------------------------

class DailyMaps:
    """Render the four standard maps for the daily brief."""

    DEFAULT_LAKE = Path("/Users/ian/Projects/worldscope/lake/db/worldscope.sqlite")
    DEFAULT_OUT_ROOT = Path("/Users/ian/Projects/worldscope/figures/daily")

    def __init__(
        self,
        lake_db_path: Path | None = None,
        output_root: Path | None = None,
    ):
        self.lake = LakeReader(lake_db_path or self.DEFAULT_LAKE)
        self.output_root = Path(output_root) if output_root else self.DEFAULT_OUT_ROOT

    # ---- helpers -------------------------------------------------------

    def _out_dir(self, date_iso: str) -> Path:
        p = self.output_root / date_iso / "maps"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _today_window(self, date_iso: str) -> tuple[str, str]:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
        start = d.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _window(self, date_iso: str, days_back: int) -> str:
        d = datetime.strptime(date_iso, "%Y-%m-%d")
        start = (d - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---- public --------------------------------------------------------

    def render_all(self, date_iso: str | None = None) -> dict[str, Path]:
        date_iso = date_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "world_today": self.render_world_today(date_iso),
            "us_today": self.render_us_today(date_iso),
            "conflict_intensity_30d": self.render_conflict_intensity_30d(date_iso),
            "ukraine_kyiv_focus": self.render_ukraine_kyiv_focus(date_iso),
        }

    # ---- world today ---------------------------------------------------

    def render_world_today(self, date_iso: str) -> Path:
        bbox = (-180, 180, -60, 80)
        fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=PARCHMENT)
        ax = fig.add_axes([0.02, 0.07, 0.96, 0.83])
        _setup_axes(ax, bbox)
        _draw_world(ax, bbox)

        since = self._window(date_iso, days_back=1)
        weather = self.lake.fetch_section("weather", since)
        firms = self.lake.fetch_section("firms", since)
        acled = self.lake.fetch_section("acled", since)
        vipf = self.lake.fetch_section("vip_flights", since)

        sources_used: list[str] = []
        any_data = False

        # USGS earthquakes
        qx, qy, qs = [], [], []
        for w in weather:
            if w.get("subsection") != "earthquake":
                continue
            coords = w.get("coordinates")
            if not coords or len(coords) < 2:
                continue
            lon, lat = _flt(coords[0]), _flt(coords[1])
            mag = _flt(w.get("magnitude")) or _flt(w.get("mag")) or 4.5
            if lon is None or lat is None:
                continue
            qx.append(lon); qy.append(lat); qs.append(_quake_size(mag))
        if qx:
            ax.scatter(qx, qy, s=qs, facecolor=INDIANA_CRIMSON, edgecolor=CAROLINA_NAVY,
                       linewidths=0.4, alpha=0.75, zorder=4, label=f"Quakes ({len(qx)})")
            sources_used.append("USGS")
            any_data = True

        # NHC tropical
        nx_, ny_ = [], []
        for w in weather:
            sub = w.get("subsection")
            if sub not in ("tropical", "nhc", "tropical_storm", "nhc_tropical"):
                continue
            coords = w.get("coordinates") or [w.get("longitude"), w.get("latitude")]
            if not coords or coords[0] is None:
                continue
            lon, lat = _flt(coords[0]), _flt(coords[1])
            if lon is None or lat is None:
                continue
            nx_.append(lon); ny_.append(lat)
        if nx_:
            ax.scatter(nx_, ny_, s=160, marker="*", facecolor="#7B2CBF",
                       edgecolor=CAROLINA_NAVY, linewidths=0.5, alpha=0.85, zorder=4,
                       label=f"Tropical ({len(nx_)})")
            sources_used.append("NHC")
            any_data = True

        # NASA FIRMS active fires
        fx, fy = [], []
        for r in firms:
            lon = _flt(r.get("longitude")); lat = _flt(r.get("latitude"))
            if lon is None or lat is None:
                continue
            fx.append(lon); fy.append(lat)
        if fx:
            ax.scatter(fx, fy, s=14, marker="^", facecolor="#E07A1F",
                       edgecolor="none", alpha=0.55, zorder=3.5, label=f"Fires ({len(fx)})")
            sources_used.append("NASA FIRMS")
            any_data = True

        # ACLED conflict
        cx, cy, cs = [], [], []
        for r in acled:
            lon = _flt(r.get("longitude")); lat = _flt(r.get("latitude"))
            if lon is None or lat is None:
                continue
            fat = _flt(r.get("fatalities")) or 0.0
            cx.append(lon); cy.append(lat); cs.append(_conflict_size(fat))
        if cx:
            ax.scatter(cx, cy, s=cs, marker="D", facecolor=INDIANA_CRIMSON,
                       edgecolor=CAROLINA_NAVY, linewidths=0.4, alpha=0.7, zorder=4,
                       label=f"Conflict events ({len(cx)})")
            sources_used.append("ACLED")
            any_data = True

        # VIP flights
        flight_lines = 0
        for f in vipf:
            origin = (f.get("origin") or "").upper()
            dest = (f.get("destination") or "").upper()
            o = AIRPORTS.get(origin); d = AIRPORTS.get(dest)
            if not o or not d:
                continue
            ax.plot(
                [o[1], d[1]], [o[0], d[0]],
                color=OLD_GOLD, lw=1.4, alpha=0.85, zorder=5,
            )
            ax.scatter([o[1], d[1]], [o[0], d[0]], s=20, facecolor=OLD_GOLD,
                       edgecolor=CAROLINA_NAVY, linewidths=0.5, zorder=5.1)
            flight_lines += 1
        if flight_lines:
            sources_used.append("VIP flights")
            any_data = True

        if not any_data:
            _no_data_label(ax, "no geolocated events today")

        # Legend
        self._world_legend(ax, qx, fx, cx, flight_lines, nx_)
        _draw_title(fig, "WORLD TODAY", f"Geolocated events, last 24 hours, {date_iso}")
        _draw_caption(fig, sources_used or ["lake"], date_iso)

        path = self._out_dir(date_iso) / "world_today.png"
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path

    def _world_legend(self, ax, qx, fx, cx, flights, tropical) -> None:
        handles = []
        if qx:
            handles.append(plt.scatter([], [], s=80, c=INDIANA_CRIMSON, edgecolor=CAROLINA_NAVY,
                                       linewidths=0.4, alpha=0.75, label=f"Quakes ({len(qx)})"))
        if fx:
            handles.append(plt.scatter([], [], s=40, c="#E07A1F", marker="^",
                                       edgecolor="none", alpha=0.6, label=f"Fires ({len(fx)})"))
        if cx:
            handles.append(plt.scatter([], [], s=70, c=INDIANA_CRIMSON, marker="D",
                                       edgecolor=CAROLINA_NAVY, linewidths=0.4, alpha=0.7,
                                       label=f"Conflict ({len(cx)})"))
        if tropical:
            handles.append(plt.scatter([], [], s=140, c="#7B2CBF", marker="*",
                                       edgecolor=CAROLINA_NAVY, linewidths=0.4, alpha=0.85,
                                       label=f"Tropical ({len(tropical)})"))
        if flights:
            from matplotlib.lines import Line2D
            handles.append(Line2D([0], [0], color=OLD_GOLD, lw=1.8,
                                  label=f"VIP flights ({flights})"))
        if handles:
            leg = ax.legend(
                handles=handles, loc="lower left",
                frameon=True, facecolor=PARCHMENT, edgecolor=SLATE,
                fontsize=9, prop={"family": "Helvetica", "size": 9},
            )
            leg.set_zorder(20)

    # ---- US today ------------------------------------------------------

    def render_us_today(self, date_iso: str) -> Path:
        bbox = (-128, -65, 22, 51)
        fig = plt.figure(figsize=(16, 10), dpi=100, facecolor=PARCHMENT)
        ax = fig.add_axes([0.02, 0.07, 0.96, 0.83])
        _setup_axes(ax, bbox)
        _draw_world(ax, bbox, highlight={"United States of America"})

        since = self._window(date_iso, days_back=1)
        weather = self.lake.fetch_section("weather", since)
        bills = self.lake.fetch_section("state_bills", since)

        sources = []
        any_data = False

        # State-bill activity heat (count per state -> colored circle at centroid)
        bill_counts: dict[str, int] = {}
        for b in bills:
            st = b.get("state")
            if not st:
                continue
            if st in STATE_CODE_TO_NAME:
                st = STATE_CODE_TO_NAME[st]
            if st in STATE_CENTROIDS:
                bill_counts[st] = bill_counts.get(st, 0) + 1
        if bill_counts:
            max_c = max(bill_counts.values())
            for st, c in bill_counts.items():
                lon, lat = STATE_CENTROIDS[st]
                frac = c / max_c if max_c else 0
                radius = 200 + 1400 * frac
                ax.scatter(lon, lat, s=radius, facecolor=_rgba(CAROLINA_BLUE, 0.55),
                           edgecolor=CAROLINA_NAVY, linewidths=0.7, zorder=3.4)
                ax.text(lon, lat, str(c), ha="center", va="center",
                        family="Helvetica", fontsize=10, fontweight="bold",
                        color=CAROLINA_NAVY, zorder=3.5)
            sources.append("OpenStates")
            any_data = True

        # NWS active alerts — shade state centroid by severity
        severity_color = {
            "Extreme": "#7A0019",
            "Severe": INDIANA_CRIMSON,
            "Moderate": OLD_GOLD,
            "Minor": CAROLINA_BLUE,
            "Unknown": SLATE,
        }
        alert_state_counts: dict[str, dict[str, int]] = {}
        alert_total = 0
        for w in weather:
            if w.get("subsection") != "active_alert":
                continue
            sev = w.get("severity") or "Unknown"
            areas = w.get("areas") or []
            if isinstance(areas, str):
                areas = [areas]
            # Try ugc_codes first (every NWS alert has them, prefix = state code)
            ugcs = w.get("ugc_codes") or []
            st = _state_from_ugc(ugcs)
            if st is None:
                for a in areas:
                    desc = a if isinstance(a, str) else (a.get("area-desc") or a.get("areaDesc"))
                    st = _extract_state_from_area(desc or "")
                    if st:
                        break
            if st is None:
                # Fall back to issuing_office, e.g. "NWS San Francisco CA"
                off = w.get("issuing_office") or ""
                tokens = off.replace(",", " ").split()
                for t in reversed(tokens):
                    if t.upper() in STATE_CODE_TO_NAME:
                        st = STATE_CODE_TO_NAME[t.upper()]
                        break
            if st is None:
                continue
            alert_state_counts.setdefault(st, {})
            alert_state_counts[st][sev] = alert_state_counts[st].get(sev, 0) + 1
            alert_total += 1
        for st, svmap in alert_state_counts.items():
            top_sev = max(svmap, key=svmap.get)
            total = sum(svmap.values())
            lon, lat = STATE_CENTROIDS[st]
            col = severity_color.get(top_sev, SLATE)
            ax.scatter(lon + 1.5, lat - 1.2, s=120 + 40 * total,
                       facecolor=col, edgecolor=CAROLINA_NAVY, linewidths=0.5,
                       alpha=0.7, zorder=3.6, marker="s")
        if alert_total:
            sources.append("NWS")
            any_data = True

        # SPC convective outlook polygons
        spc_drawn = 0
        for w in weather:
            sub = (w.get("subsection") or "").lower()
            if "spc_day" not in sub:
                continue
            coords = w.get("polygon") or w.get("coordinates")
            if not coords:
                continue
            try:
                arr = np.asarray(coords, dtype=float)
                if arr.ndim == 3:
                    arr = arr[0]
                if arr.shape[1] >= 2:
                    poly = MplPolygon(arr[:, :2], closed=True,
                                      facecolor=_rgba(OLD_GOLD, 0.25),
                                      edgecolor=OLD_GOLD, linewidth=1.2,
                                      zorder=3.3)
                    ax.add_patch(poly)
                    spc_drawn += 1
            except Exception:
                continue
        if spc_drawn:
            sources.append("SPC")
            any_data = True

        # US earthquakes
        qx, qy, qs = [], [], []
        for w in weather:
            if w.get("subsection") != "earthquake":
                continue
            coords = w.get("coordinates")
            if not coords or len(coords) < 2:
                continue
            lon, lat = _flt(coords[0]), _flt(coords[1])
            mag = _flt(w.get("magnitude")) or _flt(w.get("mag")) or 4.0
            if lon is None or lat is None:
                continue
            if not (bbox[0] <= lon <= bbox[1] and bbox[2] <= lat <= bbox[3]):
                continue
            qx.append(lon); qy.append(lat); qs.append(_quake_size(mag))
        if qx:
            ax.scatter(qx, qy, s=qs, facecolor=INDIANA_CRIMSON, edgecolor=CAROLINA_NAVY,
                       linewidths=0.4, alpha=0.8, zorder=4)
            sources.append("USGS")
            any_data = True

        if not any_data:
            _no_data_label(ax, "no US events today")

        # State labels (subtle)
        for st, (lon, lat) in STATE_CENTROIDS.items():
            if st in ("Alaska", "Hawaii"):
                continue
            if not (bbox[0] <= lon <= bbox[1] and bbox[2] <= lat <= bbox[3]):
                continue
            ax.text(lon, lat, _state_code(st), ha="center", va="center",
                    family="Helvetica", fontsize=7, color=SLATE, alpha=0.5,
                    zorder=2)

        # Legend
        from matplotlib.lines import Line2D
        legend_handles = []
        if bill_counts:
            legend_handles.append(plt.scatter([], [], s=120, c=_rgba(CAROLINA_BLUE, 0.6),
                                              edgecolor=CAROLINA_NAVY, linewidths=0.5,
                                              label=f"State bills ({sum(bill_counts.values())})"))
        if alert_total:
            legend_handles.append(plt.scatter([], [], s=80, c=INDIANA_CRIMSON, marker="s",
                                              edgecolor=CAROLINA_NAVY, linewidths=0.4,
                                              label=f"NWS alerts ({alert_total})"))
        if spc_drawn:
            legend_handles.append(mpatches.Patch(facecolor=_rgba(OLD_GOLD, 0.3),
                                                 edgecolor=OLD_GOLD, label=f"SPC outlook ({spc_drawn})"))
        if qx:
            legend_handles.append(plt.scatter([], [], s=80, c=INDIANA_CRIMSON,
                                              edgecolor=CAROLINA_NAVY, linewidths=0.4,
                                              label=f"Quakes ({len(qx)})"))
        if legend_handles:
            ax.legend(handles=legend_handles, loc="lower left", frameon=True,
                      facecolor=PARCHMENT, edgecolor=SLATE, fontsize=9,
                      prop={"family": "Helvetica", "size": 9})

        _draw_title(fig, "UNITED STATES TODAY", f"Alerts, legislation, and quakes, {date_iso}")
        _draw_caption(fig, sources or ["lake"], date_iso)
        path = self._out_dir(date_iso) / "us_today.png"
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path

    # ---- conflict 30d --------------------------------------------------

    def render_conflict_intensity_30d(self, date_iso: str) -> Path:
        bbox = (-180, 180, -60, 80)
        fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=PARCHMENT)
        ax = fig.add_axes([0.02, 0.07, 0.96, 0.83])
        _setup_axes(ax, bbox)
        _draw_world(ax, bbox)

        since = self._window(date_iso, days_back=30)
        acled = self.lake.fetch_section("acled", since)

        pts: list[tuple[float, float]] = []
        weights: list[float] = []
        for r in acled:
            lon = _flt(r.get("longitude")); lat = _flt(r.get("latitude"))
            if lon is None or lat is None:
                continue
            fat = _flt(r.get("fatalities")) or 0.0
            pts.append((lon, lat))
            weights.append(1.0 + math.log1p(fat))

        if pts:
            arr = np.asarray(pts)
            w = np.asarray(weights)
            grid = _kde_grid(arr, w, bbox, nx=360, ny=180, bandwidth_deg=2.5)
            grid_n = grid / (grid.max() + 1e-9)
            # Mask very low density so it doesn't muddy the ocean
            grid_n = np.where(grid_n < 0.02, np.nan, grid_n)
            ax.imshow(
                grid_n,
                origin="lower",
                extent=bbox,
                cmap=_crimson_cmap(),
                alpha=0.78,
                zorder=2.5,
                interpolation="bilinear",
            )
            # overlay individual events at low alpha for granularity
            ax.scatter(arr[:, 0], arr[:, 1], s=4, c=INDIANA_CRIMSON,
                       alpha=0.18, edgecolor="none", zorder=3)
            ax.text(
                0.99, 0.04, f"{len(pts):,} events, last 30 days",
                transform=ax.transAxes, ha="right", va="bottom",
                family="Helvetica", fontsize=10, color=SLATE,
                bbox=dict(facecolor=PARCHMENT, edgecolor=SLATE, boxstyle="round,pad=0.3", alpha=0.85),
            )
            sources = ["ACLED"]
        else:
            _no_data_label(ax, "no conflict data in lake (30 day window)")
            sources = ["lake"]

        _draw_title(fig, "CONFLICT INTENSITY",
                    f"ACLED events, rolling 30 days ending {date_iso}")
        _draw_caption(fig, sources, date_iso)
        path = self._out_dir(date_iso) / "conflict_intensity_30d.png"
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path

    # ---- Ukraine / Kyiv ------------------------------------------------

    def render_ukraine_kyiv_focus(self, date_iso: str) -> Path:
        bbox = (21, 41, 44, 53)
        fig = plt.figure(figsize=(12, 10), dpi=100, facecolor=PARCHMENT)
        ax = fig.add_axes([0.02, 0.07, 0.96, 0.83])
        _setup_axes(ax, bbox)
        _draw_world(ax, bbox, highlight={"Ukraine"})

        since = self._window(date_iso, days_back=7)
        acled = self.lake.fetch_section("acled", since)
        ukr = self.lake.fetch_section("ukrainian_internal", since)

        # ACLED 7-day events
        cx, cy, cs = [], [], []
        for r in acled:
            lon = _flt(r.get("longitude")); lat = _flt(r.get("latitude"))
            if lon is None or lat is None:
                continue
            if not (bbox[0] <= lon <= bbox[1] and bbox[2] <= lat <= bbox[3]):
                continue
            fat = _flt(r.get("fatalities")) or 0.0
            cx.append(lon); cy.append(lat); cs.append(_conflict_size(fat))
        any_data = bool(cx)
        sources: list[str] = []
        if cx:
            ax.scatter(cx, cy, s=cs, marker="D", facecolor=INDIANA_CRIMSON,
                       edgecolor=CAROLINA_NAVY, linewidths=0.4, alpha=0.7, zorder=4,
                       label=f"Conflict events ({len(cx)})")
            sources.append("ACLED")

        # Major cities
        cities = {
            "Kyiv": (50.4501, 30.5234),
            "Lviv": (49.8397, 24.0297),
            "Odesa": (46.4825, 30.7233),
            "Kharkiv": (49.9935, 36.2304),
            "Dnipro": (48.4647, 35.0462),
            "Donetsk": (48.0159, 37.8028),
            "Mariupol": (47.0971, 37.5407),
            "Zaporizhzhia": (47.8388, 35.1396),
            "Kherson": (46.6354, 32.6169),
            "Mykolaiv": (46.9750, 31.9946),
        }
        for name, (lat, lon) in cities.items():
            if name == "Kyiv":
                continue
            ax.scatter(lon, lat, s=18, facecolor=CAROLINA_NAVY,
                       edgecolor=PARCHMENT, linewidths=0.6, zorder=5)
            ax.text(lon + 0.15, lat + 0.15, name,
                    family="Helvetica", fontsize=10, color=CAROLINA_NAVY, zorder=5)

        # Kyiv star
        kyiv_lat, kyiv_lon = cities["Kyiv"]
        ax.scatter(kyiv_lon, kyiv_lat, s=420, marker="*",
                   facecolor=OLD_GOLD, edgecolor=CAROLINA_NAVY, linewidths=1.0,
                   zorder=6, label="Kyiv")
        ax.text(kyiv_lon + 0.3, kyiv_lat + 0.3, "Kyiv",
                family="Georgia", fontsize=13, fontweight="bold",
                color=CAROLINA_NAVY, zorder=6)

        # Reporting density badge
        ukr_count = len(ukr)
        badge = FancyBboxPatch(
            (kyiv_lon + 0.6, kyiv_lat - 1.6), 5.8, 1.0,
            boxstyle="round,pad=0.2", linewidth=1.0,
            facecolor=PARCHMENT, edgecolor=CAROLINA_NAVY, zorder=7,
        )
        ax.add_patch(badge)
        ax.text(
            kyiv_lon + 3.5, kyiv_lat - 1.1,
            f"Kyiv reporting: {ukr_count} items (7d)",
            ha="center", va="center",
            family="Helvetica", fontsize=10, color=CAROLINA_NAVY, zorder=8,
        )
        if ukr_count:
            sources.append("Hmarochos + Vechirniy Kyiv")

        if not any_data and ukr_count == 0:
            _no_data_label(ax, "no events in Ukraine window")

        if cx or True:
            # Always show legend (Kyiv at minimum)
            from matplotlib.lines import Line2D
            handles = []
            if cx:
                handles.append(plt.scatter([], [], s=70, c=INDIANA_CRIMSON, marker="D",
                                           edgecolor=CAROLINA_NAVY, linewidths=0.4,
                                           alpha=0.7, label=f"Conflict events ({len(cx)})"))
            handles.append(plt.scatter([], [], s=300, c=OLD_GOLD, marker="*",
                                       edgecolor=CAROLINA_NAVY, linewidths=0.8,
                                       label="Kyiv"))
            ax.legend(handles=handles, loc="lower left", frameon=True,
                      facecolor=PARCHMENT, edgecolor=SLATE, fontsize=9,
                      prop={"family": "Helvetica", "size": 9})

        _draw_title(fig, "UKRAINE", f"Conflict and Kyiv reporting, last 7 days, {date_iso}")
        _draw_caption(fig, sources or ["lake"], date_iso)
        path = self._out_dir(date_iso) / "ukraine_kyiv_focus.png"
        fig.savefig(path, dpi=100, facecolor=PARCHMENT)
        plt.close(fig)
        return path


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _crimson_cmap():
    """Custom matplotlib colormap from transparent through gold to crimson."""
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(
        "worldscope_crimson",
        [
            (0.0, (1, 1, 1, 0.0)),
            (0.15, _rgba(OLD_GOLD, 0.35)),
            (0.5, _rgba("#C77316", 0.6)),
            (1.0, _rgba(INDIANA_CRIMSON, 0.95)),
        ],
    )


def _state_code(name: str) -> str:
    for code, n in STATE_CODE_TO_NAME.items():
        if n == name:
            return code
    return name[:2].upper()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Render Worldscope daily maps")
    p.add_argument("--date", help="ISO date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--lake", help="path to worldscope.sqlite")
    p.add_argument("--out", help="output root for figures/daily")
    args = p.parse_args(argv)
    dm = DailyMaps(
        lake_db_path=Path(args.lake) if args.lake else None,
        output_root=Path(args.out) if args.out else None,
    )
    results = dm.render_all(args.date)
    for k, v in results.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
