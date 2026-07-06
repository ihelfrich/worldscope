"""
vip_flights.py — government / military aircraft visible to OpenSky right now.

Spike result (2026-05-25): the anonymous OpenSky tier filters out LADD/BARR-
protected presidential aircraft, but tier-2 government and military traffic
(USAF Reach, RAF, Canadian Forces, French government, etc.) IS visible.
That tier-2 traffic is arguably more diagnostic of diplomatic activity than
the principal's plane itself — entourage and support aircraft converge on
meeting locations whether or not POTUS's own jet is broadcasting.

API: https://opensky-network.org/api/states/all

Auth (2025): OpenSky moved to OAuth2 client-credentials; Basic auth is
deprecated. The anonymous tier still works but gets only 400 credits/day and
rate-limits /states/all, so the unauthenticated full-snapshot pull times out
intermittently (the failure mode that stranded this section stale). Two
robustness measures: (1) if OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET are set
we authenticate for the higher, more reliable quota; (2) either way we retry
the snapshot a few times with backoff before giving up, so a single transient
timeout no longer marks the section stale. Create an API client on your
OpenSky Account page to obtain the client id/secret.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import requests

from . import Section, UpstreamHTTPError

API = "https://opensky-network.org/api/states/all"
TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# In-process OAuth token cache: {"access_token": str, "expires_at": epoch}.
_TOKEN_CACHE: dict = {}


def _opensky_token() -> str | None:
    """Return a Bearer token via the OAuth2 client-credentials grant when
    OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET are set, else None (anonymous).
    Cached in-process and refreshed proactively (tokens live ~30 min)."""
    client_id = os.environ.get("OPENSKY_CLIENT_ID")
    client_secret = os.environ.get("OPENSKY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    cached = _TOKEN_CACHE.get("access_token")
    if cached and _TOKEN_CACHE.get("expires_at", 0) - time.time() > 120:
        return cached
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token")
    if token:
        _TOKEN_CACHE["access_token"] = token
        _TOKEN_CACHE["expires_at"] = time.time() + int(body.get("expires_in", 1800))
    return token

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

    # Room for the retry budget below within the section's wall-clock deadline:
    # worst case is 3×REQUEST_TIMEOUT_S + sum(RETRY_BACKOFF_S) (+ token fetch).
    PULL_TIMEOUT_S = 100

    # Retry the snapshot before conceding failure. The anonymous /states/all
    # tier times out intermittently under rate-limiting; a couple of backed-off
    # retries turn most of those transient failures into a successful pull.
    MAX_ATTEMPTS = 3
    REQUEST_TIMEOUT_S = 20
    RETRY_BACKOFF_S = (3, 6)   # sleep before attempts 2 and 3

    def pull(self) -> list[dict]:
        # Let network/parse failures propagate: the base class records them as
        # STATE_STALE and carries the last good snapshot forward. Catching and
        # returning [] here would mask an outage as a legitimate "0 aircraft".
        headers = {"User-Agent": UA}
        token = _opensky_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        last_exc: Exception | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            if attempt:
                time.sleep(self.RETRY_BACKOFF_S[min(attempt - 1, len(self.RETRY_BACKOFF_S) - 1)])
            try:
                resp = requests.get(API, headers=headers, timeout=self.REQUEST_TIMEOUT_S)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                # Transient: retry.
                last_exc = e
                continue
            except requests.RequestException as e:
                # Non-transient HTTP error (e.g. 401/429): don't hammer it.
                raise UpstreamHTTPError(f"OpenSky request failed: {e}") from e
        else:
            raise UpstreamHTTPError(
                f"OpenSky unreachable after {self.MAX_ATTEMPTS} attempts: {last_exc}"
            )

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
                    + (f"alt: {int(altitude_m)}m" if altitude_m else "on ground")
                    + (f" · {int(velocity*3.6)}km/h" if velocity else "")
                ),
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
