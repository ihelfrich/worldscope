"""
worldscope.lake — the data lake layer.

Implements the contract in docs/SECTION_ADAPTER_CONTRACT.md. Lives ALONGSIDE
the existing worldscope.store snapshot system; does not replace it. The
existing brief.py path continues to work unchanged. The new orchestrator
path (and any new section) uses the lake.

What this module owns:
    - lake/sections/<section-id>/<YYYY-MM-DD>/{raw.jsonl, summary.md, structured.json}
    - lake/db/worldscope.sqlite — the structured store (entities, relationships,
      predictions, paper_bets, anomalies, source_health, briefs, quarantine)

What this module does NOT own:
    - The existing ~/.worldscope/store.sqlite snapshot store (worldscope.store)
    - HTML rendering (worldscope.render)
    - The Pushover delivery workflow

Public API:
    Lake.open()                       → opens the lake DB, runs migrations
    Lake.write_artifacts(section, date, raw, summary, structured)
                                       → emits the three-file artifact set
    Lake.read_artifacts(section, date) → reads them back
    Lake.record_source_health(source_id, success, record_count, schema_hash, error)
    Lake.record_brief(date, kind, paths, cost)
    Lake.add_to_quarantine(source_id, raw_json, error)
    Lake.upsert_entity(eid, etype, name, aliases, metadata)
    Lake.upsert_relationship(from_id, to_id, type, weight, evidence)
    Lake.add_prediction(...)
    Lake.add_paper_bet(...)
    Lake.mark_paper_bet(bet_id, mark_date, price)
    Lake.resolve_paper_bet(bet_id, outcome, pnl)
    Lake.add_anomaly(...)

Schema versioning:
    schema_version row in `meta` table. Migrations are idempotent SQL files
    in worldscope/lake/migrations/. open() runs every pending migration.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAKE_ROOT = REPO_ROOT / "lake"
LAKE_DB   = LAKE_ROOT / "db" / "worldscope.sqlite"
LAKE_SECTIONS = LAKE_ROOT / "sections"


# --------------------------------------------------------------------- #
# Schema (kept inline so the lake is self-bootstrapping; a future
# migrations/ directory will pick this up as version 1)
# --------------------------------------------------------------------- #

SCHEMA_V1 = r"""
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');

-- Sources: the upstream APIs/feeds/scrapers we ingest from.
CREATE TABLE IF NOT EXISTS sources (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    url                   TEXT,
    license               TEXT,                   -- CC-BY-4.0, public-domain, etc.
    attribution_required  INTEGER NOT NULL DEFAULT 0,
    attribution_text      TEXT,
    tier                  TEXT NOT NULL,          -- primary_document | mainstream_independent | ...
    country               TEXT,
    language              TEXT NOT NULL DEFAULT 'en',
    added_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Source health: did this source's last pull work, and what did it return?
CREATE TABLE IF NOT EXISTS source_health (
    source_id              TEXT PRIMARY KEY REFERENCES sources(id),
    last_success_at        TEXT,
    last_record_count      INTEGER,
    last_schema_hash       TEXT,
    last_failure_at        TEXT,
    last_failure_error     TEXT,
    consecutive_failures   INTEGER NOT NULL DEFAULT 0
);

-- Records: every individual item ingested from any source.
CREATE TABLE IF NOT EXISTS records (
    id              TEXT PRIMARY KEY,             -- deterministic hash, see Section._item_id
    source_id       TEXT NOT NULL REFERENCES sources(id),
    section_id      TEXT NOT NULL,                -- which section ingested this
    ingested_at     TEXT NOT NULL,
    original_url    TEXT,
    original_text   TEXT,                          -- truncated to ~500 chars
    original_lang   TEXT NOT NULL DEFAULT 'en',
    record_date     TEXT,                          -- date the underlying event is from
    license         TEXT,
    extra_json      TEXT                           -- everything else as JSON
);
CREATE INDEX IF NOT EXISTS idx_records_section_date ON records(section_id, record_date);
CREATE INDEX IF NOT EXISTS idx_records_source       ON records(source_id);

-- Entities: people, orgs, places, bills, vessels, aircraft, etc.
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,             -- 'person:warsh-kevin' style
    type            TEXT NOT NULL,                -- person | org | place | bill | filing | vessel | aircraft | market | event | transaction
    canonical_name  TEXT NOT NULL,
    aliases_json    TEXT NOT NULL DEFAULT '[]',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    first_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);

-- Record↔entity (M:N) — which records mention which entities.
CREATE TABLE IF NOT EXISTS record_entities (
    record_id  TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (record_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_re_entity ON record_entities(entity_id);

-- Relationships: typed edges in the entity graph.
CREATE TABLE IF NOT EXISTS relationships (
    id            TEXT PRIMARY KEY,
    from_entity   TEXT NOT NULL REFERENCES entities(id),
    to_entity     TEXT NOT NULL REFERENCES entities(id),
    type          TEXT NOT NULL,                  -- mentions | sponsors-of | transacted-with | owns | etc.
    weight        REAL NOT NULL DEFAULT 1.0,
    first_seen    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    evidence_json TEXT NOT NULL DEFAULT '[]'      -- array of record IDs
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_entity, type);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relationships(to_entity, type);

-- Predictions: forward-looking claims the system has made.
CREATE TABLE IF NOT EXISTS predictions (
    id                      TEXT PRIMARY KEY,
    made_at                 TEXT NOT NULL,
    target_date             TEXT,
    resolution_criteria     TEXT NOT NULL,
    predicted_outcome       TEXT NOT NULL,        -- free text or YES/NO/etc.
    confidence              REAL NOT NULL,        -- 0-1
    training_window_days    INTEGER,
    indicators_used_json    TEXT NOT NULL DEFAULT '[]',
    method                  TEXT NOT NULL,
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    section_id              TEXT,
    resolved_at             TEXT,
    actual_outcome          TEXT,
    brier_contribution      REAL                  -- (predicted_prob - actual_prob)^2
);
CREATE INDEX IF NOT EXISTS idx_pred_target ON predictions(target_date);
CREATE INDEX IF NOT EXISTS idx_pred_made   ON predictions(made_at);

-- Paper bets: simulated trades on prediction markets (Ian's killer feature).
CREATE TABLE IF NOT EXISTS paper_bets (
    id                  TEXT PRIMARY KEY,
    market_platform     TEXT NOT NULL,            -- polymarket | kalshi | predictit | manifold
    market_id           TEXT NOT NULL,
    market_url          TEXT,
    market_question     TEXT NOT NULL,
    market_resolves_at  TEXT,                      -- may be null (open-ended)
    side                TEXT NOT NULL,             -- YES | NO
    size_usd            REAL NOT NULL,
    price_at_bet        REAL NOT NULL,             -- 0-1
    timestamp_bet       TEXT NOT NULL,
    rationale           TEXT NOT NULL,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    model_version       TEXT,
    confidence_band     TEXT NOT NULL,             -- low | medium | high
    section_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_bets_platform ON paper_bets(market_platform);
CREATE INDEX IF NOT EXISTS idx_bets_time     ON paper_bets(timestamp_bet);

-- Paper bet marks: mark-to-market at 1, 5, 14, 30, 60, 90 day milestones.
CREATE TABLE IF NOT EXISTS paper_bet_marks (
    id              TEXT PRIMARY KEY,
    bet_id          TEXT NOT NULL REFERENCES paper_bets(id) ON DELETE CASCADE,
    mark_date       TEXT NOT NULL,
    days_since_bet  INTEGER NOT NULL,
    mark_price      REAL NOT NULL,
    unrealized_pnl  REAL NOT NULL,
    delta_vs_prev   REAL,
    UNIQUE (bet_id, days_since_bet)
);

-- Paper bet resolutions: when the market resolves.
CREATE TABLE IF NOT EXISTS paper_bet_resolutions (
    bet_id              TEXT PRIMARY KEY REFERENCES paper_bets(id) ON DELETE CASCADE,
    resolved_at         TEXT NOT NULL,
    final_outcome       TEXT NOT NULL,             -- YES | NO | INVALIDATED
    final_pnl           REAL NOT NULL,
    holding_period_days INTEGER NOT NULL
);

-- Anomalies: statistical alerts surfaced by any section.
CREATE TABLE IF NOT EXISTS anomalies (
    id              TEXT PRIMARY KEY,
    section_id      TEXT NOT NULL,
    category        TEXT NOT NULL,
    z_score         REAL,
    description     TEXT NOT NULL,
    evidence_json   TEXT NOT NULL DEFAULT '[]',
    detected_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anom_section ON anomalies(section_id);
CREATE INDEX IF NOT EXISTS idx_anom_time    ON anomalies(detected_at);

-- Briefs: cost + token accounting for each rendered brief.
CREATE TABLE IF NOT EXISTS briefs (
    date         TEXT NOT NULL,
    kind         TEXT NOT NULL,                   -- daily | weekly | monthly | adhoc
    title        TEXT,
    html_path    TEXT,
    md_path      TEXT,
    composed_at  TEXT NOT NULL,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (date, kind)
);

-- Quarantine: rows that failed schema validation. Never silently dropped.
CREATE TABLE IF NOT EXISTS quarantine (
    id                TEXT PRIMARY KEY,
    source_id         TEXT,
    section_id        TEXT,
    raw_json          TEXT NOT NULL,
    validation_error  TEXT NOT NULL,
    detected_at       TEXT NOT NULL
);
"""


# --------------------------------------------------------------------- #
# Lake API
# --------------------------------------------------------------------- #

@dataclass
class ArtifactSet:
    """The three-file output of a section's synthesis pass."""
    section_id: str
    date: str                                   # YYYY-MM-DD
    raw: list[dict] = field(default_factory=list)
    summary_md: str = ""
    structured: dict = field(default_factory=dict)


class Lake:
    """Single entrypoint to the lake. Opens lazily, runs migrations once."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or LAKE_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._migrated = False

    # ---- lifecycle ------------------------------------------------------

    @classmethod
    def open(cls, db_path: Optional[Path] = None) -> "Lake":
        lake = cls(db_path)
        lake._ensure_open()
        return lake

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, isolation_level=None)
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.row_factory = sqlite3.Row
        if not self._migrated:
            self._migrate()
            self._migrated = True
        return self._conn

    def _migrate(self) -> None:
        assert self._conn is not None
        self._conn.executescript(SCHEMA_V1)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def txn(self):
        conn = self._ensure_open()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ---- artifact I/O ---------------------------------------------------

    def write_artifacts(self, artifacts: ArtifactSet) -> Path:
        """Emit raw.jsonl + summary.md + structured.json under
        lake/sections/<section>/<date>/. Returns the folder path."""
        folder = LAKE_SECTIONS / artifacts.section_id / artifacts.date
        folder.mkdir(parents=True, exist_ok=True)

        with open(folder / "raw.jsonl", "w", encoding="utf-8") as f:
            for record in artifacts.raw:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                f.write("\n")

        with open(folder / "summary.md", "w", encoding="utf-8") as f:
            f.write(artifacts.summary_md)

        with open(folder / "structured.json", "w", encoding="utf-8") as f:
            json.dump(artifacts.structured, f, indent=2, ensure_ascii=False, sort_keys=True)

        return folder

    def read_artifacts(self, section_id: str, when: str) -> Optional[ArtifactSet]:
        folder = LAKE_SECTIONS / section_id / when
        if not folder.exists():
            return None
        raw_path = folder / "raw.jsonl"
        sum_path = folder / "summary.md"
        struct_path = folder / "structured.json"
        raw: list[dict] = []
        if raw_path.exists():
            with open(raw_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        raw.append(json.loads(line))
        summary_md = sum_path.read_text(encoding="utf-8") if sum_path.exists() else ""
        structured = json.loads(struct_path.read_text(encoding="utf-8")) if struct_path.exists() else {}
        return ArtifactSet(section_id=section_id, date=when,
                           raw=raw, summary_md=summary_md, structured=structured)

    # ---- source health --------------------------------------------------

    def record_source_health(
        self, source_id: str, *, success: bool,
        record_count: int = 0, schema_hash: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        now = _utcnow()
        conn = self._ensure_open()
        # Ensure source row exists (the section's first run inserts it via
        # register_source(); this is defensive in case the order's off).
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, name, tier) VALUES (?, ?, ?)",
            (source_id, source_id, "primary_document"),
        )
        if success:
            conn.execute(
                """
                INSERT INTO source_health
                  (source_id, last_success_at, last_record_count, last_schema_hash,
                   consecutive_failures)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(source_id) DO UPDATE SET
                  last_success_at = excluded.last_success_at,
                  last_record_count = excluded.last_record_count,
                  last_schema_hash = excluded.last_schema_hash,
                  consecutive_failures = 0
                """,
                (source_id, now, record_count, schema_hash),
            )
        else:
            conn.execute(
                """
                INSERT INTO source_health
                  (source_id, last_failure_at, last_failure_error, consecutive_failures)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                  last_failure_at = excluded.last_failure_at,
                  last_failure_error = excluded.last_failure_error,
                  consecutive_failures = source_health.consecutive_failures + 1
                """,
                (source_id, now, error),
            )

    def register_source(
        self, *, source_id: str, name: str, url: Optional[str], license: str,
        tier: str, country: Optional[str] = None, language: str = "en",
        attribution_required: bool = False, attribution_text: Optional[str] = None,
    ) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT INTO sources
              (id, name, url, license, attribution_required, attribution_text,
               tier, country, language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, url=excluded.url, license=excluded.license,
              attribution_required=excluded.attribution_required,
              attribution_text=excluded.attribution_text, tier=excluded.tier,
              country=excluded.country, language=excluded.language
            """,
            (source_id, name, url, license,
             1 if attribution_required else 0, attribution_text,
             tier, country, language),
        )

    # ---- records --------------------------------------------------------

    def upsert_record(self, *, record_id: str, source_id: str, section_id: str,
                      original_url: Optional[str], original_text: Optional[str],
                      original_lang: str = "en", record_date: Optional[str] = None,
                      license: Optional[str] = None, extra: Optional[dict] = None) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT INTO records
              (id, source_id, section_id, ingested_at, original_url,
               original_text, original_lang, record_date, license, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              ingested_at = excluded.ingested_at,
              original_url = excluded.original_url,
              original_text = excluded.original_text,
              record_date = excluded.record_date,
              extra_json = excluded.extra_json
            """,
            (record_id, source_id, section_id, _utcnow(),
             original_url, (original_text or "")[:500], original_lang,
             record_date, license, json.dumps(extra or {}, sort_keys=True)),
        )

    # ---- entities + relationships ---------------------------------------

    def upsert_entity(self, *, entity_id: str, type: str, canonical_name: str,
                      aliases: Optional[list[str]] = None,
                      metadata: Optional[dict] = None) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT INTO entities
              (id, type, canonical_name, aliases_json, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              canonical_name = excluded.canonical_name,
              aliases_json = excluded.aliases_json,
              metadata_json = excluded.metadata_json,
              last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            """,
            (entity_id, type, canonical_name,
             json.dumps(sorted(aliases or []), ensure_ascii=False),
             json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False)),
        )

    def link_record_entity(self, record_id: str, entity_id: str) -> None:
        conn = self._ensure_open()
        conn.execute(
            "INSERT OR IGNORE INTO record_entities (record_id, entity_id) VALUES (?, ?)",
            (record_id, entity_id),
        )

    def upsert_relationship(self, *, from_id: str, to_id: str, type: str,
                            weight: float = 1.0, evidence: Optional[list[str]] = None
                            ) -> None:
        rel_id = hashlib.sha1(
            f"{from_id}|{type}|{to_id}".encode("utf-8")
        ).hexdigest()
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT INTO relationships
              (id, from_entity, to_entity, type, weight, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              weight = excluded.weight,
              evidence_json = excluded.evidence_json,
              last_seen = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            """,
            (rel_id, from_id, to_id, type, weight,
             json.dumps(sorted(evidence or []), ensure_ascii=False)),
        )

    # ---- predictions + paper bets + anomalies ---------------------------

    def add_prediction(self, *, prediction_id: str, made_at: Optional[str] = None,
                       target_date: Optional[str], resolution_criteria: str,
                       predicted_outcome: str, confidence: float,
                       training_window_days: Optional[int], indicators_used: list[str],
                       method: str, evidence: list[str], section_id: Optional[str]
                       ) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO predictions
              (id, made_at, target_date, resolution_criteria, predicted_outcome,
               confidence, training_window_days, indicators_used_json, method,
               evidence_json, section_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (prediction_id, made_at or _utcnow(), target_date, resolution_criteria,
             predicted_outcome, confidence, training_window_days,
             json.dumps(indicators_used, sort_keys=True),
             method, json.dumps(evidence, sort_keys=True), section_id),
        )

    def add_paper_bet(self, *, bet_id: str, market_platform: str, market_id: str,
                      market_url: Optional[str], market_question: str,
                      market_resolves_at: Optional[str], side: str,
                      size_usd: float, price_at_bet: float,
                      rationale: str, evidence: list[str], model_version: str,
                      confidence_band: str, section_id: Optional[str]) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_bets
              (id, market_platform, market_id, market_url, market_question,
               market_resolves_at, side, size_usd, price_at_bet, timestamp_bet,
               rationale, evidence_json, model_version, confidence_band, section_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bet_id, market_platform, market_id, market_url, market_question,
             market_resolves_at, side, size_usd, price_at_bet, _utcnow(),
             rationale, json.dumps(evidence, sort_keys=True),
             model_version, confidence_band, section_id),
        )

    def mark_paper_bet(self, *, bet_id: str, mark_date: str,
                       days_since_bet: int, mark_price: float) -> None:
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT side, size_usd, price_at_bet FROM paper_bets WHERE id=?", (bet_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown bet_id {bet_id!r}")
        side, size, price = row["side"], row["size_usd"], row["price_at_bet"]
        # Direction-adjusted unrealized P&L on a $1-resolution-payoff market.
        # YES at price p means: if outcome=YES → win $(1-p) per $1 staked; if NO → lose $p
        # We use a simpler proxy: unrealized PnL = size × (mark - price) for YES,
        # or size × (price - mark) for NO.
        if side == "YES":
            unrealized = size * (mark_price - price)
        else:
            unrealized = size * (price - mark_price)
        prev_mark = conn.execute(
            "SELECT mark_price FROM paper_bet_marks WHERE bet_id=? ORDER BY days_since_bet DESC LIMIT 1",
            (bet_id,),
        ).fetchone()
        delta = (mark_price - prev_mark["mark_price"]) if prev_mark else None
        mark_id = hashlib.sha1(f"{bet_id}|{days_since_bet}".encode()).hexdigest()
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_bet_marks
              (id, bet_id, mark_date, days_since_bet, mark_price,
               unrealized_pnl, delta_vs_prev)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (mark_id, bet_id, mark_date, days_since_bet, mark_price, unrealized, delta),
        )

    def resolve_paper_bet(self, *, bet_id: str, resolved_at: str,
                          final_outcome: str) -> None:
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT side, size_usd, price_at_bet, timestamp_bet FROM paper_bets WHERE id=?",
            (bet_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown bet_id {bet_id!r}")
        side, size, price, bet_ts = (
            row["side"], row["size_usd"], row["price_at_bet"], row["timestamp_bet"]
        )
        # YES at price p, resolves YES: payoff = (1 - p) * size; resolves NO: payoff = -p * size
        # NO at price p, resolves YES: payoff = -(1 - p) * size; resolves NO: payoff = p * size
        if final_outcome == "INVALIDATED":
            final_pnl = 0.0
        elif side == "YES":
            final_pnl = size * ((1 - price) if final_outcome == "YES" else -price)
        else:  # NO
            final_pnl = size * (-(1 - price) if final_outcome == "YES" else price)
        bet_date = datetime.fromisoformat(bet_ts.replace("Z", "+00:00"))
        res_date = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
        holding = (res_date.date() - bet_date.date()).days
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_bet_resolutions
              (bet_id, resolved_at, final_outcome, final_pnl, holding_period_days)
            VALUES (?, ?, ?, ?, ?)
            """,
            (bet_id, resolved_at, final_outcome, final_pnl, holding),
        )

    def add_anomaly(self, *, anomaly_id: str, section_id: str, category: str,
                    z_score: Optional[float], description: str,
                    evidence: list[str]) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO anomalies
              (id, section_id, category, z_score, description, evidence_json, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (anomaly_id, section_id, category, z_score, description,
             json.dumps(evidence, sort_keys=True), _utcnow()),
        )

    # ---- brief accounting + quarantine ----------------------------------

    def record_brief(self, *, when: str, kind: str, title: Optional[str],
                     html_path: Optional[str], md_path: Optional[str],
                     tokens_in: int = 0, tokens_out: int = 0,
                     cost_usd: float = 0.0) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO briefs
              (date, kind, title, html_path, md_path, composed_at,
               tokens_in, tokens_out, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (when, kind, title, html_path, md_path, _utcnow(),
             tokens_in, tokens_out, cost_usd),
        )

    def add_to_quarantine(self, *, q_id: str, source_id: Optional[str],
                          section_id: Optional[str], raw_json: dict,
                          validation_error: str) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO quarantine
              (id, source_id, section_id, raw_json, validation_error, detected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (q_id, source_id, section_id,
             json.dumps(raw_json, sort_keys=True, ensure_ascii=False),
             validation_error, _utcnow()),
        )


# --------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------- #

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def schema_hash_of(rows: Iterable[dict]) -> str:
    """Stable hash of the column structure of incoming rows. Used to detect
    when an upstream API has changed its response shape."""
    keys: set[str] = set()
    for row in rows:
        keys.update(row.keys() if isinstance(row, dict) else [])
    return hashlib.sha1("|".join(sorted(keys)).encode("utf-8")).hexdigest()[:12]
