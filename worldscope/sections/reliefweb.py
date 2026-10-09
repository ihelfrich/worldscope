"""
reliefweb.py — ReliefWeb humanitarian reports (OCHA).

ReliefWeb is the UN OCHA aggregator for humanitarian situation reports,
flash appeals, cluster reports, and assessments. Excellent coverage of
under-reported crises (Sahel, DRC, Sudan, Yemen, Myanmar, Haiti).

API: https://apidoc.reliefweb.int/  (polite UA needed)

Verified live 2026-10-09:
  - v1 is decommissioned: GET /v1/reports answers 410
    {"status":410,"error":{"message":"The API version 'v1' has been
    decommissioned. Please use version 'v2' instead."}}
  - v2 keeps the same query shape (fields[include][], sort[], limit) and
    the same result envelope (time/href/links/totalCount/count/data, each
    item {id, score, href, fields}).
  - Since 1 Nov 2025 the mandatory `appname` must be pre-approved by
    ReliefWeb (request form linked from
    https://apidoc.reliefweb.int/parameters#appname). An unapproved name
    answers 403 {"status":403,"error":{"type":"AccessDeniedHttpException",
    "message":"You are not using an approved appname. ..."}}; a missing
    one answers 400 "Missing appname parameter". Limits: 1000 entries per
    call, 1000 calls per day.

The appname is read from RELIEFWEB_APPNAME (default "worldscope"). A 403 is
raised as UpstreamAuthError so the run report shows the real cause rather
than a quiet empty.
"""
from __future__ import annotations

import os
import time

import requests

from . import Section, UpstreamAuthError, UpstreamHTTPError, UpstreamParseError

API = "https://api.reliefweb.int/v2/reports"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"
DEFAULT_APPNAME = "worldscope"

MAX_ATTEMPTS = 3
BACKOFF_S = 2.0
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _upstream_message(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:200]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])[:300]
    return str(body)[:200]


class ReliefWebSection(Section):
    id = "reliefweb"
    title = "ReliefWeb — humanitarian situation reports"
    emoji = "🚨"

    PULL_TIMEOUT_S = 45
    LIMIT = 40

    @staticmethod
    def appname() -> str:
        return (os.environ.get("RELIEFWEB_APPNAME") or "").strip() or DEFAULT_APPNAME

    def _request(self, params: dict) -> requests.Response:
        """GET with retry on 429/5xx; typed errors for everything else."""
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=30)
            except requests.RequestException as e:
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_S * (2 ** attempt))
                    continue
                raise UpstreamHTTPError(f"ReliefWeb request failed: {e}") from e
            if resp.status_code == 200:
                return resp
            if resp.status_code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                time.sleep(BACKOFF_S * (2 ** attempt))
                continue
            if resp.status_code in (401, 403):
                raise UpstreamAuthError(
                    f"ReliefWeb rejected appname {self.appname()!r} ({resp.status_code}): "
                    f"{_upstream_message(resp)} — set RELIEFWEB_APPNAME to an approved name")
            raise UpstreamHTTPError(
                f"ReliefWeb returned {resp.status_code}: {_upstream_message(resp)}")
        raise UpstreamHTTPError("ReliefWeb kept failing with 429/5xx")

    def pull(self) -> list[dict]:
        params = {
            "appname": self.appname(),
            "limit": self.LIMIT,
            "sort[]": "date.created:desc",
            "fields[include][]": [
                "title", "date.created", "url_alias", "country.name",
                "format.name", "source.name", "primary_country.name",
                "body-html"
            ],
        }
        resp = self._request(params)
        try:
            data = resp.json()
        except ValueError as e:
            raise UpstreamParseError(f"ReliefWeb returned non-JSON: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise UpstreamParseError("ReliefWeb envelope lacks a 'data' list")
        items: list[dict] = []
        for r in data["data"]:
            f = r.get("fields") or {}
            title = f.get("title", "")
            created = (f.get("date") or {}).get("created", "")
            date_str = created[:10] if created else ""
            url = f.get("url_alias") or f"https://reliefweb.int/node/{r.get('id','')}"
            countries = [c.get("name") for c in (f.get("country") or []) if c.get("name")]
            primary = (f.get("primary_country") or {}).get("name", "")
            fmt = ", ".join(x.get("name") for x in (f.get("format") or []) if x.get("name"))
            source = ", ".join(x.get("name") for x in (f.get("source") or []) if x.get("name"))
            body = (f.get("body-html") or "")[:280]
            items.append({
                "id": f"rw-{r.get('id','')}",
                "date": date_str,
                "title": f"[{primary or (countries[0] if countries else 'Global')}] {title}",
                "url": url,
                "summary": f"{fmt} · {source}",
                "country": primary or (countries[0] if countries else ""),
                "all_countries": countries,
                "topics": ["humanitarian"],
                "_source": self.id,
                "_body": body,
            })
        return items
