"""Which claims are news, and how to show them.

One ranking for every surface that leads with claims (the automated brief,
the evidence board, the homepage hero), so they agree on what matters.

The claim graph grades corroboration, but raw source counts reward
syndication: five local stations running one wire story outrank a story
confirmed by independent desks. This module ranks by independence (distinct
reporting sections, then distinct outlets with all local/state stations
counted once), drops what is not world news (roundups, local-only items,
structured-feed clusters, "contradicted" clusters), and picks a readable
headline (an English evidence title for an untranslated claim).
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# Sections that are not independent reporting of an event: prediction
# markets, scored registries, filings feeds.
NON_REPORTING = {"paper_bets", "forecasts", "political_figures", "congressional_trades",
                 "billionaires", "form4"}
# Structured feeds whose rows cluster by shape, not by story (unrelated quakes
# merge into one "claim"): fine for domain lines, never a lead story.
STRUCTURED = {"usgs_quakes", "weather", "gdacs", "markets", "markets_global", "macro",
              "firms", "cisa_kev", "epss"}
LOCAL = {"local_news", "state_news"}
ROUNDUP = re.compile(
    r"\b(morning brief|evening brief|news ?brief|live updates?|live blog|newsletter|"
    r"what to know|the latest|week in review|recap|podcast|watch live|in pictures|"
    r"quiz|horoscope|today in history|top stories|headlines)\b", re.I)
TAGGED = re.compile(r"^\[[^\]]*·[^\]]*\]")       # "[Area · themes] ..." watch-area echoes

# Non-Latin scripts flagged deterministically (same ranges as the board).
_SCRIPTS = [("RU", (0x0400, 0x04FF)), ("ZH", (0x4E00, 0x9FFF)),
            ("AR", (0x0600, 0x06FF))]


def lang_tag(text: str) -> str:
    for ch in text or "":
        o = ord(ch)
        for code, (lo, hi) in _SCRIPTS:
            if lo <= o <= hi:
                return code
    return ""


def evidence(c) -> list:
    return list(getattr(c, "evidence", None) or [])


def reporting_sections(c) -> set[str]:
    secs = {e.section for e in evidence(c)} or set(c.topics)
    return secs - NON_REPORTING


def outlets(c) -> int:
    """Distinct outlets, counting all local/state stations as one: they mostly
    syndicate the same wire copy, so five of them are one source, not five."""
    keys = {"local" if e.section in LOCAL else (e.source_id or e.section)
            for e in evidence(c)}
    return len(keys) or c.n_sources


def display_text(c) -> str:
    """The headline to show: the claim text, or a better evidence title when
    the claim text is untranslated or a newsletter roundup."""
    text = c.claim_text or ""
    if not lang_tag(text) and not ROUNDUP.search(text):
        return text
    for e in evidence(c):
        t = (e.title or "").strip()
        if (t and not lang_tag(t) and not ROUNDUP.search(t)
                and e.section not in NON_REPORTING and len(t.split()) >= 5):
            return t
    return text


def is_junk(c) -> bool:
    # "contradicted" clusters were, on inspection (2026-11-12), unrelated items
    # merged by shared words, not genuine disputes; a deterministic ranking
    # cannot tell the two apart, so lead surfaces leave them out.
    text = display_text(c)
    return bool(ROUNDUP.search(text) or TAGGED.search(text)
                or not reporting_sections(c)
                or c.status == "contradicted")


def newsworthiness(c) -> float | None:
    """Score a claim for lead surfaces; None drops it.

    Independence first: distinct reporting sections, then distinct outlets
    (local syndication counted once), then the corroboration grade.
    """
    if is_junk(c):
        return None
    secs = reporting_sections(c)
    if secs <= LOCAL:
        return None                       # local-only: not world news
    ev = evidence(c)
    if ev and sum(e.section in STRUCTURED for e in ev) * 2 > len(ev):
        return None                       # structured-feed cluster, not a story
    grade = {"primary_confirmed": 1.0, "multi_source": 0.6}.get(c.status, 0.0)
    return len(secs) * 2.0 + min(outlets(c), 8) * 0.5 + grade + c.confidence


def rank(claims) -> list:
    """Newsworthy claims, best first (junk and non-news removed)."""
    scored = [(newsworthiness(c), c) for c in claims]
    return [c for sc, c in sorted(((sc, c) for sc, c in scored if sc is not None),
                                  key=lambda t: t[0], reverse=True)]


_LONG_WORD = re.compile(r"[A-Za-z]{4,}")


def sentence_case(text: str) -> bool:
    """Title Case capitalises every word, so only headlines with a fair share
    of lowercase words say anything about which words are names."""
    words = _LONG_WORD.findall(text or "")
    return bool(words) and sum(w[0].islower() for w in words) >= 0.3 * len(words)


def named_mid_sentence(label: str, text: str) -> bool:
    """True when ``label`` appears capitalised after the first word of a
    sentence-case headline: a name, not a capitalised first word ("Power",
    "Following") or a Title Case word ("Meet")."""
    if not sentence_case(text):
        return False
    text = _PREFIX.sub("", text or "")              # "[St. Louis] Meet the hiker..."
    pat = re.compile(rf"\S\s+{re.escape(label)}\b")
    # each "|", ":", "—" segment starts a new sentence
    return any(pat.search(seg.strip()) for seg in _SEGMENT.split(text))


_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")
_SEGMENT = re.compile(r"\s[|:—–]\s|[:?!.]\s|[‘’'\"“”]")


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (text or "").lower())}   # crude stem


_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".mp3", ".pdf")
STATE_MEDIA = {"russian_internal", "chinese_internal"}


def source_url(c) -> str:
    """The best link for a claim: a real article (not a homepage or image),
    preferably the one whose headline is shown (other cluster members can be
    different stories), then independent over local, non-roundup, non-state
    reporting; anything with a usable URL as a last resort."""
    shown = display_text(c).strip()
    shown_words = _words(shown)
    ev = [e for e in evidence(c) if (e.url or "").startswith("http")]

    def rank(e):
        reporting = e.section not in NON_REPORTING and e.section not in STRUCTURED
        # a bare domain ("https://liveuamap.com") is a homepage, not a source
        path = urlsplit(e.url).path.lower()
        article = len(path.strip("/")) > 0 and not path.endswith(_MEDIA_EXT)
        return (article,
                (e.title or "").strip() == shown,        # the article actually shown
                len(_words(e.title) & shown_words),      # else the most similar story
                reporting and e.section not in LOCAL,
                not ROUNDUP.search(e.title or ""),       # a newsletter is not the source
                e.section not in STATE_MEDIA,            # prefer independent outlets
                reporting)
    return max(ev, key=rank).url if ev else ""
