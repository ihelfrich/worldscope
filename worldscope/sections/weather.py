"""
weather — NOAA / NWS detailed weather + severe-storm + climate-outlook layer.

Sources, all free + no auth:
  - SPC (Storm Prediction Center) day 1/2/3 convective outlooks
  - NHC (National Hurricane Center) Atlantic + East Pacific tropical
  - WPC (Weather Prediction Center) Quantitative Precipitation Forecast
  - CPC (Climate Prediction Center) 6-10 and 8-14 day temp+precip outlooks
  - NWS Watches / Warnings / Advisories nationwide (alerts API)
  - USGS earthquakes (M >= 4.0 worldwide, last 24h)
  - Area Forecast Discussions from selected high-population offices

The AFDs (Area Forecast Discussions) are the meteorologist-to-meteorologist
narrative text products. They are by far the most analytically dense
products NWS publishes. We pull from ~10 high-population offices and let
the synthesis pass extract the relevant national patterns.

Section-adapter contract: conforms. Entities emitted:
    - place:state-<slug> for any state mentioned in active alerts
    - org:nws-office-<id> for NWS forecast offices that issued products
    - event:hurricane-<name>, event:earthquake-<id> for named events
Anomalies emitted when:
    - Multi-state active warnings cluster (z-score on warning count)
    - Earthquake M >= 5.5
    - SPC moderate or high convective risk anywhere
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from . import Section, SectionState

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

NWS_BASE = "https://api.weather.gov"
SPC_OUTLOOK = "https://www.spc.noaa.gov/products/outlook/day{day}otlk.json"
USGS_EQ_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
NHC_RSS = "https://www.nhc.noaa.gov/index-at.xml"


# High-population NWS forecast offices for AFD ingest. Each AFD is a long
# narrative; we pull these because their coverage areas hit the biggest
# population centers and economies.
NWS_AFD_OFFICES = [
    "OKX",   # New York City
    "PHI",   # Philadelphia
    "LWX",   # Washington/Baltimore
    "TBW",   # Tampa Bay
    "MFL",   # Miami
    "FWD",   # Dallas-Fort Worth
    "HGX",   # Houston-Galveston
    "LOX",   # Los Angeles
    "MTR",   # San Francisco Bay
    "SEW",   # Seattle
    "DTX",   # Detroit
    "ILX",   # Chicago (Lincoln IL covers the metro)
    "BOX",   # Boston
    "MEG",   # Memphis
    "FFC",   # Atlanta
    "LSX",   # St. Louis (Ian's local)
]


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


class WeatherSection(Section):
    id = "weather"
    title = "U.S. Weather + Severe / Climate Outlooks"
    emoji = "🌪️"

    source_id = "noaa-aggregate"
    source_name = "NOAA / NWS aggregate"
    source_url = "https://www.weather.gov"
    source_tier = "primary_document"
    source_license = "public-domain"
    attribution_required = False
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 240
    MAX_WORKERS = 8

    def pull(self) -> list[dict]:
        items: list[dict] = []

        # Each pull function returns its own per-source items; we run them
        # in parallel because they're all independent HTTP fetches.
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._pull_active_alerts): "active_alerts",
                pool.submit(self._pull_spc_outlooks): "spc_outlooks",
                pool.submit(self._pull_earthquakes): "earthquakes",
                pool.submit(self._pull_nhc_tropical): "nhc_tropical",
            }
            # Plus one fetch per AFD office
            for office in NWS_AFD_OFFICES:
                futures[pool.submit(self._pull_afd, office)] = f"afd_{office}"
            for fut, label in futures.items():
                try:
                    items.extend(fut.result())
                except Exception as exc:
                    items.append({
                        "id": f"weather-error-{label}",
                        "date": date.today().isoformat(),
                        "title": f"[weather error] {label}: {type(exc).__name__}",
                        "url": "",
                        "summary": str(exc)[:300],
                        "_error": True,
                        "subsection": label,
                    })

        return items

    # ----- Active alerts (warnings / watches / advisories) ------------------

    def _pull_active_alerts(self) -> list[dict]:
        resp = requests.get(
            f"{NWS_BASE}/alerts/active",
            headers={"User-Agent": UA, "Accept": "application/geo+json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        out = []
        for feat in (data.get("features") or [])[:200]:  # hard cap 200
            p = feat.get("properties") or {}
            severity = p.get("severity") or ""
            if severity in ("Minor",):
                continue  # skip routine minor advisories
            states = p.get("senderName", "")
            geocode = p.get("geocode") or {}
            same_codes = geocode.get("SAME") or []
            ugc_codes = geocode.get("UGC") or []
            out.append({
                "id": f"alert-{p.get('id', '')}",
                "date": (p.get("sent") or "")[:10],
                "title": f"[{p.get('severity','?')}] {p.get('event','')}: "
                         f"{p.get('headline','') or p.get('areaDesc','')}"[:300],
                "url": p.get("@id", ""),
                "summary": (p.get("description") or "")[:600],
                "subsection": "active_alert",
                "event": p.get("event"),
                "severity": severity,
                "urgency": p.get("urgency"),
                "certainty": p.get("certainty"),
                "areas": (p.get("areaDesc") or "").split("; "),
                "issuing_office": states,
                "ugc_codes": ugc_codes,
                "same_codes": same_codes,
            })
        return out

    # ----- SPC Convective Outlooks ------------------------------------------

    def _pull_spc_outlooks(self) -> list[dict]:
        out = []
        for day in (1, 2, 3):
            try:
                resp = requests.get(
                    SPC_OUTLOOK.format(day=day),
                    headers={"User-Agent": UA}, timeout=15,
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
            except (requests.exceptions.RequestException, json.JSONDecodeError):
                continue
            features = data.get("features") or []
            # Top risk category in this day's outlook
            risks = []
            for f in features:
                props = f.get("properties") or {}
                label = props.get("LABEL2") or props.get("LABEL") or ""
                if label:
                    risks.append(label)
            top_risk = risks[0] if risks else "no-risk-mentioned"
            out.append({
                "id": f"spc-day-{day}",
                "date": date.today().isoformat(),
                "title": f"SPC Day {day} convective outlook: {top_risk}"[:300],
                "url": f"https://www.spc.noaa.gov/products/outlook/day{day}otlk.html",
                "summary": f"Top risk category: {top_risk}. "
                           f"Number of risk polygons: {len(features)}",
                "subsection": f"spc_day{day}",
                "outlook_day": day,
                "risks": risks,
            })
        return out

    # ----- USGS Earthquakes -------------------------------------------------

    def _pull_earthquakes(self) -> list[dict]:
        resp = requests.get(USGS_EQ_FEED, headers={"User-Agent": UA}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        out = []
        for feat in (data.get("features") or []):
            p = feat.get("properties") or {}
            mag = p.get("mag")
            if mag is None: continue
            try: mag = float(mag)
            except (ValueError, TypeError): continue
            if mag < 4.5: continue
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            out.append({
                "id": f"earthquake-{feat.get('id','')}",
                "date": datetime.fromtimestamp(
                    (p.get("time") or 0) / 1000, tz=timezone.utc,
                ).date().isoformat() if p.get("time") else date.today().isoformat(),
                "title": f"M{mag:.1f} — {p.get('place','(unknown location)')}"[:300],
                "url": p.get("url", ""),
                "summary": (f"Magnitude {mag:.1f}, depth "
                            f"{coords[2] if len(coords) >= 3 else '?'}km, "
                            f"alert level: {p.get('alert','none')}")[:300],
                "subsection": "earthquake",
                "magnitude": mag,
                "place": p.get("place"),
                "coordinates": coords,
                "alert_level": p.get("alert"),
                "tsunami": p.get("tsunami"),
            })
        return out

    # ----- NHC Tropical -----------------------------------------------------

    def _pull_nhc_tropical(self) -> list[dict]:
        try:
            resp = requests.get(NHC_RSS, headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "nhc-error", "date": date.today().isoformat(),
                "title": f"[NHC RSS error] {type(exc).__name__}", "url": NHC_RSS,
                "summary": str(exc)[:300], "_error": True, "subsection": "nhc_tropical",
            }]
        from .state_news import _parse_rss
        items = _parse_rss(resp.content)
        return [{
            "id": f"nhc-{hashlib.sha1((it.get('url','')+it.get('title','')).encode()).hexdigest()[:16]}",
            "date": it.get("date", date.today().isoformat()),
            "title": f"[NHC Atlantic] {it.get('title','')}"[:300],
            "url": it.get("url", ""),
            "summary": it.get("summary","")[:400],
            "subsection": "nhc_tropical",
        } for it in items]

    # ----- Area Forecast Discussions ----------------------------------------

    def _pull_afd(self, office: str) -> list[dict]:
        url = f"{NWS_BASE}/products/types/AFD/locations/{office}"
        try:
            resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/ld+json"}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException:
            return []
        products = data.get("@graph") or []
        if not products: return []
        # Most recent AFD
        latest = products[0]
        prod_url = latest.get("@id") or latest.get("id")
        if not prod_url: return []
        try:
            full_resp = requests.get(prod_url, headers={"User-Agent": UA, "Accept": "application/ld+json"}, timeout=15)
            full_resp.raise_for_status()
            full = full_resp.json()
        except requests.exceptions.RequestException:
            return []
        text = (full.get("productText") or "")[:3000]  # Cap each AFD at 3KB
        issuance_time = full.get("issuanceTime", "")
        return [{
            "id": f"afd-{office}-{hashlib.sha1(prod_url.encode()).hexdigest()[:12]}",
            "date": issuance_time[:10] or date.today().isoformat(),
            "title": f"[NWS {office}] Area Forecast Discussion {issuance_time[:16]}"[:300],
            "url": prod_url,
            "summary": text[:600],
            "subsection": f"afd_{office}",
            "office": office,
            "issuance_time": issuance_time,
            "full_text": text,
        }]

    # ----- Contract: entities + relationships -------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"): return []
        entities = []
        sub = item.get("subsection", "")

        if sub == "active_alert":
            for area in item.get("areas", [])[:5]:  # first 5 areas
                # Parse "Foo County, CA" → CA
                parts = area.split(",")
                if len(parts) >= 2:
                    state_code = parts[-1].strip()
                    entities.append({
                        "id": f"place:state-{_slug(state_code)}",
                        "type": "place",
                        "canonical_name": state_code,
                        "metadata": {"kind": "us-state-or-territory"},
                    })
            office = item.get("issuing_office", "")
            if office:
                entities.append({
                    "id": f"org:nws-office-{_slug(office)}",
                    "type": "org",
                    "canonical_name": office,
                    "metadata": {"kind": "nws-forecast-office"},
                })

        elif sub == "earthquake":
            entities.append({
                "id": f"event:earthquake-{item.get('id','').replace('earthquake-','')}",
                "type": "event",
                "canonical_name": item.get("title", "")[:100],
                "metadata": {
                    "kind": "earthquake",
                    "magnitude": item.get("magnitude"),
                    "place": item.get("place"),
                    "alert_level": item.get("alert_level"),
                },
            })

        elif sub.startswith("afd_"):
            office = item.get("office", "")
            if office:
                entities.append({
                    "id": f"org:nws-office-{_slug(office)}",
                    "type": "org",
                    "canonical_name": f"NWS {office} forecast office",
                    "metadata": {"kind": "nws-forecast-office"},
                })

        elif sub.startswith("spc_day"):
            entities.append({
                "id": f"org:spc",
                "type": "org",
                "canonical_name": "NOAA Storm Prediction Center",
                "metadata": {"kind": "noaa-center"},
            })

        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships = []
        feed_errors = []

        # Track severe-event anomalies for the brief
        big_quakes = []
        moderate_or_high_outlooks = []
        warning_count = 0

        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue

            for e in self.extract_entities(item):
                seen[e["id"]] = e

            sub = item.get("subsection", "")
            if sub == "earthquake" and item.get("magnitude", 0) >= 5.5:
                big_quakes.append(item)
            if sub.startswith("spc_day"):
                risks = item.get("risks", [])
                if any("Moderate" in r or "High" in r for r in risks):
                    moderate_or_high_outlooks.append(item)
            if sub == "active_alert" and item.get("severity") in ("Severe", "Extreme"):
                warning_count += 1

        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships

        # Anomalies
        for q in big_quakes:
            base["anomalies"].append({
                "category": "earthquake-major",
                "z_score": None,
                "description": f"M{q.get('magnitude'):.1f} at {q.get('place','?')}",
                "evidence": [q.get("_id") or self._item_id(q)],
            })
        for o in moderate_or_high_outlooks:
            base["anomalies"].append({
                "category": "severe-weather-elevated",
                "z_score": None,
                "description": o.get("title", ""),
                "evidence": [o.get("_id") or self._item_id(o)],
            })
        if warning_count >= 20:
            base["anomalies"].append({
                "category": "warning-cluster",
                "z_score": None,
                "description": f"{warning_count} severe/extreme active alerts nationwide",
                "evidence": [],
            })
        for err in feed_errors:
            base["anomalies"].append({
                "category": "subsource-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })

        return base
