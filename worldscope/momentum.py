"""momentum.py — cross-day story memory for the WORLDSCOPE board.

The board has always been a perfect snapshot of *today*; this module gives it
a memory. Using the lake's rolling window (~9 days) and the SAME key
extraction the claims/signals engines use (signals.record_key_pairs), it
tracks each story key's daily trajectory — records/day and independent
sections/day — and classifies the movement an analyst actually cares about:

  SURGING   today is a multiple of the story's own prior baseline
  NEW       first appearance today, already multi-section
  BUILDING  three straight days of growth
  FADING    was a major story, now collapsing out of the cycle
  STEADY    the carrying narrative — present nearly every day

Structural hub terms (keys that are huge every single day: country names,
heads of state) are excluded; they are backdrop, not movement.

Output feeds the board's "Developing" panel (headline + classification +
server-rendered sparkline), so the reader sees not just what is happening
but what is *becoming*.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .signals import record_key_pairs

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LAKE = REPO / "lake" / "db" / "worldscope.sqlite"

MAX_DAYS = 9          # matches the lake's committed rolling window
MIN_TODAY_RECORDS = 3  # a story must be alive today to classify as moving
HUB_MIN_DAILY = 20     # key with >= this many records EVERY day = structural hub
HUB_TOTAL = 400        # or just overwhelmingly common across the window

# Not stories: calendar fragments, generic nouns, and outlet names (which are
# attribution, not events). Kept small and surgical — the hub guard and the
# multi-section requirement do most of the real filtering.
_JUNK = {
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "today", "yesterday",
    "state", "news", "world", "report", "update", "breaking", "live",
    "video", "opinion", "analysis", "exclusive",
    "man", "woman", "police", "people", "officials", "government", "house",
    "court", "president", "minister", "city", "county",
    "wsj", "nyt", "reuters", "ap", "afp", "bbc", "cnn", "bloomberg", "ft",
    "axios", "politico", "guardian", "times", "post",
}


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


def _day_records(conn: sqlite3.Connection, day: str) -> list[dict]:
    out = []
    for row in conn.execute(
            "SELECT section_id, original_text, extra_json FROM records "
            "WHERE substr(ingested_at,1,10)=?", (day,)):
        rec = {"section_id": row[0], "original_text": row[1] or ""}
        if row[2]:
            try:
                extra = json.loads(row[2])
                if isinstance(extra, dict) and extra.get("entities"):
                    rec["entities"] = extra["entities"]
            except (ValueError, TypeError):
                pass
        out.append(rec)
    return out


def _classify(r: list[int], s: list[int], days_present: int) -> str:
    today, prior = r[-1], r[:-1]
    if today < MIN_TODAY_RECORDS or s[-1] < 2:
        # not alive today — only interesting if it USED to be big
        peak = max(prior) if prior else 0
        return "fading" if peak >= 8 and today <= peak / 3 else ""
    if sum(prior) == 0:
        return "new" if today >= 4 else ""
    baseline = sum(prior) / len(prior)
    if today >= max(6, 2.5 * baseline):
        return "surging"
    if len(r) >= 3 and r[-3] < r[-2] < r[-1] and today >= 4:
        return "building"
    peak = max(prior)
    if peak >= 8 and today <= peak / 3:
        return "fading"
    if days_present >= 6 and today >= 3:
        return "steady"
    return ""


def build_momentum(today: date, *, lake_path: Path | None = None,
                   days: int = MAX_DAYS, limit: int = 12) -> dict:
    """Return {'days': [iso...], 'stories': [Story-as-dict...]} for the board."""
    lake_path = lake_path or DEFAULT_LAKE
    if not Path(lake_path).exists():
        return {"days": [], "stories": []}
    day_list = [(today - timedelta(days=d)).isoformat() for d in range(days - 1, -1, -1)]

    # per key: day-index -> (record count, section set); plus label + best headline
    counts: dict[str, list] = {}
    sections: dict[str, list] = {}
    labels: dict[str, str] = {}
    headlines: dict[str, tuple[int, str]] = {}   # (day_idx, text)

    conn = sqlite3.connect(f"file:{lake_path}?mode=ro", uri=True)
    try:
        for di, day in enumerate(day_list):
            for rec in _day_records(conn, day):
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
                    text = rec["original_text"]
                    best = headlines.get(key)
                    if text and (best is None or di > best[0]
                                 or (di == best[0] and len(text) > len(best[1]))):
                        headlines[key] = (di, text)
    finally:
        conn.close()

    stories: list[Story] = []
    for key, r in counts.items():
        total = sum(r)
        if total < 4:
            continue
        if total >= HUB_TOTAL or all(x >= HUB_MIN_DAILY for x in r):
            continue                                  # structural hub, not a story
        s = [len(x) for x in sections[key]]
        days_present = sum(1 for x in r if x > 0)
        klass = _classify(r, s, days_present)
        if not klass:
            continue
        stories.append(Story(
            key=key, label=labels[key],
            headline=(headlines.get(key, (0, ""))[1] or labels[key]),
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
    for st in stories:
        if used.get(st.klass, 0) >= caps[st.klass]:
            continue
        # containment dedupe: 'russia' vs 'russian', 'iran' vs 'iranian'
        if any(st.key in p.key or p.key in st.key for p in picked):
            continue
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
