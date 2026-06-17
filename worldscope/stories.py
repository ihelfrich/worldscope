"""worldscope.stories — event-level story clustering → the daily Top Stories front page.

The lake holds thousands of records/day across ~40 sections. `signals.py` fuses
them at the *entity* level ("Iran is salient across 6 sections today") and
`claims.py` at the *assertion* level ("X said Y, corroborated by N sources").
Neither produces the unit a reader of a world-class news aggregator actually
wants on the front page: the **story** — one real-world event, the set of every
record across every source and language that is covering it, ranked by how
*broadly and independently* it is being reported.

That breadth-of-independent-coverage ranking is exactly what Google News and
Ground News lead with, and it is the missing centerpiece here. This module
builds it.

What it does
------------
1. Loads the day's records (DB if a connection is given, else committed JSONL).
2. Clusters records that describe the same event using a deterministic,
   explainable signal: shared salient **entities** plus headline **token**
   overlap. When the optional embedding index is populated it folds in cosine
   similarity too (this recovers paraphrase / cross-language matches the lexical
   path misses), but it never *requires* the ML stack — the lake ships with no
   embeddings, so the deterministic path must stand on its own.
3. Ranks each cluster by independent-source breadth (distinct outlets first,
   then sections, then languages, decayed by recency) and keeps the ones that
   clear a cross-source corroboration bar — a single-source item is news, but it
   is not a *top story*.
4. Renders a "Top Stories" panel in the brief's house style and persists a
   `top_stories.json` artifact under `lake/sections/_meta/<date>/`, the same
   convention `radar.py` and `analysis/cross_section.py` use.

Design constraints, matched to the rest of the codebase
-------------------------------------------------------
  * Pure, offline core. `cluster_records` takes a plain list of dicts (and an
    optional ``embeddings_by_id`` map) and returns dataclasses, so the whole
    clustering + ranking logic is unit-testable with no DB and no model.
  * Thin adapters read the populated lake DB (production) or the committed JSONL
    (local/CI fallback), reusing `signals`' loaders and text hygiene.
  * Nothing here can abort a brief: the brief-stage wrapper swallows failures.

Run standalone against the local lake:
    python -m worldscope.stories --days 2           # print today's top stories
    python -m worldscope.stories --days 2 --write   # also write the _meta artifact
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

from . import signals as sg
from .lake import LAKE_SECTIONS

REPO = Path(__file__).resolve().parent.parent
LAKE_META = LAKE_SECTIONS / "_meta"

METHOD = "story-cluster-v1"

# ---- tuning knobs (all overridable through the public functions) ------------
DEFAULT_WINDOW_DAYS = 2       # how many days of records a story may draw on
DEFAULT_HALF_LIFE = 1.5       # recency decay half-life, in days
DEFAULT_TOP_N = 30            # how many stories to keep / persist
DEFAULT_MAX_TITLE_TOKENS = 16 # salient tokens kept per headline for matching

# A cluster must clear one of these to count as a *top story* (cross-source is
# the whole point — a lone item from a single feed is news, not a top story).
MIN_OUTLETS = 2               # distinct sources OR ...
MIN_SECTIONS = 2              # ... distinct sections

# Edge thresholds for the deterministic lexical matcher. Precision-oriented:
# two records join only on a shared entity backed by token overlap, on two
# shared entities, or on strong token overlap alone.
TOKEN_JACCARD_STRONG = 0.5    # token overlap alone is enough at/above this
# Embedding cosine at/above this joins two records (matches dedup.py's 0.78).
EMB_THRESHOLD = 0.78

# A shared entity/token defines a candidate "block". Up to this many members we
# compare every pair in the block (full, order-independent). Above it we fall
# back to a linear "star" against a representative member, which still unions the
# whole block transitively but avoids the O(m^2) blow-up a generic token in
# thousands of a busy day's records would otherwise cause.
_FULL_PAIRWISE_CAP = 500

# An entity/token that appears in more than this share of the window's records
# is not discriminative enough to *join* two records on (it would chain
# unrelated stories — the classic single-link failure mode). It is dropped from
# the matching signature. Absolute floors keep small inputs (and tests) intact.
_ENTITY_DF_FRACTION = 0.05
_ENTITY_DF_FLOOR = 25
_TOKEN_DF_FRACTION = 0.08
_TOKEN_DF_FLOOR = 40

# Weaker join thresholds used once non-discriminative keys are removed.
TOKEN_JACCARD_WITH_ENTITY = 0.20   # 1 shared entity needs this much token overlap

_WORD_RE = re.compile(r"[0-9a-zÀ-ɏ][0-9a-zÀ-ɏ.&'-]*")
# Leading source/region/theme tags of any length, e.g. "[China] " or
# "[US tariff & trade dockets · themes] ", and gdelt's "· themes" suffix. These
# are origin labels, not story content, and must not drive matching or display.
_LEAD_TAG_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
_THEME_SUFFIX_RE = re.compile(r"\s*[·|]\s*themes?\s*$", re.IGNORECASE)
# Section mastheads / dated newsletter stamps are not story units — they carry a
# source's name + a date, not an event, and bridge unrelated records. Drop them.
_MASTHEAD_RE = re.compile(
    r"(news:\s|\bspotlight news\b|\bnewsletter\b|\bdaily briefing\b|\bmorning briefing\b"
    r"|:\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})",
    re.IGNORECASE,
)
# Pseudo-entities that are structural tags, not real-world actors/places.
_ENTITY_NOISE_SUBSTR = ("theme", "docket", "· ", "tariff & trade")
# Domain-style feed handles masquerading as entities, e.g. 'lenta.ru'.
_DOMAIN_RE = re.compile(r"\b[\w-]+\.(ru|com|org|net|news|tv|co|uk|de|fr|io|gov|info|cn)\b")


# ============================================================================
# Feature extraction (pure)
# ============================================================================

def _clean_headline(text: str) -> str:
    """signals._clean_text, plus stripping leading source/region/theme tags of
    any length and gdelt's trailing '· themes' label — neither is story content."""
    t = sg._clean_text(text or "")
    t = _LEAD_TAG_RE.sub("", t)
    t = _THEME_SUFFIX_RE.sub("", t)
    return t.strip()


def _is_source_name_entity(k: str) -> bool:
    """Heuristic for entity keys that are really news-outlet/feed labels, not
    real-world actors — e.g. 'the guardian (uk', 'caixin 财新 (via google news'.
    These (mis-)extracted source names bridge unrelated stories and pollute
    labels, so they are excluded from matching and display."""
    if "(" in k or ")" in k:        # truncated parenthetical outlet label
        return True
    if _DOMAIN_RE.search(k):        # domain-style feed handle, e.g. 'lenta.ru'
        return True
    return any(p in k for p in ("via google news", "google news", " via ",
                                "(via", "newswire", "press release"))


def _entity_keys(rec: dict, *, stop_entities: Optional[set[str]] = None) -> set[str]:
    """Normalized salient entity keys for a record, reusing signals' entity
    decoding + stopword filter, minus structural pseudo-entities (theme/docket
    tags) and news-outlet/feed names. These are the high-precision join signal."""
    out: set[str] = set()
    for name in sg._entity_names(rec):
        k = sg._norm_key(name)
        words = k.split()
        if not words:
            continue
        if len(words) == 1 and (len(k) < 3 or k in sg._STOPWORDS):
            continue
        if all(w in sg._STOPWORDS for w in words):
            continue
        if any(sub in k for sub in _ENTITY_NOISE_SUBSTR):
            continue
        if _is_source_name_entity(k):
            continue
        if stop_entities and k in stop_entities:
            continue
        out.add(k)
    return out


def _title_tokens(rec: dict, *, max_tokens: int = DEFAULT_MAX_TITLE_TOKENS) -> set[str]:
    """Salient content tokens from the headline: lowercased words of length >= 3
    that are not stopwords. Order-independent set used for Jaccard overlap."""
    text = _clean_headline(rec.get("title") or rec.get("original_text") or "")
    toks: list[str] = []
    seen: set[str] = set()
    for m in _WORD_RE.findall(text.lower()):
        t = m.strip(".&'-")
        if len(t) < 3 or t in sg._STOPWORDS or t in seen:
            continue
        seen.add(t)
        toks.append(t)
        if len(toks) >= max_tokens:
            break
    return set(toks)


@dataclass
class _Feat:
    """Per-record matching features + the display fields a story needs."""
    idx: int
    rid: str
    section: str
    source: str
    lang: str
    title: str
    url: str
    day: Optional[date]
    tier: str
    entities: set[str]              # discriminative, used for matching (DF-pruned)
    tokens: set[str]                # discriminative, used for matching (DF-pruned)
    label_entities: set[str] = field(default_factory=set)  # full set, for labels


def _build_features(
    records: list[dict], *, today: date, window_days: int,
    stop_entities: Optional[set[str]] = None,
) -> list[_Feat]:
    """Clean, window-filter, and featurize records. Drops feed-failure stubs and
    anything outside the window or missing usable text."""
    horizon = today - timedelta(days=window_days)
    feats: list[_Feat] = []
    for rec in records:
        if sg.is_noise_record(rec):
            continue
        day = sg._parse_day(rec)
        if day is None or day < horizon or day > today:
            continue
        title = _clean_headline(rec.get("title") or rec.get("original_text") or "")
        if len(title) < 8 or _MASTHEAD_RE.search(title):
            continue
        ents = _entity_keys(rec, stop_entities=stop_entities)
        toks = _title_tokens(rec)
        if not ents and len(toks) < 2:
            # Nothing to match on — a story needs at least a couple of handles.
            continue
        section = str(rec.get("section_id") or rec.get("section") or "?")
        feats.append(_Feat(
            idx=len(feats),
            rid=str(rec.get("id") or rec.get("_id") or f"{section}:{len(feats)}"),
            section=section,
            source=str(rec.get("source_id") or section),
            lang=str(rec.get("original_lang") or rec.get("lang") or "en"),
            title=title,
            url=str(rec.get("original_url") or rec.get("url") or ""),
            day=day,
            tier=str(rec.get("tier") or "unknown"),
            entities=ents,
            tokens=toks,
            label_entities=set(ents),
        ))
    _prune_generic_keys(feats)
    return feats


def _prune_generic_keys(feats: list[_Feat]) -> None:
    """Drop entities/tokens that recur across too large a share of the window's
    records: they are not discriminative enough to *join* two records on, and
    keeping them is what chains unrelated stories into one hairball under
    single-link clustering. Mutates each feat's `entities`/`tokens` in place."""
    n = len(feats)
    if n < 2:
        return
    ent_df: dict[str, int] = {}
    tok_df: dict[str, int] = {}
    for f in feats:
        for e in f.entities:
            ent_df[e] = ent_df.get(e, 0) + 1
        for t in f.tokens:
            tok_df[t] = tok_df.get(t, 0) + 1
    ent_cap = max(_ENTITY_DF_FLOOR, int(n * _ENTITY_DF_FRACTION))
    tok_cap = max(_TOKEN_DF_FLOOR, int(n * _TOKEN_DF_FRACTION))
    for f in feats:
        f.entities = {e for e in f.entities if ent_df[e] <= ent_cap}
        f.tokens = {t for t in f.tokens if tok_df[t] <= tok_cap}


# ============================================================================
# Similarity + clustering (pure)
# ============================================================================

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _is_edge(
    a: _Feat, b: _Feat, *,
    embeddings_by_id: Optional[dict] = None,
) -> bool:
    """Decide whether two records describe the same story. Precision-first, over
    the *discriminative* keys left after document-frequency pruning:

      * two or more shared entities, or
      * one shared entity backed by real token overlap (Jaccard or >= 3 tokens), or
      * strong headline-token overlap on its own, or
      * embedding cosine over threshold (when an index is present).
    """
    shared_ents = a.entities & b.entities
    shared_toks = a.tokens & b.tokens
    if len(shared_ents) >= 2:
        return True
    tok_j = _jaccard(a.tokens, b.tokens)
    if len(shared_ents) >= 1 and (tok_j >= TOKEN_JACCARD_WITH_ENTITY or len(shared_toks) >= 3):
        return True
    if tok_j >= TOKEN_JACCARD_STRONG:
        return True
    if embeddings_by_id is not None:
        va = embeddings_by_id.get(a.rid)
        vb = embeddings_by_id.get(b.rid)
        if va is not None and vb is not None:
            # Vectors are stored unit-normalized, so the dot product is cosine.
            if float((va * vb).sum()) >= EMB_THRESHOLD:
                return True
    return False


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def _candidate_pairs(feats: list[_Feat]) -> Iterable[tuple[int, int]]:
    """Generate candidate (i, j) pairs via an inverted index over entities and
    salient tokens, so we only compare records that share at least one handle.
    Blocks up to ``_FULL_PAIRWISE_CAP`` are compared exhaustively; larger blocks
    fall back to a linear star against the block's first member (which still
    connects the whole block transitively under union-find) to stay near-linear
    on a busy day's thousands of records."""
    index: dict[str, list[int]] = {}
    for f in feats:
        # Entities are namespaced from tokens so an entity "iran" and a token
        # "iran" don't collapse into one (slightly) noisier block.
        for k in f.entities:
            index.setdefault("e:" + k, []).append(f.idx)
        for k in f.tokens:
            index.setdefault("t:" + k, []).append(f.idx)

    emitted: set[tuple[int, int]] = set()

    def emit(ia: int, ib: int):
        pair = (ia, ib) if ia < ib else (ib, ia)
        if pair[0] != pair[1] and pair not in emitted:
            emitted.add(pair)
            return pair
        return None

    for members in index.values():
        m = len(members)
        if m < 2:
            continue
        if m <= _FULL_PAIRWISE_CAP:
            for a in range(m):
                for b in range(a + 1, m):
                    pair = emit(members[a], members[b])
                    if pair is not None:
                        yield pair
        else:
            hub = members[0]
            for other in members[1:]:
                pair = emit(hub, other)
                if pair is not None:
                    yield pair


@dataclass
class Story:
    id: str
    headline: str
    n_records: int
    n_outlets: int                # distinct sources (independence unit)
    n_sections: int
    n_languages: int
    sources: list[str]
    sections: list[str]
    languages: list[str]
    top_entities: list[str]
    score: float
    recency_days: int
    representative_url: str
    representative_source: str
    members: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)  # diverse {id,section,source,title,url,lang}
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "headline": self.headline,
            "n_records": self.n_records,
            "n_outlets": self.n_outlets,
            "n_sections": self.n_sections,
            "n_languages": self.n_languages,
            "sources": self.sources,
            "sections": self.sections,
            "languages": self.languages,
            "top_entities": self.top_entities,
            "score": round(self.score, 4),
            "recency_days": self.recency_days,
            "representative_url": self.representative_url,
            "representative_source": self.representative_source,
            "members": self.members,
            "evidence": self.evidence,
            "rationale": self.rationale,
        }


def _score(n_outlets: int, n_sections: int, n_languages: int, min_age: int) -> float:
    """Breadth-of-independent-coverage, decayed by recency.

    Distinct *sections* drive significance: a story corroborated across many
    kinds of source (news + filings + conflict data + markets) matters more than
    one echoed by many near-identical local outlets running the same wire. So
    sections enter super-linearly while raw outlet count enters logarithmically
    (diminishing returns — 40 syndicated copies are not 40× a story). Distinct
    languages add breadth, and fresh stories rank above stale ones.
    """
    recency = 0.5 ** (min_age / DEFAULT_HALF_LIFE)
    return (
        (n_sections ** 1.3)
        * (1.0 + math.log1p(n_outlets))
        * (1.0 + 0.12 * max(n_languages - 1, 0))
        * recency
    )


def _pick_representative(members: list[_Feat]) -> _Feat:
    """Highest source tier wins (mirrors dedup.TIER_RANK); ties break by the
    earliest day, then a longer headline, then record id for determinism."""
    from .dedup import TIER_RANK, TIER_DEFAULT

    def key(f: _Feat):
        return (
            TIER_RANK.get((f.tier or "").strip(), TIER_DEFAULT),
            f.day or date.max,
            -len(f.title),
            f.rid,
        )
    return sorted(members, key=key)[0]


def _story_id(today: date, top_entities: list[str], rep_id: str) -> str:
    handle = "|".join(top_entities) if top_entities else rep_id
    return hashlib.sha1(
        f"{METHOD}|{today.isoformat()}|{handle}".encode("utf-8")
    ).hexdigest()[:16]


def cluster_records(
    records: list[dict], *,
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_outlets: int = MIN_OUTLETS,
    min_sections: int = MIN_SECTIONS,
    top_n: int = DEFAULT_TOP_N,
    embeddings_by_id: Optional[dict] = None,
    stop_entities: Optional[set[str]] = None,
) -> list[Story]:
    """Cluster records into ranked story threads. Pure: no DB, no model.

    A cluster surfaces as a top story only if it clears the cross-source bar
    (``min_outlets`` distinct sources OR ``min_sections`` distinct sections);
    that corroboration requirement is what separates a top story from a lone
    item. Returns the top ``top_n`` by breadth-of-coverage score.
    """
    feats = _build_features(records, today=today, window_days=window_days,
                            stop_entities=stop_entities)
    if not feats:
        return []

    uf = _UnionFind(len(feats))
    for i, j in _candidate_pairs(feats):
        if _is_edge(feats[i], feats[j], embeddings_by_id=embeddings_by_id):
            uf.union(i, j)

    groups: dict[int, list[_Feat]] = {}
    for f in feats:
        groups.setdefault(uf.find(f.idx), []).append(f)

    stories: list[Story] = []
    for members in groups.values():
        sources = sorted({m.source for m in members})
        sections = sorted({m.section for m in members})
        languages = sorted({m.lang for m in members})
        n_outlets, n_sections = len(sources), len(sections)
        # Cross-source corroboration bar.
        if n_outlets < min_outlets and n_sections < min_sections:
            continue

        # Most-shared entities across the cluster make the best handle/label.
        # Use the *unpruned* label set so a dominant story whose key entity was
        # DF-pruned for matching still labels with that entity (e.g. "iran").
        ent_counts: dict[str, int] = {}
        for m in members:
            for e in m.label_entities:
                ent_counts[e] = ent_counts.get(e, 0) + 1
        top_entities = [
            e for e, _ in sorted(ent_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if ent_counts[e] >= 2
        ][:6]

        rep = _pick_representative(members)
        min_age = min((today - m.day).days for m in members if m.day)
        min_age = max(min_age, 0)
        score = _score(n_outlets, n_sections, len(languages), min_age)

        # Diverse evidence: one item per distinct source, highest tier first,
        # so the reader sees the spread of who is covering it (not 6 dupes).
        evidence = _diverse_evidence(members)
        ent_label = ", ".join(e.title() for e in top_entities[:3])
        rationale = (
            f"Covered by {n_outlets} source{'s' if n_outlets != 1 else ''} "
            f"across {n_sections} section{'s' if n_sections != 1 else ''}"
            + (f" in {len(languages)} languages" if len(languages) > 1 else "")
            + (f"; recurring: {ent_label}" if ent_label else "")
            + (f"; most recent {min_age}d ago." if min_age else "; breaking today.")
        )
        stories.append(Story(
            id=_story_id(today, top_entities, rep.rid),
            headline=rep.title[:200],
            n_records=len(members),
            n_outlets=n_outlets,
            n_sections=n_sections,
            n_languages=len(languages),
            sources=sources,
            sections=sections,
            languages=languages,
            top_entities=top_entities,
            score=score,
            recency_days=min_age,
            representative_url=rep.url,
            representative_source=rep.source,
            members=[m.rid for m in members],
            evidence=evidence,
            rationale=rationale,
        ))

    stories.sort(key=lambda s: (s.score, s.n_outlets, s.n_records), reverse=True)
    return stories[:top_n]


def _diverse_evidence(members: list[_Feat], *, max_items: int = 6) -> list[dict]:
    """One representative item per distinct source (highest tier, then longest
    headline), so the evidence trail shows coverage spread rather than dupes."""
    from .dedup import TIER_RANK, TIER_DEFAULT

    best_per_source: dict[str, _Feat] = {}
    for m in members:
        cur = best_per_source.get(m.source)
        if cur is None:
            best_per_source[m.source] = m
            continue
        better = (
            TIER_RANK.get((m.tier or "").strip(), TIER_DEFAULT),
            -len(m.title),
        ) < (
            TIER_RANK.get((cur.tier or "").strip(), TIER_DEFAULT),
            -len(cur.title),
        )
        if better:
            best_per_source[m.source] = m

    ordered = sorted(
        best_per_source.values(),
        key=lambda f: (TIER_RANK.get((f.tier or "").strip(), TIER_DEFAULT), -len(f.title)),
    )
    return [
        {
            "id": f.rid, "section": f.section, "source": f.source,
            "title": f.title[:160], "url": f.url, "lang": f.lang,
        }
        for f in ordered[:max_items]
    ]


# ============================================================================
# Brief panel
# ============================================================================

def render_stories_panel(stories: list[Story], *, max_show: int = 10) -> str:
    """Render the 'Top Stories' section in the brief's house style (matches the
    section markup in worldscope.render and the signals/radar panels)."""
    import html as _html

    if not stories:
        return ""
    rows = []
    for s in stories[:max_show]:
        link = ""
        if s.representative_url:
            link = (f"<a href='{_html.escape(s.representative_url, quote=True)}'>"
                    f"{_html.escape(s.headline[:140])}</a>")
        else:
            link = f"<strong>{_html.escape(s.headline[:140])}</strong>"
        lang_note = f" · {s.n_languages} langs" if s.n_languages > 1 else ""
        rows.append(
            "<li>"
            f"<span class='new-badge'>{s.n_outlets}×</span>"
            f"{link}"
            f"<span class='meta'> · {s.n_outlets} sources / {s.n_sections} sections"
            f"{lang_note}</span>"
            f"<div class='abs'>{_html.escape(s.rationale)}</div>"
            "</li>"
        )
    multi_lang = sum(1 for s in stories if s.n_languages > 1)
    synth = (
        f"<p class='synth'>{len(stories)} stories clustered from today's records "
        f"by independent cross-source coverage"
        + (f"; {multi_lang} are being reported in more than one language" if multi_lang else "")
        + ". Ranked by how broadly and independently each is being reported.</p>"
    )
    return (
        "<section class='section'>"
        "<h2>🗞️ Top Stories — what the world is covering "
        f"<span class='count'>· {len(stories)} stories</span></h2>"
        f"{synth}"
        f"<ul class='items'>{''.join(rows)}</ul>"
        "</section>"
    )


# ============================================================================
# Lake adapters + orchestration
# ============================================================================

def load_records_from_db(conn, *, today: date, days: int) -> list[dict]:
    """Read windowed records from the populated lake DB, enriched with the
    source tier + language and joined entity names — everything the clusterer
    needs to rank by independent-source breadth and pick a representative."""
    horizon = (today - timedelta(days=days)).isoformat()
    cur = conn.execute(
        "SELECT r.id, r.section_id, r.source_id, r.original_text, r.original_url, "
        "       r.original_lang, r.record_date, s.tier "
        "  FROM records r LEFT JOIN sources s ON s.id = r.source_id "
        " WHERE COALESCE(r.record_date, r.ingested_at) >= ?",
        (horizon,),
    )
    rows = [dict(r) for r in cur.fetchall()]
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


def _load_embeddings(conn, record_ids: list[str]) -> Optional[dict]:
    """Return {record_id: np.ndarray} for any windowed records that have a
    vector, or None when the index is empty / unavailable. Optional boost only —
    the deterministic matcher works without it."""
    if not record_ids:
        return None
    try:
        import numpy as np  # numpy is a hard dep, but guard anyway
        rows = conn.execute(
            f"SELECT record_id, vector FROM record_embeddings "
            f"WHERE record_id IN ({','.join('?' * len(record_ids))})",
            record_ids,
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    out: dict[str, "np.ndarray"] = {}
    for rid, blob in rows:
        try:
            out[rid] = np.frombuffer(blob, dtype=np.float32)
        except Exception:
            continue
    return out or None


def _load_source_name_keys(conn) -> set[str]:
    """Normalized names + ids of every known source, so entities that are really
    just outlet names ('Reuters', 'The Guardian') are excluded from matching."""
    stop: set[str] = set()
    try:
        for row in conn.execute("SELECT id, name FROM sources"):
            for v in (row[0], row[1]):
                if v:
                    stop.add(sg._norm_key(str(v).replace("-", " ").replace("_", " ")))
    except Exception:
        return set()
    return stop


def build_stories(
    *, today: date, days: int = DEFAULT_WINDOW_DAYS, conn=None, top_n: int = DEFAULT_TOP_N,
) -> list[Story]:
    """Load records (DB if a populated connection is given, else committed JSONL)
    and cluster them into ranked stories, folding in embeddings when present."""
    records: list[dict] = []
    embeddings_by_id: Optional[dict] = None
    stop_entities: Optional[set[str]] = None
    if conn is not None:
        try:
            records = load_records_from_db(conn, today=today, days=days)
        except Exception:
            records = []
        if records:
            try:
                embeddings_by_id = _load_embeddings(conn, [r["id"] for r in records])
            except Exception:
                embeddings_by_id = None
            stop_entities = _load_source_name_keys(conn)
    if not records:
        records = sg.load_records_from_jsonl(today=today, days=days)
    return cluster_records(
        records, today=today, window_days=days, top_n=top_n,
        embeddings_by_id=embeddings_by_id, stop_entities=stop_entities,
    )


def write_stories_artifact(
    today: date, stories: list[Story], *, out_root: Path = LAKE_META,
) -> Path:
    """Persist the Top Stories JSON artifact under lake/sections/_meta/<date>/,
    the same convention radar + cross_section use. Consumed by the weekly
    rollup, the desk-officer routine, and any downstream dataset build."""
    out_dir = Path(out_root) / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "top_stories.json"
    payload = {
        "date": today.isoformat(),
        "method": METHOD,
        "story_count": len(stories),
        "stories": [s.to_dict() for s in stories],
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out_path


# ============================================================================
# CLI
# ============================================================================

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Event-level story clustering (Top Stories).")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="as-of date (YYYY-MM-DD); default today")
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help="record window in days (default 2)")
    ap.add_argument("--top", type=int, default=15, help="how many to print")
    ap.add_argument("--write", action="store_true",
                    help="persist the top_stories.json artifact")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.date)
    conn = None
    if args.write or True:
        try:
            from .lake import Lake
            conn = Lake.open()._ensure_open()
        except Exception:
            conn = None
    stories = build_stories(today=today, days=args.days, conn=conn,
                            top_n=max(args.top, DEFAULT_TOP_N))

    print(f"[stories] {len(stories)} top stories as of {today} "
          f"(window {args.days}d):\n")
    for i, s in enumerate(stories[:args.top], 1):
        lang = f" · {s.n_languages} langs" if s.n_languages > 1 else ""
        print(f"{i:2}. [{s.n_outlets} src / {s.n_sections} sec{lang} | score {s.score:.2f}] "
              f"{s.headline[:74]}")
        print(f"      sections: {', '.join(s.sections[:8])}")

    if args.write:
        path = write_stories_artifact(today, stories)
        print(f"\n[stories] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
