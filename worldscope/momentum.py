"""momentum.py — cross-day story memory for the WORLDSCOPE board.

The board has always been a perfect snapshot of *today*; this module gives it
a memory. It reads the full cross-time corpus through worldscope.history —
the live lake PLUS the monthly gzip archive that lake_maintenance evicts
into — so trajectories span weeks, not just the committed DB's ~week. Using
the SAME key extraction the claims/signals engines use
(signals.record_key_pairs), it tracks each story key's daily trajectory and
classifies the movement an analyst actually cares about:

  SURGING   today is a multiple of the story's own prior baseline
  NEW       first appearance in the whole window, already multi-section
  BUILDING  three straight days of growth
  FADING    was a major story within the last ~10 days, now collapsing
  STEADY    the carrying narrative — present most of the window

Structural hub terms (keys that are huge every single day: country names,
heads of state) are excluded; they are backdrop, not movement. Days at the
start of the window with no data at all (archive gaps) are trimmed so thin
coverage can't masquerade as novelty.
"""
from __future__ import annotations

import re

import sqlite3  # noqa: F401  (kept for callers that still pass lake paths)
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .signals import record_key_pairs
from . import history

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LAKE = REPO / "lake" / "db" / "worldscope.sqlite"

WINDOW_DAYS = 35       # live lake + archive via worldscope.history
MIN_TODAY_RECORDS = 3  # a story must be alive today to classify as moving
HUB_MIN_DAILY = 20     # key with >= this many records EVERY day = structural hub
HUB_PER_DAY = 45       # or averaging this many per day across the window
FADE_RECENT_DAYS = 10  # a peak older than this is history, not "fading"

# Not stories: calendar fragments, generic nouns, and outlet names (which are
# attribution, not events). Kept small and surgical — the hub guard and the
# multi-section requirement do most of the real filtering.
_JUNK = {
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "today", "yesterday",
    "state", "news", "world", "report", "update", "breaking", "live",
    "video", "opinion", "analysis", "exclusive",
    "pst", "pdt", "mst", "mdt", "cst", "cdt", "est", "edt", "akst", "hst",
    "utc", "gmt", "am", "pm",  # weather-alert timestamps, not stories
    "man", "woman", "police", "people", "officials", "government", "house",
    "court", "president", "minister", "city", "county",
    "wsj", "nyt", "reuters", "ap", "afp", "bbc", "cnn", "bloomberg", "ft",
    "axios", "politico", "guardian", "times", "post",
    # Party and institution words, and bare US state names: the state/local
    # feeds cover the same parties and states every day, so these "surge"
    # with feed volume, not with any story (2026-11-12: Democratic, Florida,
    # Oregon, Republican, Mississippi, Senate, Election in the Developing list).
    "democratic", "democrat", "democrats", "republican", "republicans", "gop",
    "senate", "congress", "election", "elections", "governor", "legislature",
    "lawmakers", "voters", "campaign",
    "watch", "feds", "commentary", "recap", "editorial", "letters",
    # prepositions/adverbs that open a headline and get capitalised there
    "following", "after", "before", "amid", "despite", "during", "inside",
    "behind", "ahead", "over", "under", "against", "without", "why", "how",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
}


_TAG_RE = re.compile(r"<[^>]*>?")
_WRAP_RE = re.compile(r"\[(?:TITLE|LEDE)\s*[:\]]\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


from .claim_rank import lang_tag, named_mid_sentence  # noqa: E402


def _source_rank(section: str) -> int:
    """Which record's text should represent a story: independent reporting
    first, then local/state outlets, then markets and structured feeds (a
    Polymarket question or a quake row is a poor headline for a story)."""
    from .claim_rank import LOCAL, NON_REPORTING, STRUCTURED
    if section in NON_REPORTING or section in STRUCTURED:
        return 0
    if section in LOCAL:
        return 1
    return 2


def _canon(key: str, label: str) -> tuple[str, str]:
    """Merge possessives into their base key (boeing's -> boeing)."""
    for suf in ("'s", "’s"):
        if key.endswith(suf):
            return key[:-len(suf)].strip(), label[:-len(suf)].strip()
    return key, label


@dataclass
class Story:
    key: str
    label: str
    headline: str = ""
    sections_today: int = 0
    records: list = field(default_factory=list)   # per-day record counts
    sections: list = field(default_factory=list)  # per-day distinct-section counts
    klass: str = ""
    days_present: int = 0
    peak: int = 0


def _iter_window(today: date, days: int, lake_path: Path):
    """Yield (day_iso, rec) across live DB + archive via worldscope.history."""
    start = today - timedelta(days=days - 1)
    for row in history.iter_records(start, today, db_path=lake_path):
        day = str(row.get("ingested_at") or "")[:10]
        if not day:
            continue
        rec = {"section_id": row.get("section_id") or "",
               "original_text": row.get("original_text") or ""}
        extra = row.get("extra_json")
        if extra:
            try:
                import json as _json
                e = _json.loads(extra) if isinstance(extra, str) else extra
                if isinstance(e, dict) and e.get("entities"):
                    rec["entities"] = e["entities"]
            except (ValueError, TypeError):
                pass
        yield day, rec


def _classify(r: list[int], s: list[int], days_present: int, window: int) -> str:
    today, prior = r[-1], r[:-1]

    def _fading() -> bool:
        if not prior:
            return False
        peak = max(prior)
        if peak < 8 or today > peak / 3:
            return False
        # only "fading" while the collapse is fresh — a peak older than
        # FADE_RECENT_DAYS is history, not news
        peak_idx = len(prior) - 1 - prior[::-1].index(peak)
        return (len(r) - 1 - peak_idx) <= FADE_RECENT_DAYS

    if today < MIN_TODAY_RECORDS or s[-1] < 2:
        return "fading" if _fading() else ""
    if sum(prior) == 0:
        return "new" if today >= 4 else ""
    baseline = sum(prior) / len(prior)
    # a surge must be near the story's own window high — a big day two days
    # ago with today in decline is not "surging", however low the baseline
    if today >= max(6, 2.5 * baseline) and today >= 0.8 * max(r):
        return "surging"
    if len(r) >= 3 and r[-3] < r[-2] < r[-1] and today >= 4:
        return "building"
    if _fading():
        return "fading"
    if days_present >= (2 * window) // 3 and today >= 3:
        return "steady"
    return ""


def build_momentum(today: date, *, lake_path: Path | None = None,
                   days: int = WINDOW_DAYS, limit: int = 12) -> dict:
    """Return {'days': [iso...], 'stories': [Story-as-dict...]} for the board.
    Reads live lake + archive through worldscope.history, so the window can
    be far longer than the committed DB's rolling week."""
    lake_path = lake_path or DEFAULT_LAKE
    if not Path(lake_path).exists():
        return {"days": [], "stories": []}
    day_list = [(today - timedelta(days=d)).isoformat() for d in range(days - 1, -1, -1)]
    day_idx = {d: i for i, d in enumerate(day_list)}

    # per key: day-index -> (record count, section set); plus label + best headline
    counts: dict[str, list] = {}
    sections: dict[str, list] = {}
    labels: dict[str, str] = {}
    headlines: dict[str, tuple] = {}   # key -> (rank tuple, text)
    named: dict[str, int] = {}         # single-word key: records naming it mid-sentence
    day_totals = [0] * len(day_list)

    for day, rec in _iter_window(today, days, lake_path):
        di = day_idx.get(day)
        if di is None:
            continue
        day_totals[di] += 1
        for key, cased in record_key_pairs(rec):
            key, cased = _canon(key, cased)
            if not key or key in _JUNK:
                continue
            if key not in counts:
                counts[key] = [0] * len(day_list)
                sections[key] = [set() for _ in day_list]
                labels[key] = cased
            counts[key][di] += 1
            sections[key][di].add(rec["section_id"])
            text = _TAG_RE.sub("", _WRAP_RE.sub(" ", rec["original_text"] or ""))
            text = _WS_RE.sub(" ", text).strip()
            if text:
                rank = (di, _source_rank(rec["section_id"]), not lang_tag(text[:160]),
                        cased.lower() in text[:140].lower(), min(len(text), 400))
                if (" " not in key and named.get(key, 0) < 2
                        and named_mid_sentence(cased, text.split(" — ")[0][:200])):
                    named[key] = named.get(key, 0) + 1
                best = headlines.get(key)
                if best is None or rank > best[0]:
                    headlines[key] = (rank, text)

    # Trim leading days with no data at all (archive gap / short history):
    # otherwise every established story looks NEW against phantom zeros.
    first = next((i for i, t in enumerate(day_totals) if t > 0), 0)
    if first:
        day_list = day_list[first:]
        for key in counts:
            counts[key] = counts[key][first:]
            sections[key] = sections[key][first:]
    window = len(day_list)
    if window < 2:
        return {"days": day_list, "stories": []}

    stories: list[Story] = []
    for key, r in counts.items():
        total = sum(r)
        if total < 4:
            continue
        if " " not in key and named.get(key, 0) < 2:
            continue                      # "Meet", "Following": not a name anywhere
        days_on = sum(1 for x in r if x > 0)
        if (total >= HUB_PER_DAY * window
                or all(x >= HUB_MIN_DAILY for x in r)
                or (days_on >= 0.9 * window and total / window >= 12)):
            continue                                  # structural hub, not a story
        s = [len(x) for x in sections[key]]
        days_present = sum(1 for x in r if x > 0)
        klass = _classify(r, s, days_present, window)
        if not klass:
            continue
        stories.append(Story(
            key=key, label=labels[key],
            headline=(headlines.get(key, (None, ""))[1] or labels[key]),
            sections_today=s[-1], records=r, sections=s,
            klass=klass, days_present=days_present, peak=max(r)))

    order = {"surging": 0, "new": 1, "building": 2, "fading": 3, "steady": 4}
    # within a class, cross-source breadth beats raw volume
    stories.sort(key=lambda st: (order[st.klass], -st.sections_today,
                                 -st.records[-1], -st.peak))

    # class diversity: movement classes get most slots, quiet ones a few,
    # and no single class may swallow the whole panel
    caps = {"surging": 5, "new": 3, "building": 3, "fading": 2, "steady": 2}
    picked, used = [], {}
    seen_heads: set[str] = set()
    for st in stories:
        if used.get(st.klass, 0) >= caps[st.klass]:
            continue
        # containment dedupe: 'russia' vs 'russian', 'iran' vs 'iranian'
        if any(st.key in p.key or p.key in st.key for p in picked):
            continue
        # headline dedupe: two keys mined from the same record would show the
        # same headline twice (e.g. a person and their caucus in one story)
        hnorm = (st.headline or "")[:80].lower().strip()
        if hnorm and hnorm in seen_heads:
            continue
        seen_heads.add(hnorm)
        used[st.klass] = used.get(st.klass, 0) + 1
        picked.append(st)
        if len(picked) >= limit:
            break

    return {"days": day_list,
            "stories": [{
                "key": st.key, "label": st.label, "headline": st.headline,
                "klass": st.klass, "records": st.records, "sections": st.sections,
                "sections_today": st.sections_today, "days_present": st.days_present,
                "peak": st.peak,
            } for st in picked]}


if __name__ == "__main__":
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    m = build_momentum(d)
    print(f"{len(m['stories'])} stories over {len(m['days'])} days")
    for st in m["stories"]:
        print(f"  [{st['klass']:8}] {st['label'][:30]:30} {st['records']}  "
              f"today {st['sections_today']} sections")
