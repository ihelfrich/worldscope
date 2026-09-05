"""Dated readiness artifacts are the daily producer/consumer boundary."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import json

import pytest


def _readiness_module():
    try:
        return importlib.import_module("worldscope.readiness")
    except ModuleNotFoundError:
        pytest.fail("worldscope.readiness is not implemented")


def _write_successful_run(dist, *, day="2026-09-05",
                          generated_at="2026-09-05T08:10:00Z"):
    report = {
        "schema": 1, "generated_at": generated_at, "date": day,
        "failed_required_stages": [], "ok": True,
        "sections": {
            "gdacs": {"state": "fresh", "source_date": day, "error_type": None},
            "markets": {"state": "carry_forward", "source_date": "2026-09-04",
                        "error_type": None},
            "acled": {"state": "stale_after_failure", "source_date": "2026-09-03",
                      "error_type": "TimeoutError"},
            "promed": {"state": "no_data", "source_date": None,
                       "error_type": "UpstreamHTTPError"},
        },
    }
    (dist / "run_report.json").write_text(json.dumps(report))
    zips = dist / "zips"
    zips.mkdir()
    (zips / f"{day}.zip").write_bytes(b"controlled daily bundle")


def test_publish_refuses_failed_required_stage(tmp_path):
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist)
    report_path = dist / "run_report.json"
    report = json.loads(report_path.read_text())
    report.update(ok=False, failed_required_stages=["warehouse"])
    report_path.write_text(json.dumps(report))
    with pytest.raises(readiness.ReadinessError, match="required stages failed"):
        readiness.publish_daily_ready(dist, "2026-09-05")
    assert not (dist / "status/daily/2026-09-05.json").exists()


def test_publish_refuses_bundle_from_different_date(tmp_path):
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist, day="2026-09-04")
    with pytest.raises(readiness.ReadinessError, match="run report date"):
        readiness.publish_daily_ready(dist, "2026-09-05")
    assert not (dist / "status/daily/2026-09-05.json").exists()


def test_publish_refuses_malformed_generated_at(tmp_path):
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist, generated_at="not-a-timestamp")
    with pytest.raises(readiness.ReadinessError, match="generated_at is invalid"):
        readiness.publish_daily_ready(dist, "2026-09-05")
    assert not (dist / "status/daily/2026-09-05.json").exists()


def test_publish_refuses_future_generated_at(tmp_path):
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist, generated_at="2026-09-05T09:10:01Z")
    with pytest.raises(readiness.ReadinessError, match="generated_at is in the future"):
        readiness.publish_daily_ready(
            dist, "2026-09-05",
            now=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
        )
    assert not (dist / "status/daily/2026-09-05.json").exists()


def test_publish_writes_hash_bound_dated_manifest(tmp_path):
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist)
    path = readiness.publish_daily_ready(
        dist, "2026-09-05", repository="ihelfrich/worldscope",
        run_id="33962276791", run_attempt="1", commit_sha="a" * 40,
    )
    manifest = json.loads(path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["producer"] == "worldscope-daily-brief"
    assert manifest["status"] == "ready"
    assert manifest["data_date"] == "2026-09-05"
    assert manifest["bundle"] == {
        "path": "zips/2026-09-05.zip",
        "sha256": "43b56f931f050590dfbfcb89c688ad7227b97fd818a971f1605c890c7c4faf8e",
        "bytes": 23,
    }
    assert manifest["run_report"]["failed_required_stages"] == []
    assert manifest["source_health"] == {
        "counts_by_state": {
            "carry_forward": 1,
            "fresh": 1,
            "no_data": 1,
            "stale_after_failure": 1,
        },
        "carry_forward": ["markets"],
        "stale_after_failure": ["acled"],
        "no_data": ["promed"],
        "failed_sections": ["acled", "promed"],
        "source_dates": {
            "acled": "2026-09-03",
            "gdacs": "2026-09-05",
            "markets": "2026-09-04",
            "promed": None,
        },
    }
    assert manifest["github"]["run_id"] == "33962276791"


def test_consumer_gate_rejects_yesterdays_manifest():
    readiness = _readiness_module()
    manifest = {
        "schema_version": 1, "producer": "worldscope-daily-brief",
        "status": "ready", "data_date": "2026-09-04",
        "generated_at": "2026-09-05T08:10:00Z",
        "bundle": {"path": "zips/2026-09-04.zip", "sha256": "a" * 64,
                   "bytes": 10},
        "run_report": {"failed_required_stages": []},
    }
    with pytest.raises(readiness.ReadinessError, match="data_date"):
        readiness.validate_daily_ready(
            manifest, expected_date="2026-09-05",
            now=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
        )


def test_consumer_gate_rejects_stale_manifest():
    readiness = _readiness_module()
    manifest = {
        "schema_version": 1, "producer": "worldscope-daily-brief",
        "status": "ready", "data_date": "2026-09-05",
        "generated_at": "2026-09-05T01:00:00Z",
        "bundle": {"path": "zips/2026-09-05.zip", "sha256": "a" * 64,
                   "bytes": 10},
        "run_report": {"failed_required_stages": []},
    }
    with pytest.raises(readiness.ReadinessError, match="older than 6"):
        readiness.validate_daily_ready(
            manifest, expected_date="2026-09-05",
            now=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
        )


def test_consumer_gate_requires_explicit_source_health():
    readiness = _readiness_module()
    manifest = {
        "schema_version": 1, "producer": "worldscope-daily-brief",
        "status": "ready", "data_date": "2026-09-05",
        "generated_at": "2026-09-05T08:10:00Z",
        "bundle": {"path": "zips/2026-09-05.zip", "sha256": "a" * 64,
                   "bytes": 10},
        "run_report": {"failed_required_stages": []},
    }
    with pytest.raises(readiness.ReadinessError, match="source_health"):
        readiness.validate_daily_ready(
            manifest, expected_date="2026-09-05",
            now=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
        )
