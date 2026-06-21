"""worldscope.story_threads — cross-day story identity → Developing Situations.

``stories.py`` answers *"what are today's top stories?"* by clustering one day's
records into event-level threads ranked by independent-coverage breadth. But a
world event is not a one-day object: it builds, peaks, and fades over days. A
reader of an intelligence product wants the *trajectory* — "this is day 5 of the
Israel–Iran exchange, and coverage breadth just doubled" — not a fresh,
amnesiac top-10 every morning.

This module supplies that missing temporal identity (the documented roadmap
"Next" for ``stories.py``). It runs the existing per-day clustering across a
window, **links** each day's clusters to the same evolving real-world situation,
and assigns a **persistent thread id**. From the linked chain it derives, per
thread: age, days active, a day-by-day breadth series, peak, and — the payload —
**momentum**: is independent coverage *escalating*, *steady*, or *cooling*
versus the thread's own recent baseline? The result is a **Developing
Situations** board that turns daily snapshots into tracked narratives.

How linking works (deterministic, explainable)
----------------------------------------------
Each daily cluster carries a **signature**: its salient entities (cleaned of
source-desk and geo-prefix noise) plus its headline tokens. Processing days
oldest→newest, a cluster joins the *active* thread (one seen within a small day
gap) whose signature it most overlaps (Jaccard ≥ threshold); otherwise it starts
a new thread. No model, no embeddings required — it reuses the same lexical
signal ``stories.py`` already trusts, so identical inputs give identical threads.

Design constraints, matched to the rest of the codebase
-------------------------------------------------------
  * Pure, offline core. ``link_threads`` takes a list of ``(day, [story_dict])``
    and returns dataclasses, so the whole linker is unit-testable with no DB,
    no model, and no clustering.
  * Thin adapters reuse ``stories.build_stories`` (DB or committed JSONL), so the
    threads are built from exactly the clusters the front page shows.
  * Build-time / static, and a defensive brief stage — a failure logs but never
    blocks a brief.

Run standalone against the local lake::

    python -m worldscope.story_threads --date 2026-06-10 --days 21
    python -m worldscope.story_threads --date 2026-06-10 --days 21 --write dist
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from . import signals as sg
from . import stories as st
from .lake import LAKE_SECTIONS

LAKE_META = LAKE_SECTIONS / "_meta"
PAGE_PATH = "developing.html"

# ---- tuning knobs -----------------------------------------------------------
DEFAULT_WINDOW_DAYS = 21      # how far back to chain threads
DEFAULT_LINK_JACCARD = 0.18   # min signature overlap to join a thread
DEFAULT_GAP_TOLERANCE = 2     # a thread stays "active" across up to this many quiet days
DEFAULT_STORIES_PER_DAY = 8   # clusters considered per day (breadth-ranked)
DEFAULT_TOP_N = 30
ESCALATING, STEADY, COOLING, NEW = "escalating", "steady", "cooling", "new"

_SPARK = "▁▂▃▄▅▆▇█"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]+")

# Entity noise the JSONL path leaks (no source-name stoplist without a DB):
# desk bylines ("newsroom tass") carry no situational meaning; geo prefixes
# ("country russia", "city atlanta") are meaningful but should keep just the place.
_DROP_ENTITY_PREFIXES = ("newsroom ", "source ", "publication ")
_STRIP_ENTITY_PREFIXES = ("country ", "city ", "region ", "province ", "state ")


# ============================================================================
# Signatures (pure)
# ============================================================================

def clean_entity(ent: str) -> Optional[str]:
    """Normalize a story entity for linking/labeling, or None to drop it."""
    e = (ent or "").strip().lower()
    if not e:
        return None
    for p in _DROP_ENTITY_PREFIXES:
        if e.startswith(p):
            return None
    for p in _STRIP_ENTITY_PREFIXES:
        if e.startswith(p):
            e = e[len(p):].strip()
            break
    return e or None


def _headline_tokens(headline: str, *, max_tokens: int = 8) -> set[str]:
    toks: list[str] = []
    for m in _TOKEN_RE.findall((headline or "").lower()):
        if len(m) < 3 or m in sg._STOPWORDS:
            continue
        toks.append(m)
        if len(toks) >= max_tokens:
            break
    return set(toks)


def signature(story: dict) -> set[str]:
    """The set of tokens identifying a daily cluster: cleaned entities (prefixed
    ``e:`` so they can't collide with headline tokens) plus headline tokens."""
    sig: set[str] = set()
    for ent in (story.get("top_entities") or [])[:8]:
        ce = clean_entity(ent)
        if ce:
            sig.add("e:" + ce)
    sig |= _headline_tokens(story.get("headline") or "")
    return sig


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def _label_from_signature(sig: set[str], fallback: str) -> str:
    """Human label for a thread: prefer its cleaned entities."""
    ents = sorted(t[2:] for t in sig if t.startswith("e:"))
    if ents:
        ents.sort(key=lambda s: (-len(s), s))     # favor specific multi-word names
        return " · ".join(e.title() for e in ents[:3])
    return fallback[:80]


# ============================================================================
# Linking
# ============================================================================

@dataclass
class _Node:
    day: str
    headline: str
    n_outlets: int
    n_sections: int
    n_records: int
    url: str
    sig: set[str]


@dataclass
class Thread:
    id: str
    label: str
    first_seen: str
    last_seen: str
    days_active: int
    age_days: int               # first_seen..as_of span in days
    breadth_by_day: list[tuple[str, int]]   # (day, n_outlets) oldest→newest
    peak_breadth: int
    latest_headline: str
    latest_url: str
    top_keys: list[str]
    direction: str              # escalating | steady | cooling | new
    momentum: float             # today's breadth / recent baseline (1.0 = flat)
    score: float
    active_today: bool
    nodes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
            "days_active": self.days_active, "age_days": self.age_days,
            "breadth_by_day": [list(b) for b in self.breadth_by_day],
            "peak_breadth": self.peak_breadth,
            "latest_headline": self.latest_headline, "latest_url": self.latest_url,
            "top_keys": self.top_keys, "direction": self.direction,
            "momentum": round(self.momentum, 3), "score": round(self.score, 4),
            "active_today": self.active_today, "nodes": self.nodes,
        }


def _thread_id(first_day: str, sig: set[str]) -> str:
    base = first_day + "|" + ",".join(sorted(sig))
    return "thr-" + hashlib.sha1(base.encode()).hexdigest()[:12]


class _ThreadAccum:
    """Mutable accumulator used during linking; frozen into a Thread at the end."""

    def __init__(self, node: _Node) -> None:
        self.id = _thread_id(node.day, node.sig)
        self.nodes: list[_Node] = [node]
        self.sig: set[str] = set(node.sig)   # running union, for matching

    def add(self, node: _Node) -> None:
        self.nodes.append(node)
        # bias the running signature toward recent days but keep history
        self.sig = set(node.sig) | {t for t in self.sig}

    @property
    def last_day(self) -> str:
        return self.nodes[-1].day


def link_threads(
    daily_stories: list[tuple[str, list[dict]]],
    *,
    jaccard: float = DEFAULT_LINK_JACCARD,
    gap_tolerance: int = DEFAULT_GAP_TOLERANCE,
) -> list[_ThreadAccum]:
    """Chain per-day clusters into threads. ``daily_stories`` is ``[(iso_day,
    [story_dict, …]), …]`` in ANY order; it is sorted oldest→newest here. Pure
    and deterministic."""
    days_sorted = sorted(daily_stories, key=lambda kv: kv[0])
    threads: list[_ThreadAccum] = []

    for iso_day, stories in days_sorted:
        day = date.fromisoformat(iso_day)
        # Only threads seen within the gap window are eligible to extend.
        active = [
            t for t in threads
            if 0 <= (day - date.fromisoformat(t.last_day)).days <= gap_tolerance
        ]
        used_threads: set[int] = set()
        for s in stories:
            sig = signature(s)
            if not sig:
                continue
            node = _Node(
                day=iso_day, headline=s.get("headline") or "",
                n_outlets=int(s.get("n_outlets") or 0),
                n_sections=int(s.get("n_sections") or 0),
                n_records=int(s.get("n_records") or 0),
                url=s.get("representative_url") or "", sig=sig,
            )
            best_i, best_j = -1, jaccard
            for i, t in enumerate(active):
                if i in used_threads:
                    continue
                j = _jaccard(sig, t.sig)
                if j >= best_j:
                    best_i, best_j = i, j
            if best_i >= 0:
                active[best_i].add(node)
                used_threads.add(best_i)
            else:
                threads.append(_ThreadAccum(node))
    return threads


# ============================================================================
# Scoring + freezing into Threads
# ============================================================================

def _direction(breadth: list[int]) -> tuple[str, float]:
    """Classify the trajectory from the breadth series. Momentum = last value
    over the mean of the preceding values (1.0 = flat)."""
    if len(breadth) <= 1:
        return NEW, 1.0
    last = breadth[-1]
    prior = breadth[:-1]
    baseline = sum(prior) / len(prior) if prior else 0.0
    if baseline <= 0:
        return (ESCALATING, 2.0) if last > 0 else (STEADY, 1.0)
    mom = last / baseline
    if mom >= 1.4:
        return ESCALATING, mom
    if mom <= 0.7:
        return COOLING, mom
    return STEADY, mom


def _freeze(t: _ThreadAccum, *, as_of: date) -> Thread:
    nodes = sorted(t.nodes, key=lambda n: n.day)
    # collapse multiple clusters on the same day → keep the broadest
    by_day: dict[str, _Node] = {}
    for n in nodes:
        cur = by_day.get(n.day)
        if cur is None or n.n_outlets > cur.n_outlets:
            by_day[n.day] = n
    day_nodes = [by_day[d] for d in sorted(by_day)]
    breadth_by_day = [(n.day, n.n_outlets) for n in day_nodes]
    breadth = [n.n_outlets for n in day_nodes]
    first_seen, last_seen = day_nodes[0].day, day_nodes[-1].day
    age_days = (as_of - date.fromisoformat(first_seen)).days
    days_active = len(day_nodes)
    direction, momentum = _direction(breadth)
    active_today = last_seen == as_of.isoformat()
    latest = day_nodes[-1]

    # union signature for label/keys
    union_sig: set[str] = set()
    for n in day_nodes:
        union_sig |= n.sig
    label = _label_from_signature(union_sig, latest.headline)
    top_keys = sorted(
        (k[2:] for k in union_sig if k.startswith("e:")),
        key=lambda s: (-len(s), s),
    )[:6]

    # Score: reward broad, escalating, durable, currently-active situations.
    peak = max(breadth) if breadth else 0
    recency_w = 1.0 if active_today else 0.5 ** ((as_of - date.fromisoformat(last_seen)).days)
    dir_w = {ESCALATING: 1.6, NEW: 1.2, STEADY: 1.0, COOLING: 0.6}[direction]
    durab_w = 1.0 + 0.15 * (days_active - 1)
    score = peak * dir_w * durab_w * recency_w

    return Thread(
        id=t.id, label=label, first_seen=first_seen, last_seen=last_seen,
        days_active=days_active, age_days=age_days, breadth_by_day=breadth_by_day,
        peak_breadth=peak, latest_headline=latest.headline,
        latest_url=latest.url, top_keys=top_keys, direction=direction,
        momentum=momentum, score=score, active_today=active_today,
        nodes=[{"day": n.day, "headline": n.headline[:160], "n_outlets": n.n_outlets,
                "n_sections": n.n_sections, "url": n.url} for n in day_nodes],
    )


def freeze_threads(
    accums: list[_ThreadAccum], *, as_of: date, min_days: int = 2, top_n: int = DEFAULT_TOP_N,
) -> list[Thread]:
    """Turn accumulators into scored Threads, keeping only genuinely *developing*
    ones (seen on ≥ ``min_days`` distinct days), ranked by score."""
    out: list[Thread] = []
    for acc in accums:
        thr = _freeze(acc, as_of=as_of)
        if thr.days_active < min_days:
            continue
        out.append(thr)
    out.sort(key=lambda t: t.score, reverse=True)
    return out[:top_n]


def sparkline(breadth: Iterable[int]) -> str:
    vals = list(breadth)
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return _SPARK[3] * len(vals)
    span = hi - lo
    return "".join(_SPARK[min(len(_SPARK) - 1, int((v - lo) / span * (len(_SPARK) - 1)))]
                    for v in vals)


# ============================================================================
# Orchestration (build_stories per day → link → freeze)
# ============================================================================

def build_threads(
    *, today: date, days: int = DEFAULT_WINDOW_DAYS, conn=None,
    stories_per_day: int = DEFAULT_STORIES_PER_DAY,
) -> list[Thread]:
    """Run per-day clustering across the window, link clusters into threads, and
    return scored, developing threads. Reuses ``stories.build_stories`` so the
    threads are built from exactly the clusters the Top Stories page shows."""
    daily: list[tuple[str, list[dict]]] = []
    for i in range(days + 1):
        d = today - timedelta(days=i)
        try:
            ss = st.build_stories(today=d, days=0, conn=conn, top_n=stories_per_day)
        except Exception:
            ss = []
        if ss:
            daily.append((d.isoformat(), [s.to_dict() for s in ss[:stories_per_day]]))
    accums = link_threads(daily)
    return freeze_threads(accums, as_of=today)


# ============================================================================
# Rendering — brief panel + public page (pure)
# ============================================================================

_ARROW = {ESCALATING: "▲", STEADY: "▬", COOLING: "▼", NEW: "✷"}
_DIR_WORD = {ESCALATING: "escalating", STEADY: "steady", COOLING: "cooling", NEW: "new"}


def render_threads_panel(threads: list[Thread], *, max_show: int = 8) -> str:
    """Compact 'Developing Situations' panel for the daily brief (house style).
    Leads with threads still active today, escalating first."""
    import html as _html
    active = [t for t in threads if t.active_today]
    if not active:
        return ""
    rows = []
    for t in active[:max_show]:
        spark = sparkline(b for _, b in t.breadth_by_day)
        link = ""
        if t.latest_url:
            link = (f" — <a href='{_html.escape(t.latest_url, quote=True)}'>"
                    f"{_html.escape(t.latest_headline[:80])}</a>")
        rows.append(
            "<li>"
            f"<span class='new-badge'>{_ARROW[t.direction]} {t.days_active}d</span>"
            f"<strong>{_html.escape(t.label[:70])}</strong>"
            f"<span class='meta'> · {spark} · {t.peak_breadth} outlets peak · "
            f"{_DIR_WORD[t.direction]}</span>"
            f"<div class='abs'>day {t.days_active} of coverage; "
            f"breadth {_DIR_WORD[t.direction]} "
            f"({t.momentum:.1f}× recent baseline).{link}</div>"
            "</li>"
        )
    return (
        "<section class='section'>"
        "<h2>🧵 Developing Situations "
        f"<span class='count'>· {len(active)} active threads</span></h2>"
        "<p class='synth'>Top-story clusters linked across days into tracked "
        "threads, so a situation's <em>trajectory</em> — building, steady, or "
        "cooling — is visible, not just today's snapshot.</p>"
        f"<ul class='items'>{''.join(rows)}</ul>"
        "</section>"
    )


def build_body(threads: list[Thread], *, today: date) -> str:
    """Full Developing Situations page body (no chrome)."""
    import html as _html
    active = [t for t in threads if t.active_today]
    dormant = [t for t in threads if not t.active_today]

    def _thread_block(t: Thread) -> str:
        spark = sparkline(b for _, b in t.breadth_by_day)
        days = "".join(
            f'<span class="inline-block mr-3 mb-1">{_html.escape(d[5:])}'
            f'<span class="text-slate">·{b}</span></span>'
            for d, b in t.breadth_by_day
        )
        link = (f'<a href="{_html.escape(t.latest_url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{_html.escape(t.latest_headline[:140])}</a>'
                if t.latest_url else _html.escape(t.latest_headline[:140]))
        keys = (' · '.join(_html.escape(k) for k in t.top_keys[:5])) if t.top_keys else ""
        badge_cls = {
            ESCALATING: "text-rose-700", COOLING: "text-sky-700",
            STEADY: "text-slate", NEW: "text-emerald-700",
        }[t.direction]
        return (
            '<li class="py-4 border-b border-mist">'
            f'<div class="flex items-baseline justify-between gap-3">'
            f'<span class="font-semibold text-navy">{_html.escape(t.label[:90])}</span>'
            f'<span class="font-sans text-xs {badge_cls} whitespace-nowrap">'
            f'{_ARROW[t.direction]} {_DIR_WORD[t.direction]} · {t.momentum:.1f}×</span>'
            f'</div>'
            f'<div class="font-mono text-base text-navy mt-1">{spark} '
            f'<span class="font-sans text-xs text-slate">peak {t.peak_breadth} outlets · '
            f'day {t.days_active} of {t.age_days + 1} · since {_html.escape(t.first_seen)}</span></div>'
            f'<div class="font-sans text-sm mt-1">{link}</div>'
            + (f'<div class="font-sans text-xs text-slate mt-1">{keys}</div>' if keys else "")
            + f'<div class="font-sans text-xs text-slate mt-1">{days}</div>'
            '</li>'
        )

    intro = (
        '<h1>Developing Situations</h1>'
        '<p class="font-sans text-sm text-slate mt-2 mb-7">Each row is a real-world '
        'situation tracked <strong>across days</strong>: WORLDSCOPE links the daily '
        'top-story clusters that cover the same event into one thread, so you can see '
        'whether independent coverage is <span class="text-rose-700">escalating</span>, '
        'steady, or <span class="text-sky-700">cooling</span> — the trajectory, not just '
        f'today. <span class="text-slate">As of {_html.escape(today.isoformat())}.</span></p>'
    )
    parts = [intro]
    parts.append('<h2 class="mt-4 mb-3 pb-2 border-b border-mist">Active today '
                 f'<span class="font-sans text-sm text-slate font-normal">· {len(active)}'
                 '</span></h2>')
    parts.append('<ul>' + "".join(_thread_block(t) for t in active) + '</ul>'
                 if active else '<p class="font-sans text-sm text-slate">No threads are '
                 'active today.</p>')
    if dormant:
        parts.append('<h2 class="mt-10 mb-3 pb-2 border-b border-mist">Recently active '
                     f'<span class="font-sans text-sm text-slate font-normal">· '
                     f'{len(dormant)}</span></h2>')
        parts.append('<ul>' + "".join(_thread_block(t) for t in dormant[:20]) + '</ul>')
    return "".join(parts)


# ============================================================================
# Artifact + page writer + lake adapter
# ============================================================================

def write_threads_artifact(today: date, threads: list[Thread], *,
                           meta_root: Path = LAKE_META) -> Path:
    out_dir = meta_root / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": today.isoformat(),
        "window_days": DEFAULT_WINDOW_DAYS,
        "n_threads": len(threads),
        "n_active_today": sum(1 for t in threads if t.active_today),
        "threads": [t.to_dict() for t in threads],
    }
    out_path = out_dir / "story_threads.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_path


def write_page(out_root: Path, body: str, *, wrap) -> Path:
    crumbs = [("WORLDSCOPE", "index.html"), ("Developing", "")]
    html_doc = wrap(
        "Developing Situations", body, crumbs, base="",
        description="WORLDSCOPE developing situations: top-story threads tracked "
                    "across days, with coverage-breadth momentum.",
    )
    out_path = Path(out_root) / PAGE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


# ============================================================================
# CLI
# ============================================================================

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-day story-thread tracker.")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--write", metavar="OUT_DIR", help="render <OUT_DIR>/developing.html")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.date)
    threads = build_threads(today=today, days=args.days)
    active = [t for t in threads if t.active_today]
    print(f"[threads] {len(threads)} developing threads over {args.days}d "
          f"({len(active)} active on {today}).\n")
    for i, t in enumerate(threads[:args.top], 1):
        spark = sparkline(b for _, b in t.breadth_by_day)
        flag = "•" if t.active_today else " "
        print(f"{i:2}.{flag} [{_ARROW[t.direction]} {_DIR_WORD[t.direction]:10s} "
              f"{t.momentum:.1f}× | {t.days_active}d | peak {t.peak_breadth}] "
              f"{t.label[:54]}")
        print(f"      {spark}  {t.first_seen}→{t.last_seen}")

    if args.write:
        from . import site_builder as sb
        body = build_body(threads, today=today)
        path = write_page(Path(args.write), body, wrap=sb._wrap)
        write_threads_artifact(today, threads)
        print(f"\n[threads] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
