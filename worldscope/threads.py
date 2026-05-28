"""Story threads — multi-day arcs detected from entity persistence.

The daily brief has historically been ephemeral: 24 hours, then archived.
Anything spanning multiple days had to be reconstructed by hand. This
module promotes the brief from a daily product to a longitudinal one.

A *thread* is an entity (from today's cross-section signals plus the
heaviest mentioned entities overall) that appears in records across
≥ MIN_DAYS distinct days in the last LOOKBACK_DAYS window, with
≥ MIN_ITEMS total items.

For each thread we emit:

  - id, slug
  - title (canonical entity name)
  - entity_type
  - days_active, items_total
  - items_by_day: {YYYY-MM-DD: [{section_id, title, url, summary}, ...]}
  - sections_touched: distinct sections the thread has been in
  - heat_score: weighted recency × volume × breadth
  - is_active_today: True if any items today
  - synth: deterministic 2-sentence current-state summary

Threads are sorted by heat_score descending. The homepage hero surfaces
the top active thread; /threads/index.html lists them all; each thread
gets its own URL at /threads/<slug>/index.html.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# Tuning knobs
LOOKBACK_DAYS = 14
MIN_DAYS = 3
MIN_ITEMS = 5
MAX_THREADS = 24
MAX_ITEMS_IN_THREAD = 80
MAX_RECENT_PER_DAY = 8
MIN_NAME_LEN = 4   # short names ("AI", "US") produce false-positive matches

# Names to skip even if cross_section surfaces them — too generic.
GENERIC_NAMES = frozenset({
    "Today", "Recent", "United States", "U.S.", "US", "America",
    "President", "Senator", "Congress", "House", "Senate",
})


@dataclass
class Thread:
    id: str
    slug: str
    title: str
    entity_type: str
    days_active: int
    items_total: int
    items_by_day: dict[str, list[dict]] = field(default_factory=dict)
    sections_touched: list[str] = field(default_factory=list)
    heat_score: float = 0.0
    is_active_today: bool = False
    synth: str = ""


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "thread"


def _candidate_entities(lake_db: Path, cross_section: dict | None,
                         today_iso: str, limit: int = 80) -> list[dict]:
    """Build the list of entities to test for thread-hood.

    Mix: every cross-section pinned entity from today + heaviest mentioned
    entities from today. The pinned ones MUST be considered (they're the
    day's signal); the heavy ones cover the persistent base rate of
    political coverage (legislators, agencies, etc.)."""
    cands: list[dict] = []
    seen: set[str] = set()

    if cross_section:
        for band in ("high", "medium", "low"):
            for ent in (cross_section.get("by_confidence", {}).get(band) or []):
                name = ent.get("canonical_name")
                if name and name not in seen and len(name) >= MIN_NAME_LEN \
                   and name not in GENERIC_NAMES:
                    cands.append({"name": name,
                                  "type": ent.get("entity_type") or "topic",
                                  "pinned": True})
                    seen.add(name)

    if lake_db.exists():
        conn = sqlite3.connect(f"file:{lake_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT e.canonical_name AS name, e.type, COUNT(*) AS mentions
                  FROM entities e
                  JOIN record_entities re ON e.id = re.entity_id
                  JOIN records r          ON re.record_id = r.id
                 WHERE substr(r.ingested_at, 1, 10) = ?
                 GROUP BY e.id
                 ORDER BY mentions DESC, e.canonical_name
                 LIMIT ?
                """,
                (today_iso, limit),
            ).fetchall()
            for r in rows:
                n = r["name"]
                if n and n not in seen and len(n) >= MIN_NAME_LEN \
                   and n not in GENERIC_NAMES:
                    cands.append({"name": n, "type": r["type"] or "topic",
                                  "pinned": False})
                    seen.add(n)
        finally:
            conn.close()
    return cands


def _load_snapshot_items(store_db: Path, today: date,
                          lookback_days: int) -> dict[str, dict[str, list[dict]]]:
    """Return {section_id: {YYYY-MM-DD: [items]}} for the last N days from
    the snapshot store. The snapshot payload's items carry title, url,
    summary, date — what we need to substring-match against."""
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    if not store_db.exists():
        return out
    cutoff = (today - timedelta(days=lookback_days - 1)).isoformat()
    conn = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        cur = conn.execute(
            "SELECT section_id, snapshot_date, payload "
            "  FROM snapshots WHERE snapshot_date >= ? ORDER BY snapshot_date",
            (cutoff,)
        )
        for sid, snap_date, payload in cur.fetchall():
            try:
                items = json.loads(payload).get("items") or []
            except Exception:
                continue
            for it in items:
                # Defensive: items can be huge; we only need the lookup fields.
                slim = {
                    "section_id": sid,
                    "title":   (it.get("title")   or "")[:240],
                    "summary": (it.get("summary") or "")[:340],
                    "url":     it.get("url") or "",
                    "date":    it.get("date") or snap_date,
                }
                out[sid][snap_date].append(slim)
    finally:
        conn.close()
    return out


def _find_thread_items(
    snapshot_index: dict[str, dict[str, list[dict]]],
    needle: str,
) -> dict[str, list[dict]]:
    """Substring-match an entity name against every item across all sections
    and days. Returns {YYYY-MM-DD: [items]} sorted by section."""
    pat = re.compile(r"\b" + re.escape(needle) + r"\b", re.IGNORECASE)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for sid, by_date in snapshot_index.items():
        for day, items in by_date.items():
            for it in items:
                blob = it["title"] + " " + (it["summary"] or "")
                if pat.search(blob):
                    by_day[day].append(it)
    return by_day


def _synth_current_state(items_by_day: dict[str, list[dict]],
                          title: str) -> str:
    """Deterministic 1-2 sentence summary. The LLM hook can override later."""
    days = sorted(items_by_day.keys(), reverse=True)
    if not days:
        return f"No items yet for {title}."
    total = sum(len(v) for v in items_by_day.values())
    sections = set()
    for v in items_by_day.values():
        for it in v: sections.add(it["section_id"])
    today_n = len(items_by_day.get(days[0], []))
    span = (date.fromisoformat(days[0]) - date.fromisoformat(days[-1])).days + 1
    activity = "active today" if today_n else f"last seen {days[0]}"
    return (
        f"{title} has surfaced in {total} item{'s' if total != 1 else ''} "
        f"across {len(sections)} section{'s' if len(sections) != 1 else ''} "
        f"over {span} day{'s' if span != 1 else ''}; {activity}."
    )


def _heat_score(items_by_day: dict[str, list[dict]],
                 today_iso: str,
                 sections_touched: list[str],
                 pinned: bool) -> float:
    """Weighted score: today × 5, yesterday × 3, anything older × 1, plus
    section-breadth bonus, plus pinning bonus."""
    score = 0.0
    today_d = date.fromisoformat(today_iso)
    for d, items in items_by_day.items():
        try:
            age = (today_d - date.fromisoformat(d)).days
        except Exception:
            age = 99
        if age == 0:    w = 5.0
        elif age == 1:  w = 3.0
        elif age <= 3:  w = 1.5
        else:           w = 1.0
        score += w * len(items)
    score *= 1.0 + 0.06 * len(sections_touched)
    if pinned: score *= 1.25
    return round(score, 2)


def build_threads(
    *,
    store_db: Path,
    lake_db: Path,
    cross_section: dict | None,
    today: date | None = None,
) -> list[Thread]:
    today = today or date.today()
    today_iso = today.isoformat()
    cands = _candidate_entities(lake_db, cross_section, today_iso, limit=80)
    snap = _load_snapshot_items(store_db, today, LOOKBACK_DAYS)
    threads: list[Thread] = []
    seen_slugs: set[str] = set()

    for c in cands:
        name = c["name"]
        items_by_day = _find_thread_items(snap, name)
        days_active = len(items_by_day)
        items_total = sum(len(v) for v in items_by_day.values())
        if days_active < MIN_DAYS or items_total < MIN_ITEMS:
            continue
        sections = sorted({it["section_id"]
                           for v in items_by_day.values() for it in v})
        # Cap per-day items so the page stays readable
        trimmed: dict[str, list[dict]] = {}
        for d, items in items_by_day.items():
            items.sort(key=lambda it: it["section_id"])
            trimmed[d] = items[:MAX_RECENT_PER_DAY]
        # Cap total items per thread
        flat_count = sum(len(v) for v in trimmed.values())
        if flat_count > MAX_ITEMS_IN_THREAD:
            for d in sorted(trimmed, reverse=False)[:-7]:
                trimmed.pop(d, None)
        slug = _slug(name)
        # disambiguate slug collisions
        base = slug; i = 2
        while slug in seen_slugs:
            slug = f"{base}-{i}"; i += 1
        seen_slugs.add(slug)
        t = Thread(
            id=f"thread:{slug}",
            slug=slug,
            title=name,
            entity_type=c.get("type") or "topic",
            days_active=days_active,
            items_total=items_total,
            items_by_day=trimmed,
            sections_touched=sections,
            heat_score=_heat_score(items_by_day, today_iso, sections,
                                   pinned=c.get("pinned", False)),
            is_active_today=today_iso in items_by_day and bool(items_by_day[today_iso]),
            synth=_synth_current_state(items_by_day, name),
        )
        threads.append(t)
    threads.sort(key=lambda t: -t.heat_score)
    return threads[:MAX_THREADS]


def write_threads_json(threads: list[Thread], out_dir: Path,
                        today: date | None = None) -> Path:
    out_dir = Path(out_dir)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    doc = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lookback_days": LOOKBACK_DAYS,
        "thread_count": len(threads),
        "threads": [asdict(t) for t in threads],
    }
    path = out_dir / "data" / "threads.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return path


def build_from_repo(repo: Path, out_dir: Path,
                     today: date | None = None,
                     cross_section: dict | None = None) -> tuple[list[Thread], Path]:
    if cross_section is None:
        cs_path = repo / "lake" / "sections" / "_meta" / (today or date.today()).isoformat() / "cross_section.json"
        if cs_path.exists():
            cross_section = json.loads(cs_path.read_text(encoding="utf-8"))
    threads = build_threads(
        store_db=repo / "data" / "store.sqlite",
        lake_db=repo / "lake"  / "db" / "worldscope.sqlite",
        cross_section=cross_section,
        today=today,
    )
    path = write_threads_json(threads, out_dir, today=today)
    return threads, path
