"""Required post-section stage failures must remain machine-readable."""
from __future__ import annotations

import json

import pytest

from worldscope import brief


def _contracts():
    assert hasattr(brief, "REQUIRED_STAGES"), "required-stage contract is missing"
    return brief.REQUIRED_STAGES, brief._run_stage


def test_successful_stage_is_recorded_ok():
    _, run_stage = _contracts()
    report: dict = {}
    run_stage("graphics", lambda: None, report)
    assert report["graphics"]["status"] == "ok"
    assert report["graphics"]["required"] is True
    assert report["graphics"]["duration_ms"] >= 0


def test_import_error_is_fatal_not_swallowed():
    _, run_stage = _contracts()
    def boom():
        raise ImportError("No module named 'matplotlib'")
    report: dict = {}
    with pytest.raises(ImportError):
        run_stage("graphics", boom, report)
    assert report["graphics"]["status"] == "failed"
    assert report["graphics"]["error_type"] == "ImportError"


def test_runtime_error_is_recorded_but_survived():
    _, run_stage = _contracts()
    def boom():
        raise ValueError("upstream returned nonsense")
    report: dict = {}
    run_stage("radar", boom, report)
    assert report["radar"]["status"] == "failed"
    assert report["radar"]["required"] is False


def test_required_and_optional_stages_are_distinguished():
    required_stages, _ = _contracts()
    for name in ("graphics", "maps", "signals", "claims", "site-builder"):
        assert name in required_stages
    for name in ("embeddings", "ukraine-maps", "radar", "stories"):
        assert name not in required_stages


def test_error_message_is_bounded_and_report_is_json_serialisable():
    _, run_stage = _contracts()
    report: dict = {}
    run_stage("radar", lambda: (_ for _ in ()).throw(ValueError("x" * 5000)), report)
    blob = json.loads(json.dumps(report))
    assert len(blob["radar"]["error_message"]) <= 500
