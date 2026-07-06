"""
reliefweb.py — ReliefWeb humanitarian reports (OCHA).

ReliefWeb is the UN OCHA aggregator for humanitarian situation reports,
flash appeals, cluster reports, and assessments. Excellent coverage of
under-reported crises (Sahel, DRC, Sudan, Yemen, Myanmar, Haiti).

API: https://apidoc.reliefweb.int/

Endpoint change (2025): the legacy v1 endpoint was retired and now returns
410 Gone. v2 is request-compatible with v1 but enforces a *pre-approved*
``appname`` — an unregistered appname returns 403. Register an appname at
https://apidoc.reliefweb.int/ and set it via the RELIEFWEB_APPNAME env var
(falls back to "worldscope", which will 403 until registered).
"""
from __future__ import annotations

import os

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

# v1 was retired (returns 410 Gone); v2 is request-compatible with v1.
API = "https://api.reliefweb.int/v2/reports"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"
# ReliefWeb enforces a pre-approved appname since 2025-11-01. Overridable so an
# operator can drop in their registered appname without editing code.
APPNAME = os.environ.get("RELIEFWEB_APPNAME", "worldscope")


class ReliefWebSection(Section):
    id = "reliefweb"
    title = "ReliefWeb — humanitarian situation reports"
    emoji = "🚨"

    PULL_TIMEOUT_S = 45
    LIMIT = 40

    def pull(self) -> list[dict]:
        params = {
            "appname": APPNAME,
            "limit": self.LIMIT,
            "sort[]": "date.created:desc",
            "fields[include][]": [
                "title", "date.created", "url_alias", "country.name",
                "format.name", "source.name", "primary_country.name",
                "body-html"
            ],
        }
        try:
            resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=30)
            # A 403 here is the appname gate, not a transient error: point the
            # operator at the fix rather than emitting an opaque HTTP status.
            if resp.status_code == 403:
                raise UpstreamHTTPError(
                    f"ReliefWeb rejected appname '{APPNAME}' (HTTP 403). Register "
                    f"an appname at https://apidoc.reliefweb.int/ and set the "
                    f"RELIEFWEB_APPNAME secret."
                )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"ReliefWeb request failed: {e}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise UpstreamParseError(f"ReliefWeb returned non-JSON: {e}") from e
        items: list[dict] = []
        for r in (data.get("data") or []):
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
