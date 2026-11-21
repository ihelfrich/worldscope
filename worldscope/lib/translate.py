"""Headline translation shared by every non-English section.

Replaces three copies of ``_translate_with_haiku`` that differed only in the
language named in the prompt. Those copies returned the original text when
there was no credential or the call failed, and their callers then stored
that original as ``title_en``: for weeks the Russian, Chinese and Ukrainian
sections recorded "translations" that were the untranslated headline.

``translate()`` returns ``None`` for anything it could not translate, so a
caller can tell a translation from its absence. Results are cached on disk by
content hash; a headline is paid for once.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .. import llm

LANGUAGE_NAMES = {
    "ar": "Arabic", "de": "German", "es": "Spanish", "fa": "Persian",
    "fr": "French", "he": "Hebrew", "hi": "Hindi", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean", "my": "Burmese", "pt": "Portuguese",
    "ru": "Russian", "sw": "Swahili", "tr": "Turkish", "uk": "Ukrainian",
    "ur": "Urdu", "vi": "Vietnamese", "zh": "Chinese", "am": "Amharic",
}

# Per-language conventions the prompt should state.
_NOTES = {
    "uk": "Use Ukrainian transliterations (Kyiv, not Kiev; Mykolaiv, not Nikolaev).",
    "zh": "Use pinyin for personal and place names.",
    "ru": ("If the source uses a propagandistic euphemism (for example 'special "
           "military operation'), keep the original phrasing in quotes and add a "
           "brief clarification in brackets."),
}

BATCH_SIZE = 40
_CACHE_PATH = Path(os.environ.get(
    "WORLDSCOPE_TRANSLATION_CACHE",
    Path(__file__).resolve().parent.parent.parent / "lake" / "cache" / "translations.json"))
_CACHE_MAX_ENTRIES = 12000


def _key(lang: str, text: str) -> str:
    return hashlib.sha1(f"{lang}\x00{text}".encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    if len(cache) > _CACHE_MAX_ENTRIES:  # insertion order: oldest first
        cache = dict(list(cache.items())[-_CACHE_MAX_ENTRIES:])
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except OSError as exc:
        print(f"[translate] cache not saved: {exc}", file=sys.stderr)


def _translate_batch(texts: list[str], lang: str, purpose: str) -> list[Optional[str]]:
    name = LANGUAGE_NAMES.get(lang, lang)
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(texts, start=1))
    prompt = (
        f"Translate each of the following {name} news items into concise English. "
        "Preserve names, organizations and numeric values exactly. "
        f"{_NOTES.get(lang, '')} "
        "Reply with ONLY a JSON array of strings, one per input, in order. "
        "No commentary, no markdown.\n\n"
        f"Items:\n{numbered}"
    )
    try:
        answer = llm.complete(prompt, purpose=purpose, model=llm.BULK_MODEL,
                              max_tokens=120 * len(texts) + 500)
        parsed = json.loads(llm.strip_code_fence(answer))
    except (llm.LLMUnavailable, ValueError) as exc:
        print(f"[translate] {lang}: batch of {len(texts)} not translated "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return [None] * len(texts)
    if not isinstance(parsed, list) or len(parsed) != len(texts):
        print(f"[translate] {lang}: answer did not match the {len(texts)} inputs",
              file=sys.stderr)
        return [None] * len(texts)
    return [str(item).strip() or None for item in parsed]


def translate(texts: list[str], lang: str, *,
              purpose: str = "translation") -> list[Optional[str]]:
    """Translate ``texts`` from ``lang`` into English.

    Returns one entry per input, in order: the English text, or ``None`` when
    it could not be translated (no credential, API failure, malformed answer).
    """
    if not texts:
        return []
    cache = _load_cache()
    results: list[Optional[str]] = [cache.get(_key(lang, text)) for text in texts]
    missing = [i for i, value in enumerate(results) if value is None and texts[i].strip()]
    if not missing or not llm.available():
        return results
    changed = False
    for start in range(0, len(missing), BATCH_SIZE):
        indexes = missing[start:start + BATCH_SIZE]
        translated = _translate_batch([texts[i] for i in indexes], lang, purpose)
        for i, english in zip(indexes, translated):
            if english:
                results[i] = english
                cache[_key(lang, texts[i])] = english
                changed = True
    if changed:
        _save_cache(cache)
    return results


def translate_item_titles(items: list[dict], lang: str, *, purpose: str,
                          limit: Optional[int] = None) -> int:
    """Translate the title and lede of feed items in place.

    A translated item gets ``title_en``, ``title_original`` and a display
    title that leads with the English. An item that could not be translated
    keeps its original title and gets ``untranslated: True``; it never
    receives a ``title_en`` that is not English. Returns the number translated.
    """
    batch = items if limit is None else items[:limit]
    inputs = [
        f"TITLE: {it.get('title', '')} | LEDE: {(it.get('summary', '') or '')[:200]}"
        for it in batch
    ]
    done = 0
    for it, english in zip(batch, translate(inputs, lang, purpose=purpose)):
        if not english:
            it["untranslated"] = True
            continue
        it["title_en"] = english
        it["title_original"] = it.get("title", "")
        it["title"] = f"[{english[:200]}] ({lang}: {it['title_original'][:80]})"
        done += 1
    for it in items[len(batch):]:
        it["untranslated"] = True
    return done
