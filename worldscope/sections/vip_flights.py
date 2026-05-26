"""
vip_flights.py — government / military aircraft visible to OpenSky right now.

Spike result (2026-05-25): the anonymous OpenSky tier filters out LADD/BARR-
protected presidential aircraft, but tier-2 government and military traffic
(USAF Reach, RAF, Canadian Forces, French government, etc.) IS visible.
That tier-2 traffic is arguably more diagnostic of diplomatic activity than
the principal's plane itself — entourage and support aircraft converge on
meeting locations whether or not POTUS's own jet is broadcasting.

API: https://opensky-network.org/api/states/all  (anonymous; ~10s cadence)
No auth on the free tier. Authenticated tier (free signup) raises limits.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from . import Section

API = "https://opensky-network.org/api/states/all"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Callsign prefixes for known government / military air operators.
# Sources: ICAO operator codes, public OSINT plane-spotter lists.
GOV_PREFIXES = (
    # United States
    "RCH",     # USAF Reach (strategic airlift, KC-46/C-17/C-5)
    "SAM",     # Special Air Mission (VC-25, C-32, C-37 — VIP transport)
    "AF1", "AF2",  # AF One / Two
    "EXEC1", "EXEC2",  # Executive support
    "PAT",     # US Army priority air mission
    "NCR",     # National Capital Region transport
    "OPEC",    # USAF OperationsCommand
    "NIGHT",   # E-4B Nightwatch
    "PUMA",    # Special operations
    "DOS",     # State Department
    # NATO/UK
    "RRR",     # RAF "Rafair"
    "BAF",     # Belgian AF
    "GAF",     # German AF (Bundeswehr)
    "KAF",     # Dutch AF
    "FAF",     # Finnish AF
    "CNA",     # Czech / Slovenian AF
    "IAF",     # Italian AF
    "FNY",     # French Navy
    # Other
    "CFC",     # Canadian Forces
    "JEDI",   # NATO AWACS
    "BLY",     # NATO transport
    "PLA",     # Chinese PLA Air Force
    "CHN",     # Chinese government
    "RFF",     # Russian government
    "JAF",     # Japanese SDF
    "KOR",     # South Korean AF
    "INDIA",   # Indian AF
    "EAGL",    # Israeli AF
    "NIGER",   # Nigerian government (also "NGA")
    "AZIA",    # Various Asian
)


class VipFlightsSection(Section):
    id = "vip_flights"
    title = "Government & military aircraft airborne (OpenSky)"
    emoji = "✈️"

    LIMIT = 30

    def pull(self) -> list[dict]:
        try:
            resp = requests.get(API, headers={"User-Agent": UA}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        states = data.get("states") or []
        snapshot_ts = data.get("time")
        snapshot_iso = (
            datetime.fromtimestamp(snapshot_ts, tz=timezone.utc).isoformat()
            if snapshot_ts else ""
        )

        items: list[dict] = []
        for s in states:
            if not s:
                continue
            icao24 = (s[0] or "").lower()
            callsign = (s[1] or "").strip().upper()
            country = s[2] or ""
            lon, lat = s[5], s[6]
            altitude_m = s[7]
            on_ground = s[8] if len(s) > 8 else None
            velocity = s[9] if len(s) > 9 else None
            if not callsign:
                continue
            if not any(callsign.startswith(p) for p in GOV_PREFIXES):
                continue
            items.append({
                "id": icao24 + ":" + callsign,
                "date": snapshot_iso[:10] if snapshot_iso else "",
                "title": f"{callsign} ({country})",
                "url": f"https://opensky-network.org/aircraft-profile?icao24={icao24}",
                "summary": (
                    f"icao24: {icao24} · "
                    f"position: ({lat}, {lon}) · "
                    f"alt: {int(altitude_m)}m" if altitude_m else "on ground"
                ) + (f" · {int(velocity*3.6)}km/h" if velocity else ""),
                "icao24": icao24,
                "callsign": callsign,
                "country": country,
                "lat": lat,
                "lon": lon,
                "altitude_m": altitude_m,
                "on_ground": on_ground,
            })
            if len(items) >= self.LIMIT:
                break
        return items
