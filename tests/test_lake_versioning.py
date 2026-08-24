"""Append-only, point-in-time forecast record.

The lake wrote every forecast table with INSERT OR REPLACE against a
`id TEXT PRIMARY KEY`, which is a full-row overwrite:

    lake/__init__.py:604  INSERT OR REPLACE INTO predictions
    lake/__init__.py:652  INSERT OR REPLACE INTO paper_bets
    lake/__init__.py:689  INSERT OR REPLACE INTO paper_bet_marks
    lake/__init__.py:722  INSERT OR REPLACE INTO paper_bet_resolutions
    lake/__init__.py:735  INSERT OR REPLACE INTO anomalies
    lake/__init__.py:764  INSERT OR REPLACE INTO claims

A prediction's confidence, or a bet's price_at_bet and side, could be silently
rewritten by any later run. For a system whose entire claim is "this call was
made before the event", that is not a track record — it is a document that
agrees with whatever ran last.

It was not hypothetical. daily-brief.yml also triggered on push, so the
pipeline ran twice every day and the second run overwrote the first run's
forecasts by construction.

Versioning is append-only with a `_versions` table behind a view at the
original name, so every existing reader keeps working unchanged.
"""
from __future__ import annotations

import sqlite3

import pytest

from worldscope.lake import Lake, VERSIONED_TABLES


@pytest.fixture
def lake(tmp_path):
    lk = Lake.open(tmp_path / "worldscope.sqlite")
    yield lk
    lk.close()


def _add_prediction(lake, *, pid="p1", confidence=0.7, run_id="run-a"):
    lake.add_prediction(
        prediction_id=pid,
        target_date="2026-09-01",
        resolution_criteria="X happens",
        predicted_outcome="YES",
        confidence=confidence,
        training_window_days=90,
        indicators_used=["a"],
        method="signals",
        evidence=[],
        section_id="signals",
        run_id=run_id,
    )


# --------------------------------------------------------------------------- #
# Schema shape
# --------------------------------------------------------------------------- #

def test_every_forecast_table_is_versioned(lake):
    conn = lake._ensure_open()
    for table in VERSIONED_TABLES:
        rows = conn.execute(
            "SELECT type FROM sqlite_master WHERE name = ?", (f"{table}_versions",)
        ).fetchone()
        assert rows is not None, f"{table}_versions missing"
        assert rows["type"] == "table"

        view = conn.execute(
            "SELECT type FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()
        assert view is not None and view["type"] == "view", (
            f"{table} must be a view over {table}_versions so existing readers "
            f"keep working unchanged"
        )


def test_view_exposes_exactly_the_original_columns(lake):
    """`SELECT *` results must stay byte-identical for existing consumers."""
    conn = lake._ensure_open()
    view_cols = [r["name"] for r in conn.execute("PRAGMA table_info(predictions)")]
    assert "row_uid" not in view_cols
    assert "superseded_at" not in view_cols
    assert "revision" not in view_cols
    assert view_cols[0] == "id"
    assert "confidence" in view_cols


def test_versions_table_carries_the_provenance_columns(lake):
    conn = lake._ensure_open()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(predictions_versions)")}
    assert {"row_uid", "as_of", "run_id", "revision", "superseded_at"} <= cols


# --------------------------------------------------------------------------- #
# Append-only semantics
# --------------------------------------------------------------------------- #

def test_first_write_is_revision_one_and_current(lake):
    _add_prediction(lake, confidence=0.7)
    conn = lake._ensure_open()
    rows = conn.execute(
        "SELECT revision, superseded_at FROM predictions_versions WHERE id='p1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["revision"] == 1
    assert rows[0]["superseded_at"] is None


def test_rewriting_appends_a_revision_and_keeps_the_original(lake):
    _add_prediction(lake, confidence=0.7, run_id="run-a")
    _add_prediction(lake, confidence=0.2, run_id="run-b")

    conn = lake._ensure_open()
    rows = conn.execute(
        "SELECT revision, confidence, superseded_at, run_id "
        "FROM predictions_versions WHERE id='p1' ORDER BY revision"
    ).fetchall()
    assert [r["revision"] for r in rows] == [1, 2]
    assert rows[0]["confidence"] == 0.7, "the original call must survive"
    assert rows[0]["superseded_at"] is not None
    assert rows[0]["run_id"] == "run-a"
    assert rows[1]["confidence"] == 0.2
    assert rows[1]["superseded_at"] is None


def test_the_view_shows_only_the_current_revision(lake):
    _add_prediction(lake, confidence=0.7)
    _add_prediction(lake, confidence=0.2)
    conn = lake._ensure_open()
    rows = conn.execute("SELECT id, confidence FROM predictions").fetchall()
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.2


def test_paper_bets_are_versioned_too(lake):
    for price in (0.40, 0.55):
        lake.add_paper_bet(
            bet_id="b1", market_platform="polymarket", market_id="m1",
            market_url=None, market_question="q?", market_resolves_at=None,
            side="YES", size_usd=100.0, price_at_bet=price, rationale="r",
            evidence=[], model_version="v1", confidence_band="medium",
            section_id="paper_bet_placement",
        )
    conn = lake._ensure_open()
    prices = [r["price_at_bet"] for r in conn.execute(
        "SELECT price_at_bet FROM paper_bets_versions WHERE id='b1' ORDER BY revision"
    )]
    assert prices == [0.40, 0.55], "the entry price at the time of the call must survive"


# --------------------------------------------------------------------------- #
# knowledge_as_of
# --------------------------------------------------------------------------- #

def test_knowledge_as_of_returns_the_state_at_a_past_instant(lake):
    _add_prediction(lake, confidence=0.7, run_id="run-a")
    conn = lake._ensure_open()
    t1 = conn.execute(
        "SELECT as_of FROM predictions_versions WHERE revision=1"
    ).fetchone()["as_of"]

    _add_prediction(lake, confidence=0.2, run_id="run-b")

    then = lake.knowledge_as_of("predictions", t1)
    assert len(then) == 1
    assert then[0]["confidence"] == 0.7

    now = lake.knowledge_as_of("predictions", "2999-01-01T00:00:00Z")
    assert now[0]["confidence"] == 0.2


def test_knowledge_as_of_excludes_rows_written_after_the_instant(lake):
    _add_prediction(lake, pid="early")
    conn = lake._ensure_open()
    t = conn.execute(
        "SELECT as_of FROM predictions_versions WHERE id='early'"
    ).fetchone()["as_of"]
    _add_prediction(lake, pid="later")

    ids = {r["id"] for r in lake.knowledge_as_of("predictions", t)}
    assert ids == {"early"}, "a backtest must not see rows written after its as-of"


def test_knowledge_as_of_rejects_an_unversioned_table(lake):
    with pytest.raises(ValueError):
        lake.knowledge_as_of("records", "2026-08-24T00:00:00Z")


# --------------------------------------------------------------------------- #
# Migration from the pre-versioning schema
# --------------------------------------------------------------------------- #

def test_migration_preserves_existing_rows(tmp_path):
    """Rows written before versioning must survive, marked as pre-migration."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE predictions (
            id TEXT PRIMARY KEY, made_at TEXT NOT NULL, target_date TEXT,
            resolution_criteria TEXT NOT NULL, predicted_outcome TEXT NOT NULL,
            confidence REAL NOT NULL, training_window_days INTEGER,
            indicators_used_json TEXT NOT NULL DEFAULT '[]',
            method TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]',
            section_id TEXT, resolved_at TEXT, actual_outcome TEXT,
            brier_contribution REAL
        );
        INSERT INTO predictions (id, made_at, resolution_criteria,
                                 predicted_outcome, confidence, method)
        VALUES ('legacy-1', '2026-07-01T00:00:00Z', 'crit', 'YES', 0.6, 'signals');
    """)
    conn.commit()
    conn.close()

    lk = Lake.open(db)
    try:
        rows = lk._ensure_open().execute(
            "SELECT id, confidence, revision, run_id, as_of "
            "FROM predictions_versions WHERE id='legacy-1'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["confidence"] == 0.6
        assert rows[0]["revision"] == 1
        # The marker that this row predates immutability and must not be
        # treated as a pre-registered call.
        assert rows[0]["run_id"] is None
        assert rows[0]["as_of"] == "2026-07-01T00:00:00Z"
    finally:
        lk.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "w.sqlite"
    lk = Lake.open(db)
    _add_prediction(lk)
    lk.close()

    for _ in range(3):
        lk = Lake.open(db)
        n = lk._ensure_open().execute(
            "SELECT COUNT(*) c FROM predictions_versions"
        ).fetchone()["c"]
        lk.close()
        assert n == 1, "re-opening must not duplicate or re-migrate rows"


def test_pre_migration_rows_are_identifiable(lake):
    """run_id IS NULL is the boundary marker for the honest track record."""
    _add_prediction(lake, run_id="run-a")
    conn = lake._ensure_open()
    n = conn.execute(
        "SELECT COUNT(*) c FROM predictions_versions WHERE run_id IS NULL"
    ).fetchone()["c"]
    assert n == 0


# --------------------------------------------------------------------------- #
# Run provenance
# --------------------------------------------------------------------------- #

def test_every_new_row_is_attributed_to_a_run(lake):
    """run_id is the marker separating the honest record from the old one."""
    from worldscope.lake import process_run_id
    _add_prediction(lake, run_id=None)
    conn = lake._ensure_open()
    rid = conn.execute(
        "SELECT run_id FROM predictions_versions WHERE id='p1'"
    ).fetchone()["run_id"]
    assert rid is not None
    assert rid == process_run_id()


def test_run_id_prefers_the_actions_run(monkeypatch):
    import worldscope.lake as lk
    monkeypatch.setattr(lk, "_PROCESS_RUN_ID", None)
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert lk.process_run_id() == "gha:12345.2"


def test_run_id_is_stable_within_a_process(monkeypatch):
    import worldscope.lake as lk
    monkeypatch.setattr(lk, "_PROCESS_RUN_ID", None)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert lk.process_run_id() == lk.process_run_id()


def test_resolution_appends_rather_than_mutating(lake):
    """A settled prediction must not erase the unresolved row: a backtest has
    to be able to ask whether the call was still open on a given date."""
    _add_prediction(lake, confidence=0.8)
    lake.resolve_prediction(
        prediction_id="p1", resolved_at="2026-09-02T00:00:00Z", actual_outcome="YES")

    conn = lake._ensure_open()
    rows = conn.execute(
        "SELECT revision, resolved_at, brier_contribution "
        "FROM predictions_versions WHERE id='p1' ORDER BY revision"
    ).fetchall()
    assert [r["revision"] for r in rows] == [1, 2]
    assert rows[0]["resolved_at"] is None, "the open call must remain queryable"
    assert rows[1]["resolved_at"] == "2026-09-02T00:00:00Z"
    assert rows[1]["brier_contribution"] == pytest.approx((0.8 - 1.0) ** 2)


def test_the_two_runs_a_day_bug_no_longer_destroys_the_record(lake):
    """The exact production failure: daily-brief ran on cron AND on push, so a
    second run overwrote the first run's forecasts every single day."""
    _add_prediction(lake, pid="d1", confidence=0.75, run_id="gha:cron")
    conn = lake._ensure_open()
    t_after_first = conn.execute(
        "SELECT as_of FROM predictions_versions WHERE id='d1'"
    ).fetchone()["as_of"]

    _add_prediction(lake, pid="d1", confidence=0.30, run_id="gha:push")

    at_the_time = lake.knowledge_as_of("predictions", t_after_first)
    assert len(at_the_time) == 1
    assert at_the_time[0]["confidence"] == 0.75
    assert at_the_time[0]["run_id"] == "gha:cron"
