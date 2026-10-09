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

Verified live 2026-10-09:
  - geteventlist/MAP with no parameters now answers
    400 {"message":"Eventtype is required."}; it accepts exactly one
    `eventtype=EQ|TC|FL|VO|DR|WF` per call (two or more → 400 "Please
    specify only 1 eventtype.").
  - geteventlist/EVENTS4APP answers 200 with the current event list across
    all types (~100 features) in the same GeoJSON FeatureCollection shape:
    properties.eventtype/eventid (int)/name/alertlevel/country/iso3/fromdate,
    url {report, details, geometry}, severitydata {severity, severitytext,
    severityunit}, iscurrent "true"/"false".
  - https://www.gdacs.org/xml/rss.xml is also live (~200 items) but a
    different schema; not used.

Primary: EVENTS4APP (one call). Fallback when it fails or is empty: one MAP
call per event type, merged. Items keep the schema they always had.
"""
from __future__ import annotations

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

API_ROOT = "https://www.gdacs.org/gdacsapi/api/events/geteventlist"
API = f"{API_ROOT}/EVENTS4APP"          # all current events, all types
API_BY_TYPE = f"{API_ROOT}/MAP"         # requires a single ?eventtype=XX
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"

EVENT_TYPES = {
    "EQ": "Earthquake", "TC": "Tropical Cyclone", "FL": "Flood",
    "VO": "Volcano", "DR": "Drought", "WF": "Wildfire", "TS": "Tsunami",
}
FALLBACK_TYPES = ("EQ", "TC", "FL", "VO", "DR", "WF")
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

    def _fetch_features(self, url: str, params: dict | None = None) -> list[dict]:
        """GET one GeoJSON event list; HTTP failure → UpstreamHTTPError,
        non-JSON → UpstreamParseError."""
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"GDACS request failed: {e}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise UpstreamParseError(f"GDACS returned non-JSON: {e}") from e
        if not isinstance(data, dict):
            raise UpstreamParseError("GDACS returned a non-object body")
        return data.get("features") or []

    def _fetch_all(self) -> list[dict]:
        """EVENTS4APP first; per-type MAP calls if that fails or is empty."""
        try:
            feats = self._fetch_features(API)
        except UpstreamHTTPError:
            feats = []
        if feats:
            return feats
        merged: dict[tuple, dict] = {}
        last_err: Exception | None = None
        for et in FALLBACK_TYPES:
            try:
                for ft in self._fetch_features(API_BY_TYPE, {"eventtype": et}):
                    p = ft.get("properties") or {}
                    merged.setdefault((p.get("eventtype"), p.get("eventid")), ft)
            except UpstreamHTTPError as e:
                last_err = e
        if not merged:
            if last_err is not None:
                raise last_err
            # GDACS always carries current events; empty means a real outage or
            # an upstream shape change, not a quiet day.
            raise UpstreamHTTPError("GDACS returned no events (outage / shape change)")
        return list(merged.values())

    def pull(self) -> list[dict]:
        feats = self._fetch_all()
        items: list[dict] = []
        for ft in feats:
            p = ft.get("properties") or {}
            if str(p.get("iscurrent", "true")).lower() == "false":
                continue
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
        if not items:
            raise UpstreamHTTPError("GDACS returned no current events (outage / shape change)")
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
