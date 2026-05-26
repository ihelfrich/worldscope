"""
reliefweb.py — ReliefWeb humanitarian reports (OCHA).

ReliefWeb is the UN OCHA aggregator for humanitarian situation reports,
flash appeals, cluster reports, and assessments. Excellent coverage of
under-reported crises (Sahel, DRC, Sudan, Yemen, Myanmar, Haiti).

API: https://apidoc.reliefweb.int/  (no key required, polite UA needed)
"""
from __future__ import annotations

import requests

from . import Section

API = "https://api.reliefweb.int/v1/reports"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"


class ReliefWebSection(Section):
    id = "reliefweb"
    title = "ReliefWeb — humanitarian situation reports"
    emoji = "🚨"

    PULL_TIMEOUT_S = 45
    LIMIT = 40

    def pull(self) -> list[dict]:
        params = {
            "appname": "worldscope",
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
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
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
