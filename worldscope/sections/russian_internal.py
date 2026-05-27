"""
russian_internal — Russian-language news from inside Russia + in-exile media.

Three tiers:

  State-controlled (Russian state media, Russian-language native):
    - TASS                  https://tass.ru/rss/v2.xml
    - RIA Novosti           https://ria.ru/export/rss2/index.xml
    - Izvestia              https://iz.ru/xml/rss/all.xml
    - Lenta.ru              https://lenta.ru/rss

  Semi-independent business (operating inside Russia, careful):
    - Kommersant            https://www.kommersant.ru/RSS/news.xml
    - Vedomosti             https://www.vedomosti.ru/rss/articles
    - RBC                   https://rssexport.rbc.ru/rbcnews/news/30/full.rss

  Independent / banned-from-Russia / in-exile:
    - Meduza               https://meduza.io/rss/all
    - Novaya Gazeta Europe https://novayagazeta.eu/feed
    - The Insider          https://theins.ru/feed
    - iStories             https://istories.media/rss/
    - BBC Russian Service  https://www.bbc.com/russian/index.xml
    - DW Russian           https://www.dw.com/atom/rss-ru-all
    - Radio Liberty Russia https://www.svoboda.org/api/zr-piye

Russian-language items are translated to English at ingestion via Claude
Haiku (~$0.003/day at our volume). The gap between TASS framing and
Meduza framing of the same event tells you what the Kremlin is
comfortable having amplified vs what's actually happening.

Section-adapter contract: conforms. Per-record source_tier preserved
so the synthesis pass weights state-controlled appropriately.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from . import Section, SectionState
from .state_news import _parse_rss

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com) Mozilla/5.0"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


# (source_label, feed_url, language, tier, notes)
FEEDS: list[tuple[str, str, str, str, str]] = [
    # ---- State-controlled --------------------------------------------------
    ("TASS",                  "https://tass.ru/rss/v2.xml",                       "ru", "state_controlled",         "Official Russian wire"),
    ("RIA Novosti",           "https://ria.ru/export/rss2/index.xml",             "ru", "state_controlled",         "Rossiya Segodnya state media"),
    ("Izvestia",              "https://iz.ru/xml/rss/all.xml",                    "ru", "state_controlled",         "Kremlin-aligned daily"),
    ("Lenta.ru",              "https://lenta.ru/rss/news",                        "ru", "state_controlled",         "Pro-Kremlin since 2014 editorial purge"),

    # ---- Semi-independent business inside Russia ---------------------------
    ("Kommersant",            "https://www.kommersant.ru/RSS/news.xml",           "ru", "mainstream_partisan_right", "Business, semi-independent, careful"),
    ("Vedomosti",             "https://www.vedomosti.ru/rss/articles",            "ru", "mainstream_partisan_right", "Business, semi-independent"),
    ("RBC",                   "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "ru", "mainstream_partisan_right", "Business, mixed state-private"),

    # ---- Independent / in-exile --------------------------------------------
    ("Meduza",                "https://meduza.io/rss/all",                        "ru", "mainstream_independent",   "Latvia-based, banned in Russia"),
    ("Novaya Gazeta Europe",  "https://novayagazeta.eu/feed",                     "ru", "mainstream_independent",   "Reconstituted in Latvia post-2022"),
    ("The Insider",           "https://theins.ru/feed",                           "ru", "mainstream_independent",   "Investigative, banned in Russia"),
    ("iStories",              "https://istories.media/rss/",                      "ru", "mainstream_independent",   "Investigative, banned in Russia"),
    ("BBC Russian",           "https://www.bbc.com/russian/index.xml",            "ru", "mainstream_independent",   "BBC Russian Service"),
    ("DW Russian",            "https://www.dw.com/atom/rss-ru-all",               "ru", "mainstream_independent",   "Deutsche Welle Russian Service"),
    ("Radio Liberty Russia",  "https://www.svoboda.org/api/zr-piye",              "ru", "mainstream_independent",   "RFE/RL Russian Service"),
]


def _translate_with_haiku(texts: list[str], source_lang: str = "Russian") -> list[str]:
    if not texts: return []
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key: return texts
    try:
        from anthropic import Anthropic
    except ImportError:
        return texts
    client = Anthropic(api_key=api_key)
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, start=1))
    prompt = (
        f"Translate each of the following {source_lang} news items into concise English. "
        "Preserve names, organizations, and numeric values exactly. Note: if the source "
        "uses propagandistic euphemisms (e.g. 'special military operation'), preserve "
        "the original phrasing in quotes and add a brief clarification in brackets. "
        "Reply with ONLY a JSON array of strings, one per input, in order. "
        "No commentary, no markdown.\n\n"
        f"Items:\n{numbered}"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(line for line in lines if not line.startswith("```"))
        translations = json.loads(text)
        if isinstance(translations, list) and len(translations) == len(texts):
            return [str(t) for t in translations]
    except Exception as exc:
        print(f"[russian_internal] translation failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    return texts


class RussianInternalSection(Section):
    id = "russian_internal"
    title = "Russian Internal News (state + in-exile)"
    emoji = "🇷🇺"

    source_id = "russian-internal-aggregate"
    source_name = "Russian-language news aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mixed"
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "Russian-language excerpts translated by Claude Haiku at ingestion. "
        "Per-feed attribution preserved in raw.jsonl. State-controlled "
        "(TASS, RIA, Izvestia, Lenta) and in-exile independent (Meduza, "
        "Novaya Gazeta Europe, iStories, etc.) sources distinguished by "
        "source_tier."
    )
    source_country = "Russia"
    source_language = "ru"

    PULL_TIMEOUT_S = 240
    LOOKBACK_DAYS = 2
    MAX_WORKERS = 10

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        raw_items: list[dict] = []

        def fetch(source_label: str, url: str, lang: str, tier: str, notes: str) -> list[dict]:
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                return [{
                    "id": f"russian-internal-error-{_slug(source_label)}",
                    "date": date.today().isoformat(),
                    "title": f"[feed error] {source_label}: {type(exc).__name__}",
                    "url": url,
                    "summary": str(exc)[:300],
                    "source_label": source_label,
                    "source_tier": tier,
                    "source_lang": lang,
                    "_error": True,
                }]
            feed_items = _parse_rss(resp.content)
            out = []
            for it in feed_items:
                d_str = (it.get("date") or "").strip()
                if not d_str: continue
                try:
                    item_date = date.fromisoformat(d_str[:10])
                except ValueError:
                    continue
                if item_date < cutoff: continue
                item_id = hashlib.sha1(
                    f"{source_label}|{it.get('url','')}|{it.get('title','')}".encode()
                ).hexdigest()
                it["id"] = item_id
                it["source_label"] = source_label
                it["source_tier"] = tier
                it["source_lang"] = lang
                it["notes"] = notes
                out.append(it)
            return out

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = [pool.submit(fetch, *f) for f in FEEDS]
            for fut in as_completed(futures):
                raw_items.extend(fut.result())

        # Batch-translate Russian-language items
        ru_items = [it for it in raw_items if it.get("source_lang") == "ru" and not it.get("_error")]
        if ru_items:
            # Cap to 60 per run so the translation cost stays bounded (~$0.005/day)
            translate_batch = ru_items[:60]
            inputs = [
                f"TITLE: {it.get('title','')} | LEDE: {(it.get('summary','') or '')[:200]}"
                for it in translate_batch
            ]
            translations = _translate_with_haiku(inputs, source_lang="Russian")
            for it, en in zip(translate_batch, translations):
                it["title_en"] = en
                it["title_original"] = it.get("title", "")
                it["title"] = f"[{en[:200]}] (ru: {it['title_original'][:80]})"

        return raw_items

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"russian-internal:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        record["original_lang"] = item.get("source_lang", "ru")
        if item.get("title_en"):
            record["extra"] = dict(record.get("extra") or {})
            record["extra"]["title_en"] = item["title_en"]
            record["extra"]["title_original"] = item.get("title_original")
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"): return []
        entities = [{
            "id": "place:country-russia",
            "type": "place",
            "canonical_name": "Russia",
            "metadata": {"kind": "country"},
        }]
        if item.get("source_label"):
            entities.append({
                "id": f"org:newsroom-{_slug(item['source_label'])}",
                "type": "org",
                "canonical_name": item["source_label"],
                "metadata": {
                    "kind": "newsroom", "country": "Russia",
                    "tier": item.get("source_tier"),
                    "language": item.get("source_lang"),
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
            if item.get("source_label"):
                relationships.append({
                    "from": f"org:newsroom-{_slug(item['source_label'])}",
                    "to": "place:country-russia",
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
