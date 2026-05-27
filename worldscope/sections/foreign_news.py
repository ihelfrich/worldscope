"""
foreign_news — international news coverage, ~70 feeds across ~40 countries.

Curated by tier:
  - Major wire services (Reuters, AP, AFP, BBC, NHK, DW, Al Jazeera, etc.)
  - Major regional flagship papers (SCMP, Strait Times, Le Monde, etc.)
  - State-controlled (RT, Xinhua-English, Press TV, Sputnik) — flagged as such
  - International organizations (IMF, World Bank, UN, OCHA, BIS, OECD)
  - Central banks (ECB, BoJ, BoE, RBI, BoC, RBA, PBoC English)

Section-adapter contract: conforms. Per-record source tier preserved so the
synthesis pass can weight tier-1 (primary documents, central banks) over
state-controlled when they conflict.

For the Chinese-language internal layer, see chinese_internal.py (separate
section because it requires a Claude-Haiku translation step at ingest).
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from . import Section, SectionState
from .state_news import _parse_rss

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


# (country, feed_url, source_label, tier)
FEEDS: list[tuple[str, str, str, str]] = [
    # ---- WIRE SERVICES + GLOBAL OUTLETS (mainstream_independent) -----------
    ("Global",        "https://feeds.reuters.com/reuters/topNews",          "Reuters Top News",          "mainstream_independent"),
    ("Global",        "https://feeds.reuters.com/Reuters/worldNews",        "Reuters World",             "mainstream_independent"),
    ("Global",        "https://feeds.reuters.com/reuters/businessNews",     "Reuters Business",          "mainstream_independent"),
    ("Global",        "https://rsshub.app/ap/topics/apf-topnews",           "AP Top News (RSSHub)",      "mainstream_independent"),
    ("Global",        "https://feeds.bbci.co.uk/news/world/rss.xml",        "BBC World News",            "mainstream_independent"),
    ("Global",        "https://feeds.bbci.co.uk/news/business/rss.xml",     "BBC Business",              "mainstream_independent"),
    ("Global",        "https://www.aljazeera.com/xml/rss/all.xml",          "Al Jazeera English",        "mainstream_independent"),
    ("Global",        "https://www3.nhk.or.jp/nhkworld/en/news/feeds/news.xml", "NHK World English",     "mainstream_independent"),
    ("Global",        "https://rss.dw.com/rdf/rss-en-top",                  "Deutsche Welle English",    "mainstream_independent"),
    ("Global",        "https://www.france24.com/en/rss",                    "France 24 English",         "mainstream_independent"),
    ("Global",        "https://www.theguardian.com/world/rss",              "The Guardian (World)",      "mainstream_independent"),
    ("Global",        "https://www.economist.com/the-world-this-week/rss.xml", "The Economist (weekly)", "mainstream_independent"),

    # ---- ASIA-PACIFIC ------------------------------------------------------
    ("Hong Kong",     "https://www.scmp.com/rss/91/feed",                   "South China Morning Post (HK)", "mainstream_independent"),
    ("Singapore",     "https://www.straitstimes.com/news/world/rss.xml",    "The Straits Times (SG)",    "mainstream_independent"),
    ("Japan",         "https://www.japantimes.co.jp/feed/",                 "The Japan Times",           "mainstream_independent"),
    ("South Korea",   "https://www.koreaherald.com/common/rss_xml.php?ct=102", "Korea Herald",          "mainstream_independent"),
    ("India",         "https://www.thehindu.com/news/national/feeder/default.rss", "The Hindu",         "mainstream_independent"),
    ("India",         "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml", "Hindustan Times", "mainstream_independent"),
    ("Australia",     "https://www.abc.net.au/news/feed/2942460/rss.xml",   "ABC News (Australia)",      "mainstream_independent"),
    ("New Zealand",   "https://www.stuff.co.nz/rss/national",               "Stuff (NZ)",                "mainstream_independent"),
    ("Indonesia",     "https://www.thejakartapost.com/rss",                 "The Jakarta Post",          "mainstream_independent"),
    ("Philippines",   "https://www.philstar.com/rss/headlines",             "The Philippine Star",       "mainstream_independent"),

    # ---- EUROPE ------------------------------------------------------------
    ("France",        "https://www.lemonde.fr/en/rss/une.xml",              "Le Monde (English)",        "mainstream_independent"),
    ("Germany",       "https://rss.sueddeutsche.de/rss/Top",                "Süddeutsche Zeitung",       "mainstream_independent"),
    ("Germany",       "https://www.dw.com/atom/rss-en-ger",                 "DW Germany news",           "mainstream_independent"),
    ("United Kingdom","https://www.ft.com/world?format=rss",                "Financial Times (World)",   "mainstream_independent"),
    ("United Kingdom","https://www.theguardian.com/uk-news/rss",            "The Guardian (UK)",         "mainstream_independent"),
    ("Spain",         "https://english.elpais.com/rss/elpais/inenglish.xml","El País (English)",         "mainstream_independent"),
    ("Italy",         "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml", "ANSA (IT)",           "mainstream_independent"),
    ("Switzerland",   "https://www.swissinfo.ch/eng/rss",                   "Swissinfo",                 "mainstream_independent"),
    ("Belgium",       "https://www.politico.eu/feed",                       "POLITICO Europe",           "mainstream_independent"),

    # ---- MIDDLE EAST -------------------------------------------------------
    ("Israel",        "https://www.timesofisrael.com/feed/",                "The Times of Israel",       "mainstream_independent"),
    ("UAE",           "https://www.thenationalnews.com/feed",               "The National (UAE)",        "mainstream_independent"),
    ("Lebanon",       "https://www.dailystar.com.lb/rss/News",              "The Daily Star (Lebanon)",  "mainstream_independent"),
    ("Saudi Arabia",  "https://www.arabnews.com/rss.xml",                   "Arab News",                 "mainstream_independent"),
    ("Iran",          "https://www.iranintl.com/en/rss",                    "Iran International (Persian-language diaspora)", "mainstream_independent"),

    # ---- AFRICA ------------------------------------------------------------
    ("South Africa",  "https://mg.co.za/feed/",                             "Mail & Guardian",           "mainstream_independent"),
    ("South Africa",  "https://www.news24.com/feeds/rss/news/world.rss",    "News24",                    "mainstream_independent"),
    ("Kenya",         "https://nation.africa/kenya/rss",                    "Nation (Kenya)",            "mainstream_independent"),
    ("Nigeria",       "https://punchng.com/feed/",                          "The Punch (Nigeria)",       "mainstream_independent"),
    ("Egypt",         "https://english.ahram.org.eg/rss/3.aspx",            "Al-Ahram English",          "mainstream_independent"),

    # ---- AMERICAS ----------------------------------------------------------
    ("Canada",        "https://www.theglobeandmail.com/feeds/world/rss/",   "Globe and Mail (Canada)",   "mainstream_independent"),
    ("Brazil",        "https://noticias.uol.com.br/internacional/index.xml","UOL Internacional (BR)",    "mainstream_independent"),
    ("Argentina",     "https://www.batimes.com.ar/feed/rss/",               "Buenos Aires Times",        "mainstream_independent"),
    ("Mexico",        "https://www.eluniversal.com.mx/rss.xml",             "El Universal (MX)",         "mainstream_independent"),
    ("Chile",         "https://www.df.cl/noticias/rss",                     "Diario Financiero (CL)",    "mainstream_independent"),

    # ---- STATE-CONTROLLED (flagged: tier=state_controlled) -----------------
    ("Russia",        "https://www.rt.com/rss/news/",                       "RT (Russia, state-controlled)",      "state_controlled"),
    ("China",         "https://www.chinadaily.com.cn/rss/china_rss.xml",    "China Daily (state-controlled)",     "state_controlled"),
    ("China",         "http://www.xinhuanet.com/english/rss/chinarss.xml",  "Xinhua English (state-controlled)",  "state_controlled"),
    ("Iran",          "https://www.presstv.ir/RSS",                         "Press TV (Iran, state-controlled)",  "state_controlled"),
    ("Russia",        "https://sputnikglobe.com/rss20/",                    "Sputnik (state-controlled)",         "state_controlled"),
    ("Turkey",        "https://www.aa.com.tr/en/rss/default?cat=world",     "Anadolu Agency (semi-state)",        "state_controlled"),
    ("North Korea",   "https://kcnawatch.org/feed/",                        "KCNA Watch (NK monitor)",            "mainstream_independent"),

    # ---- INTERNATIONAL ORGS (primary_document) -----------------------------
    ("UN",            "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "UN News",               "primary_document"),
    ("UN",            "https://www.unocha.org/news/rss.xml",                "UN OCHA",                   "primary_document"),
    ("UN",            "https://reliefweb.int/updates/rss.xml",              "ReliefWeb (UN-OCHA)",       "primary_document"),
    ("Global",        "https://www.imf.org/en/News/RSS?language=eng",       "IMF News",                  "primary_document"),
    ("Global",        "https://www.worldbank.org/en/news/all.rss",          "World Bank News",           "primary_document"),
    ("Global",        "https://www.who.int/feeds/entity/csr/don/en/rss.xml","WHO Disease Outbreak News", "primary_document"),
    ("Global",        "https://www.bis.org/list/cbspeeches/index.rss",      "BIS Central-Bank Speeches", "primary_document"),
    ("Global",        "https://www.oecd.org/rss/oecd_news.xml",             "OECD News",                 "primary_document"),
    ("Global",        "https://www.sipri.org/rss/publications.xml",         "SIPRI Publications",        "primary_document"),

    # ---- CENTRAL BANKS (primary_document) ----------------------------------
    ("Eurozone",      "https://www.ecb.europa.eu/rss/press.html",           "ECB Press",                 "primary_document"),
    ("Japan",         "https://www.boj.or.jp/en/rss/whatsnew.xml",          "Bank of Japan",             "primary_document"),
    ("United Kingdom","https://www.bankofengland.co.uk/rss/news",           "Bank of England",           "primary_document"),
    ("China",         "http://www.pbc.gov.cn/english/130721/rss.xml",       "PBoC English",              "primary_document"),
    ("India",         "https://rbi.org.in/Scripts/RSSFeed.aspx?Mode=0",     "Reserve Bank of India",     "primary_document"),
    ("Canada",        "https://www.bankofcanada.ca/feed/",                  "Bank of Canada",            "primary_document"),
    ("Australia",     "https://www.rba.gov.au/rss/rss-cb-media-releases.xml", "Reserve Bank of Australia", "primary_document"),
]


class ForeignNewsSection(Section):
    id = "foreign_news"
    title = "International News + Multilateral Institutions"
    emoji = "🌍"

    source_id = "foreign-news-aggregate"
    source_name = "International news + multilateral aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mainstream_independent"   # per-record tier preserved
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "Per-feed attribution preserved in raw.jsonl. Headlines and excerpts "
        "used under fair use. State-controlled and state-affiliated sources "
        "labeled explicitly per the source-tier enum."
    )
    source_country = None     # multi-country aggregate
    source_language = "en"

    PULL_TIMEOUT_S = 360       # 70 feeds × ~1s with 16 workers ≈ 5-8s, plus slack
    LOOKBACK_DAYS = 2
    MAX_WORKERS = 16

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        items: list[dict] = []

        def fetch(country: str, url: str, source_label: str, tier: str) -> list[dict]:
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                return [{
                    "id": f"foreign-news-error-{_slug(country)}-{_slug(source_label)}",
                    "date": date.today().isoformat(),
                    "title": f"[feed error] {source_label}: {type(exc).__name__}",
                    "url": url,
                    "summary": str(exc)[:300],
                    "country": country,
                    "source_label": source_label,
                    "source_tier": tier,
                    "_error": True,
                }]
            feed_items = _parse_rss(resp.content)
            out = []
            for it in feed_items:
                try:
                    item_date = date.fromisoformat(it.get("date", "")[:10])
                except ValueError:
                    item_date = date.today()
                if item_date < cutoff:
                    continue
                item_id = hashlib.sha1(
                    f"{country}|{source_label}|{it.get('url','')}|{it.get('title','')}".encode()
                ).hexdigest()
                it["id"] = item_id
                it["country"] = country
                it["source_label"] = source_label
                it["source_tier"] = tier
                it["title"] = f"[{country}] {it['title']}"[:300]
                out.append(it)
            return out

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = [
                pool.submit(fetch, c, u, lbl, tier)
                for (c, u, lbl, tier) in FEEDS
            ]
            for fut in as_completed(futures):
                items.extend(fut.result())

        return items

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"foreign-news:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"):
            return []
        entities = []
        country = item.get("country", "")
        if country and country != "Global":
            entities.append({
                "id": f"place:country-{_slug(country)}",
                "type": "place",
                "canonical_name": country,
                "metadata": {"kind": "country"},
            })
        if item.get("source_label"):
            entities.append({
                "id": f"org:newsroom-{_slug(item['source_label'])}",
                "type": "org",
                "canonical_name": item["source_label"],
                "metadata": {
                    "kind": "newsroom",
                    "country": country,
                    "tier": item.get("source_tier"),
                },
            })
        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        relationships = []
        feed_errors = []
        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e
            country = item.get("country", "")
            label = item.get("source_label", "")
            if country and country != "Global" and label:
                relationships.append({
                    "from": f"org:newsroom-{_slug(label)}",
                    "to": f"place:country-{_slug(country)}",
                    "type": "reports-on",
                    "weight": 1.0,
                    "evidence": [item.get("_id") or self._item_id(item)],
                })

        base["entities_added"] = list(seen.values())
        base["relationships"] = relationships
        for err in feed_errors:
            base["anomalies"].append({
                "category": "feed-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })
        return base
