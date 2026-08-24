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
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
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

-- Source runs: append-only history of every pull (source_health is latest-only).
-- Lets us see "ReliefWeb failed 6 days straight", reliability rates, latencies.
CREATE TABLE IF NOT EXISTS source_runs (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    section_id    TEXT,
    started_at    TEXT,
    finished_at   TEXT NOT NULL,
    success       INTEGER NOT NULL,
    record_count  INTEGER NOT NULL DEFAULT 0,
    new_count     INTEGER NOT NULL DEFAULT 0,
    schema_hash   TEXT,
    error_type    TEXT,
    error_message TEXT,
    latency_ms    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_source_runs_src ON source_runs(source_id, finished_at);

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

-- Claims: the unit of the evidence engine. A claim is an assertion that
-- appeared across the lake, carrying its evidence, epistemic status, type, and
-- a confidence that moves over time. Identity is the normalized claim_key, so a
-- claim accumulates evidence across days rather than being re-minted daily.
CREATE TABLE IF NOT EXISTS claims (
    id                TEXT PRIMARY KEY,        -- sha1(claim_key)
    claim_key         TEXT NOT NULL,
    claim_text        TEXT NOT NULL,
    claim_type        TEXT NOT NULL,           -- reported_fact | official_statement | market_signal | statistical_anomaly | osint_observation | inference | forecast | correction | contradiction
    status            TEXT NOT NULL,           -- not_enough_info | single_source | multi_source | primary_confirmed | contradicted | stale | retracted
    confidence        REAL NOT NULL,
    confidence_prev   REAL,                    -- previous value, to show movement
    actors_json       TEXT NOT NULL DEFAULT '[]',
    places_json       TEXT NOT NULL DEFAULT '[]',
    topics_json       TEXT NOT NULL DEFAULT '[]',
    n_sources         INTEGER NOT NULL DEFAULT 0,
    n_sections        INTEGER NOT NULL DEFAULT 0,
    event_time        TEXT,
    requires_followup INTEGER NOT NULL DEFAULT 0,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    method            TEXT NOT NULL,
    model_version     TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_seen   ON claims(last_seen);

-- Claim evidence: which records support / refute / contextualize a claim, with
-- the source tier and role so confidence + status are auditable.
CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id      TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    record_id     TEXT NOT NULL,
    section_id    TEXT,
    source_id     TEXT,
    source_tier   TEXT,
    support_label TEXT NOT NULL DEFAULT 'supports',  -- supports | refutes | context
    evidence_role TEXT,                              -- reported | official | market | physical | osint
    weight        REAL NOT NULL DEFAULT 1.0,
    record_date   TEXT,
    PRIMARY KEY (claim_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_claim_ev_record ON claim_evidence(record_id);

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

-- Rule registry: pre-registered decision rules.
--
-- Every multiple-testing correction needs to know how many hypotheses were
-- tried, and that number cannot be recovered after the fact. If a rule is
-- edited in place and its record follows it, "one rule that worked" is
-- indistinguishable from "the eighteenth variant of a rule that did not".
--
-- rule_id is derived from the CONTENT (name + canonical params + code
-- fingerprint), so a tuned threshold is a different rule with a fresh record,
-- and re-registering identical content is a no-op that cannot move the
-- timestamp. Backdating a rule to cover a trade already made is therefore
-- impossible by construction rather than by policy.
CREATE TABLE IF NOT EXISTS rule_registry (
    rule_id           TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    params_json       TEXT NOT NULL,
    code_fingerprint  TEXT,
    registered_at     TEXT NOT NULL,
    registered_by_run TEXT,
    notes             TEXT
);
CREATE INDEX IF NOT EXISTS idx_rule_name ON rule_registry(name, registered_at);

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

-- Record embeddings: 384-dim float32 vectors from a multilingual
-- sentence-transformer (paraphrase-multilingual-MiniLM-L12-v2). Drives
-- cross-language semantic search and cross-source headline dedup.
-- vector is 384 * 4 = 1536 bytes (float32, little-endian).
CREATE TABLE IF NOT EXISTS record_embeddings (
    record_id  TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
    vector     BLOB NOT NULL,
    model      TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_re_indexed ON record_embeddings(indexed_at);
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
        # SCHEMA_V1 re-runs on every open (CREATE ... IF NOT EXISTS). Once a
        # forecast table has become a view, its original CREATE INDEX lines
        # fail with "views may not be indexed" — the equivalent indexes now
        # live on the _versions table and are created by the migration.
        already = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'predictions_versions'"
        ).fetchone()
        self._conn.executescript(_schema_sql(skip_versioned_indexes=bool(already)))
        self._migrate_to_versioned()
        self._ensure_rule_columns()
        self._drop_stale_foreign_keys()

    # ------------------------------------------------------------------ #
    # Append-only forecast record
    # ------------------------------------------------------------------ #

    def _migrate_to_versioned(self) -> None:
        """Convert each forecast table into `<t>_versions` + a view named `<t>`.

        Every one of these was written with INSERT OR REPLACE against a single
        primary key, which is a full-row overwrite: a prediction's confidence
        or a bet's entry price could be rewritten by any later run. The
        pipeline also ran twice a day (daily-brief.yml triggered on push as
        well as cron), so the second run overwrote the first run's forecasts
        by construction.

        The view keeps the ORIGINAL column list, in the original order, so
        `SELECT *` returns exactly what it did before and no reader — the MCP
        server, track_record, signals, claims, graphics, site_builder — needs
        to change.

        Idempotent: presence of `<t>_versions` means the work is done.
        """
        conn = self._conn
        assert conn is not None

        for table, spec in VERSIONED_TABLES.items():
            existing = conn.execute(
                "SELECT type FROM sqlite_master WHERE name = ?", (table,)
            ).fetchone()
            already = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (f"{table}_versions",)
            ).fetchone()
            if already or existing is None or existing["type"] != "table":
                continue

            cols = list(conn.execute(f"PRAGMA table_info({table})"))
            names = [c["name"] for c in cols]

            # Rebuild the column definitions from PRAGMA rather than copying
            # the original DDL. That deliberately drops PRIMARY KEY and
            # REFERENCES clauses: `id` is now a logical key that repeats across
            # revisions, and paper_bet_marks.bet_id cannot carry a foreign key
            # to paper_bets once that name is a view.
            defs = []
            for c in cols:
                piece = f'"{c["name"]}" {c["type"] or "TEXT"}'
                if c["notnull"]:
                    piece += " NOT NULL"
                if c["dflt_value"] is not None:
                    piece += f" DEFAULT {c['dflt_value']}"
                defs.append(piece)

            col_list = ", ".join(f'"{n}"' for n in names)
            as_of_col = spec["as_of"]
            key_col = spec["key"]

            conn.executescript(f"""
                CREATE TABLE "{table}_versions" (
                    row_uid       INTEGER PRIMARY KEY AUTOINCREMENT,
                    {", ".join(defs)},
                    as_of         TEXT NOT NULL,
                    run_id        TEXT,
                    revision      INTEGER NOT NULL DEFAULT 1,
                    superseded_at TEXT
                );
                INSERT INTO "{table}_versions" ({col_list}, as_of, run_id, revision)
                    SELECT {col_list},
                           COALESCE("{as_of_col}", '1970-01-01T00:00:00Z'),
                           NULL,
                           1
                    FROM "{table}";
                DROP TABLE "{table}";
                CREATE VIEW "{table}" AS
                    SELECT {col_list} FROM "{table}_versions"
                     WHERE superseded_at IS NULL;
                CREATE INDEX IF NOT EXISTS "idx_{table}_ver_key"
                    ON "{table}_versions"("{key_col}", revision);
                CREATE INDEX IF NOT EXISTS "idx_{table}_ver_asof"
                    ON "{table}_versions"(as_of);
                CREATE INDEX IF NOT EXISTS "idx_{table}_ver_current"
                    ON "{table}_versions"(superseded_at);
            """)

    def _drop_stale_foreign_keys(self) -> None:
        """Rebuild claim_evidence without its REFERENCES claims(id) clause.

        `claims` is a view now, and SQLite reports `foreign key mismatch` on
        any insert into a table whose FK points at one. claim_evidence is not
        itself versioned (its parent claim is), so it only needs the constraint
        removed, not a version history.
        """
        conn = self._conn
        assert conn is not None
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'claim_evidence'"
        ).fetchone()
        if row is None or "REFERENCES claims" not in (row["sql"] or ""):
            return
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TABLE claim_evidence_new (
                claim_id      TEXT NOT NULL,
                record_id     TEXT NOT NULL,
                section_id    TEXT,
                source_id     TEXT,
                source_tier   TEXT,
                support_label TEXT NOT NULL DEFAULT 'supports',
                evidence_role TEXT,
                weight        REAL NOT NULL DEFAULT 1.0,
                record_date   TEXT,
                PRIMARY KEY (claim_id, record_id)
            );
            INSERT INTO claim_evidence_new SELECT
                claim_id, record_id, section_id, source_id, source_tier,
                support_label, evidence_role, weight, record_date
            FROM claim_evidence;
            DROP TABLE claim_evidence;
            ALTER TABLE claim_evidence_new RENAME TO claim_evidence;
            CREATE INDEX IF NOT EXISTS idx_claim_ev_record
                ON claim_evidence(record_id);
            PRAGMA foreign_keys = ON;
        """)

    def _append_version(self, table: str, key_value: str, payload: dict,
                        *, run_id: Optional[str] = None) -> None:
        """Supersede the current revision for `key_value`, then insert a new one.

        `as_of` is the moment the system came to hold this row, which is the
        write time -- NOT the domain time. They are different things and
        conflating them breaks point-in-time reconstruction: a prediction
        carries `made_at`, a bet carries `timestamp_bet`, and those stay in
        their own columns as domain facts. `knowledge_as_of` asks "what did the
        system believe at instant T", which only the write time can answer.

        Both statements run in one transaction: a partial application would
        leave either two current revisions or none, and the forecast record is
        the one thing in this system that must not be ambiguous.
        """
        spec = VERSIONED_TABLES[table]
        key_col = spec["key"]
        now = _utcnow_precise()
        conn = self._ensure_open()

        conn.execute("BEGIN")
        try:
            row = conn.execute(
                f'SELECT MAX(revision) AS r FROM "{table}_versions" '
                f'WHERE "{key_col}" = ?',
                (key_value,),
            ).fetchone()
            next_rev = (row["r"] or 0) + 1

            conn.execute(
                f'UPDATE "{table}_versions" SET superseded_at = ? '
                f'WHERE "{key_col}" = ? AND superseded_at IS NULL',
                (now, key_value),
            )

            payload = dict(payload)
            payload["as_of"] = now
            payload["run_id"] = run_id or process_run_id()
            payload["revision"] = next_rev

            cols = ", ".join(f'"{k}"' for k in payload)
            marks = ", ".join("?" for _ in payload)
            conn.execute(
                f'INSERT INTO "{table}_versions" ({cols}) VALUES ({marks})',
                tuple(payload.values()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _ensure_rule_columns(self) -> None:
        """Add rule_id to the versioned forecast tables and refresh the views.

        Additive: existing readers keep working, and rows written before
        pre-registration existed carry NULL, which is exactly the marker
        `preregistration_violations()` looks for.
        """
        conn = self._conn
        assert conn is not None
        for table in ("predictions", "paper_bets"):
            versions = f"{table}_versions"
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (versions,)
            ).fetchone()
            if row is None:
                continue
            cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({versions})")]
            if "rule_id" in cols:
                continue
            conn.execute(f'ALTER TABLE "{versions}" ADD COLUMN rule_id TEXT')
            cols.append("rule_id")
            public = [c for c in cols if c not in
                      ("row_uid", "as_of", "run_id", "revision", "superseded_at")]
            col_list = ", ".join(f'"{c}"' for c in public)
            conn.executescript(f"""
                DROP VIEW IF EXISTS "{table}";
                CREATE VIEW "{table}" AS
                    SELECT {col_list} FROM "{versions}"
                     WHERE superseded_at IS NULL;
            """)

    # ---- pre-registration ------------------------------------------------

    def register_rule(self, *, name: str, params: dict,
                      code_fingerprint: Optional[str] = None,
                      notes: Optional[str] = None) -> str:
        """Register a decision rule and return its content-derived id.

        Idempotent: registering identical content again returns the same id
        and leaves the original timestamp untouched. That is the whole point —
        a rule cannot be quietly backdated to cover a position already taken.
        """
        payload = json.dumps(
            {"name": name, "params": params, "code": code_fingerprint or ""},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        rule_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR IGNORE INTO rule_registry
              (rule_id, name, params_json, code_fingerprint,
               registered_at, registered_by_run, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (rule_id, name,
             json.dumps(params, sort_keys=True, default=str),
             code_fingerprint, _utcnow_precise(), process_run_id(), notes),
        )
        return rule_id

    def get_rule(self, rule_id: str) -> Optional[dict]:
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT * FROM rule_registry WHERE rule_id = ?", (rule_id,)
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        try:
            out["params"] = json.loads(out.get("params_json") or "{}")
        except json.JSONDecodeError:
            out["params"] = {}
        return out

    def rule_versions(self, name: str) -> list[dict]:
        """Every registered variant of `name`, oldest first.

        The length of this list IS `n_trials` for the deflated Sharpe ratio.
        """
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT * FROM rule_registry WHERE name = ? ORDER BY registered_at",
            (name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def trial_count(self, name: Optional[str] = None) -> int:
        """How many distinct hypotheses have been registered."""
        conn = self._ensure_open()
        if name is None:
            row = conn.execute("SELECT COUNT(*) c FROM rule_registry").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) c FROM rule_registry WHERE name = ?", (name,)
            ).fetchone()
        return int(row["c"])

    def preregistration_violations(self) -> list[dict]:
        """Bets whose rule was absent, unregistered, or registered afterwards.

        Any of the three means the position was not taken under a rule fixed in
        advance, so it cannot contribute to an out-of-sample record. Reported
        rather than blocked: the bets are real and belong in the P&L; they just
        do not count as evidence of pre-registered skill.
        """
        conn = self._ensure_open()
        rows = conn.execute(
            """
            SELECT b.id AS bet_id, b.rule_id, b.as_of, r.registered_at
              FROM paper_bets_versions b
              LEFT JOIN rule_registry r ON r.rule_id = b.rule_id
             WHERE b.superseded_at IS NULL
            """
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            if not r["rule_id"]:
                reason = "no_rule"
            elif r["registered_at"] is None:
                reason = "unregistered_rule"
            elif r["registered_at"] > r["as_of"]:
                reason = "rule_registered_after_bet"
            else:
                continue
            out.append({"bet_id": r["bet_id"], "rule_id": r["rule_id"],
                        "bet_as_of": r["as_of"],
                        "rule_registered_at": r["registered_at"],
                        "reason": reason})
        return out

    def knowledge_as_of(self, table: str, when: str) -> list[dict]:
        """What the system held to be true in `table` at instant `when`.

        The precondition for any backtest that is not contaminated by
        look-ahead: it returns the revision that was current at that moment,
        not the one that is current now.
        """
        if table not in VERSIONED_TABLES:
            raise ValueError(
                f"knowledge_as_of: {table!r} is not versioned. Versioned "
                f"tables are: {', '.join(sorted(VERSIONED_TABLES))}"
            )
        conn = self._ensure_open()
        rows = conn.execute(
            f'SELECT * FROM "{table}_versions" '
            f' WHERE as_of <= ? AND (superseded_at IS NULL OR superseded_at > ?)',
            (when, when),
        ).fetchall()
        return [dict(r) for r in rows]

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

    def record_source_run(
        self, *, source_id: str, section_id: Optional[str] = None,
        success: bool, record_count: int = 0, new_count: int = 0,
        schema_hash: Optional[str] = None, error_type: Optional[str] = None,
        error_message: Optional[str] = None, latency_ms: Optional[int] = None,
        started_at: Optional[str] = None, finished_at: Optional[str] = None,
    ) -> str:
        """Append one pull to the source_runs history. Returns the run id."""
        finished = finished_at or _utcnow()
        run_id = hashlib.sha1(
            f"{source_id}|{section_id}|{finished}".encode("utf-8")).hexdigest()
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO source_runs
              (id, source_id, section_id, started_at, finished_at, success,
               record_count, new_count, schema_hash, error_type, error_message,
               latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, source_id, section_id, started_at, finished,
             1 if success else 0, record_count, new_count, schema_hash,
             error_type, (error_message or "")[:500] or None, latency_ms),
        )
        return run_id

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
                       method: str, evidence: list[str], section_id: Optional[str],
                       run_id: Optional[str] = None,
                       rule_id: Optional[str] = None,
                       ) -> None:
        """Record a forecast. Append-only: re-recording the same prediction_id
        supersedes the prior revision rather than overwriting it, so the
        confidence stated at the time of the call survives."""
        stamped = made_at or _utcnow()
        self._append_version("predictions", prediction_id, {
            "id": prediction_id,
            "made_at": stamped,
            "target_date": target_date,
            "resolution_criteria": resolution_criteria,
            "predicted_outcome": predicted_outcome,
            "confidence": confidence,
            "training_window_days": training_window_days,
            "indicators_used_json": json.dumps(indicators_used, sort_keys=True),
            "method": method,
            "evidence_json": json.dumps(evidence, sort_keys=True),
            "section_id": section_id,
        }, run_id=run_id)

    def resolve_prediction(self, *, prediction_id: str, resolved_at: str,
                           actual_outcome: str) -> None:
        """Settle a prediction. Records the ground-truth outcome and stores the
        Brier contribution (predicted_prob - actual_prob)^2 for the scorer.

        ``actual_outcome`` is matched against ``predicted_outcome`` to derive the
        realized 0/1 truth value; the predicted probability is ``confidence``."""
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT * FROM predictions WHERE id = ?", (prediction_id,),
        ).fetchone()
        if row is None:
            return
        brier: Optional[float] = None
        try:
            actual = 1.0 if str(actual_outcome).strip().upper() == \
                str(row["predicted_outcome"]).strip().upper() else 0.0
            brier = (float(row["confidence"]) - actual) ** 2
        except (TypeError, ValueError):
            brier = None

        # Settlement is a new revision, not an in-place UPDATE. The unresolved
        # row stays queryable, which is what lets knowledge_as_of() answer
        # "was this call still open on date D" -- the question a backtest has
        # to ask. (It also has to be an append now: `predictions` is a view.)
        payload = dict(row)
        payload["resolved_at"] = resolved_at
        payload["actual_outcome"] = actual_outcome
        payload["brier_contribution"] = brier
        self._append_version("predictions", prediction_id, payload)

    def add_paper_bet(self, *, bet_id: str, market_platform: str, market_id: str,
                      market_url: Optional[str], market_question: str,
                      market_resolves_at: Optional[str], side: str,
                      size_usd: float, price_at_bet: float,
                      rationale: str, evidence: list[str], model_version: str,
                      confidence_band: str, section_id: Optional[str],
                      run_id: Optional[str] = None,
                      rule_id: Optional[str] = None) -> None:
        """Record a simulated trade. Append-only: price_at_bet and side are the
        entry terms at the moment of the call and must never be rewritten."""
        stamped = _utcnow()
        self._append_version("paper_bets", bet_id, {
            "id": bet_id,
            "market_platform": market_platform,
            "market_id": market_id,
            "market_url": market_url,
            "market_question": market_question,
            "market_resolves_at": market_resolves_at,
            "side": side,
            "size_usd": size_usd,
            "price_at_bet": price_at_bet,
            "timestamp_bet": stamped,
            "rationale": rationale,
            "evidence_json": json.dumps(evidence, sort_keys=True),
            "model_version": model_version,
            "confidence_band": confidence_band,
            "section_id": section_id,
            "rule_id": rule_id,
        }, run_id=run_id)

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
        self._append_version("paper_bet_marks", mark_id, {
            "id": mark_id,
            "bet_id": bet_id,
            "mark_date": mark_date,
            "days_since_bet": days_since_bet,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized,
            "delta_vs_prev": delta,
        })

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
        self._append_version("paper_bet_resolutions", bet_id, {
            "bet_id": bet_id,
            "resolved_at": resolved_at,
            "final_outcome": final_outcome,
            "final_pnl": final_pnl,
            "holding_period_days": holding,
        })

    def add_anomaly(self, *, anomaly_id: str, section_id: str, category: str,
                    z_score: Optional[float], description: str,
                    evidence: list[str], run_id: Optional[str] = None) -> None:
        stamped = _utcnow()
        self._append_version("anomalies", anomaly_id, {
            "id": anomaly_id,
            "section_id": section_id,
            "category": category,
            "z_score": z_score,
            "description": description,
            "evidence_json": json.dumps(evidence, sort_keys=True),
            "detected_at": stamped,
        }, run_id=run_id)

    # ---- claims (the evidence engine) -----------------------------------

    def upsert_claim(self, *, claim_key: str, claim_text: str, claim_type: str,
                     status: str, confidence: float, actors: list[str],
                     places: list[str], topics: list[str], n_sources: int,
                     n_sections: int, event_time: Optional[str] = None,
                     requires_followup: bool = False, when: Optional[str] = None,
                     method: str = "claim-extract-v1",
                     model_version: Optional[str] = None,
                     run_id: Optional[str] = None) -> str:
        """Insert or update a claim by its stable key. Preserves first_seen and
        records the previous confidence so movement is visible. Returns the id."""
        conn = self._ensure_open()
        claim_id = hashlib.sha1(claim_key.encode("utf-8")).hexdigest()
        when = when or _utcnow()
        existing = conn.execute(
            "SELECT confidence, first_seen FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        confidence_prev = existing["confidence"] if existing else None
        first_seen = existing["first_seen"] if existing else when
        # Append-only. A claim's confidence movement over days IS the signal
        # (claims.py tracks it explicitly), so overwriting the row destroyed
        # the very history the module was built to accumulate.
        self._append_version("claims", claim_id, {
            "id": claim_id,
            "claim_key": claim_key,
            "claim_text": claim_text,
            "claim_type": claim_type,
            "status": status,
            "confidence": float(confidence),
            "confidence_prev": confidence_prev,
            "actors_json": json.dumps(actors, ensure_ascii=False),
            "places_json": json.dumps(places, ensure_ascii=False),
            "topics_json": json.dumps(topics, ensure_ascii=False),
            "n_sources": n_sources,
            "n_sections": n_sections,
            "event_time": event_time,
            "requires_followup": 1 if requires_followup else 0,
            "first_seen": first_seen,
            "last_seen": when,
            "method": method,
            "model_version": model_version,
        }, run_id=run_id)
        return claim_id

    def add_claim_evidence(self, *, claim_id: str, record_id: str,
                           section_id: Optional[str] = None,
                           source_id: Optional[str] = None,
                           source_tier: Optional[str] = None,
                           support_label: str = "supports",
                           evidence_role: Optional[str] = None,
                           weight: float = 1.0,
                           record_date: Optional[str] = None) -> None:
        conn = self._ensure_open()
        conn.execute(
            """
            INSERT OR REPLACE INTO claim_evidence
              (claim_id, record_id, section_id, source_id, source_tier,
               support_label, evidence_role, weight, record_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (claim_id, record_id, section_id, source_id, source_tier,
             support_label, evidence_role, weight, record_date),
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


_PROCESS_RUN_ID: Optional[str] = None


def process_run_id() -> str:
    """Stable identifier for the pipeline run that is writing.

    Prefers GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT so a row can be traced back to the
    exact Actions run that produced it; falls back to a per-process uuid for
    local runs. Generated once and reused, so every forecast written by one run
    shares an id and `knowledge_as_of` can attribute a revision to its author.

    A NULL run_id means the row predates versioning and must not be treated as
    a pre-registered call.
    """
    global _PROCESS_RUN_ID
    if _PROCESS_RUN_ID is None:
        gh = os.environ.get("GITHUB_RUN_ID")
        if gh:
            attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
            _PROCESS_RUN_ID = f"gha:{gh}.{attempt}"
        else:
            _PROCESS_RUN_ID = f"local:{uuid.uuid4().hex[:16]}"
    return _PROCESS_RUN_ID


def _utcnow_precise() -> str:
    """Microsecond-resolution stamp for the version timeline.

    `as_of` and `superseded_at` order revisions against each other, so second
    resolution is not enough: two forecasts written in the same second would be
    indistinguishable in time and knowledge_as_of() could not reconstruct which
    was current. Sorts lexicographically alongside the second-resolution stamps
    on migrated rows, because both are zero-padded ISO-8601 UTC.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")



def _schema_sql(*, skip_versioned_indexes: bool) -> str:
    """SCHEMA_V1, optionally without the CREATE INDEX lines that target tables
    which the versioning migration has already replaced with views."""
    if not skip_versioned_indexes:
        return SCHEMA_V1
    pattern = re.compile(
        r"^CREATE INDEX[^;]*\bON\s+(" + "|".join(VERSIONED_TABLES) + r")\s*\(",
        re.IGNORECASE,
    )
    return "\n".join(
        line for line in SCHEMA_V1.splitlines() if not pattern.match(line.strip())
    )

# Tables that constitute the forecast record: what the system claimed, when,
# and at what price. Each becomes `<name>_versions` with a view at `<name>`.
#
#   key    the logical identity that repeats across revisions
#   as_of  the existing column that dates a pre-migration row
#
# claim_evidence is deliberately NOT versioned: it is a join table whose parent
# claim IS versioned, and versioning both would multiply storage without adding
# recoverable information. records/ is likewise left on upsert — its
# authoritative vintage is the on-disk lake/sections/<id>/<date>/raw.jsonl
# snapshot, which the table merely indexes.
VERSIONED_TABLES: dict[str, dict[str, str]] = {
    "predictions":           {"key": "id",     "as_of": "made_at"},
    "paper_bets":            {"key": "id",     "as_of": "timestamp_bet"},
    "paper_bet_marks":       {"key": "id",     "as_of": "mark_date"},
    "paper_bet_resolutions": {"key": "bet_id", "as_of": "resolved_at"},
    "anomalies":             {"key": "id",     "as_of": "detected_at"},
    "claims":                {"key": "id",     "as_of": "first_seen"},
}


def schema_hash_of(rows: Iterable[dict]) -> str:
    """Stable hash of the column structure of incoming rows. Used to detect
    when an upstream API has changed its response shape."""
    keys: set[str] = set()
    for row in rows:
        keys.update(row.keys() if isinstance(row, dict) else [])
    return hashlib.sha1("|".join(sorted(keys)).encode("utf-8")).hexdigest()[:12]
