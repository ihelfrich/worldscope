"""
ukrainian_internal — Ukrainian-language news, national + regional + local.

Coverage:

  National independent (Ukrainian-language):
    - Ukrainska Pravda     https://www.pravda.com.ua/rss/
    - Hromadske            https://hromadske.ua/rss
    - LIGA.net             https://www.liga.net/news/rss.xml
    - Babel                https://babel.ua/rss
    - LB.ua                https://lb.ua/rss
    - Censor.NET           https://censor.net/news/rss

  English-language Ukrainian:
    - Kyiv Independent     https://kyivindependent.com/rss/
    - Kyiv Post            https://www.kyivpost.com/feed

  State / government:
    - Ukrinform (state agency)     https://www.ukrinform.ua/rss/rss-news/index.xml
    - President of Ukraine         https://www.president.gov.ua/en/news/rss/2027
    - Cabinet of Ministers         https://www.kmu.gov.ua/news/rss/news/all

  Local Kyiv:
    - Vechirniy Kyiv (Evening Kyiv)   https://vechirniy.kyiv.ua/feed
    - Hmarochos (Kyiv lifestyle/urban) https://hmarochos.kiev.ua/feed/

  War-relevant (already in foreign_news; not re-ingested here):
    - ISW daily assessments tracked separately

Ukrainian-language items are translated to English at ingest. English-
language outlets are passed through unchanged.
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


# (source_label, feed_url, language, tier, scope, notes)
FEEDS: list[tuple[str, str, str, str, str, str]] = [
    # ---- Ukrainian-language, national, independent --------------------------
    ("Ukrainska Pravda",  "https://www.pravda.com.ua/rss/",                "uk", "mainstream_independent", "national", "Major independent national"),
    ("Hromadske",         "https://hromadske.ua/rss",                       "uk", "mainstream_independent", "national", "Public-service broadcaster"),
    ("LIGA.net",          "https://www.liga.net/news/rss.xml",             "uk", "mainstream_independent", "national", "Business + politics independent"),
    ("Babel",             "https://babel.ua/rss",                           "uk", "mainstream_independent", "national", "Cultural + politics"),
    ("LB.ua",             "https://lb.ua/rss",                              "uk", "mainstream_independent", "national", "Left Bank (Levyi Bereg)"),
    ("Censor.NET",        "https://censor.net/news/rss",                    "uk", "mainstream_partisan_right", "national", "Right-leaning, nationalist"),

    # ---- English-language Ukrainian -----------------------------------------
    ("Kyiv Independent",  "https://kyivindependent.com/rss/",               "en", "mainstream_independent", "national", "English-language, Kyiv-based"),
    ("Kyiv Post",         "https://www.kyivpost.com/feed",                  "en", "mainstream_independent", "national", "English-language daily"),

    # ---- Government / state news -------------------------------------------
    ("Ukrinform",         "https://www.ukrinform.ua/rss/rss-news/index.xml", "uk", "primary_document",       "national", "State news agency"),
    ("President of Ukraine", "https://www.president.gov.ua/en/news/rss/2027", "en", "primary_document",     "national", "President's office (English)"),
    ("Cabinet of Ministers", "https://www.kmu.gov.ua/news/rss/news/all",     "uk", "primary_document",      "national", "Government press"),

    # ---- Local Kyiv ---------------------------------------------------------
    ("Vechirniy Kyiv",    "https://vechirniy.kyiv.ua/feed",                 "uk", "mainstream_independent", "local-kyiv", "Evening Kyiv, local daily"),
    ("Hmarochos",         "https://hmarochos.kiev.ua/feed/",                "uk", "mainstream_independent", "local-kyiv", "Kyiv urban affairs + culture"),
]


def _translate_with_haiku(texts: list[str]) -> list[str]:
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
        "Translate each of the following Ukrainian news items into concise English. "
        "Preserve names (Ukrainian transliterations preferred: Kyiv not Kiev; "
        "Mykolaiv not Nikolaev; etc.), organizations, and numeric values exactly. "
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
        print(f"[ukrainian_internal] translation failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    return texts


class UkrainianInternalSection(Section):
    id = "ukrainian_internal"
    title = "Ukrainian Internal News (national + local Kyiv)"
    emoji = "🇺🇦"

    source_id = "ukrainian-internal-aggregate"
    source_name = "Ukrainian-language news aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mixed"
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "Ukrainian-language excerpts translated by Claude Haiku at ingestion. "
        "Per-feed attribution preserved in raw.jsonl. Ukrainian transliterations "
        "used throughout (Kyiv not Kiev; Mykolaiv not Nikolaev)."
    )
    source_country = "Ukraine"
    source_language = "uk"

    PULL_TIMEOUT_S = 240
    LOOKBACK_DAYS = 2
    MAX_WORKERS = 10

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        raw_items: list[dict] = []

        def fetch(source_label: str, url: str, lang: str, tier: str, scope: str, notes: str) -> list[dict]:
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                return [{
                    "id": f"ukrainian-internal-error-{_slug(source_label)}",
                    "date": date.today().isoformat(),
                    "title": f"[feed error] {source_label}: {type(exc).__name__}",
                    "url": url,
                    "summary": str(exc)[:300],
                    "source_label": source_label,
                    "source_tier": tier,
                    "source_lang": lang,
                    "scope": scope,
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
                it["scope"] = scope
                it["notes"] = notes
                out.append(it)
            return out

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = [pool.submit(fetch, *f) for f in FEEDS]
            for fut in as_completed(futures):
                raw_items.extend(fut.result())

        # Batch-translate Ukrainian-language items
        uk_items = [it for it in raw_items if it.get("source_lang") == "uk" and not it.get("_error")]
        if uk_items:
            translate_batch = uk_items[:60]
            inputs = [
                f"TITLE: {it.get('title','')} | LEDE: {(it.get('summary','') or '')[:200]}"
                for it in translate_batch
            ]
            translations = _translate_with_haiku(inputs)
            for it, en in zip(translate_batch, translations):
                it["title_en"] = en
                it["title_original"] = it.get("title", "")
                it["title"] = f"[{en[:200]}] (uk: {it['title_original'][:80]})"

        return raw_items

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"ukrainian-internal:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        record["original_lang"] = item.get("source_lang", "uk")
        if item.get("title_en"):
            record["extra"] = dict(record.get("extra") or {})
            record["extra"]["title_en"] = item["title_en"]
            record["extra"]["title_original"] = item.get("title_original")
            record["extra"]["scope"] = item.get("scope")
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"): return []
        entities = [{
            "id": "place:country-ukraine",
            "type": "place",
            "canonical_name": "Ukraine",
            "metadata": {"kind": "country"},
        }]
        if item.get("scope") == "local-kyiv":
            entities.append({
                "id": "place:city-kyiv",
                "type": "place",
                "canonical_name": "Kyiv",
                "metadata": {"kind": "city", "country": "Ukraine"},
            })
        if item.get("source_label"):
            entities.append({
                "id": f"org:newsroom-{_slug(item['source_label'])}",
                "type": "org",
                "canonical_name": item["source_label"],
                "metadata": {
                    "kind": "newsroom", "country": "Ukraine",
                    "tier": item.get("source_tier"),
                    "language": item.get("source_lang"),
                    "scope": item.get("scope"),
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
                target = ("place:city-kyiv" if item.get("scope") == "local-kyiv"
                          else "place:country-ukraine")
                relationships.append({
                    "from": f"org:newsroom-{_slug(item['source_label'])}",
                    "to": target,
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
