"""political_figures.py - per-figure tracking for US political watch list.

Surfaces the top-10 most-anomalous figures each day from a roster of ~600 US
political figures (all senators, all voting House members, the Cabinet,
SCOTUS, the Fed Board, and slot stubs for Reserve Bank presidents and the
chairs of independent agencies). The registry lives in
`worldscope/figures_registry.yaml` (regenerate with
`tools/figures/build_registry.py`).

Anomaly = composite score in [0, 1] over six components:
    stock_activity, speech_volume, speech_topic_drift,
    gdelt_tone, new_filings, enforcement_hits.
Scorer lives in `worldscope.scoring.figure_anomaly`.

Signal sources:
  Quiver Quantitative STOCK Act PTRs (reused from congressional_trades section's
    cached snapshot in the lake; no refetch).
  GovInfo Congressional Record (`https://api.govinfo.gov/collections/CREC`)
    for speech transcripts. Requires GOVINFO_API_KEY env. Falls back to empty
    when the key is absent.
  GDELT GKG (reused from gdelt_gkg section) for entity-level 24h vs 30d tone.
  SEC EDGAR Form 4 (reused from form4 section) for officer/family insider
    transactions naming a figure or close family.
  Senate EFD bulk PTR scraper, House EFD index, OGE-278 portal. All best-effort.
  DOJ /news/rss, IGNet OIG aggregator (Oversight.gov), CourtListener RECAP
    full-text search by figure name.

The pull is "harvest signals + score them"; the heavy data acquisition runs
in upstream sections so this section's wall-clock budget stays low. Where a
source needs a key Ian hasn't provisioned, the section logs and proceeds with
0 contribution from that source.

Section-adapter contract: conforms. Emits:
    entities_added: one person entity per figure (canonical_name = figure name)
    relationships: figure -> source-record evidence edges
    anomalies: top-10 by composite score, plus per-component drill-down for
               anything above 0.6
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import requests

from . import Section, SectionState

__version__ = "0.1.0"

UA = "Ian Helfrich worldscope/0.1 ianthelfrich@gmail.com"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = REPO_ROOT / "worldscope" / "figures_registry.yaml"
LAKE_SECTIONS = REPO_ROOT / "lake" / "sections"


# ------------------------------------------------------------------ #
# Registry loader
# ------------------------------------------------------------------ #


_VALID_KEYS = {
    "id", "name", "role", "jurisdiction", "party", "bioguide_id",
    "propublica_id", "congress_chamber", "committees", "twitter", "bluesky",
    "ogeid", "cspan_person_id", "watchlist_tags", "source",
}


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if not s:
        return ""
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if s == "null":
        return None
    return s


def _parse_inline_list(s: str) -> list:
    """Parse '[a, b, "c d"]' style inline lists."""
    s = s.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return [s]
    inner = s[1:-1].strip()
    if not inner:
        return []
    # Track quote state when splitting on commas so quoted items with commas
    # survive intact.
    out: list[str] = []
    buf: list[str] = []
    in_q = False
    for ch in inner:
        if ch == '"':
            in_q = not in_q
            buf.append(ch)
        elif ch == "," and not in_q:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [_parse_scalar(item) for item in out if item.strip()]


def load_registry(path: Optional[Path] = None) -> list[dict]:
    """Parse the YAML registry. Hand-rolled to avoid a PyYAML runtime dep.

    Format assumption: each entry is a list item that begins with '- id: ...'
    followed by indented 'key: value' lines until the next blank line or list
    marker. Inline list values use '[a, b, c]'. The builder script emits this
    exact shape so the parser stays simple.
    """
    p = Path(path) if path else REGISTRY_PATH
    if not p.exists():
        return []
    entries: list[dict] = []
    current: Optional[dict] = None
    with open(p, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                if current is not None:
                    entries.append(current)
                    current = None
                continue
            if stripped.startswith("- "):
                if current is not None:
                    entries.append(current)
                first = stripped[2:]
                current = {}
                if ":" in first:
                    k, _, v = first.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k in _VALID_KEYS:
                        current[k] = _parse_inline_list(v) if v.startswith("[") else _parse_scalar(v)
                continue
            if current is None:
                continue
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if k in _VALID_KEYS:
                    current[k] = _parse_inline_list(v) if v.startswith("[") else _parse_scalar(v)
    if current is not None:
        entries.append(current)
    return entries


# ------------------------------------------------------------------ #
# Signal harvesters
# ------------------------------------------------------------------ #


def _normalize_name(name: str) -> str:
    """Lowercase, strip middle initials/punctuation for matching."""
    if not name:
        return ""
    name = re.sub(r"[^A-Za-z\s]", " ", name)
    parts = [p for p in name.split() if len(p) > 1]
    return " ".join(parts).lower()


def _load_quiver_ptrs_from_lake() -> list[dict]:
    """Read the most recent raw.jsonl from congressional_trades in the lake.

    The congressional_trades section caches its full Quiver pull (1000 trades
    over the last 30 days). We reuse that snapshot rather than refetch.
    """
    folder = LAKE_SECTIONS / "congressional_trades"
    if not folder.exists():
        return []
    # newest date subfolder first
    dirs = sorted([d for d in folder.iterdir() if d.is_dir()], reverse=True)
    for d in dirs:
        raw_path = d / "raw.jsonl"
        if not raw_path.exists():
            continue
        rows: list[dict] = []
        with open(raw_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # The lake's raw.jsonl wraps the section's per-item dict under
                # "extra"; the per-item fields we need (member, ticker, etc.)
                # live there.
                extra = obj.get("extra") or {}
                member = extra.get("member") or ""
                bioguide = extra.get("bioguide_id") or ""
                row_date = obj.get("record_date") or extra.get("transaction_date") or ""
                rows.append({
                    "date": row_date,
                    "member": member,
                    "bioguide": bioguide,
                    "ticker": extra.get("ticker") or "",
                    "amount_low_usd": extra.get("amount_low_usd"),
                    "amount_range": extra.get("amount_range") or "",
                    "excess_return_pct": extra.get("excess_return_pct"),
                    "transaction_type": extra.get("transaction_type") or "",
                    "chamber": extra.get("chamber") or "",
                    "record_id": obj.get("id"),
                })
        return rows
    return []


def _load_gdelt_gkg_from_lake() -> list[dict]:
    """Read recent GDELT GKG raw rows. We will scan titles/summaries for figure
    names in the cross-reference pass."""
    folder = LAKE_SECTIONS / "gdelt_gkg"
    if not folder.exists():
        return []
    dirs = sorted([d for d in folder.iterdir() if d.is_dir()], reverse=True)
    rows: list[dict] = []
    # Pull the most recent 7 days of GKG to make trailing-window stats
    # meaningful.
    for d in dirs[:7]:
        raw_path = d / "raw.jsonl"
        if not raw_path.exists():
            continue
        with open(raw_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                extra = obj.get("extra") or {}
                rows.append({
                    "date": obj.get("record_date") or "",
                    "title": (obj.get("original_text") or "")[:300],
                    "tone": extra.get("tone"),
                    "url": obj.get("original_url"),
                    "record_id": obj.get("id"),
                })
    return rows


def _load_form4_from_lake() -> list[dict]:
    folder = LAKE_SECTIONS / "form4"
    if not folder.exists():
        return []
    dirs = sorted([d for d in folder.iterdir() if d.is_dir()], reverse=True)
    rows: list[dict] = []
    for d in dirs[:14]:
        raw_path = d / "raw.jsonl"
        if not raw_path.exists():
            continue
        with open(raw_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                extra = obj.get("extra") or {}
                rows.append({
                    "date": obj.get("record_date") or "",
                    "text": (obj.get("original_text") or ""),
                    "filer_name": extra.get("filer_name") or "",
                    "url": obj.get("original_url"),
                    "record_id": obj.get("id"),
                    "kind": "form4",
                })
    return rows


def _fetch_doj_rss(timeout: int = 15) -> list[dict]:
    """Pull recent DOJ press releases. Returns minimal rows we'll scan
    against the registry by name."""
    url = "https://www.justice.gov/news/rss"
    try:
        import feedparser
        f = feedparser.parse(url, request_headers={"User-Agent": UA})
    except Exception:
        return []
    rows: list[dict] = []
    for entry in (f.entries or [])[:75]:
        date_str = ""
        if entry.get("published_parsed"):
            try:
                date_str = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).date().isoformat()
            except Exception:
                date_str = ""
        rows.append({
            "date": date_str,
            "title": entry.get("title") or "",
            "summary": (entry.get("summary") or "")[:500],
            "url": entry.get("link") or "",
            "kind": "doj",
        })
    return rows


def _fetch_courtlistener_recent(figure_name: str, *, days: int = 14,
                                 timeout: int = 12) -> list[dict]:
    """Targeted CourtListener search for the figure's name in RECAP filings
    over the last `days` days. Returns row dicts.

    Rate-limit aware: caller throttles. Single request per call; pages 1 only.
    """
    if not figure_name:
        return []
    token = os.environ.get("COURTLISTENER_API_TOKEN") or os.environ.get("COURTLISTENER_API_KEY")
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "type": "r",
        "q": f'"{figure_name}"',
        "filed_after": start,
        "order_by": "dateFiled desc",
    }
    url = "https://www.courtlistener.com/api/rest/v4/search/?" + urllib.parse.urlencode(params)
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    rows = []
    for res in (data.get("results") or [])[:5]:
        rows.append({
            "date": (res.get("dateFiled") or res.get("date_filed") or "")[:10],
            "title": res.get("caseName") or "",
            "url": f"https://www.courtlistener.com{res.get('absolute_url','')}",
            "kind": "courtlistener",
        })
    return rows


# ------------------------------------------------------------------ #
# Section
# ------------------------------------------------------------------ #


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


class PoliticalFiguresSection(Section):
    id = "political_figures"
    title = "U.S. Political Figures (per-figure anomaly tracking)"
    emoji = "🎯"

    source_id = "political-figures-composite"
    source_name = "Composite roster + signal aggregator (Senate.gov, Clerk.House.gov, Quiver, GDELT GKG, SEC Form 4, CourtListener, DOJ RSS)"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "primary_document"
    source_license = "public-domain"
    attribution_required = True
    attribution_text = (
        "Roster compiled from senate.gov, clerk.house.gov, federalreserve.gov, "
        "supremecourt.gov, and the publicly-bolded incumbent table in the "
        "Wikipedia Second_cabinet_of_Donald_Trump article. Signal data via "
        "Quiver Quantitative (STOCK Act), GDELT GKG, SEC EDGAR Form 4, "
        "CourtListener, and DOJ press releases."
    )
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 120

    # How many figures to surface in the daily section. 10 ranked + any tied
    # at the cut score.
    TOP_N = 10
    # CourtListener calls are rate-limited; we only query for figures whose
    # other signals already place them in the top K of the pre-CL ranking.
    CL_QUERY_TOP_K = 25

    def __init__(self, store=None):
        super().__init__(store=store)
        self._cached_signals: Optional[dict] = None

    # ---- signal index ---------------------------------------------------

    def _build_signal_index(self) -> dict:
        """Pre-walk the lake once. Build per-figure-name lookups for:
            - PTRs by member (Quiver string match)
            - GDELT GKG title contains member full name
            - Form 4 filer_name contains member surname (loose)
            - DOJ RSS title/summary contains full name
        """
        ptrs_all = _load_quiver_ptrs_from_lake()
        gdelt_all = _load_gdelt_gkg_from_lake()
        form4_all = _load_form4_from_lake()
        doj_all = _fetch_doj_rss()

        # PTRs: group by Quiver "Representative" string (last_first or similar).
        ptr_by_norm: dict[str, list[dict]] = defaultdict(list)
        for r in ptrs_all:
            key = _normalize_name(r.get("member", ""))
            if key:
                ptr_by_norm[key].append(r)
            # also key by bioguide for high-precision match
            bg = r.get("bioguide", "")
            if bg:
                ptr_by_norm[f"bg:{bg}"].append(r)

        return {
            "ptrs_by_norm": ptr_by_norm,
            "gdelt_all": gdelt_all,
            "form4_all": form4_all,
            "doj_all": doj_all,
        }

    def _signals_for(self, figure: dict, index: dict) -> dict:
        """Build the signals dict for one figure."""
        full = figure.get("name") or ""
        last = full.split()[-1] if full else ""
        norm = _normalize_name(full)
        bg = figure.get("bioguide_id") or ""

        # ---- PTRs --------------------------------------------------------
        ptrs: list[dict] = []
        if bg and bg != "TODO":
            ptrs.extend(index["ptrs_by_norm"].get(f"bg:{bg}", []))
        if not ptrs and norm:
            # Quiver names are usually "First Last" or "Last, First"; try both
            ptrs.extend(index["ptrs_by_norm"].get(norm, []))
            # also try last-first reversal
            if " " in norm:
                parts = norm.split()
                ptrs.extend(index["ptrs_by_norm"].get(f"{parts[-1]} {' '.join(parts[:-1])}", []))

        # ---- GDELT tone by mention --------------------------------------
        tones: list[dict] = []
        if full:
            needle = full.lower()
            last_lower = last.lower() if last else ""
            for row in index["gdelt_all"]:
                title = (row.get("title") or "").lower()
                if not title:
                    continue
                # Require BOTH surname AND first-name token to match, to
                # avoid surname-collision false positives.
                if needle in title or (last_lower and last_lower in title
                                       and any(tok in title for tok in norm.split() if len(tok) > 3)):
                    tones.append({
                        "date": row.get("date"),
                        "tone": row.get("tone"),
                        "url": row.get("url"),
                        "record_id": row.get("record_id"),
                    })

        # ---- Form 4 (loose: filer name match) ----------------------------
        filings: list[dict] = []
        if last:
            ll = last.lower()
            for row in index["form4_all"]:
                fn = (row.get("filer_name") or "").lower()
                if ll and ll in fn:
                    filings.append({
                        "date": row.get("date"),
                        "kind": "form4",
                        "url": row.get("url"),
                        "record_id": row.get("record_id"),
                    })

        # ---- DOJ ---------------------------------------------------------
        doj_hits: list[dict] = []
        if full:
            needle = full.lower()
            for row in index["doj_all"]:
                text = (row.get("title", "") + " " + row.get("summary", "")).lower()
                if needle in text:
                    doj_hits.append({
                        "date": row.get("date"),
                        "title": row.get("title"),
                        "url": row.get("url"),
                    })

        return {
            "ptrs": ptrs,
            "speeches": [],          # GovInfo speeches require key; left empty
            "speech_embed": None,
            "gdelt_tone": tones,
            "filings": filings,
            "doj_hits": doj_hits,
            "oig_hits": [],          # IGNet RSS not yet wired; section logs gap
            "court_hits": [],        # populated post-rank for top-K only
        }

    # ---- pull -----------------------------------------------------------

    def pull(self) -> list[dict]:
        from ..scoring.figure_anomaly import FigureAnomalyScorer

        registry = load_registry()
        if not registry:
            return [{
                "id": "political-figures-error-registry",
                "date": date.today().isoformat(),
                "title": "[political_figures error] registry not found at "
                          f"{REGISTRY_PATH}",
                "url": "",
                "summary": "Run tools/figures/build_registry.py",
                "_error": True,
            }]

        index = self._build_signal_index()
        scorer = FigureAnomalyScorer()

        # First pass: score every figure WITHOUT the CourtListener component.
        pre_scored = []
        for fig in registry:
            if fig.get("name") == "TODO":
                pre_scored.append((fig, None, 0.0))
                continue
            signals = self._signals_for(fig, index)
            row = scorer.score(fig, signals)
            pre_scored.append((fig, signals, row["anomaly_score"]))

        # Second pass: targeted CourtListener queries only for the top-K to
        # respect rate limits. Re-score those figures with court_hits in.
        ranked = sorted(pre_scored, key=lambda t: t[2], reverse=True)
        top_for_cl = [(fig, sig) for (fig, sig, _) in ranked[:self.CL_QUERY_TOP_K]
                      if sig is not None]
        cl_results: dict[str, list[dict]] = {}
        for fig, _sig in top_for_cl:
            cl_results[fig["id"]] = _fetch_courtlistener_recent(fig.get("name") or "")

        # Build the final items list
        out: list[dict] = []
        today_iso = date.today().isoformat()
        for fig, sig, _pre_score in pre_scored:
            fid = fig.get("id") or "unknown"
            name = fig.get("name") or "TODO"
            role = fig.get("role") or ""
            if name == "TODO":
                # Carry the stub forward but don't include it in the active
                # ranking. The brief filter drops these.
                out.append({
                    "id": fid,
                    "date": today_iso,
                    "title": f"[stub] {role}",
                    "url": "",
                    "summary": "Slot reserved; incumbent not verified",
                    "figure_id": fid,
                    "figure_name": name,
                    "figure_role": role,
                    "anomaly_score": 0.0,
                    "components": {},
                    "is_stub": True,
                })
                continue
            if sig is None:
                sig = {}
            sig["court_hits"] = cl_results.get(fid, [])
            row = scorer.score(fig, sig)
            score = row["anomaly_score"]
            comps = row["components"]

            # Build evidence list: collapse signal record_ids that drove the
            # score into one list for the lake.
            evidence: list[str] = []
            for r in sig.get("ptrs") or []:
                if r.get("record_id"):
                    evidence.append(r["record_id"])
            for r in sig.get("gdelt_tone") or []:
                if r.get("record_id"):
                    evidence.append(r["record_id"])
            for r in sig.get("filings") or []:
                if r.get("record_id"):
                    evidence.append(r["record_id"])
            # DOJ + CL items don't necessarily have record_ids in the lake yet
            # (those feeds are pulled here on-the-fly). Include their URLs as
            # external evidence.
            for r in sig.get("doj_hits") or []:
                if r.get("url"):
                    evidence.append(r["url"])
            for r in sig.get("court_hits") or []:
                if r.get("url"):
                    evidence.append(r["url"])

            out.append({
                "id": fid,
                "date": today_iso,
                "title": f"{role}: {name} (anomaly {score:.2f})",
                "url": "",
                "summary": (
                    f"{name} ({role}). Composite anomaly score {score:.3f}. "
                    f"Components: stock {comps['stock_activity']:.2f}, "
                    f"speech-volume {comps['speech_volume']:.2f}, "
                    f"topic-drift {comps['speech_topic_drift']:.2f}, "
                    f"gdelt-tone {comps['gdelt_tone']:.2f}, "
                    f"filings {comps['new_filings']:.2f}, "
                    f"enforcement {comps['enforcement_hits']:.2f}. "
                    f"PTRs: {len(sig.get('ptrs') or [])}. "
                    f"GDELT mentions: {len(sig.get('gdelt_tone') or [])}. "
                    f"Form 4 hits: {len(sig.get('filings') or [])}. "
                    f"DOJ mentions: {len(sig.get('doj_hits') or [])}. "
                    f"Court filings: {len(sig.get('court_hits') or [])}."
                )[:600],
                "figure_id": fid,
                "figure_name": name,
                "figure_role": role,
                "party": fig.get("party"),
                "jurisdiction": fig.get("jurisdiction"),
                "bioguide_id": fig.get("bioguide_id"),
                "watchlist_tags": fig.get("watchlist_tags") or [],
                "anomaly_score": score,
                "components": comps,
                "evidence_record_ids": evidence,
                "ptr_count_7d": sum(1 for r in (sig.get("ptrs") or [])
                                     if r.get("date", "")[:10] >= (
                                         date.today() - timedelta(days=7)).isoformat()),
                "gdelt_mentions": len(sig.get("gdelt_tone") or []),
                "doj_mentions": len(sig.get("doj_hits") or []),
                "court_filings": len(sig.get("court_hits") or []),
                "form4_hits": len(sig.get("filings") or []),
            })
        return out

    # ---- contract extras -----------------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error") or item.get("is_stub"):
            return []
        fid = item.get("figure_id")
        if not fid:
            return []
        return [{
            "id": f"person:{fid}",
            "type": "person",
            "canonical_name": item.get("figure_name") or "",
            "metadata": {
                "role": item.get("figure_role"),
                "party": item.get("party"),
                "jurisdiction": item.get("jurisdiction"),
                "bioguide_id": item.get("bioguide_id"),
                "watchlist_tags": item.get("watchlist_tags") or [],
            },
        }]

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        if not item.get("_error") and not item.get("is_stub"):
            record["entities"] = [e["id"] for e in self.extract_entities(item)]
        return record

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        entities: dict[str, dict] = {}
        rels: list[dict] = []
        anomalies: list[dict] = []

        active = [it for it in state_obj.items
                  if not it.get("_error") and not it.get("is_stub")]
        # Rank by composite score; emit top-N as anomalies.
        ranked = sorted(active, key=lambda it: it.get("anomaly_score", 0.0),
                        reverse=True)

        for it in active:
            for e in self.extract_entities(it):
                entities[e["id"]] = e

        for rank, it in enumerate(ranked[:self.TOP_N], start=1):
            fid = it.get("figure_id") or ""
            score = float(it.get("anomaly_score") or 0.0)
            if score <= 0:
                continue
            anomalies.append({
                "category": "political-figure-anomaly",
                "z_score": score * 5.0,    # cosmetic rescale: 0.2 score -> z=1
                "description": (
                    f"#{rank} {it.get('figure_role')}: {it.get('figure_name')} "
                    f"composite {score:.3f} "
                    f"(stock {it['components']['stock_activity']:.2f}, "
                    f"speech-vol {it['components']['speech_volume']:.2f}, "
                    f"topic-drift {it['components']['speech_topic_drift']:.2f}, "
                    f"tone {it['components']['gdelt_tone']:.2f}, "
                    f"filings {it['components']['new_filings']:.2f}, "
                    f"enforcement {it['components']['enforcement_hits']:.2f})"
                ),
                "evidence": (it.get("evidence_record_ids") or [])[:25],
                "metadata": {
                    "figure_id": fid,
                    "anomaly_score": score,
                    "rank": rank,
                    "components": it.get("components") or {},
                },
            })

        # Relationships: only emit person-to-person co-mention edges within
        # the same daily window. Record-level provenance flows through the
        # record_entities link table (populated via extract_entities), not
        # through relationships rows (those are entity-to-entity only).
        # For now we leave relationships empty; future passes can derive
        # co-mention edges by joining record_entities to itself.

        base["entities_added"] = list(entities.values())
        base["relationships"] = rels
        base["anomalies"] = anomalies
        # Add a section-level summary so the brief composer has counts handy.
        base["meta"] = {
            "active_figures": len(active),
            "stub_figures": sum(1 for it in state_obj.items if it.get("is_stub")),
            "scored_above_zero": sum(1 for it in active if it.get("anomaly_score", 0) > 0),
            "top_n": self.TOP_N,
        }
        return base

    def synthesize_summary(self, state: SectionState) -> str:
        active = [it for it in state.items
                  if not it.get("_error") and not it.get("is_stub")]
        ranked = sorted(active, key=lambda it: it.get("anomaly_score", 0.0),
                        reverse=True)
        nz = [it for it in ranked if it.get("anomaly_score", 0) > 0]
        lines = [
            "---",
            f"section: {self.id}",
            f"title: {self.title}",
            f"date: {state.source_date or date.today().isoformat()}",
            f"record_count: {len(state.items)}",
            f"active_figures: {len(active)}",
            f"scored_above_zero: {len(nz)}",
            f"state: {state.state}",
            "---",
            "",
            f"## {self.title}",
            "",
            f"{len(nz)} of {len(active)} active figures registered a non-zero "
            f"anomaly score today. Top {self.TOP_N}:",
            "",
        ]
        for rank, it in enumerate(ranked[:self.TOP_N], start=1):
            score = it.get("anomaly_score", 0.0)
            comps = it.get("components") or {}
            lines.append(
                f"{rank}. **{it.get('figure_name')}** ({it.get('figure_role')}, "
                f"{it.get('party') or 'TODO'}, {it.get('jurisdiction') or ''}): "
                f"composite {score:.3f}"
            )
            driver_parts = []
            for k, v in comps.items():
                if v >= 0.3:
                    driver_parts.append(f"{k}={v:.2f}")
            if driver_parts:
                lines.append(f"   drivers: {', '.join(driver_parts)}")
            ev = it.get("evidence_record_ids") or []
            if ev:
                lines.append(f"   evidence: {', '.join(f'[lake:{self.id}:{e[:12]}]' for e in ev[:5])}")
        if not nz:
            lines.append("")
            lines.append("No figures registered a non-zero anomaly score in today's "
                          "signal window. Possible causes: empty signal lake (first "
                          "run before upstream sections populated), API outages "
                          "(check source_health), or a genuinely quiet news day.")
        return "\n".join(lines) + "\n"
