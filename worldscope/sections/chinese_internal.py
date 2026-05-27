"""
chinese_internal — Chinese-language news from inside the PRC.

Pulls from a curated set of mainland Chinese sources spanning the
party-line / state-media / market-liberal / nationalist-intellectual
spectrum. Each item is translated to English at ingestion time using
Claude Haiku (~$0.003/day at our volume), and both the original Chinese
and the English translation are stored in raw.jsonl.

This is the ONE section where ingestion is not pure-Python — the
translation step requires the Anthropic SDK. The cost stays low because:
  - Haiku is cheap ($0.25/M input, $1.25/M output)
  - We only translate title + first ~200 Chinese characters
  - We're capped at ~50 items/day across all feeds

Feed registry covers the spectrum the contract calls out:

  - People's Daily 人民日报       (state-controlled, party-line flagship)
  - Xinhua 新华社                  (state-controlled, official wire)
  - The Paper 澎湃新闻             (mainstream_independent, semi-private
                                     Shanghai United Media liberal-left)
  - Caixin 财新                    (mainstream_independent, market-liberal
                                     business news)
  - Guancha 观察者网               (mainstream_partisan_right, nationalist
                                     intellectual)
  - Sixth Tone                     (mainstream_independent, English-lang
                                     SUM Group, social-issues focus)
  - Caijing 财经                   (mainstream_independent, business)

The gap between Xinhua framing and Caixin framing of the same story tells
you what the leadership is comfortable with vs what the urban-professional
class thinks. The synthesis pass surfaces these gaps explicitly.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from . import Section, SectionState
from .state_news import _parse_rss

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com) Mozilla/5.0"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


# (source_label, feed_url, language, tier, notes)
#
# URL audit 2026-05-27: Chinese RSS landscape is hostile. Findings:
#   - People's Daily rss.people.com.cn host times out from outside CN; the
#     en.people.cn /index.xml endpoints all 404. Replaced with Google News
#     site-search for people.com.cn (Chinese-language query). Tier kept as
#     state_controlled.
#   - Caixin (caixin.com /rss/topnews.xml) returns 200 with empty/HTML body
#     (paywall wall behind the marker). RSSHub public instance is 403. No
#     working direct feed found; replaced with Google News site-search for
#     caixin.com.
#   - Caixin Global (caixinglobal.com /rss/news.xml) returns 403. Replaced
#     with Google News site-search.
#   - Caijing (caijing.com.cn /rss/topnews.xml) 404s and homepage redirect
#     suggests RSS retired entirely. Google News site-search has only ~1
#     indexed result. Caijing source removed; SCMP China business RSS added
#     as the market-liberal Chinese-business voice (Hong Kong-based, still
#     PRC-adjacent in coverage).
#   - Sixth Tone /feed -> /rss (verified 200, 51 items).
#   - Guancha /rss returns 200 but HTML, not RSS. No working RSS found;
#     removed and substituted with Global Voices Chinese coverage placeholder
#     comment. The nationalist-intellectual register is partially covered by
#     Global Times.
#   - The Paper (feedx.net mirror) verified working.
#   - Xinhua and Global Times English direct feeds still work.
#
# RSSHub public instance (rsshub.app) returns 403 on every probe today —
# Cloudflare appears to block datacenter IPs. If we deploy worldscope on a
# residential IP later, RSSHub becomes viable; until then, Google News
# site-search is the most reliable bridge.
FEEDS: list[tuple[str, str, str, str, str]] = [
    # ---- State-controlled (party line) -------------------------------------
    ("People's Daily 人民日报 (via Google News)",  "https://news.google.com/rss/search?q=site%3Apeople.com.cn+OR+site%3Apeople.cn&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "zh", "state_controlled",        "Party-line flagship via Google News proxy (direct feed dead 2026-05-27)"),
    ("Xinhua 新华社",                              "http://www.xinhuanet.com/world/news_world.xml",                                                                "zh", "state_controlled",        "Official wire service (verified 2026-05-27)"),

    # ---- Mainstream Independent (market-liberal, semi-private) -------------
    ("Caixin 财新 (via Google News)",              "https://news.google.com/rss/search?q=site%3Acaixin.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",                       "zh", "mainstream_independent",  "Market-liberal business via Google News proxy (direct feed paywalled 2026-05-27)"),
    ("The Paper 澎湃",                             "https://feedx.net/rss/thepaper.xml",                                                                          "zh", "mainstream_independent",  "Shanghai United Media liberal-left (verified 2026-05-27)"),
    # Caijing 财经: removed 2026-05-27 — RSS retired, Google News indexes ~1
    # item; coverage of market-liberal Chinese business comes via Caixin and
    # SCMP business below.
    ("SCMP China business",                        "https://www.scmp.com/rss/92/feed",                                                                            "en", "mainstream_independent",  "Hong Kong-based China business coverage (added 2026-05-27 to fill Caijing gap)"),

    # ---- English-language semi-state / private (no translation needed) ----
    ("Sixth Tone",                                 "https://www.sixthtone.com/rss",                                                                               "en", "mainstream_independent",  "SUM Group English, social issues (URL verified 2026-05-27: /feed -> /rss)"),
    ("Caixin Global (via Google News)",            "https://news.google.com/rss/search?q=site%3Acaixinglobal.com&hl=en-US&gl=US&ceid=US:en",                     "en", "mainstream_independent",  "Caixin's English daily via Google News proxy (direct feed 403 2026-05-27)"),

    # ---- Nationalist intellectual ------------------------------------------
    # Guancha 观察者网: removed 2026-05-27 — no working RSS endpoint found
    # (homepage HTML at /rss). Nationalist-intellectual register partially
    # covered by Global Times. RSSHub /guancha/headline would work if we run
    # our own RSSHub instance; not viable on the public instance.
    ("Guancha 观察者网 (via Google News)",         "https://news.google.com/rss/search?q=site%3Aguancha.cn&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",                       "zh", "mainstream_partisan_right", "Nationalist intellectual via Google News proxy (direct RSS dead 2026-05-27)"),

    # ---- State-controlled, English ----------------------------------------
    ("Global Times (English)",                     "https://www.globaltimes.cn/rss/outbrain.xml",                                                                 "en", "state_controlled",        "Hawkish state-controlled English (verified 2026-05-27)"),
]


def _translate_with_haiku(texts: list[str]) -> list[str]:
    """Translate a batch of Chinese strings to English via Claude Haiku.
    Returns a list of English translations in the same order. On any failure,
    returns the input texts unchanged (degraded but not broken)."""
    if not texts:
        return []
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # No key in environment -> skip translation, return originals
        return texts

    try:
        from anthropic import Anthropic
    except ImportError:
        return texts

    client = Anthropic(api_key=api_key)

    # Single batched call: prompt has all the items, model returns JSON array.
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, start=1))
    prompt = (
        "Translate each of the following Chinese news items into concise English. "
        "Preserve names, organizations, and numeric values exactly. Reply with ONLY "
        "a JSON array of strings, one per input, in order. No commentary, no markdown.\n\n"
        f"Items:\n{numbered}"
    )

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",   # cheap + fast; falls back if unavailable
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip code-fence wrappers if Haiku added them
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(line for line in lines if not line.startswith("```"))
        translations = json.loads(text)
        if isinstance(translations, list) and len(translations) == len(texts):
            return [str(t) for t in translations]
    except Exception as exc:
        print(f"[chinese_internal] translation failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    return texts   # degraded: return originals on any failure


class ChineseInternalSection(Section):
    id = "chinese_internal"
    title = "Chinese Internal News"
    emoji = "🇨🇳"

    source_id = "chinese-internal-aggregate"
    source_name = "Chinese internal news aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "mixed"        # per-record tier preserved
    source_license = "varies-per-feed"
    attribution_required = True
    attribution_text = (
        "Chinese-language excerpts translated by Claude Haiku at ingestion. "
        "Per-feed attribution preserved in raw.jsonl. State-controlled sources "
        "labeled explicitly (tier=state_controlled)."
    )
    source_country = "China"
    source_language = "zh"       # aggregate is primarily Chinese

    PULL_TIMEOUT_S = 180
    LOOKBACK_DAYS = 2
    MAX_WORKERS = 8

    def pull(self) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        raw_items: list[dict] = []

        def fetch(source_label: str, url: str, lang: str, tier: str, notes: str) -> list[dict]:
            try:
                resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
                resp.raise_for_status()
            except requests.exceptions.RequestException as exc:
                return [{
                    "id": f"chinese-internal-error-{_slug(source_label)}",
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
                # Strict-date filter: drop items without parseable pubDate.
                # The Xinhua RSS feed in particular publishes items without
                # dates that go back years; without this filter the 7-day
                # lookback becomes meaningless.
                d_str = (it.get("date") or "").strip()
                if not d_str:
                    continue
                try:
                    item_date = date.fromisoformat(d_str[:10])
                except ValueError:
                    continue
                if item_date < cutoff:
                    continue
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

        # Batch-translate the Chinese-language items
        zh_items = [it for it in raw_items if it.get("source_lang") == "zh" and not it.get("_error")]
        if zh_items:
            # Title + lede combined for each, capped at 200 chars to keep
            # Haiku input size reasonable
            inputs = [
                f"TITLE: {it.get('title','')} | LEDE: {(it.get('summary','') or '')[:200]}"
                for it in zh_items
            ]
            translations = _translate_with_haiku(inputs)
            for it, en in zip(zh_items, translations):
                it["title_en"] = en   # combined translation; downstream code
                                       # uses this for the English-facing surface
                # Prefix display title with the English translation
                it["title_original"] = it.get("title", "")
                it["title"] = f"[{en[:200]}] (zh: {it['title_original'][:80]})"

        return raw_items

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"chinese-internal:{_slug(item.get('source_label',''))}"
        record["source_tier"] = item.get("source_tier", self.source_tier)
        record["original_lang"] = item.get("source_lang", "zh")
        if item.get("title_en"):
            # Preserve both original and translation in the extra blob
            record["extra"] = dict(record.get("extra") or {})
            record["extra"]["title_en"] = item["title_en"]
            record["extra"]["title_original"] = item.get("title_original")
        if not item.get("_error"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"):
            return []
        entities = [{
            "id": "place:country-china",
            "type": "place",
            "canonical_name": "China",
            "metadata": {"kind": "country"},
        }]
        if item.get("source_label"):
            entities.append({
                "id": f"org:newsroom-{_slug(item['source_label'])}",
                "type": "org",
                "canonical_name": item["source_label"],
                "metadata": {
                    "kind": "newsroom",
                    "country": "China",
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
                    "to": "place:country-china",
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
