"""
acled.py — ACLED conflict events via OAuth password flow.

ACLED moved off the static API key model in 2025. Auth is now OAuth2
password grant: exchange email + password for a 1-hour Bearer token,
then call /api/acled/read with the token in the Authorization header.

Credentials come from env:
  ACLED_EMAIL
  ACLED_PASSWORD

Register at https://acleddata.com/register/ then accept the API terms.
Tokens are cached on disk at ~/.worldscope/acled_token.json with their
expiry timestamp; we refresh proactively when <120s remain.

We pull the last 7 days of events, filtered to a watchlist of high-event
countries plus all events with fatalities >= 5 worldwide. That keeps
the daily payload bounded (typically 200-500 events) while ensuring we
catch surge days everywhere.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from . import Section

TOKEN_URL = "https://acleddata.com/oauth/token"
READ_URL = "https://acleddata.com/api/acled/read"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"
TOKEN_CACHE = Path.home() / ".worldscope" / "acled_token.json"

# High-event-density watchlist. ACLED uses ISO country names; we send
# them as the `country` field which accepts pipe-delimited OR semantics.
WATCHLIST = [
    "Ukraine", "Russia", "Israel", "Palestine", "Lebanon", "Syria",
    "Iraq", "Yemen", "Iran", "Sudan", "South Sudan", "Ethiopia",
    "Somalia", "Democratic Republic of Congo", "Burkina Faso", "Mali",
    "Niger", "Nigeria", "Cameroon", "Myanmar", "Pakistan", "Afghanistan",
    "Mexico", "Colombia", "Haiti", "Venezuela",
]


class AcledSection(Section):
    id = "acled"
    title = "ACLED conflict events"
    emoji = "⚔️"

    PULL_TIMEOUT_S = 120

    def _load_cached_token(self) -> str | None:
        if not TOKEN_CACHE.exists():
            return None
        try:
            data = json.loads(TOKEN_CACHE.read_text())
            if data.get("expires_at", 0) - time.time() > 120:
                return data.get("access_token")
        except Exception:
            return None
        return None

    def _save_token(self, token: str, expires_in: int) -> None:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "access_token": token,
            "expires_at": time.time() + expires_in,
        }))

    def _get_token(self) -> str | None:
        cached = self._load_cached_token()
        if cached:
            return cached
        email = os.environ.get("ACLED_EMAIL")
        password = os.environ.get("ACLED_PASSWORD")
        if not email or not password:
            return None
        resp = requests.post(
            TOKEN_URL,
            data={
                "username": email,
                "password": password,
                "grant_type": "password",
                "client_id": "acled",
                "scope": "authenticated",
            },
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        token = body.get("access_token")
        if not token:
            return None
        self._save_token(token, int(body.get("expires_in", 3600)))
        return token

    def _query(self, token: str, params: dict[str, Any]) -> list[dict]:
        resp = requests.get(
            READ_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
            timeout=60,
        )
        if resp.status_code == 401:
            # Token rejected; nuke cache so next run refreshes.
            try:
                TOKEN_CACHE.unlink()
            except Exception:
                pass
            raise RuntimeError(
                f"[{self.id}] ACLED 401 unauthorized — cached token cleared, "
                "next run will refresh"
            )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", []) or []

    def pull(self) -> list[dict]:
        token = self._get_token()
        if not token:
            raise RuntimeError(
                f"[{self.id}] ACLED token unavailable: set ACLED_EMAIL and "
                "ACLED_PASSWORD env vars, or check that the OAuth token "
                "endpoint is reachable"
            )
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=7)
        items: list[dict] = []
        # Watchlist countries: all events.
        watchlist_params = {
            "country": "|".join(WATCHLIST),
            "event_date": f"{start.isoformat()}|{end.isoformat()}",
            "event_date_where": "BETWEEN",
            "limit": 1500,
        }
        for ev in self._query(token, watchlist_params):
            items.append(self._normalize(ev))
        # Global high-fatality sweep (catches surge days outside watchlist).
        try:
            global_params = {
                "event_date": f"{start.isoformat()}|{end.isoformat()}",
                "event_date_where": "BETWEEN",
                "fatalities": 5,
                "fatalities_where": ">=",
                "limit": 500,
            }
            seen_ids = {it["id"] for it in items}
            for ev in self._query(token, global_params):
                it = self._normalize(ev)
                if it["id"] not in seen_ids:
                    items.append(it)
        except Exception:
            pass
        items.sort(key=lambda it: (it.get("date", ""), it.get("fatalities", 0)), reverse=True)
        return items

    @staticmethod
    def _normalize(ev: dict) -> dict:
        ev_id = ev.get("data_id") or ev.get("event_id_cnty", "")
        date = ev.get("event_date", "")
        country = ev.get("country", "")
        ev_type = ev.get("event_type", "")
        sub_type = ev.get("sub_event_type", "")
        loc = ev.get("location", "")
        fatalities = int(ev.get("fatalities", 0) or 0)
        notes = ev.get("notes", "")
        actor1 = ev.get("actor1", "")
        actor2 = ev.get("actor2", "")
        actors = " vs ".join(a for a in (actor1, actor2) if a) or actor1 or ""
        return {
            "id": f"acled-{ev_id}",
            "date": date,
            "title": f"[{country}] {ev_type}: {sub_type} at {loc} ({fatalities} fatalities)",
            "url": f"https://acleddata.com/dashboard/#/dashboard?event_id={ev_id}" if ev_id else "https://acleddata.com/dashboard/",
            "summary": (notes[:280] + "...") if len(notes) > 280 else notes,
            "country": country,
            "event_type": ev_type,
            "sub_event_type": sub_type,
            "actors": actors,
            "fatalities": fatalities,
            "location": loc,
            "latitude": ev.get("latitude"),
            "longitude": ev.get("longitude"),
        }
