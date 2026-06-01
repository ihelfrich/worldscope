"""worldscope.radar — the research radar: development flagging, source-credibility
scoring, dataset building, and light exploratory analysis over the lake.

Where ``signals.py`` answers *"what is converging across sources right now?"*,
the radar answers the adjacent research questions:

  * **Developments** — which keys *spiked* today versus their own recent
    baseline (a surge), or *broke in broadly for the first time* (novel
    multi-section emergence)? These are the "something is happening here" flags
    a researcher wants surfaced before it is obvious. They are written to the
    lake's ``anomalies`` table — the same table the daily graphics already read
    ("Top anomalies") but which no detector populated until now.

  * **Source credibility** — beyond mere uptime (``source_health``), how
    *trustworthy* is each source's signal? We score it by **corroboration**:
    when a source reports something, do *independent* sections corroborate the
    same key? A source whose claims are routinely echoed elsewhere earns a high
    score; one that is consistently alone does not.

  * **Datasets** — pull every record matching a key/term into a tidy, tabular
    dataset (CSV, plus Parquet when pandas is available) for downstream
    quantitative work, and emit a short exploratory note (counts over time,
    section spread, co-occurring keys, a crude trend read).

  * **Candidate sources** — external domains frequently cited across the lake
    that we do *not* yet ingest as first-class sources: a grounded, free seed
    for source discovery. (Promotion to a real adapter stays human-in-the-loop;
    see ARCHITECTURE.md.)

Design constraints, identical to ``signals.py``:
  * Pure, offline, stdlib-only core. Every scorer takes plain dicts so it is
    trivially unit-testable. Parquet export is the only optional dependency and
    degrades to CSV.
  * Thin adapters read the populated lake SQLite DB (production) or the
    committed JSONL section files (local/CI fallback) — reusing the same loaders
    as ``signals.py`` so the two engines always see the same records.
  * Nothing here can abort a brief: the brief-stage wrapper swallows failures.

Run standalone against the local lake::

    python -m worldscope.radar developments --days 14          # print today's flags
    python -m worldscope.radar developments --write            # + persist to anomalies
    python -m worldscope.radar credibility --days 14           # score every source
    python -m worldscope.radar dataset "iran" --days 30        # build + explore a dataset
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

from . import signals as sg

REPO = Path(__file__).resolve().parent.parent
LAKE_ROOT = REPO / "lake"
LAKE_META = LAKE_ROOT / "sections" / "_meta"
DATASETS_ROOT = LAKE_ROOT / "datasets"

METHOD = "research-radar-v1"

# ---- tuning knobs (all overridable through the public functions) ------------
RADAR_WINDOW_DAYS = 14       # baseline window for surge detection
SURGE_MIN_TODAY = 3          # a surge must clear this absolute count today
SURGE_Z = 2.0               # Poisson z threshold for "spiked vs baseline"
NOVEL_MIN_SECTIONS = 2       # a novel key must break in across >= this many sections
TOP_DEVELOPMENTS = 30

CRED_MIN_RECORDS = 5         # below this, credibility is reported as low-confidence
CRED_W_CORROBORATION = 0.55  # weight on cross-source corroboration (echo)
CRED_W_RELIABILITY = 0.20    # weight on uptime reliability
CRED_W_TIER = 0.25           # weight on the source-tier trust prior

# Tier prior: corroboration measures *echo*, which unfairly punishes niche but
# authoritative primary documents (a CVE feed or insider-filing index rarely
# gets quoted by the foreign-press desk). The tier the source was registered
# with supplies an authority floor so those sources aren't graded as unreliable.
TIER_PRIOR = {
    "primary_document": 1.0,
    "official": 1.0,
    "government": 1.0,
    "mainstream_independent": 0.8,
    "specialist": 0.8,
    "aggregator": 0.6,
    "social": 0.45,
    "unknown": 0.6,
}
DEFAULT_TIER_PRIOR = 0.6

# Hosts that are infrastructure / aggregators, not original sources worth
# promoting to a first-class adapter.
_AGGREGATOR_HOSTS = {
    "news.google.com", "google.com", "t.co", "bit.ly", "youtube.com",
    "youtu.be", "twitter.com", "x.com", "facebook.com", "archive.org",
    "web.archive.org", "rss.app", "feedproxy.google.com", "feeds.feedburner.com",
}


# ============================================================================
# Shared aggregation
# ============================================================================

@dataclass
class _KeyAgg:
    counts_by_day: dict[str, int] = field(default_factory=dict)
    today_sections: set = field(default_factory=set)
    all_sections: set = field(default_factory=set)
    evidence: list = field(default_factory=list)         # [{id, section, title, url}]
    label_votes: dict = field(default_factory=dict)

    def label(self, key: str) -> str:
        if self.label_votes:
            return max(self.label_votes.items(), key=lambda kv: kv[1])[0]
        return key


def aggregate_keys(
    records: Iterable[dict], *, today: date, days: int = RADAR_WINDOW_DAYS,
) -> dict[str, _KeyAgg]:
    """Roll records up by normalized key, tracking per-day counts, the sections
    each key touches (overall and today), and a little evidence. Reuses
    ``signals.record_key_pairs`` so radar and signals key on identical phrases."""
    horizon = today - timedelta(days=days)
    today_iso = today.isoformat()
    agg: dict[str, _KeyAgg] = defaultdict(_KeyAgg)
    for rec in records:
        if sg.is_noise_record(rec):
            continue
        day = sg._parse_day(rec)
        if day is None or day < horizon or day > today:
            continue
        day_iso = day.isoformat()
        section = str(rec.get("section_id") or rec.get("section") or "?")
        title = sg._clean_text(rec.get("title") or rec.get("original_text") or "")
        rid = rec.get("id") or rec.get("_id") or ""
        url = rec.get("original_url") or rec.get("url") or ""
        for key, cased in sg.record_key_pairs(rec):
            a = agg[key]
            a.counts_by_day[day_iso] = a.counts_by_day.get(day_iso, 0) + 1
            a.all_sections.add(section)
            if day_iso == today_iso:
                a.today_sections.add(section)
                a.label_votes[cased] = a.label_votes.get(cased, 0) + 1
                if len(a.evidence) < 6 and title:
                    a.evidence.append({"id": rid, "section": section,
                                       "title": title[:160], "url": url})
    return agg


# ============================================================================
# Developments (surges + novel emergence)
# ============================================================================

@dataclass
class Development:
    key: str
    label: str
    category: str                 # 'surge' | 'novel'
    today_count: int
    baseline_mean: float
    z_score: float
    n_sections: int               # distinct sections corroborating today
    sections: list                # sorted
    description: str
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "category": self.category,
            "today_count": self.today_count,
            "baseline_mean": round(self.baseline_mean, 3),
            "z_score": round(self.z_score, 3), "n_sections": self.n_sections,
            "sections": self.sections, "description": self.description,
            "evidence": self.evidence,
        }


def _poisson_z(today_count: int, baseline_mean: float) -> float:
    """Count-data z-score against a Poisson-ish baseline. The +1 floor on the
    variance keeps tiny baselines from manufacturing huge z-scores."""
    return (today_count - baseline_mean) / math.sqrt(baseline_mean + 1.0)


def detect_developments(
    agg: dict[str, _KeyAgg], *, today: date, days: int = RADAR_WINDOW_DAYS,
    surge_min_today: int = SURGE_MIN_TODAY, surge_z: float = SURGE_Z,
    surge_min_sections: int = NOVEL_MIN_SECTIONS,
    novel_min_sections: int = NOVEL_MIN_SECTIONS, top_n: int = TOP_DEVELOPMENTS,
) -> list[Development]:
    """Flag keys that either spiked today vs their own baseline (surge) or broke
    in broadly for the first time in the window (novel). Prior-day counts include
    implicit zeros, so a key absent for 13 days then loud today reads correctly.

    Both kinds require cross-section breadth (``surge_min_sections`` /
    ``novel_min_sections``): a spike confined to one feed is that feed being
    busy, not a development. Corroboration across independent sections is the
    whole point — same discipline as the signals engine.
    """
    today_iso = today.isoformat()
    prior_days = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    out: list[Development] = []
    for key, a in agg.items():
        today_count = a.counts_by_day.get(today_iso, 0)
        if today_count <= 0:
            continue
        prior = [a.counts_by_day.get(d, 0) for d in prior_days]
        prior_total = sum(prior)
        n_sections = len(a.today_sections)
        sections = sorted(a.today_sections)
        label = a.label(key)
        if prior_total == 0:
            # Never seen in the baseline window, yet broad today → novel.
            if n_sections >= novel_min_sections:
                out.append(Development(
                    key=key, label=label, category="novel",
                    today_count=today_count, baseline_mean=0.0,
                    z_score=float(today_count), n_sections=n_sections,
                    sections=sections,
                    description=(
                        f"'{label}' has no prior mention in {days}d but broke in "
                        f"across {n_sections} sections today "
                        f"({', '.join(sections[:5])})."),
                    evidence=a.evidence,
                ))
            continue
        mean = prior_total / len(prior_days)
        z = _poisson_z(today_count, mean)
        if (today_count >= surge_min_today and z >= surge_z
                and n_sections >= surge_min_sections):
            out.append(Development(
                key=key, label=label, category="surge",
                today_count=today_count, baseline_mean=mean, z_score=z,
                n_sections=n_sections, sections=sections,
                description=(
                    f"'{label}' spiked to {today_count} mentions today vs a "
                    f"{days}d baseline of {mean:.1f}/day (z={z:.1f}), across "
                    f"{n_sections} sections."),
                evidence=a.evidence,
            ))
    # Novel first (genuinely new), then surges, each by strength.
    out.sort(key=lambda d: (d.category != "novel", -d.z_score, -d.today_count))
    return out[:top_n]


def _development_id(key: str, category: str, today: date) -> str:
    return hashlib.sha1(
        f"{METHOD}|{category}|{key}|{today.isoformat()}".encode()).hexdigest()


def persist_developments(lake, devs: list[Development], *, today: date) -> int:
    """Write developments to the lake's ``anomalies`` table so they flow into the
    existing graphics + drill-down surfaces. Idempotent per (key, category, day)."""
    n = 0
    for d in devs:
        lake.add_anomaly(
            anomaly_id=_development_id(d.key, d.category, today),
            section_id="radar",
            category=f"radar-{d.category}",
            z_score=round(d.z_score, 3),
            description=d.description,
            evidence=[e["id"] for e in d.evidence if e.get("id")],
        )
        n += 1
    return n


# ============================================================================
# Source credibility
# ============================================================================

@dataclass
class SourceCredibility:
    source_id: str
    n_records: int
    corroboration_rate: float     # fraction of records echoed by another section
    consecutive_failures: int
    reliability: float            # uptime component, 0-1
    score: float                  # blended 0-1
    grade: str                    # A–F, or 'n/a' when under-sampled
    low_confidence: bool          # True when n_records < CRED_MIN_RECORDS
    tier: str = "unknown"         # registered source tier
    tier_prior: float = DEFAULT_TIER_PRIOR

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "n_records": self.n_records,
            "corroboration_rate": round(self.corroboration_rate, 4),
            "consecutive_failures": self.consecutive_failures,
            "reliability": round(self.reliability, 4),
            "tier": self.tier, "tier_prior": round(self.tier_prior, 4),
            "score": round(self.score, 4), "grade": self.grade,
            "low_confidence": self.low_confidence,
        }


def _grade(score: float) -> str:
    for cut, g in ((0.85, "A"), (0.70, "B"), (0.55, "C"), (0.40, "D")):
        if score >= cut:
            return g
    return "F"


def score_sources(
    records: Iterable[dict], *,
    health_by_source: Optional[dict[str, int]] = None,
    tier_by_source: Optional[dict[str, str]] = None,
) -> list[SourceCredibility]:
    """Score each source by cross-source corroboration + uptime + tier prior.

    A record is *corroborated* when at least one of its keys is also reported by
    a record from a **different section**. ``health_by_source`` maps source_id →
    consecutive_failures (from ``source_health``); ``tier_by_source`` maps
    source_id → tier (from ``sources``). Both absent → healthy / unknown tier.

    Score = ``CRED_W_CORROBORATION`` · corroboration_rate
          + ``CRED_W_RELIABILITY``  · reliability (1/(1+consecutive_failures))
          + ``CRED_W_TIER``         · tier_prior.
    Deliberately transparent v1; every component is reported for audit.
    """
    records = [r for r in records if not sg.is_noise_record(r)]
    health_by_source = health_by_source or {}
    tier_by_source = tier_by_source or {}

    # Global key → set(sections) over the whole window.
    key_sections: dict[str, set] = defaultdict(set)
    for rec in records:
        section = str(rec.get("section_id") or rec.get("section") or "?")
        for k in sg.record_keys(rec):
            key_sections[k].add(section)

    by_source: dict[str, dict] = defaultdict(lambda: {"n": 0, "corrob": 0})
    for rec in records:
        src = str(rec.get("source_id") or rec.get("section_id") or "?")
        section = str(rec.get("section_id") or rec.get("section") or "?")
        bucket = by_source[src]
        bucket["n"] += 1
        corroborated = any(
            len(key_sections.get(k, set()) - {section}) >= 1
            for k in sg.record_keys(rec)
        )
        if corroborated:
            bucket["corrob"] += 1

    out: list[SourceCredibility] = []
    for src, b in by_source.items():
        n = b["n"]
        rate = b["corrob"] / n if n else 0.0
        fails = int(health_by_source.get(src, 0) or 0)
        reliability = 1.0 / (1.0 + fails)
        tier = (tier_by_source.get(src) or "unknown")
        tier_prior = TIER_PRIOR.get(tier, DEFAULT_TIER_PRIOR)
        score = (CRED_W_CORROBORATION * rate
                 + CRED_W_RELIABILITY * reliability
                 + CRED_W_TIER * tier_prior)
        low = n < CRED_MIN_RECORDS
        out.append(SourceCredibility(
            source_id=src, n_records=n, corroboration_rate=rate,
            consecutive_failures=fails, reliability=reliability,
            score=score, grade=("n/a" if low else _grade(score)),
            low_confidence=low, tier=tier, tier_prior=tier_prior,
        ))
    out.sort(key=lambda c: c.score, reverse=True)
    return out


# ============================================================================
# Candidate source discovery (grounded seed)
# ============================================================================

def _host_of(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def discover_candidate_sources(
    records: Iterable[dict], *, known_hosts: Optional[set[str]] = None,
    min_mentions: int = 3, top_n: int = 25,
) -> list[dict]:
    """Rank external domains cited across the lake that we do not already ingest.

    A grounded, zero-cost seed for source discovery: if a domain keeps showing
    up in what our existing sources link to, it may be worth ingesting directly.
    Promotion to a first-class adapter stays human-in-the-loop (see ARCHITECTURE).
    """
    known = {h.lower() for h in (known_hosts or set())}
    counts: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "sections": set()})
    for rec in records:
        host = _host_of(rec.get("original_url") or rec.get("url") or "")
        if not host or host in _AGGREGATOR_HOSTS or host in known:
            continue
        if any(host == k or host.endswith("." + k) for k in known):
            continue
        c = counts[host]
        c["mentions"] += 1
        c["sections"].add(str(rec.get("section_id") or rec.get("section") or "?"))
    out = [
        {"host": h, "mentions": c["mentions"],
         "sections": sorted(c["sections"]), "n_sections": len(c["sections"])}
        for h, c in counts.items() if c["mentions"] >= min_mentions
    ]
    out.sort(key=lambda d: (d["n_sections"], d["mentions"]), reverse=True)
    return out[:top_n]


# ============================================================================
# Dataset builder + light exploration
# ============================================================================

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-") or "dataset"


def build_dataset(
    records: Iterable[dict], *, terms: list[str], today: date,
    days: int = 30,
) -> list[dict]:
    """Extract a tidy, one-row-per-record dataset for every record whose key set
    or text matches any of ``terms`` within the window. Columns are stable and
    quant-friendly: date, section, source, key_matched, title, url, n_entities."""
    horizon = today - timedelta(days=days)
    # Word-boundary match so 'iran' hits Iran/Iranian/Iran's but not 'Tirante'.
    term_res = [(t.strip().lower(), re.compile(r"\b" + re.escape(t.strip()), re.I))
                for t in terms if t.strip()]
    rows: list[dict] = []
    for rec in records:
        if sg.is_noise_record(rec):
            continue
        day = sg._parse_day(rec)
        if day is None or day < horizon or day > today:
            continue
        keys_blob = " ".join(sg.record_keys(rec))
        text = sg._clean_text(rec.get("title") or rec.get("original_text") or "")
        haystack = f"{text} {keys_blob}"
        matched = None
        for label, rx in term_res:
            if rx.search(haystack):
                matched = label
                break
        if matched is None:
            continue
        rows.append({
            "date": day.isoformat(),
            "section": str(rec.get("section_id") or rec.get("section") or "?"),
            "source": str(rec.get("source_id") or ""),
            "key_matched": matched,
            "title": sg._clean_text(rec.get("title") or rec.get("original_text") or "")[:200],
            "url": rec.get("original_url") or rec.get("url") or "",
            "n_entities": len(sg._entity_names(rec)),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def explore_dataset(rows: list[dict]) -> dict:
    """Cheap, deterministic exploratory pass over a built dataset: volume over
    time, section spread, top co-occurring keys, and a crude first-half vs
    second-half trend read. No LLM, no external deps."""
    if not rows:
        return {"n_records": 0}
    per_day: dict[str, int] = defaultdict(int)
    per_section: dict[str, int] = defaultdict(int)
    per_key: dict[str, int] = defaultdict(int)
    for r in rows:
        per_day[r["date"]] += 1
        per_section[r["section"]] += 1
        per_key[r["key_matched"]] += 1
    days_sorted = sorted(per_day)
    counts = [per_day[d] for d in days_sorted]
    mid = len(counts) // 2 or 1
    first_half = sum(counts[:mid]) / max(mid, 1)
    second_half = sum(counts[mid:]) / max(len(counts) - mid, 1)
    if second_half > first_half * 1.25:
        trend = "rising"
    elif second_half < first_half * 0.8:
        trend = "fading"
    else:
        trend = "steady"
    return {
        "n_records": len(rows),
        "date_range": [days_sorted[0], days_sorted[-1]],
        "per_day": dict(sorted(per_day.items())),
        "top_sections": sorted(per_section.items(), key=lambda kv: -kv[1])[:10],
        "top_terms": sorted(per_key.items(), key=lambda kv: -kv[1])[:10],
        "trend": trend,
        "first_half_daily_avg": round(first_half, 2),
        "second_half_daily_avg": round(second_half, 2),
    }


def render_exploration_md(slug: str, stats: dict) -> str:
    """A short, human-readable note summarizing the exploratory pass."""
    if not stats.get("n_records"):
        return f"# {slug}\n\nNo matching records.\n"
    lines = [
        f"# Dataset: {slug}", "",
        f"- **Records:** {stats['n_records']}",
        f"- **Date range:** {stats['date_range'][0]} → {stats['date_range'][1]}",
        f"- **Trend:** {stats['trend']} "
        f"({stats['first_half_daily_avg']} → {stats['second_half_daily_avg']} /day)",
        "", "## Top sections", "",
    ]
    lines += [f"- {sec}: {n}" for sec, n in stats["top_sections"]]
    lines += ["", "## Top matched terms", ""]
    lines += [f"- {t}: {n}" for t, n in stats["top_terms"]]
    return "\n".join(lines) + "\n"


def export_dataset(
    rows: list[dict], stats: dict, *, slug: str, today: date,
    out_root: Path = DATASETS_ROOT,
) -> dict[str, str]:
    """Persist a dataset as CSV (always) + Parquet (when pandas is available) +
    an exploration note, under lake/datasets/<slug>/<date>/. Returns paths."""
    import csv

    folder = Path(out_root) / slug / today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    csv_path = folder / "data.csv"
    fields = ["date", "section", "source", "key_matched", "title", "url", "n_entities"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    written["csv"] = str(csv_path)

    try:  # Parquet is the analyst-friendly format, but optional.
        import pandas as pd  # noqa: WPS433
        pq_path = folder / "data.parquet"
        pd.DataFrame(rows, columns=fields).to_parquet(pq_path, index=False)
        written["parquet"] = str(pq_path)
    except Exception:  # pragma: no cover - pandas/pyarrow optional
        pass

    note_path = folder / "exploration.md"
    note_path.write_text(render_exploration_md(slug, stats), encoding="utf-8")
    written["note"] = str(note_path)

    stats_path = folder / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    written["stats"] = str(stats_path)
    return written


# ============================================================================
# Brief panel
# ============================================================================

def render_radar_panel(
    devs: list[Development], creds: list[SourceCredibility], *, max_show: int = 10,
) -> str:
    """Render a 'Research radar' section in the daily brief's house style."""
    import html as _html

    if not devs and not creds:
        return ""
    rows = []
    for d in devs[:max_show]:
        badge = "NEW" if d.category == "novel" else f"{d.z_score:.0f}σ"
        ev = d.evidence[0] if d.evidence else {}
        link = ""
        if ev.get("url"):
            link = (f" — <a href='{_html.escape(ev['url'], quote=True)}'>"
                    f"{_html.escape(ev.get('title', 'source')[:90])}</a>")
        rows.append(
            "<li>"
            f"<span class='new-badge'>{badge}</span>"
            f"<strong>{_html.escape(d.label[:90])}</strong>"
            f"<span class='meta'> · {_html.escape(', '.join(d.sections[:6]))}</span>"
            f"<div class='abs'>{_html.escape(d.description)}{link}</div>"
            "</li>"
        )
    # A compact credibility footnote: the least-corroborated active sources.
    cred_note = ""
    flagged = [c for c in creds if not c.low_confidence and c.grade in ("D", "F")]
    if flagged:
        names = ", ".join(f"{c.source_id} ({c.grade})" for c in flagged[:6])
        cred_note = (
            f"<p class='synth'>Low cross-source corroboration this window: "
            f"{_html.escape(names)} — treat single-source claims here with care.</p>"
        )
    n_novel = sum(1 for d in devs if d.category == "novel")
    synth = (
        f"<p class='synth'>{len(devs)} developments flagged "
        f"({n_novel} newly emerging, {len(devs) - n_novel} surging) against the "
        f"trailing {RADAR_WINDOW_DAYS}-day baseline.</p>"
    )
    return (
        "<section class='section'>"
        "<h2>📡 Research radar — developments &amp; source credibility "
        f"<span class='count'>· {len(devs)} flags</span></h2>"
        f"{synth}{cred_note}"
        f"<ul class='items'>{''.join(rows)}</ul>"
        "</section>"
    )


# ============================================================================
# Lake adapters + orchestration
# ============================================================================

def _health_map(conn) -> dict[str, int]:
    """source_id → consecutive_failures from source_health (best effort)."""
    try:
        cur = conn.execute(
            "SELECT source_id, consecutive_failures FROM source_health")
        return {r[0]: int(r[1] or 0) for r in cur.fetchall()}
    except Exception:
        return {}


def _tier_map(conn) -> dict[str, str]:
    """source_id → tier from the sources table (best effort)."""
    try:
        cur = conn.execute("SELECT id, tier FROM sources")
        return {r[0]: (r[1] or "unknown") for r in cur.fetchall()}
    except Exception:
        return {}


def _known_hosts(conn) -> set[str]:
    """Hosts we already ingest, from the sources table's url column."""
    hosts: set[str] = set()
    try:
        for r in conn.execute("SELECT url FROM sources WHERE url IS NOT NULL"):
            h = _host_of(r[0] or "")
            if h:
                hosts.add(h)
    except Exception:
        pass
    return hosts


def _load_records(today: date, days: int, conn=None) -> list[dict]:
    records: list[dict] = []
    if conn is not None:
        try:
            records = sg.load_records_from_db(conn, today=today, days=days)
        except Exception:
            records = []
    if not records:
        records = sg.load_records_from_jsonl(today=today, days=days)
    return records


def build_developments(
    *, today: date, days: int = RADAR_WINDOW_DAYS, conn=None,
) -> list[Development]:
    records = _load_records(today, days, conn)
    agg = aggregate_keys(records, today=today, days=days)
    return detect_developments(agg, today=today, days=days)


def build_credibility(
    *, today: date, days: int = RADAR_WINDOW_DAYS, conn=None,
) -> list[SourceCredibility]:
    records = _load_records(today, days, conn)
    health = _health_map(conn) if conn is not None else {}
    tiers = _tier_map(conn) if conn is not None else {}
    return score_sources(records, health_by_source=health, tier_by_source=tiers)


def write_radar_artifact(
    today: date, devs: list[Development], creds: list[SourceCredibility],
    candidates: list[dict], *, out_root: Path = LAKE_META,
) -> Path:
    """Persist the radar's JSON artifact under lake/sections/_meta/<date>/, the
    same convention the cross-section pass uses. The desk-officer routine reads
    this instead of re-deriving it from raw text."""
    out_dir = Path(out_root) / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "research_radar.json"
    payload = {
        "date": today.isoformat(),
        "method": METHOD,
        "developments": [d.to_dict() for d in devs],
        "source_credibility": [c.to_dict() for c in creds],
        "candidate_sources": candidates,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_path


# ============================================================================
# CLI
# ============================================================================

def _open_lake():
    from .lake import Lake
    lake = Lake.open()
    return lake, lake._ensure_open()


def _cmd_developments(args) -> int:
    today = date.fromisoformat(args.date)
    conn = None
    lake = None
    if args.write:
        lake, conn = _open_lake()
    try:
        devs = build_developments(today=today, days=args.days, conn=conn)
        print(f"[radar] {len(devs)} developments as of {today} "
              f"(baseline {args.days}d):\n")
        for i, d in enumerate(devs[:args.top], 1):
            tag = "NEW " if d.category == "novel" else f"{d.z_score:4.1f}σ"
            print(f"{i:2}. [{tag}] {d.label[:64]}")
            print(f"      {', '.join(d.sections[:8])}")
        if args.write and lake is not None:
            n = persist_developments(lake, devs, today=today)
            print(f"\n[radar] persisted {n} developments to the anomalies table.")
    finally:
        if lake is not None:
            lake.close()
    return 0


def _cmd_credibility(args) -> int:
    today = date.fromisoformat(args.date)
    lake, conn = _open_lake()
    try:
        creds = build_credibility(today=today, days=args.days, conn=conn)
        print(f"[radar] source credibility as of {today} (window {args.days}d):\n")
        print(f"{'source':32} {'grade':5} {'score':>6} {'corrob':>7} {'n':>5}")
        for c in creds[:args.top]:
            print(f"{c.source_id[:32]:32} {c.grade:5} {c.score:6.2f} "
                  f"{c.corroboration_rate:7.2f} {c.n_records:5}")
    finally:
        lake.close()
    return 0


def _cmd_dataset(args) -> int:
    today = date.fromisoformat(args.date)
    lake, conn = _open_lake()
    try:
        records = _load_records(today, args.days, conn)
        terms = [t for t in (args.terms or [args.query]) if t]
        rows = build_dataset(records, terms=terms, today=today, days=args.days)
        stats = explore_dataset(rows)
        slug = slugify(args.query)
        paths = export_dataset(rows, stats, slug=slug, today=today)
        print(f"[radar] dataset '{slug}': {len(rows)} records "
              f"({stats.get('trend', 'n/a')}).")
        for kind, p in paths.items():
            print(f"      {kind}: {p}")
    finally:
        lake.close()
    return 0


def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Research radar over the lake.")
    ap.add_argument("--date", default=date.today().isoformat())
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("developments", help="flag surges + novel emergence")
    d.add_argument("--days", type=int, default=RADAR_WINDOW_DAYS)
    d.add_argument("--top", type=int, default=30)
    d.add_argument("--write", action="store_true",
                   help="persist flags to the anomalies table")
    d.set_defaults(func=_cmd_developments)

    c = sub.add_parser("credibility", help="score every source by corroboration")
    c.add_argument("--days", type=int, default=RADAR_WINDOW_DAYS)
    c.add_argument("--top", type=int, default=60)
    c.set_defaults(func=_cmd_credibility)

    ds = sub.add_parser("dataset", help="build + explore a quant dataset")
    ds.add_argument("query", help="primary term, also used to name the dataset")
    ds.add_argument("--terms", nargs="*", help="extra match terms (OR)")
    ds.add_argument("--days", type=int, default=30)
    ds.set_defaults(func=_cmd_dataset)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(_main())
