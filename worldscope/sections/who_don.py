"""who_don.py — WHO Disease Outbreak News (official outbreak notices).

WHO DON is the World Health Organization's curated channel for acute public-
health events — the authoritative, official disease-outbreak layer (3000+
notices since 1996). A live replacement for the (now access-gated) ProMED feed,
and a primary-source disease sensor for the claim graph.

API: https://www.who.int/api/news/diseaseoutbreaknews  (OData JSON, no key)
"""
from __future__ import annotations

import requests

from ..textutil import strip_html
from . import Section, UpstreamHTTPError, UpstreamParseError

API = "https://www.who.int/api/news/diseaseoutbreaknews"
ITEM_URL = "https://www.who.int/emergencies/disease-outbreak-news/item/{slug}"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"


class WhoDonSection(Section):
    id = "who_don"
    title = "WHO Disease Outbreak News"
    emoji = "🦠"

    source_id = "who_don"
    source_name = "WHO Disease Outbreak News"
    source_url = "https://www.who.int/emergencies/disease-outbreak-news"
    source_tier = "primary_document"
    source_license = "CC-BY-NC-SA-3.0-IGO"
    attribution_required = True
    attribution_text = "© WHO"
    source_country = None
    source_language = "en"
    PULL_TIMEOUT_S = 45
    LIMIT = 40

    def pull(self) -> list[dict]:
        params = {"$orderby": "PublicationDate desc", "$top": self.LIMIT,
                  "$select": "Id,DonId,Title,OverrideTitle,UseOverrideTitle,"
                             "Summary,PublicationDate,UrlName,ItemDefaultUrl"}
        try:
            resp = requests.get(API, params=params,
                                headers={"User-Agent": UA, "Accept": "application/json"},
                                timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"WHO DON request failed: {e}") from e
        try:
            rows = resp.json().get("value") or []
        except ValueError as e:
            raise UpstreamParseError(f"WHO DON returned non-JSON: {e}") from e
        if not rows:
            raise UpstreamHTTPError("WHO DON returned no notices (outage / shape change)")

        items: list[dict] = []
        for r in rows:
            title = ((r.get("OverrideTitle") if r.get("UseOverrideTitle") else None)
                     or r.get("Title") or "WHO outbreak notice")
            slug = (r.get("UrlName") or (r.get("ItemDefaultUrl") or "").lstrip("/"))
            pub = (r.get("PublicationDate") or "")[:10]
            summary = strip_html(r.get("Summary") or "")[:400]
            items.append({
                "id": f"who-don-{r.get('DonId') or r.get('Id') or slug}",
                "date": pub,
                "title": title.strip(),
                "url": ITEM_URL.format(slug=slug) if slug else self.source_url,
                "summary": summary,
                "topics": ["disease", "public-health"],
            })
        return items
