"""Content filter for adult / scam / clickbait junk.

Open-feed sources (Google News proxies, MediaCloud, GDELT, regional RSS)
occasionally surface OnlyFans-promotion blogspam, crypto-airdrop scams,
"doctors hate her" clickbait, and similar trash that has no place in a
political/economic briefing. The filter is applied uniformly in
Section.resolve() so every section benefits.

Two signals, both deliberately conservative to avoid hiding real news:

  1. URL host blocklist — only drops items whose link points AT a known
     adult/scam platform (so a Reuters article about "OnlyFans testifying
     before Congress" survives; only direct links to onlyfans.com are
     dropped).

  2. Word-boundary regex on title/summary/description — substring matches
     would mis-fire on Russian transliterations like "opornogo" hitting
     "porn", so every pattern uses \\b boundaries.

Sections that legitimately surface these terms (e.g. a sanctions section
reporting OFAC action against an adult platform) can set
FILTER_ADULT_SCAM = False on the subclass to opt out.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_BLOCKED_DOMAINS: frozenset[str] = frozenset({
    "onlyfans.com",
    "fansly.com",
    "manyvids.com",
    "chaturbate.com",
    "stripchat.com",
    "myfreecams.com",
    "pornhub.com",
    "xvideos.com",
    "xhamster.com",
    "xnxx.com",
    "youporn.com",
    "redtube.com",
    "spankbang.com",
    "fapello.com",
    "leakedzone.com",
    "thothub.tv",
    "coomer.party",
    "coomer.su",
    "kemono.party",
    "kemono.su",
})

_BLOCKED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        # Adult-content promo
        r"\bonlyfans\b",
        r"\bfansly\b",
        r"\bmanyvids\b",
        r"\bchaturbate\b",
        r"\bcamgirls?\b",
        r"\bcamboys?\b",
        r"\bnude\s+leaks?\b",
        r"\bleaked\s+nudes?\b",
        r"\bsubscribe\s+to\s+my\s+OF\b",
        r"\bDM\s+(me\s+)?for\s+(content|pics|nudes|exclusive)\b",
        # Crypto / airdrop / wallet-drainer scams
        r"\bfree\s+(crypto|bitcoin|btc|eth|sol)\s+(giveaway|airdrop)\b",
        r"\bcrypto\s+giveaway\b",
        r"\b(guaranteed|100x|1000x)\s+(returns?|gains?|profits?)\b",
        r"\bpump\s+and\s+dump\b",
        r"\bclaim\s+your\s+(airdrop|tokens?)\b",
        r"\bconnect\s+your\s+wallet\s+to\s+claim\b",
        r"\bverify\s+your\s+wallet\s+to\s+(claim|receive|continue)\b",
        r"\bsend\s+(eth|btc|bnb|sol|usdt)\s+to\b",
        # Generic spammy clickbait
        r"\bone\s+weird\s+trick\b",
        r"\bdoctors\s+hate\s+(her|him|this)\b",
        r"\byou\s+won['’]?t\s+believe\s+(what|how)\b",
    )
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().lstrip(".")
    except Exception:
        return ""


def _host_blocked(host: str) -> bool:
    if not host:
        return False
    if host in _BLOCKED_DOMAINS:
        return True
    return any(host.endswith("." + d) for d in _BLOCKED_DOMAINS)


def is_blocked(item: dict) -> tuple[bool, str]:
    """Return (blocked, reason). Reason is the matched signal, empty when not blocked."""
    host = _host(item.get("url") or "")
    if _host_blocked(host):
        return True, f"blocked_domain:{host}"

    haystack = " ".join(
        str(item.get(k, "") or "")
        for k in ("title", "summary", "description")
    )
    for pat in _BLOCKED_PATTERNS:
        m = pat.search(haystack)
        if m:
            return True, f"blocked_pattern:{m.group(0).lower()}"
    return False, ""


def filter_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split items into (kept, dropped). Dropped items carry _drop_reason."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for it in items:
        blocked, reason = is_blocked(it)
        if blocked:
            tagged = dict(it)
            tagged["_drop_reason"] = reason
            dropped.append(tagged)
        else:
            kept.append(it)
    return kept, dropped
