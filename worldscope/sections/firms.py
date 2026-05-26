"""
firms.py — NASA FIRMS VIIRS active-fire detections filtered to
conflict-adjacent and infrastructure-adjacent bounding boxes.

FIRMS exposes near-real-time (~3h latency) active-fire pixels from
the VIIRS instrument on Suomi NPP and NOAA-20/21. We don't want every
brushfire in the world; we want the small set that matters for
geopolitical reasoning:

  - Front-line zones (Ukraine east, Gaza/Lebanon border, Sudan, DRC east)
  - Strategic infrastructure (Russian refineries west of the Urals,
    Iranian oil terminals, Saudi/UAE oil patch, Strait of Hormuz)
  - Choke points (Suez, Bab-el-Mandeb, Hormuz, Malacca)
  - Korean DMZ

We query the FIRMS area-CSV endpoint per box, last 1-2 days, VIIRS only
(higher resolution than MODIS). Each detection becomes one item with
lat/lon/brightness/confidence/satellite + the named zone it fell in.

Requires FIRMS_MAP_KEY env. Register at
https://firms.modaps.eosdis.nasa.gov/api/map_key/
"""
from __future__ import annotations

import csv
import io
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from . import Section

# Bounding boxes: (name, west, south, east, north). FIRMS expects
# west,south,east,north as the area string.
ZONES: list[tuple[str, float, float, float, float]] = [
    ("Ukraine front (east+south)", 35.0, 46.0, 41.5, 51.5),
    ("Gaza + southern Lebanon", 34.2, 31.2, 36.0, 34.0),
    ("Syria",                   35.5, 32.0, 42.5, 37.5),
    ("Iraq",                    38.5, 29.0, 48.8, 37.5),
    ("Yemen + Red Sea",         42.0, 12.0, 53.5, 19.0),
    ("Iran oil terminals (south)", 47.0, 25.0, 56.5, 30.5),
    ("Sudan + Darfur",          22.0, 9.0, 39.0, 22.5),
    ("Eastern DRC + Great Lakes", 27.0, -5.0, 31.5, 2.0),
    ("Russian refineries (west of Urals)", 36.0, 46.0, 60.0, 60.0),
    ("Korean DMZ",              125.5, 37.5, 129.5, 39.0),
    ("Strait of Hormuz",        54.5, 24.5, 58.5, 27.5),
    ("Sahel (Mali/Burkina/Niger)", -8.0, 11.5, 8.0, 16.5),
    ("Myanmar",                 92.0, 9.5, 102.0, 28.5),
]

ENDPOINT = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCE = "VIIRS_SNPP_NRT"  # VIIRS S-NPP near-real-time; 375m pixels
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"


class FirmsSection(Section):
    id = "firms"
    title = "Active fires near conflict zones (NASA FIRMS / VIIRS)"
    emoji = "🔥"

    PULL_TIMEOUT_S = 90
    DAY_RANGE = 1     # last 24h
    MIN_CONFIDENCE = {"nominal", "n", "high", "h"}  # drop low-confidence
    THROTTLE_S = 1.0

    def _zone_url(self, key: str, west: float, south: float, east: float, north: float) -> str:
        area = f"{west},{south},{east},{north}"
        return f"{ENDPOINT}/{key}/{SOURCE}/{area}/{self.DAY_RANGE}"

    def pull(self) -> list[dict]:
        key = os.environ.get("FIRMS_MAP_KEY")
        if not key:
            return []
        items: list[dict] = []
        for zone, w, s, e, n in ZONES:
            url = self._zone_url(key, w, s, e, n)
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
                if resp.status_code != 200 or not resp.text.strip():
                    time.sleep(self.THROTTLE_S)
                    continue
                # FIRMS returns CSV with header. If quota exceeded or
                # invalid key, the body is an HTML/text error page.
                text = resp.text
                if not text.lstrip().lower().startswith("latitude") and "latitude" not in text[:200].lower():
                    time.sleep(self.THROTTLE_S)
                    continue
                reader = csv.DictReader(io.StringIO(text))
                for row in reader:
                    conf = (row.get("confidence") or "").strip().lower()
                    if conf and conf not in self.MIN_CONFIDENCE:
                        continue
                    try:
                        lat = float(row["latitude"])
                        lon = float(row["longitude"])
                    except (KeyError, ValueError):
                        continue
                    brightness = row.get("bright_ti4") or row.get("brightness", "")
                    frp = row.get("frp", "")  # fire radiative power, MW
                    acq_date = row.get("acq_date", "")
                    acq_time = row.get("acq_time", "")
                    sat = row.get("satellite", "")
                    items.append({
                        "id": f"firms-{zone}-{acq_date}-{acq_time}-{lat:.3f}-{lon:.3f}",
                        "date": acq_date,
                        "title": f"[{zone}] VIIRS fire detection at {lat:.3f}, {lon:.3f} (FRP {frp} MW, conf {conf})",
                        "url": f"https://firms.modaps.eosdis.nasa.gov/usfs/map/#d:24hrs;@{lon:.3f},{lat:.3f},9z",
                        "summary": f"sat {sat} · brightness {brightness} K · acquired {acq_date} {acq_time}Z",
                        "zone": zone,
                        "latitude": lat,
                        "longitude": lon,
                        "frp": frp,
                        "confidence": conf,
                        "satellite": sat,
                        "acq_datetime": f"{acq_date}T{acq_time}Z",
                    })
            except Exception:
                pass
            time.sleep(self.THROTTLE_S)
        # Sort by FRP desc (most intense first), then by zone
        def frp_num(it: dict) -> float:
            try:
                return float(it.get("frp") or 0)
            except ValueError:
                return 0.0
        items.sort(key=lambda it: (frp_num(it), it.get("acq_datetime", "")), reverse=True)
        return items
