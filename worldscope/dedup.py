"""
worldscope.dedup — cross-source headline deduplication via embeddings.

When Reuters, AP, BBC and Al Jazeera all run the same Federal Reserve story,
the daily brief currently surfaces four near-identical bullets. This module
clusters those records together so the brief can show one representative
plus a "also covered by N outlets" tag.

Algorithm: greedy single-link agglomerative clustering against a shared
embedding space. For each unclustered record (ordered by ingested_at
ASC), we find the nearest already-clustered record above the similarity
threshold; if one exists, we join that cluster, else we open a new one.
Complexity is O(N * K) where K = number of clusters so far. For the
~2k records/day worldscope ingests, this runs in well under a second.

Representative selection: highest source tier wins (primary_document >
mainstream_independent > ...). Ties break by earliest record_date, then
by lexicographic record_id.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from .embeddings import EmbeddingIndex, EMBED_DIM, _blob_to_vec, _normalize
from .lake import LAKE_DB


logger = logging.getLogger(__name__)


# Lower number = higher trust. Mirrors the contract in
# docs/SECTION_ADAPTER_CONTRACT.md "Source tier" table.
TIER_RANK = {
    "primary_document":         0,
    "mainstream_independent":   1,
    "mainstream_partisan_left": 2,
    "mainstream_partisan_right": 2,
    "mixed":                    3,
    "state_controlled":         4,
    "aggregator":               5,
    "community":                6,
    "prediction_market":        6,
    "speculative_blog":         7,
}
TIER_DEFAULT = 9


class HeadlineDedup:
    """Cluster lake records that are likely the same story across sources.

    Typical use::

        idx = EmbeddingIndex()
        idx.index_today('2026-05-27')
        dd  = HeadlineDedup(idx)
        clusters = dd.cluster_today('2026-05-27')
        summary  = dd.cluster_summary(clusters)
    """

    def __init__(
        self,
        embedding_index: Optional[EmbeddingIndex] = None,
        similarity_threshold: float = 0.78,
        time_window_hours: int = 36,
    ) -> None:
        self.idx = embedding_index or EmbeddingIndex()
        self.threshold = float(similarity_threshold)
        self.window_hours = int(time_window_hours)

    # ---- core ------------------------------------------------------------

    def _load_candidates(self, date_iso: str) -> list[sqlite3.Row]:
        """Load every embedded record whose record_date or ingested-day
        equals `date_iso`. The time-window cap is enforced downstream
        (see `_within_window`)."""
        conn = sqlite3.connect(f"file:{self.idx.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT r.id, r.source_id, r.section_id, r.original_text,
                   r.original_url, r.original_lang, r.record_date,
                   r.ingested_at, s.tier, e.vector
              FROM records r
              JOIN record_embeddings e ON e.record_id = r.id
              LEFT JOIN sources s ON s.id = r.source_id
             WHERE e.model = ?
               AND (r.record_date = ? OR substr(r.ingested_at, 1, 10) = ?)
            """,
            (self.idx.model_name, date_iso, date_iso),
        ).fetchall()
        conn.close()
        return rows

    @staticmethod
    def _row_ts(row: sqlite3.Row) -> datetime:
        """Best-effort parse of a row's effective timestamp."""
        ts = row["ingested_at"] or (row["record_date"] + "T00:00:00Z" if row["record_date"] else None)
        if not ts:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

    def _within_window(self, a: sqlite3.Row, b: sqlite3.Row) -> bool:
        delta = abs((self._row_ts(a) - self._row_ts(b)).total_seconds())
        return delta <= self.window_hours * 3600

    # ---- main entry points ----------------------------------------------

    def cluster_today(self, date_iso: Optional[str] = None) -> list[dict]:
        """Return clusters of records likely to be the same story.

        Each cluster::

            {
              "representative_id":  str,
              "members":            [record_id, ...],
              "member_count":       int,
              "sources":            [source_id, ...],
              "languages":          [lang, ...],
              "centroid_similarity": float,   # mean cosine of members to centroid
              "earliest":           ISO8601,
              "latest":             ISO8601,
              "representative_text": str,
            }
        """
        if date_iso is None:
            date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self._load_candidates(date_iso)
        if not rows:
            return []

        # Sort by ingested_at ASC so the earliest report seeds each cluster.
        rows = sorted(rows, key=lambda r: (r["ingested_at"] or "", r["id"]))
        vecs = np.stack([_blob_to_vec(r["vector"]) for r in rows], axis=0)
        vecs = _normalize(vecs)

        # Cluster index per row, -1 = unassigned.
        N = len(rows)
        cluster_of = [-1] * N
        # Each cluster carries a running centroid (unit-normalized sum-of-members).
        centroids: list[np.ndarray] = []
        # And the row-indices that belong to each cluster.
        members: list[list[int]] = []

        for i in range(N):
            best_c = -1
            best_score = -1.0
            v = vecs[i]
            for c_idx in range(len(centroids)):
                # Time-window check: at least one existing member must fall
                # within ±window of this candidate. Cheap because we hold
                # the row indices on `members`.
                if not any(self._within_window(rows[i], rows[m]) for m in members[c_idx]):
                    continue
                score = float(centroids[c_idx] @ v)
                if score > best_score:
                    best_score = score
                    best_c = c_idx
            if best_c >= 0 and best_score >= self.threshold:
                cluster_of[i] = best_c
                members[best_c].append(i)
                # Update centroid: incremental mean + renormalize.
                k = len(members[best_c])
                centroids[best_c] = _normalize(
                    (centroids[best_c] * (k - 1) + v).reshape(1, -1)
                )[0]
            else:
                cluster_of[i] = len(centroids)
                centroids.append(v.copy())
                members.append([i])

        # Materialize cluster dicts.
        out: list[dict] = []
        for c_idx, member_idxs in enumerate(members):
            member_rows = [rows[i] for i in member_idxs]
            rep = self._pick_representative(member_rows)
            # Centroid similarity = mean dot product of members with centroid.
            sub = vecs[member_idxs]
            cent = centroids[c_idx]
            mean_sim = float((sub @ cent).mean()) if len(member_idxs) > 0 else 0.0
            timestamps = [self._row_ts(r) for r in member_rows]
            cluster = {
                "representative_id":   rep["id"],
                "representative_text": (rep["original_text"] or "")[:240],
                "representative_url":  rep["original_url"],
                "representative_source": rep["source_id"],
                "representative_tier":  rep["tier"] or "unknown",
                "members":             [r["id"] for r in member_rows],
                "member_count":        len(member_rows),
                "sources":             sorted({r["source_id"] for r in member_rows}),
                "languages":           sorted({r["original_lang"] or "en" for r in member_rows}),
                "sections":            sorted({r["section_id"] for r in member_rows}),
                "centroid_similarity": round(mean_sim, 4),
                "earliest":            min(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "latest":              max(timestamps).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            out.append(cluster)
        return out

    @staticmethod
    def _pick_representative(member_rows: list[sqlite3.Row]) -> sqlite3.Row:
        """Highest-tier wins; ties break by earliest ingested_at, then id."""
        def key(r: sqlite3.Row):
            return (
                TIER_RANK.get((r["tier"] or "").strip(), TIER_DEFAULT),
                r["ingested_at"] or "",
                r["id"] or "",
            )
        return sorted(member_rows, key=key)[0]

    def cluster_summary(self, clusters: list[dict]) -> dict:
        """Roll-up statistics about a cluster set."""
        total_records = sum(c["member_count"] for c in clusters)
        n_clusters = len(clusters)
        merged = total_records - n_clusters
        dedup_ratio = (1.0 - n_clusters / total_records) if total_records else 0.0
        size_hist: dict[int, int] = {}
        for c in clusters:
            size_hist[c["member_count"]] = size_hist.get(c["member_count"], 0) + 1
        top = sorted(clusters, key=lambda c: -c["member_count"])[:10]
        return {
            "total_records":          total_records,
            "cluster_count":          n_clusters,
            "records_merged":         merged,
            "dedup_ratio":            round(dedup_ratio, 4),
            "size_histogram":         dict(sorted(size_hist.items())),
            "top_clusters":           [
                {
                    "representative_id":  c["representative_id"],
                    "representative_text": c["representative_text"],
                    "member_count":       c["member_count"],
                    "sources":            c["sources"],
                    "languages":          c["languages"],
                    "centroid_similarity": c["centroid_similarity"],
                }
                for c in top
            ],
        }
