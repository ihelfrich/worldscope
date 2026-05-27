"""
worldscope.embeddings — multilingual semantic index over the lake's records.

Uses sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-dim,
~50 languages, trained on parallel corpora). Cross-language queries work
because the same concept in en/ru/uk/zh/ar/es lands in nearby points in
the embedding space.

What this module owns:
    - The `record_embeddings` SQLite table (added in lake/__init__.py SCHEMA_V1).
    - On-disk vector cache: blob = 384 float32 LE = 1536 bytes per row.
    - Cosine similarity search across a time window.

What this module does NOT own:
    - The clustering / dedup logic (lives in worldscope.dedup).
    - MCP-server endpoints (mcp-server/worldscope_mcp.py imports search results).

Design notes:
    - The model loads lazily (~200 MB resident). One instance per process.
    - We embed `original_text` only; that field is already truncated to
      <=500 chars at ingestion time, which is well below the model's 128-token
      window for most headlines.
    - The model name is stored alongside the vector so a future re-indexing
      with a different model can be detected and re-embedded.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from .lake import LAKE_DB, _utcnow


logger = logging.getLogger(__name__)


DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384


def _vec_to_blob(v: np.ndarray) -> bytes:
    """Pack a 1-D float32 vector into little-endian bytes."""
    if v.dtype != np.float32:
        v = v.astype(np.float32, copy=False)
    if v.ndim != 1 or v.shape[0] != EMBED_DIM:
        raise ValueError(f"expected shape ({EMBED_DIM},), got {v.shape}")
    return v.tobytes(order="C")


def _blob_to_vec(b: bytes) -> np.ndarray:
    """Unpack the BLOB back into a float32 vector."""
    return np.frombuffer(b, dtype=np.float32, count=EMBED_DIM)


def _normalize(v: np.ndarray) -> np.ndarray:
    """L2-normalize a (..., D) array along the last axis. Safe for zeros."""
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return v / norms


class EmbeddingIndex:
    """Lake-backed multilingual embedding index.

    Typical use::

        idx = EmbeddingIndex()
        idx.index_today('2026-05-27')
        results = idx.search('Warsh Federal Reserve Chair')
    """

    def __init__(
        self,
        lake_db_path: Optional[Path] = None,
        model_name: str = DEFAULT_MODEL,
    ) -> None:
        self.db_path = Path(lake_db_path) if lake_db_path else LAKE_DB
        self.model_name = model_name
        self._model = None  # lazy
        self._mem_cache: dict[str, np.ndarray] = {}  # hash(text) -> vec

    # ---- model + connection lifecycle -----------------------------------

    def _get_model(self):
        if self._model is None:
            # local import so module-level import is cheap even when the
            # ML stack isn't installed (e.g. on CI for read-only paths).
            from sentence_transformers import SentenceTransformer
            logger.info("loading sentence-transformer model %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _open(self, *, read_only: bool = False) -> sqlite3.Connection:
        if not self.db_path.exists():
            # Bootstrap the schema by opening the Lake once.
            from .lake import Lake
            Lake.open(self.db_path).close()
        if read_only:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            # In case the DB was created by a process that didn't run the
            # full migration (older lake builds), make sure the table exists.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS record_embeddings (
                    record_id  TEXT PRIMARY KEY,
                    vector     BLOB NOT NULL,
                    model      TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                )
            """)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- single + batch embedding ---------------------------------------

    def embed_text(self, text: str) -> np.ndarray:
        """Return a 384-dim L2-normalized float32 vector for one string."""
        vecs = self.embed_batch([text])
        return vecs[0]

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Return an (N, 384) L2-normalized float32 matrix. Uses an in-memory
        cache keyed on a stable hash of the input text, so re-embedding the
        same headline across calls is free."""
        if not texts:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        keys = [hashlib.sha1((t or "").encode("utf-8")).hexdigest() for t in texts]
        out = np.empty((len(texts), EMBED_DIM), dtype=np.float32)
        miss_idx = []
        miss_text = []
        for i, (k, t) in enumerate(zip(keys, texts)):
            cached = self._mem_cache.get(k)
            if cached is not None:
                out[i] = cached
            else:
                miss_idx.append(i)
                miss_text.append(t or "")
        if miss_text:
            model = self._get_model()
            # The model handles empty strings fine; normalize_embeddings=True
            # gives unit vectors so cosine sim becomes a dot product.
            vecs = model.encode(
                miss_text,
                batch_size=64,
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True,
            ).astype(np.float32, copy=False)
            for j, (i, t) in enumerate(zip(miss_idx, miss_text)):
                out[i] = vecs[j]
                self._mem_cache[keys[i]] = vecs[j]
        return out

    # ---- bulk indexing of today's records -------------------------------

    def index_today(self, date_iso: Optional[str] = None) -> dict:
        """Embed every record whose `record_date` equals `date_iso` (or, if
        absent, whose ingested_at falls on that UTC day) and store the
        vectors. Records already embedded with this exact model are skipped.
        Returns `{section_id: count_new_embeddings}`.
        """
        if date_iso is None:
            date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn = self._open()
        # Fetch records dated to today OR ingested today, that don't already
        # have a vector under this model.
        rows = conn.execute(
            """
            SELECT r.id, r.section_id, r.original_text
              FROM records r
              LEFT JOIN record_embeddings e
                     ON e.record_id = r.id AND e.model = ?
             WHERE e.record_id IS NULL
               AND r.original_text IS NOT NULL
               AND length(r.original_text) > 0
               AND (r.record_date = ? OR substr(r.ingested_at, 1, 10) = ?)
            """,
            (self.model_name, date_iso, date_iso),
        ).fetchall()

        per_section: dict[str, int] = {}
        if not rows:
            conn.close()
            return per_section

        # Embed in chunks to keep memory bounded and to give a per-batch
        # progress signal in long runs.
        BATCH = 128
        now = _utcnow()
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            texts = [r["original_text"] for r in chunk]
            vecs = self.embed_batch(texts)
            payload = [
                (r["id"], _vec_to_blob(vecs[i]), self.model_name, now)
                for i, r in enumerate(chunk)
            ]
            conn.executemany(
                """
                INSERT INTO record_embeddings (record_id, vector, model, indexed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                  vector = excluded.vector,
                  model = excluded.model,
                  indexed_at = excluded.indexed_at
                """,
                payload,
            )
            for r in chunk:
                sid = r["section_id"]
                per_section[sid] = per_section.get(sid, 0) + 1
            logger.info("indexed %d / %d", min(start + BATCH, len(rows)), len(rows))

        conn.close()
        return per_section

    # ---- retrieval helpers ----------------------------------------------

    def _load_window(
        self,
        days_back: int,
        date_iso: Optional[str] = None,
    ) -> tuple[list[sqlite3.Row], np.ndarray]:
        """Load all (record, vector) pairs whose record_date is within the
        time window. If a record has no record_date, fall back to the
        first 10 chars of ingested_at. Returns (rows, matrix)."""
        if date_iso is None:
            anchor = datetime.now(timezone.utc).date()
        else:
            anchor = datetime.fromisoformat(date_iso).date()
        cutoff = (anchor - timedelta(days=days_back)).isoformat()
        anchor_iso = anchor.isoformat()
        conn = self._open(read_only=True)
        rows = conn.execute(
            """
            SELECT r.id, r.source_id, r.section_id, r.original_text,
                   r.original_url, r.original_lang, r.record_date,
                   r.ingested_at, e.vector
              FROM records r
              JOIN record_embeddings e ON e.record_id = r.id
             WHERE e.model = ?
               AND (
                    (r.record_date IS NOT NULL AND r.record_date >= ? AND r.record_date <= ?)
                 OR (r.record_date IS NULL
                     AND substr(r.ingested_at, 1, 10) >= ?
                     AND substr(r.ingested_at, 1, 10) <= ?)
               )
            """,
            (self.model_name, cutoff, anchor_iso, cutoff, anchor_iso),
        ).fetchall()
        conn.close()
        if not rows:
            return [], np.zeros((0, EMBED_DIM), dtype=np.float32)
        mat = np.stack([_blob_to_vec(r["vector"]) for r in rows], axis=0)
        return rows, mat

    def search(
        self,
        query: str,
        days_back: int = 7,
        limit: int = 30,
        min_similarity: float = 0.4,
        date_iso: Optional[str] = None,
    ) -> list[dict]:
        """Cross-language semantic search.

        Args:
            query: the user's text (any language).
            days_back: how many days back from today (or `date_iso`).
            limit: max number of results.
            min_similarity: cosine threshold; below this the hit is dropped.

        Each result contains: record_id, source_id, section_id, original_text,
        original_url, original_lang, similarity_score, record_date.
        """
        rows, mat = self._load_window(days_back, date_iso=date_iso)
        if not rows:
            return []
        q_vec = self.embed_text(query)  # already normalized
        # Vectors stored in DB are already unit-norm from
        # normalize_embeddings=True at encode time, so dot product is
        # cosine similarity. We still defensively renormalize the matrix
        # in case a future embedder writes unnormalized rows.
        mat_norm = _normalize(mat)
        sims = mat_norm @ q_vec
        order = np.argsort(-sims)
        out: list[dict] = []
        for idx in order:
            score = float(sims[idx])
            if score < min_similarity:
                break
            r = rows[idx]
            out.append({
                "record_id": r["id"],
                "source_id": r["source_id"],
                "section_id": r["section_id"],
                "original_text": r["original_text"],
                "original_url": r["original_url"],
                "original_lang": r["original_lang"],
                "record_date": r["record_date"],
                "ingested_at": r["ingested_at"],
                "similarity_score": round(score, 4),
            })
            if len(out) >= limit:
                break
        return out

    def find_neighbors(
        self,
        record_id: str,
        top_k: int = 10,
        days_back: int = 30,
    ) -> list[dict]:
        """Find the `top_k` most-similar records to a given record_id within
        the lookback window. Excludes the seed record itself."""
        conn = self._open(read_only=True)
        seed = conn.execute(
            """
            SELECT r.id, r.record_date, r.ingested_at, e.vector
              FROM records r JOIN record_embeddings e ON e.record_id = r.id
             WHERE r.id = ? AND e.model = ?
            """,
            (record_id, self.model_name),
        ).fetchone()
        conn.close()
        if seed is None:
            return []
        seed_vec = _normalize(_blob_to_vec(seed["vector"]).reshape(1, -1))[0]
        seed_date = seed["record_date"] or (seed["ingested_at"] or "")[:10]
        rows, mat = self._load_window(days_back, date_iso=seed_date)
        if not rows:
            return []
        mat_norm = _normalize(mat)
        sims = mat_norm @ seed_vec
        order = np.argsort(-sims)
        out: list[dict] = []
        for idx in order:
            r = rows[idx]
            if r["id"] == record_id:
                continue
            out.append({
                "record_id": r["id"],
                "source_id": r["source_id"],
                "section_id": r["section_id"],
                "original_text": r["original_text"],
                "original_url": r["original_url"],
                "original_lang": r["original_lang"],
                "record_date": r["record_date"],
                "similarity_score": round(float(sims[idx]), 4),
            })
            if len(out) >= top_k:
                break
        return out

    # ---- diagnostics ----------------------------------------------------

    def stats(self) -> dict:
        """Quick health snapshot of the index."""
        conn = self._open(read_only=True)
        total_records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        total_embeds = conn.execute(
            "SELECT COUNT(*) FROM record_embeddings WHERE model = ?",
            (self.model_name,),
        ).fetchone()[0]
        latest = conn.execute(
            "SELECT MAX(indexed_at) FROM record_embeddings WHERE model = ?",
            (self.model_name,),
        ).fetchone()[0]
        conn.close()
        return {
            "model": self.model_name,
            "total_records": total_records,
            "embedded_records": total_embeds,
            "coverage": (total_embeds / total_records) if total_records else 0.0,
            "latest_indexed_at": latest,
        }
