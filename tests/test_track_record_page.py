"""Tests for the public Forecast Track Record page (worldscope.track_record_page).

The page's two jobs are (1) honestly render the OPEN ledger even with nothing
resolved — the verifiable-today commitment — and (2) once calls resolve, surface
skill + calibration + a head-to-head by-engine breakdown. These tests pin both,
plus the pure data-shaping helpers, HTML escaping, and the DB adapter. No
network, no chrome, no model.
"""
from datetime import date

from worldscope import track_record_page as trp


def _pred(method="signal-fusion-v1", conf=0.7, made="2026-06-10",
          target="2026-06-24", outcome="YES", actual=None, criteria=None,
          resolved_at=None, indicators=None):
    return {
        "id": f"{method}-{conf}-{made}-{target}-{actual}",
        "made_at": made,
        "target_date": target,
        "resolution_criteria": criteria or "Resolves YES if key 'iran' stays salient.",
        "predicted_outcome": outcome,
        "confidence": conf,
        "method": method,
        "indicators_used_json": indicators if indicators is not None else '["conflict"]',
        "section_id": "signals",
        "actual_outcome": actual,
        "resolved_at": resolved_at,
    }


# ---------------------------------------------------------------------------
# data shaping
# ---------------------------------------------------------------------------

def test_partition_splits_resolved_and_pending():
    rows = [_pred(actual=None), _pred(actual="YES"), _pred(actual="")]
    resolved, pending = trp.partition(rows)
    assert len(resolved) == 1
    assert len(pending) == 2          # None and empty-string both count as open


def test_method_label_maps_known_and_falls_back():
    assert "Signals" in trp.method_label("signal-fusion-v1")
    assert "Foresight" in trp.method_label("lead-lag-foresight-v1")
    assert trp.method_label("some-new-engine-v3") == "Some New Engine V3"
    assert trp.method_label(None) == "Other"


def test_subject_of_extracts_key_then_follower_then_indicator():
    assert trp.subject_of(_pred(criteria="… key 'iran' …")) == "iran"
    assert trp.subject_of(_pred(criteria="… follower 'ruble' …")) == "ruble"
    # no quoted subject → first indicator
    r = _pred(criteria="no subject here", indicators='["taiwan"]')
    assert trp.subject_of(r) == "taiwan"


def test_hit_of_matches_outcome():
    assert trp.hit_of(_pred(outcome="YES", actual="YES")) is True
    assert trp.hit_of(_pred(outcome="YES", actual="NO")) is False
    assert trp.hit_of(_pred(actual=None)) is None


def test_by_method_summary_groups_and_counts():
    rows = [
        _pred(method="signal-fusion-v1", actual="YES"),
        _pred(method="signal-fusion-v1", actual=None),
        _pred(method="lead-lag-foresight-v1", actual=None),
    ]
    summ = {s["method"]: s for s in trp.by_method_summary(rows)}
    assert summ["signal-fusion-v1"]["total"] == 2
    assert summ["signal-fusion-v1"]["resolved"] == 1
    assert summ["lead-lag-foresight-v1"]["pending"] == 1
    # sorted by volume desc → signals first
    assert trp.by_method_summary(rows)[0]["method"] == "signal-fusion-v1"


# ---------------------------------------------------------------------------
# body rendering: empty / open-only / resolved
# ---------------------------------------------------------------------------

def test_body_empty_state_is_honest():
    html = trp.build_body([], today=date(2026, 6, 21))
    assert "Forecast Track Record" in html
    assert "Open forecasts" in html
    # no skill table / calibration when nothing logged
    assert "Reliability" not in html


def test_body_open_only_shows_ledger_not_calibration():
    rows = [_pred(actual=None) for _ in range(5)]
    html = trp.build_body(rows, today=date(2026, 6, 21))
    assert "No forecast has reached its resolution date yet" in html
    assert "Open forecasts" in html
    assert "Reliability" not in html        # nothing resolved → no reliability table


def test_body_with_resolved_shows_skill_calibration_and_badges():
    # 12 resolved calls spread across confidence so calibration bins are non-empty.
    rows = []
    for i in range(12):
        conf = 0.9 if i % 2 == 0 else 0.6
        actual = "YES" if i % 3 != 0 else "NO"
        rows.append(_pred(conf=conf, actual=actual, resolved_at="2026-06-20"))
    html = trp.build_body(rows, today=date(2026, 6, 21))
    assert "By engine" in html
    assert "Reliability" in html
    assert "Brier score" in html
    assert ("✓ hit" in html) or ("✗ miss" in html)


def test_body_escapes_malicious_subject():
    evil = "<script>alert('x')</script>"
    rows = [_pred(criteria=f"… key '{evil}' …", actual=None)]
    html = trp.build_body(rows)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# brief panel
# ---------------------------------------------------------------------------

def test_panel_empty_for_no_rows():
    assert trp.render_panel([]) == ""


def test_panel_open_only_vs_resolved_wording():
    open_html = trp.render_panel([_pred(actual=None)])
    assert "still open" in open_html
    assert trp.PAGE_PATH in open_html

    resolved_html = trp.render_panel(
        [_pred(conf=0.8, actual="YES", resolved_at="2026-06-20")])
    assert "graded" in resolved_html


def test_panel_respects_base_prefix_for_link():
    html = trp.render_panel([_pred(actual=None)], base="../")
    assert f"../{trp.PAGE_PATH}" in html


# ---------------------------------------------------------------------------
# DB adapter
# ---------------------------------------------------------------------------

def test_load_predictions_reads_rows_as_dicts():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE predictions (id TEXT PRIMARY KEY, made_at TEXT, target_date TEXT, "
        "resolution_criteria TEXT, predicted_outcome TEXT, confidence REAL, "
        "training_window_days INTEGER, indicators_used_json TEXT, method TEXT, "
        "evidence_json TEXT, section_id TEXT, resolved_at TEXT, actual_outcome TEXT, "
        "brier_contribution REAL)"
    )
    conn.execute(
        "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("p1", "2026-06-10", "2026-06-24", "key 'iran'", "YES", 0.7, 7,
         '["conflict"]', "signal-fusion-v1", "[]", "signals", None, None, None),
    )
    conn.commit()
    rows = trp.load_predictions(conn)
    assert len(rows) == 1
    assert rows[0]["method"] == "signal-fusion-v1"
    assert rows[0]["confidence"] == 0.7


def test_load_predictions_missing_table_returns_empty():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    assert trp.load_predictions(conn) == []
