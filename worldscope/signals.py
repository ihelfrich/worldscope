"""worldscope.signals — cross-source signal fusion → falsifiable, self-grading
predictions.

The lake holds thousands of records/day across ~40 sections (government actions,
SEC/insider filings, prediction markets, cyber advisories, conflict events,
foreign/domestic press, …). Individually each is noise; the edge is in
*convergence* — when an entity or theme lights up across several **independent**
sources at once, that co-movement tends to precede the move.

This module reads the day's lake, rolls records up by a normalized **key**
(an explicit entity name where we have one, otherwise a salient proper-noun /
ticker / CVE phrase from the headline), and scores each key's *cross-source
convergence*: how many distinct sections corroborate it, how recently, and at
what volume. The ranked result is a daily **Signals** list, each carrying its
evidence trail.

It then turns the top signals into **falsifiable, auto-gradable predictions**:
each predicts that a key will remain cross-section-salient over a short horizon,
with a resolution criterion the lake itself can grade later — so the system
accrues a real, calibrated track record (Brier / calibration / skill) on its
own trend-reading, with no human in the loop. Those graded predictions are what
let us trust a signal enough to back it with a paper bet downstream.

Design constraints, deliberately matched to the rest of the codebase:
  * Pure, offline, dependency-free core (only the stdlib). All scoring lives in
    functions that take plain dicts, so they're trivially unit-testable.
  * Thin adapters read either the populated lake SQLite DB (production, after
    sections have run) or the committed JSONL section files (local/CI fallback).
  * Nothing here can abort a brief: the brief-stage wrapper swallows failures.

Run standalone against the local lake:
    python -m worldscope.signals --days 7            # print today's signals
    python -m worldscope.signals --write             # persist predictions + grade due ones
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
LAKE_SECTIONS = REPO / "lake" / "sections"

METHOD = "signal-fusion-v1"

# ---- tuning knobs (all overridable through the public functions) ------------
DEFAULT_WINDOW_DAYS = 7      # how far back a signal may draw corroboration
DEFAULT_MIN_SECTIONS = 2     # cross-source is the whole point: require >= 2
DEFAULT_HALF_LIFE = 3.0      # recency decay half-life, in days
DEFAULT_TOP_N = 40
DEFAULT_HORIZON_DAYS = 14    # prediction look-ahead
CONF_FLOOR, CONF_CEIL = 0.55, 0.78   # conservative confidence band

# Words too generic to be a signal on their own. Multi-word phrases survive even
# if they contain these; only standalone single-word keys are filtered.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "with", "from", "into", "over",
    "new", "news", "update", "updates", "report", "reports", "says", "said",
    "will", "may", "can", "could", "would", "this", "that", "these", "those",
    "more", "most", "amid", "after", "before", "their", "they", "what", "when",
    "how", "why", "who", "first", "last", "year", "years", "day", "days", "week",
    "today", "world", "us", "u.s.", "usa", "inc", "ltd", "co", "corp",
    # data-source / platform names — these are *origins*, not subjects
    "kalshi", "polymarket", "predictit", "manifold", "metaculus", "liveuamap",
    "reuters", "bloomberg", "ap", "afp", "tass", "ria", "xinhua", "rss",
    "publication", "summary", "deliberations", "ministry",
    # feed-failure boilerplate that must never become a signal
    "feed error", "feed", "error", "httperror", "http", "timeout", "none",
    "gale warning", "warning",
    # calendar + generic administrative words that aren't subjects
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
    "district", "county", "city", "department", "office", "national",
}

# Records whose text contains any of these are dropped before fusion: they are
# feed-failure stubs or scraper errors, not content.
_ERROR_MARKERS = ("[feed error]", "httperror", "feed error", "[error]",
                  "[stub]", "incumbent not verified", "slot reserved")

_TAG_RE = re.compile(r"<[^>]+>")
_WRAP_RE = re.compile(r"\[(?:TITLE|LEDE)\s*[:\]]", re.IGNORECASE)
_PROPER_RE = re.compile(r"\b([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,3})\b")
_TICKER_RE = re.compile(r"\$[A-Z]{1,5}\b")
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{3,7}\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


# ============================================================================
# Key extraction
# ============================================================================

_LEAD_TAG_RE = re.compile(r"^\s*(?:\[[^\]]{1,30}\]\s*)+")


def _clean_text(text: str) -> str:
    """Strip HTML, the lake's "[TITLE: … | LEDE: …]" scraper wrappers, and
    leading "[China]" / "[Kalshi]" region/source tags, then keep only the
    headline portion (before the first ' | ' / ' — ' separator)."""
    text = _TAG_RE.sub(" ", text or "")
    text = _WRAP_RE.sub(" ", text)
    text = _LEAD_TAG_RE.sub("", text)
    # cut at the first lede/source separator so we key on the headline
    for sep in (" | ", " — ", " - LEDE", "|LEDE"):
        idx = text.find(sep)
        if idx > 12:
            text = text[:idx]
            break
    return _WS_RE.sub(" ", text).strip()


def is_noise_record(rec: dict) -> bool:
    """True for feed-failure stubs / scraper errors that must not be fused."""
    if rec.get("_error") or (isinstance(rec.get("extra"), dict) and rec["extra"].get("_error")):
        return True
    blob = f"{rec.get('title') or ''} {rec.get('original_text') or ''}".lower()
    return any(marker in blob for marker in _ERROR_MARKERS)


def _norm_key(s: str) -> str:
    s = _WS_RE.sub(" ", (s or "").strip().lower())
    s = s.strip(" .,:;\"'()[]")
    return s


def _entity_names(rec: dict) -> list[str]:
    """Pull canonical entity names from a record's entities field, which may be
    a list of strings or of {canonical_name|name|id} dicts."""
    raw = rec.get("entities")
    out: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        for it in raw:
            cn = None
            if isinstance(it, str):
                cn = it
            elif isinstance(it, dict):
                cn = it.get("canonical_name") or it.get("name") or it.get("id")
            if cn:
                # 'person:warsh-kevin' -> 'warsh kevin'; plain names pass through
                cn = str(cn).split(":", 1)[-1].replace("-", " ").replace("_", " ")
                out.append(cn)
    return out


def record_key_pairs(rec: dict, *, max_keys: int = 8) -> list[tuple[str, str]]:
    """Return ``(normalized_key, original_cased_label)`` pairs a record
    contributes.

    Keys come from (a) explicit entities, which are high quality, and
    (b) salient phrases mined from the headline: tickers ($AAPL), CVE ids, and
    Capitalized proper-noun runs. Liberal extraction is fine — the cross-section
    corroboration filter downstream discards anything that doesn't recur."""
    pairs: dict[str, str] = {}

    def add(cased: str) -> None:
        k = _norm_key(cased)
        words = k.split()
        if not words:
            return
        if len(words) == 1 and (len(k) < 3 or k in _STOPWORDS):
            return
        if all(w in _STOPWORDS for w in words) or k in _STOPWORDS:
            return
        pairs.setdefault(k, cased.strip())

    for name in _entity_names(rec):
        add(name)

    text = _clean_text(rec.get("title") or rec.get("original_text") or "")
    for m in _TICKER_RE.findall(text):
        add(m)
    for m in _CVE_RE.findall(text):
        add(m.upper())
    for m in _PROPER_RE.findall(text):
        add(m)
        if len(pairs) >= max_keys:
            break
    return list(pairs.items())


def record_keys(rec: dict, *, max_keys: int = 8) -> set[str]:
    """Set of normalized signal keys a record contributes (labels discarded)."""
    return {k for k, _ in record_key_pairs(rec, max_keys=max_keys)}


# ============================================================================
# Fusion
# ============================================================================

@dataclass
class Signal:
    key: str                       # normalized fusion key
    label: str                     # human-facing label (best-cased mention)
    score: float                   # convergence score (higher = stronger)
    n_sections: int
    sections: list[str]            # distinct sections, sorted
    n_records: int
    recency_days: int              # age of the most recent corroborating record
    confidence: float              # mapped persistence probability [CONF_FLOOR, CONF_CEIL]
    evidence: list[dict] = field(default_factory=list)   # [{id, section, title, url}]
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "score": round(self.score, 4),
            "n_sections": self.n_sections, "sections": self.sections,
            "n_records": self.n_records, "recency_days": self.recency_days,
            "confidence": round(self.confidence, 4), "evidence": self.evidence,
            "rationale": self.rationale,
        }


def _parse_day(rec: dict) -> Optional[date]:
    for fld in ("record_date", "date", "ingested_at_utc", "ingested_at"):
        v = rec.get(fld)
        if not v:
            continue
        s = str(v)[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            continue
    return None


def _conf_from(n_sections: int, recency_days: int) -> float:
    """Map corroboration breadth + freshness to a conservative persistence
    probability. More independent sections and fresher activity → higher, but
    capped well below certainty."""
    base = CONF_FLOOR + 0.05 * min(max(n_sections - DEFAULT_MIN_SECTIONS, 0), 4)
    if recency_days <= 1:
        base += 0.03
    return round(min(base, CONF_CEIL), 4)


def fuse(
    records: Iterable[dict],
    *,
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_sections: int = DEFAULT_MIN_SECTIONS,
    half_life: float = DEFAULT_HALF_LIFE,
    top_n: int = DEFAULT_TOP_N,
) -> list[Signal]:
    """Roll records up by key and rank by cross-source convergence.

    Each record must be a dict with at least ``section_id`` and a title (under
    ``title`` or ``original_text``); ``id``/``original_url``/``record_date`` are
    used when present. Only records within ``window_days`` of ``today`` count,
    and a key must appear in at least ``min_sections`` distinct sections to
    surface — that corroboration requirement is what turns noise into signal.
    """
    horizon = today - timedelta(days=window_days)
    agg: dict[str, dict] = {}

    for rec in records:
        if is_noise_record(rec):
            continue
        day = _parse_day(rec)
        if day is None or day < horizon or day > today:
            continue
        section = str(rec.get("section_id") or rec.get("section") or "?")
        title = _clean_text(rec.get("title") or rec.get("original_text") or "")
        rid = rec.get("id") or rec.get("_id") or ""
        url = rec.get("original_url") or rec.get("url") or ""
        age = max((today - day).days, 0)
        for key, cased in record_key_pairs(rec):
            a = agg.setdefault(key, {
                "sections": set(), "n_records": 0, "min_age": 999,
                "evidence": [], "label_votes": {},
            })
            a["sections"].add(section)
            a["n_records"] += 1
            a["min_age"] = min(a["min_age"], age)
            # vote on the cased phrase that produced this key, for a clean label
            a["label_votes"][cased] = a["label_votes"].get(cased, 0) + 1
            if len(a["evidence"]) < 6 and title:
                a["evidence"].append({
                    "id": rid, "section": section,
                    "title": title[:160], "url": url,
                })

    signals: list[Signal] = []
    for key, a in agg.items():
        n_sections = len(a["sections"])
        if n_sections < min_sections:
            continue
        recency = 0.5 ** (a["min_age"] / half_life)
        score = (n_sections ** 1.3) * recency * (1.0 + 0.2 * math.log10(a["n_records"] + 1))
        sections = sorted(a["sections"])
        # human label: the most-common headline's leading phrase, else the key
        label = key
        if a["label_votes"]:
            label = max(a["label_votes"].items(), key=lambda kv: kv[1])[0]
        conf = _conf_from(n_sections, a["min_age"])
        rationale = (
            f"{key!r} surfaced across {n_sections} independent sections "
            f"({', '.join(sections[:5])}{'…' if len(sections) > 5 else ''}) "
            f"in {a['n_records']} records; most recent {a['min_age']}d ago."
        )
        signals.append(Signal(
            key=key, label=label, score=score, n_sections=n_sections,
            sections=sections, n_records=a["n_records"], recency_days=a["min_age"],
            confidence=conf, evidence=a["evidence"], rationale=rationale,
        ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals[:top_n]


# ============================================================================
# Predictions (falsifiable + auto-gradable)
# ============================================================================

def _prediction_id(key: str, made: str) -> str:
    return hashlib.sha1(f"{METHOD}|{key}|{made}".encode()).hexdigest()


def signals_to_predictions(
    signals: list[Signal],
    *,
    today: date,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_preds: int = 12,
) -> list[dict]:
    """Turn the strongest signals into falsifiable predictions in the lake's
    ``add_prediction`` shape.

    Each prediction is deliberately self-resolving from the lake: it claims the
    signal's key will remain cross-section-salient (>= 2 distinct sections)
    within the horizon. ``grade_predictions`` can settle it later with no human
    input, so the track-record scorer always has graded calls to learn from."""
    made = today.isoformat()
    target = (today + timedelta(days=horizon_days)).isoformat()
    out: list[dict] = []
    for s in signals[:max_preds]:
        out.append({
            "id": _prediction_id(s.key, made),
            "target_date": target,
            "resolution_criteria": (
                f"Resolves YES if '{s.label}' (key '{s.key}') is referenced by "
                f">= {DEFAULT_MIN_SECTIONS} distinct WORLDSCOPE sections between "
                f"{made} and {target}; otherwise NO."
            ),
            "predicted_outcome": "YES",
            "confidence": s.confidence,
            "training_window_days": DEFAULT_WINDOW_DAYS,
            "indicators_used": s.sections,
            "method": METHOD,
            "evidence": [e["id"] for e in s.evidence if e.get("id")],
            "_key": s.key,   # internal: used by grading; not a lake column
        })
    return out


def grade_key_outcome(
    key: str,
    records: Iterable[dict],
    *,
    made: date,
    target: date,
    min_sections: int = DEFAULT_MIN_SECTIONS,
) -> str:
    """Settle one prediction: 'YES' if ``key`` appears in >= min_sections
    distinct sections among records dated in (made, target], else 'NO'."""
    sections: set[str] = set()
    for rec in records:
        day = _parse_day(rec)
        if day is None or day <= made or day > target:
            continue
        if key in record_keys(rec):
            sections.add(str(rec.get("section_id") or rec.get("section") or "?"))
            if len(sections) >= min_sections:
                return "YES"
    return "NO"


# ============================================================================
# Lake adapters
# ============================================================================

def load_records_from_jsonl(
    *, today: date, days: int, lake_dir: Path = LAKE_SECTIONS,
) -> list[dict]:
    """Read committed JSONL section files for the window (local/CI fallback)."""
    out: list[dict] = []
    if not lake_dir.exists():
        return out
    wanted = {(today - timedelta(days=d)).isoformat() for d in range(days + 1)}
    for sec_dir in lake_dir.iterdir():
        if not sec_dir.is_dir() or sec_dir.name.startswith("_"):
            continue
        for day in wanted:
            raw = sec_dir / day / "raw.jsonl"
            if not raw.exists():
                continue
            for line in raw.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and not is_noise_record(rec):
                    rec.setdefault("section_id", sec_dir.name)
                    rec.setdefault("record_date", day)
                    out.append(rec)
    return out


def load_records_from_db(conn, *, today: date, days: int) -> list[dict]:
    """Read records within the window from the populated lake SQLite DB
    (production path, after sections have run). Joins entity names in."""
    horizon = (today - timedelta(days=days)).isoformat()
    cur = conn.execute(
        "SELECT id, section_id, original_text, original_url, record_date "
        "FROM records WHERE COALESCE(record_date, ingested_at) >= ? ",
        (horizon,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    # attach entity canonical names per record (best effort)
    try:
        ent = conn.execute(
            "SELECT re.record_id, e.canonical_name FROM record_entities re "
            "JOIN entities e ON e.id = re.entity_id"
        )
        by_rec: dict[str, list[str]] = {}
        for r in ent.fetchall():
            by_rec.setdefault(r[0], []).append(r[1])
        for row in rows:
            row["entities"] = by_rec.get(row["id"], [])
            row["title"] = row.get("original_text")
    except Exception:
        for row in rows:
            row["title"] = row.get("original_text")
    return rows


# ============================================================================
# Brief panel
# ============================================================================

def render_signals_panel(
    signals: list[Signal], predictions: list[dict], *, max_show: int = 12,
) -> str:
    """Render a 'Signals' section in the daily brief's house style (matches the
    section.section markup in worldscope.render)."""
    import html as _html

    if not signals:
        return ""
    rows = []
    for s in signals[:max_show]:
        ev = s.evidence[0] if s.evidence else {}
        link = ""
        if ev.get("url"):
            link = (f" — <a href='{_html.escape(ev['url'], quote=True)}'>"
                    f"{_html.escape(ev.get('title', 'source')[:90])}</a>")
        rows.append(
            "<li>"
            f"<span class='new-badge'>{s.n_sections}×</span>"
            f"<strong>{_html.escape(s.label[:90])}</strong>"
            f"<span class='meta'> · {_html.escape(', '.join(s.sections[:6]))}"
            f" · {int(round(s.confidence * 100))}% persist</span>"
            f"<div class='abs'>{_html.escape(s.rationale)}{link}</div>"
            "</li>"
        )
    n_pred = len(predictions)
    synth = (
        f"<p class='synth'>{len(signals)} cross-source signals today; "
        f"the {min(n_pred, len(signals))} strongest were logged as falsifiable "
        f"predictions (auto-graded against the lake over the next "
        f"{DEFAULT_HORIZON_DAYS} days to build a calibrated track record).</p>"
    )
    return (
        "<section class='section'>"
        "<h2>🛰️ Signals — cross-source convergence "
        f"<span class='count'>· {len(signals)} keys / {n_pred} predictions</span></h2>"
        f"{synth}"
        f"<ul class='items'>{''.join(rows)}</ul>"
        "</section>"
    )


# ============================================================================
# Orchestration helpers (used by the brief stage and the CLI)
# ============================================================================

def build_signals(
    *, today: date, days: int = DEFAULT_WINDOW_DAYS, conn=None,
) -> list[Signal]:
    """Load records (DB if a connection is given and populated, else JSONL) and
    fuse them into ranked signals."""
    records: list[dict] = []
    if conn is not None:
        try:
            records = load_records_from_db(conn, today=today, days=days)
        except Exception:
            records = []
    if not records:
        records = load_records_from_jsonl(today=today, days=days)
    return fuse(records, today=today, window_days=days)


def persist_predictions(lake, predictions: list[dict]) -> int:
    """Write predictions to the lake (skips the internal `_key` field)."""
    n = 0
    for p in predictions:
        lake.add_prediction(
            prediction_id=p["id"],
            target_date=p.get("target_date"),
            resolution_criteria=p.get("resolution_criteria", ""),
            predicted_outcome=p.get("predicted_outcome", "YES"),
            confidence=float(p.get("confidence", CONF_FLOOR)),
            training_window_days=p.get("training_window_days"),
            indicators_used=p.get("indicators_used", []),
            method=p.get("method", METHOD),
            evidence=p.get("evidence", []),
            section_id="signals",
        )
        n += 1
    return n


def grade_due_predictions(lake, conn, *, today: date) -> int:
    """Find our unresolved predictions whose target_date has passed and settle
    each from the lake's own record history. Returns the number graded."""
    cur = conn.execute(
        "SELECT id, made_at, target_date, resolution_criteria FROM predictions "
        "WHERE method = ? AND (actual_outcome IS NULL OR actual_outcome = '') "
        "AND target_date IS NOT NULL AND target_date <= ?",
        (METHOD, today.isoformat()),
    )
    due = [dict(r) for r in cur.fetchall()]
    if not due:
        return 0
    # widest window we might need
    spans = [r for r in due]
    earliest = min(date.fromisoformat(r["made_at"][:10]) for r in spans)
    records = load_records_from_db(conn, today=today, days=(today - earliest).days + 1)
    if not records:
        records = load_records_from_jsonl(today=today, days=(today - earliest).days + 1)
    graded = 0
    for r in due:
        key = _recover_key(r["resolution_criteria"])
        if not key:
            continue
        made = date.fromisoformat(r["made_at"][:10])
        target = date.fromisoformat(r["target_date"][:10])
        outcome = grade_key_outcome(key, records, made=made, target=target)
        lake.resolve_prediction(
            prediction_id=r["id"], resolved_at=today.isoformat(),
            actual_outcome=outcome,
        )
        graded += 1
    return graded


_KEY_RE = re.compile(r"key '([^']+)'")


def _recover_key(resolution_criteria: str) -> Optional[str]:
    m = _KEY_RE.search(resolution_criteria or "")
    return m.group(1) if m else None


# ============================================================================
# CLI
# ============================================================================

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-source signal fusion.")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="as-of date (YYYY-MM-DD); default today")
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--write", action="store_true",
                    help="persist predictions to the lake and grade due ones")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.date)
    signals = build_signals(today=today, days=args.days)
    preds = signals_to_predictions(signals, today=today)

    print(f"[signals] {len(signals)} cross-source signals as of {today} "
          f"(window {args.days}d):\n")
    for i, s in enumerate(signals[:args.top], 1):
        print(f"{i:2}. [{s.n_sections}× | {int(s.confidence*100)}% | score {s.score:.2f}] "
              f"{s.label[:70]}")
        print(f"      sections: {', '.join(s.sections[:8])}")
    print(f"\n[signals] {len(preds)} predictions derived "
          f"(horizon {DEFAULT_HORIZON_DAYS}d).")

    if args.write:
        from .lake import Lake
        lake = Lake.open()
        conn = lake._ensure_open()
        n = persist_predictions(lake, preds)
        graded = grade_due_predictions(lake, conn, today=today)
        print(f"[signals] wrote {n} predictions; graded {graded} due predictions.")
        lake.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
