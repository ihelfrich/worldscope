"""
acled.py — ACLED conflict events via OAuth password flow.

ACLED moved off the static API key model in 2025. Auth is now OAuth2
password grant (verified live 2026-10-09 against the documented flow at
https://acleddata.com/api-documentation/getting-started):

  POST https://acleddata.com/oauth/token  (application/x-www-form-urlencoded)
       username=<email> password=<pw> grant_type=password client_id=acled
       scope=authenticated
  -> {"token_type": "Bearer", "expires_in": 86400,
      "access_token": "...", "refresh_token": "..."}

The access token lasts 24h, the refresh token 14 days; refreshing is the
same endpoint with grant_type=refresh_token + refresh_token + client_id.
Error responses are JSON: 400 {"error": "invalid_grant",
"error_description": "The user credentials were incorrect."} for bad
credentials, 400 {"error": "invalid_request", ...} for a malformed body.

Data: GET https://acleddata.com/api/acled/read?_format=json&... with
`Authorization: Bearer <token>`. Without a token it answers 403
{"message": "Access denied"}; with a bad/expired token 401 {"message":
"The resource owner or authorization server denied the request."}.

Credentials come from env:
  ACLED_EMAIL
  ACLED_PASSWORD

Register at https://acleddata.com/register/ then accept the API terms.
Tokens (access + refresh) are cached on disk at
~/.worldscope/acled_token.json with their expiry timestamp; we refresh
proactively when <120s remain, preferring the refresh grant and falling
back to the password grant.

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

from . import Section, MissingCredential, UpstreamAuthError, UpstreamHTTPError, UpstreamParseError

TOKEN_URL = "https://acleddata.com/oauth/token"
READ_URL = "https://acleddata.com/api/acled/read"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"
TOKEN_CACHE = Path.home() / ".worldscope" / "acled_token.json"

# Retry policy for 429 / 5xx on both the token and read endpoints.
MAX_ATTEMPTS = 3
BACKOFF_S = 2.0
RETRY_STATUSES = {429, 500, 502, 503, 504}

# High-event-density watchlist. ACLED uses ISO country names; we send
# them as the `country` field which accepts pipe-delimited OR semantics.
WATCHLIST = [
    "Ukraine", "Russia", "Israel", "Palestine", "Lebanon", "Syria",
    "Iraq", "Yemen", "Iran", "Sudan", "South Sudan", "Ethiopia",
    "Somalia", "Democratic Republic of Congo", "Burkina Faso", "Mali",
    "Niger", "Nigeria", "Cameroon", "Myanmar", "Pakistan", "Afghanistan",
    "Mexico", "Colombia", "Haiti", "Venezuela",
]


def _retry_after(resp: requests.Response, attempt: int) -> float:
    """Seconds to sleep before retrying: honor Retry-After, else exponential."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return min(float(ra), 30.0)
        except ValueError:
            pass
    return BACKOFF_S * (2 ** attempt)


def _error_detail(resp: requests.Response) -> str:
    """Best-effort human-readable detail from an ACLED error body."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:200]
    if isinstance(body, dict):
        for k in ("error_description", "message", "error", "hint"):
            if body.get(k):
                return str(body[k])[:200]
    return str(body)[:200]


class AcledSection(Section):
    id = "acled"
    title = "ACLED conflict events"
    emoji = "⚔️"

    PULL_TIMEOUT_S = 120

    # ---- token cache ---------------------------------------------------------

    def _read_cache(self) -> dict:
        if not TOKEN_CACHE.exists():
            return {}
        try:
            data = json.loads(TOKEN_CACHE.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _load_cached_token(self) -> str | None:
        data = self._read_cache()
        if data.get("expires_at", 0) - time.time() > 120:
            return data.get("access_token")
        return None

    def _save_token(self, body: dict) -> None:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "access_token": body.get("access_token"),
            "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + int(body.get("expires_in", 3600) or 3600),
        }))

    def _clear_cache(self) -> None:
        try:
            TOKEN_CACHE.unlink()
        except Exception:
            pass

    # ---- token acquisition ---------------------------------------------------

    def _token_request(self, form: dict[str, str]) -> dict | None:
        """POST to the OAuth endpoint with retry on 429/5xx.

        Returns the parsed token body on 200. Returns None on a 400/401
        (the grant was rejected: wrong password, expired refresh token, ...)
        so the caller can decide whether another grant type is worth trying.
        Raises UpstreamHTTPError when the endpoint is unreachable or keeps
        failing with 429/5xx.
        """
        last_detail = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = requests.post(
                    TOKEN_URL, data=form,
                    headers={"User-Agent": UA, "Accept": "application/json"},
                    timeout=30,
                )
            except requests.RequestException as e:
                last_detail = str(e)
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_S * (2 ** attempt))
                    continue
                raise UpstreamHTTPError(f"ACLED token endpoint unreachable: {e}") from e
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError as e:
                    raise UpstreamParseError(f"ACLED token response is not JSON: {e}") from e
                if not isinstance(body, dict) or not body.get("access_token"):
                    raise UpstreamParseError("ACLED token response lacks access_token")
                return body
            if resp.status_code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                time.sleep(_retry_after(resp, attempt))
                last_detail = f"{resp.status_code}"
                continue
            if resp.status_code in (400, 401, 403):
                self._last_grant_error = f"{resp.status_code} {_error_detail(resp)}"
                return None
            raise UpstreamHTTPError(
                f"ACLED token endpoint returned {resp.status_code}: {_error_detail(resp)}")
        raise UpstreamHTTPError(f"ACLED token endpoint kept failing ({last_detail})")

    def _get_token(self) -> str:
        cached = self._load_cached_token()
        if cached:
            return cached
        email = os.environ.get("ACLED_EMAIL")
        password = os.environ.get("ACLED_PASSWORD")
        if not email or not password:
            raise MissingCredential("ACLED_EMAIL / ACLED_PASSWORD not set")
        self._last_grant_error = ""
        # Prefer the refresh grant (14-day refresh token) when we have one.
        refresh = self._read_cache().get("refresh_token")
        if refresh:
            body = self._token_request({
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": "acled",
            })
            if body:
                self._save_token(body)
                return body["access_token"]
            self._clear_cache()
        body = self._token_request({
            "username": email,
            "password": password,
            "grant_type": "password",
            "client_id": "acled",
            "scope": "authenticated",
        })
        if not body:
            raise UpstreamAuthError(
                f"ACLED rejected credentials for {email}: {self._last_grant_error} "
                "(check ACLED_EMAIL/ACLED_PASSWORD and that API terms are accepted)")
        self._save_token(body)
        return body["access_token"]

    # ---- data ----------------------------------------------------------------

    def _query(self, token: str, params: dict[str, Any]) -> list[dict]:
        params = {"_format": "json", **params}
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = requests.get(
                    READ_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "User-Agent": UA,
                             "Accept": "application/json"},
                    timeout=60,
                )
            except requests.RequestException as e:
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_S * (2 ** attempt))
                    continue
                raise UpstreamHTTPError(f"ACLED read request failed: {e}") from e
            if resp.status_code in (401, 403):
                # Token rejected / not entitled; nuke cache so next run refreshes,
                # then fail loudly so this registers as an auth failure rather
                # than a quiet empty.
                self._clear_cache()
                raise UpstreamAuthError(
                    f"ACLED read rejected ({resp.status_code}): {_error_detail(resp)}")
            if resp.status_code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                time.sleep(_retry_after(resp, attempt))
                continue
            if resp.status_code != 200:
                raise UpstreamHTTPError(
                    f"ACLED read returned {resp.status_code}: {_error_detail(resp)}")
            try:
                body = resp.json()
            except ValueError as e:
                raise UpstreamParseError(f"ACLED read returned non-JSON: {e}") from e
            if not isinstance(body, dict):
                raise UpstreamParseError("ACLED read envelope is not an object")
            if body.get("success") is False:
                msgs = body.get("messages") or body.get("error") or ""
                raise UpstreamHTTPError(f"ACLED read reported failure: {str(msgs)[:200]}")
            return body.get("data", []) or []
        raise UpstreamHTTPError("ACLED read kept failing with 429/5xx")

    def pull(self) -> list[dict]:
        if not (os.environ.get("ACLED_EMAIL") and os.environ.get("ACLED_PASSWORD")):
            raise MissingCredential("ACLED_EMAIL / ACLED_PASSWORD not set")
        token = self._get_token()
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
        # Auth failures propagate; anything else is best-effort.
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
        except UpstreamAuthError:
            raise
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
