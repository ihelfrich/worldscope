"""gdacs.py — GDACS global disaster alerts (UN OCHA / European Commission).

The Global Disaster Alert and Coordination System publishes near-real-time
alerts for earthquakes, tropical cyclones, floods, volcanoes, droughts and
wildfires, each with a Green/Orange/Red alert level, affected country, location
and population-exposure severity. It is a live, official, key-free replacement
for the (now access-gated) ReliefWeb disaster layer — and a physical-world
sensor the claim graph can corroborate news against.

Orange/Red alerts are emitted as lake anomalies so they surface in the radar /
graphics. A total-empty response is treated as an outage and raised (GDACS
always has current events).

API: https://www.gdacs.org/gdacsapi/  (GeoJSON, no key, polite UA)
"""
from __future__ import annotations

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

API = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/MAP"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"

EVENT_TYPES = {
    "EQ": "Earthquake", "TC": "Tropical Cyclone", "FL": "Flood",
    "VO": "Volcano", "DR": "Drought", "WF": "Wildfire", "TS": "Tsunami",
}
_ALERT_RANK = {"Green": 0, "Orange": 1, "Red": 2}


def _url_of(prop: dict) -> str:
    u = prop.get("url")
    if isinstance(u, dict):
        return u.get("report") or u.get("details") or ""
    return u or ""


def _severity_text(prop: dict) -> str:
    sev = prop.get("severitydata")
    if isinstance(sev, dict):
        return sev.get("severitytext") or ""
    return ""


class GdacsSection(Section):
    id = "gdacs"
    title = "GDACS — global disaster alerts"
    emoji = "🌋"

    source_id = "gdacs"
    source_name = "Global Disaster Alert and Coordination System (GDACS)"
    source_url = "https://www.gdacs.org"
    source_tier = "primary_document"
    source_license = "open"
    source_country = None
    source_language = "en"
    PULL_TIMEOUT_S = 45

    def pull(self) -> list[dict]:
        try:
            resp = requests.get(API, headers={"User-Agent": UA}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"GDACS request failed: {e}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise UpstreamParseError(f"GDACS returned non-JSON: {e}") from e
        feats = data.get("features") or []
        if not feats:
            # GDACS always carries current events; empty means a real outage or
            # an upstream shape change, not a quiet day.
            raise UpstreamHTTPError("GDACS returned no events (outage / shape change)")

        items: list[dict] = []
        for ft in feats:
            p = ft.get("properties") or {}
            etype = EVENT_TYPES.get(p.get("eventtype", ""), p.get("eventtype", "") or "Event")
            alert = (p.get("alertlevel") or "Green").title()
            country = p.get("country") or ""
            name = p.get("name") or f"{etype} event"
            sev = _severity_text(p)
            coords = (ft.get("geometry") or {}).get("coordinates") or [None, None]
            items.append({
                "id": f"gdacs-{p.get('eventtype', '')}-{p.get('eventid', '')}",
                "date": (p.get("fromdate") or "")[:10],
                "title": f"[{alert}] {name}",
                "url": _url_of(p),
                "summary": (f"{etype} · {alert} alert · {country}"
                            + (f" · {sev}" if sev else "")),
                "alert_level": alert,
                "event_type": etype,
                "country": country,
                "iso3": p.get("iso3", ""),
                "lon": coords[0] if isinstance(coords, list) else None,
                "lat": coords[1] if isinstance(coords, list) and len(coords) > 1 else None,
            })
        items.sort(key=lambda it: _ALERT_RANK.get(it["alert_level"], 0), reverse=True)
        return items

    def emit_structured(self, state: "SectionState") -> dict:
        base = super().emit_structured(state)
        for it in state.items:
            if it.get("alert_level") in ("Orange", "Red"):
                base["anomalies"].append({
                    "category": f"disaster-{(it.get('event_type') or '').lower().replace(' ', '-')}",
                    "z_score": 2.5 if it["alert_level"] == "Red" else 1.6,
                    "description": f"{it['alert_level']} alert · {it.get('title', '')}",
                    "evidence": [it["id"]],
                })
        return base
