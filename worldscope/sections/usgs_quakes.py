"""usgs_quakes.py — USGS significant earthquakes (M4.5+, past day).

A live, key-free physical-world seismic sensor. Corroborates GDACS earthquake
alerts, feeds the claim graph with ground-truth events, and flags large quakes
(M>=6), tsunami-flagged events, and PAGER-alerted events as lake anomalies.

Feed: https://earthquake.usgs.gov/earthquakes/feed/v1.0/  (GeoJSON, no key)
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"
BIG_MAG = 6.0


def _date_of(epoch_ms) -> str:
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000,
                                      tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return ""


class UsgsQuakesSection(Section):
    id = "usgs_quakes"
    title = "USGS earthquakes (M4.5+, past day)"
    emoji = "🌐"

    source_id = "usgs_quakes"
    source_name = "USGS Earthquake Hazards Program"
    source_url = "https://earthquake.usgs.gov"
    source_tier = "primary_document"
    source_license = "public-domain"
    source_country = None
    source_language = "en"
    PULL_TIMEOUT_S = 30

    def pull(self) -> list[dict]:
        try:
            resp = requests.get(FEED, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"USGS request failed: {e}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise UpstreamParseError(f"USGS returned non-JSON: {e}") from e
        feats = data.get("features")
        if feats is None:
            raise UpstreamParseError("USGS response missing 'features'")
        # An empty list is legitimate here — a quiet day genuinely has no M4.5+.

        items: list[dict] = []
        for ft in feats:
            p = ft.get("properties") or {}
            coords = (ft.get("geometry") or {}).get("coordinates") or [None, None, None]
            try:
                mag = float(p.get("mag")) if p.get("mag") is not None else None
            except (TypeError, ValueError):
                mag = None
            alert = (p.get("alert") or "").lower()
            tsunami = int(p.get("tsunami") or 0)
            items.append({
                "id": ft.get("id") or p.get("code") or "",
                "date": _date_of(p.get("time")),
                "title": p.get("title") or f"M{mag} earthquake",
                "url": p.get("url") or "",
                "summary": (f"M{mag} · {p.get('place', '')} · depth "
                            f"{coords[2] if len(coords) > 2 else '?'} km"
                            + (" · TSUNAMI" if tsunami else "")
                            + (f" · PAGER {alert}" if alert and alert != "green" else "")),
                "mag": mag, "place": p.get("place", ""),
                "lon": coords[0], "lat": coords[1],
                "depth_km": coords[2] if len(coords) > 2 else None,
                "tsunami": tsunami, "alert": alert,
            })
        items.sort(key=lambda it: (it["mag"] or 0), reverse=True)
        return items

    def emit_structured(self, state: "SectionState") -> dict:
        base = super().emit_structured(state)
        for it in state.items:
            big = (it.get("mag") or 0) >= BIG_MAG
            alerted = it.get("alert") not in ("", "green")
            if big or it.get("tsunami") or alerted:
                base["anomalies"].append({
                    "category": "seismic-major",
                    "z_score": round(1.0 + (it.get("mag") or 0) / 3.0, 2),
                    "description": f"Major seismic event · {it.get('title', '')}"
                                   + (" · tsunami" if it.get("tsunami") else ""),
                    "evidence": [it["id"]],
                })
        return base
