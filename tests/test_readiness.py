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


def _write_successful_run(dist, *, day="2026-09-05", generated_at="2026-09-05T08:10:00Z"):
    report = {
        "schema": 1,
        "generated_at": generated_at,
        "date": day,
        "failed_required_stages": [],
        "ok": True,
    }
    (dist / "run_report.json").write_text(json.dumps(report))
    zips = dist / "zips"
    zips.mkdir()
    (zips / f"{day}.zip").write_bytes(b"controlled daily bundle")


def test_publish_refuses_failed_required_stage(tmp_path):
    """Ignoring run_report.ok would advertise a degraded build as ready."""
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


def test_publish_refuses_bundle_from_a_different_date(tmp_path):
    """Using the newest bundle instead of the requested date breaks point-in-time data."""
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist, day="2026-09-04")

    with pytest.raises(readiness.ReadinessError, match="run report date"):
        readiness.publish_daily_ready(dist, "2026-09-05")

    assert not (dist / "status/daily/2026-09-05.json").exists()


def test_publish_writes_hash_bound_dated_manifest(tmp_path):
    """Dropping the bundle hash, byte count, or date makes the gate unverifiable."""
    readiness = _readiness_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_successful_run(dist)

    path = readiness.publish_daily_ready(
        dist,
        "2026-09-05",
        repository="ihelfrich/worldscope",
        run_id="33962276791",
        run_attempt="1",
        commit_sha="a" * 40,
    )

    manifest = json.loads(path.read_text())
    assert manifest == {
        "schema_version": 1,
        "producer": "worldscope-daily-brief",
        "status": "ready",
        "data_date": "2026-09-05",
        "generated_at": "2026-09-05T08:10:00Z",
        "bundle": {
            "path": "zips/2026-09-05.zip",
            "sha256": "43b56f931f050590dfbfcb89c688ad7227b97fd818a971f1605c890c7c4faf8e",
            "bytes": 23,
        },
        "run_report": {
            "path": "run_report.json",
            "sha256": "01804252eea4aa1ed2b624e859a4a108f7ee0e35ac063da92a9406cca4096a0c",
            "failed_required_stages": [],
        },
        "github": {
            "repository": "ihelfrich/worldscope",
            "run_id": "33962276791",
            "run_attempt": "1",
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
    }


def test_consumer_gate_rejects_yesterdays_manifest(tmp_path):
    """A latest-file fallback would let stale source data masquerade as today's."""
    readiness = _readiness_module()
    manifest = {
        "schema_version": 1,
        "producer": "worldscope-daily-brief",
        "status": "ready",
        "data_date": "2026-09-04",
        "generated_at": "2026-09-05T08:10:00Z",
        "bundle": {"path": "zips/2026-09-04.zip", "sha256": "a" * 64, "bytes": 10},
        "run_report": {"failed_required_stages": []},
    }

    with pytest.raises(readiness.ReadinessError, match="data_date"):
        readiness.validate_daily_ready(
            manifest,
            expected_date="2026-09-05",
            now=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
        )


def test_consumer_gate_rejects_stale_manifest(tmp_path):
    """Checking only the date would accept an old or replayed same-date artifact."""
    readiness = _readiness_module()
    manifest = {
        "schema_version": 1,
        "producer": "worldscope-daily-brief",
        "status": "ready",
        "data_date": "2026-09-05",
        "generated_at": "2026-09-05T01:00:00Z",
        "bundle": {"path": "zips/2026-09-05.zip", "sha256": "a" * 64, "bytes": 10},
        "run_report": {"failed_required_stages": []},
    }

    with pytest.raises(readiness.ReadinessError, match="older than 6"):
        readiness.validate_daily_ready(
            manifest,
            expected_date="2026-09-05",
            now=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
        )
