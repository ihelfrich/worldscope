"""Stage failure must be visible, and a missing dependency must be fatal.

`_run_stage` caught every exception and printed. daily-brief.yml installed the
bare package, so five of eleven stages -- embeddings, graphics, maps,
ukraine-maps and the DuckDB warehouse -- ImportError'd on their lazy imports
every single run and disappeared into a log line.

The result was 100 green Actions runs out of the last 100 while
worldscope/graphics.py, at 1,330 lines the largest module in the repository,
had never produced a chart in production. A cloud routine was redrawing those
charts by hand every morning instead.
"""
from __future__ import annotations

import json

import pytest

from worldscope.brief import REQUIRED_STAGES, _run_stage


def test_a_successful_stage_is_recorded_ok():
    report: dict = {}
    _run_stage("graphics", lambda: None, report)
    assert report["graphics"]["status"] == "ok"
    assert report["graphics"]["required"] is True
    assert report["graphics"]["duration_ms"] >= 0


def test_import_error_is_fatal_not_swallowed():
    """A missing dependency is a deployment defect. It must stop the run."""
    def boom():
        raise ImportError("No module named 'matplotlib'")

    report: dict = {}
    with pytest.raises(ImportError):
        _run_stage("graphics", boom, report)
    assert report["graphics"]["status"] == "failed"
    assert report["graphics"]["error_type"] == "ImportError"


def test_module_not_found_is_also_fatal():
    def boom():
        raise ModuleNotFoundError("No module named 'fiona'")

    with pytest.raises(ModuleNotFoundError):
        _run_stage("maps", boom, {})


def test_a_runtime_error_is_recorded_but_survived():
    """A flaky upstream must never cost the day's brief."""
    def boom():
        raise ValueError("upstream returned nonsense")

    report: dict = {}
    _run_stage("radar", boom, report)          # does not raise
    assert report["radar"]["status"] == "failed"
    assert report["radar"]["error_type"] == "ValueError"
    assert report["radar"]["required"] is False


def test_required_and_optional_stages_are_distinguished():
    for name in ("graphics", "maps", "signals", "claims", "scorecard"):
        assert name in REQUIRED_STAGES
    for name in ("embeddings", "ukraine-maps", "radar", "stories"):
        assert name not in REQUIRED_STAGES, (
            f"{name} is optional pending a week of observed behaviour"
        )


def test_error_message_is_truncated_not_unbounded():
    def boom():
        raise ValueError("x" * 5000)

    report: dict = {}
    _run_stage("radar", boom, report)
    assert len(report["radar"]["error_message"]) <= 500


def test_report_is_json_serialisable():
    report: dict = {}
    _run_stage("signals", lambda: None, report)
    _run_stage("radar", lambda: (_ for _ in ()).throw(RuntimeError("nope")), report)
    blob = json.loads(json.dumps(report))
    assert blob["signals"]["status"] == "ok"
    assert blob["radar"]["status"] == "failed"


def test_stage_report_survives_a_stage_that_raises_after_partial_work():
    """finally: must record the entry even on an unusual exit path."""
    def half():
        raise KeyboardInterrupt("interrupted")

    report: dict = {}
    with pytest.raises(KeyboardInterrupt):
        _run_stage("claims", half, report)
    assert "claims" in report
