"""Publish and validate dated daily-pipeline readiness manifests."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


SCHEMA_VERSION = 1
PRODUCER = "worldscope-daily-brief"
DEFAULT_MAX_AGE_HOURS = 6

# Sources whose absence makes a brief materially worse. A stale one never
# blocks readiness (the bundle is still real), but it must be named so the
# consumer can say so in the brief instead of calling it a quiet day.
REQUIRED_SOURCES = frozenset({
    "acled", "cisa_kev", "conflict", "federal_register", "firms", "forecasts",
    "gdacs", "gdelt_gkg", "macro", "markets", "political_figures", "reliefweb",
    "ukraine_theater", "usgs_quakes",
})
MAX_STALE_DAYS = 3


class ReadinessError(RuntimeError):
    """The requested daily artifact is absent, stale, or incomplete."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ReadinessError(f"invalid data date: {value}") from exc


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ReadinessError(f"{label} missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReadinessError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} must be a JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _source_health(report: Mapping[str, Any]) -> dict[str, Any]:
    sections = report.get("sections")
    if not isinstance(sections, Mapping):
        raise ReadinessError("run report sections are missing")
    counts: dict[str, int] = {}
    by_state = {"carry_forward": [], "stale_after_failure": [], "no_data": []}
    failed: list[str] = []
    source_dates: dict[str, Any] = {}
    for section_id, entry in sorted(sections.items()):
        if not isinstance(section_id, str) or not isinstance(entry, Mapping):
            raise ReadinessError("run report section entry is invalid")
        state = entry.get("state")
        if not isinstance(state, str) or not state:
            raise ReadinessError(f"run report section state missing: {section_id}")
        counts[state] = counts.get(state, 0) + 1
        if state in by_state:
            by_state[state].append(section_id)
        if entry.get("error_type"):
            failed.append(section_id)
        source_dates[section_id] = entry.get("source_date")
    return {
        "counts_by_state": dict(sorted(counts.items())),
        "carry_forward": by_state["carry_forward"],
        "stale_after_failure": by_state["stale_after_failure"],
        "no_data": by_state["no_data"],
        "failed_sections": failed,
        "source_dates": source_dates,
    }


def degradation(source_health: Mapping[str, Any], data_date: str, *,
                required: frozenset[str] = REQUIRED_SOURCES,
                max_stale_days: int = MAX_STALE_DAYS) -> dict[str, Any]:
    """Name every required source that is missing or older than the threshold."""
    day = date.fromisoformat(_parse_day(data_date))
    source_dates = source_health.get("source_dates") or {}
    stale: dict[str, Any] = {}
    reasons: list[str] = []
    for section_id in sorted(required):
        if section_id not in source_dates:
            stale[section_id] = {"source_date": None, "stale_days": None}
            reasons.append(f"{section_id}: not in run report")
            continue
        value = source_dates.get(section_id)
        if not isinstance(value, str) or not value:
            stale[section_id] = {"source_date": None, "stale_days": None}
            reasons.append(f"{section_id}: no data")
            continue
        try:
            age = (day - date.fromisoformat(value)).days
        except ValueError:
            stale[section_id] = {"source_date": value, "stale_days": None}
            reasons.append(f"{section_id}: unparseable source_date {value!r}")
            continue
        if age > max_stale_days:
            stale[section_id] = {"source_date": value, "stale_days": age}
            reasons.append(f"{section_id}: last good data {value} ({age}d old)")
    return {
        "degraded": bool(reasons),
        "degraded_reasons": reasons,
        "stale_required_sources": stale,
        "required_sources": sorted(required),
        "max_stale_days": max_stale_days,
    }


def publish_daily_ready(dist: Path, data_date: str, *, repository: str = "",
                        run_id: str = "", run_attempt: str = "",
                        commit_sha: str = "", now: datetime | None = None) -> Path:
    """Write readiness only for a successful same-date non-empty bundle."""
    dist = Path(dist)
    data_date = _parse_day(data_date)
    report_path = dist / "run_report.json"
    report = _load_object(report_path, "run report")
    if report.get("date") != data_date:
        raise ReadinessError(
            f"run report date {report.get('date')!r} does not match {data_date}"
        )
    failed = report.get("failed_required_stages")
    if report.get("ok") is not True or failed != []:
        raise ReadinessError(f"required stages failed: {failed!r}")
    generated_at = report.get("generated_at")
    generated_time = _parse_generated_at(generated_at)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if generated_time > current_time + timedelta(minutes=5):
        raise ReadinessError("generated_at is in the future")
    bundle_path = dist / "zips" / f"{data_date}.zip"
    if not bundle_path.is_file() or bundle_path.stat().st_size <= 0:
        raise ReadinessError(f"dated bundle missing or empty: {bundle_path}")
    source_health = _source_health(report)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "producer": PRODUCER,
        "status": "ready",
        "data_date": data_date,
        "generated_at": generated_at,
        "bundle": {"path": f"zips/{data_date}.zip",
                   "sha256": _sha256(bundle_path),
                   "bytes": bundle_path.stat().st_size},
        "run_report": {"path": "run_report.json", "sha256": _sha256(report_path),
                       "failed_required_stages": []},
        "source_health": source_health,
        "degradation": degradation(source_health, data_date),
        "github": {"repository": repository, "run_id": run_id,
                   "run_attempt": run_attempt, "sha": commit_sha},
    }
    output = dist / "status" / "daily" / f"{data_date}.json"
    _atomic_json(output, manifest)
    _atomic_json(dist / "status" / "daily" / "latest.json", manifest)
    return output


def _parse_generated_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ReadinessError("generated_at is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadinessError("generated_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ReadinessError("generated_at has no timezone")
    return parsed.astimezone(timezone.utc)


def validate_daily_ready(manifest: Mapping[str, Any], *, expected_date: str,
                         now: datetime | None = None,
                         max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> Mapping[str, Any]:
    expected_date = _parse_day(expected_date)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ReadinessError("unsupported schema_version")
    if manifest.get("producer") != PRODUCER:
        raise ReadinessError("unexpected producer")
    if manifest.get("status") != "ready":
        raise ReadinessError("status is not ready")
    if manifest.get("data_date") != expected_date:
        raise ReadinessError(
            f"data_date {manifest.get('data_date')!r} does not match {expected_date}"
        )
    generated_at = _parse_generated_at(manifest.get("generated_at"))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = now - generated_at
    if age < timedelta(minutes=-5):
        raise ReadinessError("generated_at is in the future")
    if age > timedelta(hours=max_age_hours):
        raise ReadinessError(f"manifest is older than {max_age_hours} hours")
    report = manifest.get("run_report")
    if not isinstance(report, Mapping) or report.get("failed_required_stages") != []:
        raise ReadinessError("run report has failed required stages")
    source_health = manifest.get("source_health")
    if (not isinstance(source_health, Mapping)
            or not isinstance(source_health.get("counts_by_state"), Mapping)
            or not isinstance(source_health.get("source_dates"), Mapping)):
        raise ReadinessError("source_health is missing or invalid")
    bundle = manifest.get("bundle")
    if not isinstance(bundle, Mapping):
        raise ReadinessError("bundle metadata is missing")
    if bundle.get("path") != f"zips/{expected_date}.zip":
        raise ReadinessError("bundle path does not match data_date")
    digest = bundle.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ReadinessError("bundle sha256 is invalid")
    size = bundle.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ReadinessError("bundle byte count is invalid")
    return manifest


def degraded_reasons(manifest: Mapping[str, Any]) -> list[str]:
    """Reasons the producer flagged, recomputed for manifests that predate the field."""
    block = manifest.get("degradation")
    if isinstance(block, Mapping) and isinstance(block.get("degraded_reasons"), list):
        return [str(item) for item in block["degraded_reasons"]]
    source_health = manifest.get("source_health")
    data_date = manifest.get("data_date")
    if not isinstance(source_health, Mapping) or not isinstance(data_date, str):
        return []
    try:
        return degradation(source_health, data_date)["degraded_reasons"]
    except ReadinessError:
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish or validate daily readiness")
    sub = parser.add_subparsers(dest="command", required=True)
    publish = sub.add_parser("publish-daily")
    publish.add_argument("--dist", type=Path, default=Path("dist"))
    publish.add_argument("--date", required=True)
    publish.add_argument("--repository", default="")
    publish.add_argument("--run-id", default="")
    publish.add_argument("--run-attempt", default="")
    publish.add_argument("--sha", default="")
    check = sub.add_parser("check-daily")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--date", required=True)
    check.add_argument("--max-age-hours", type=int, default=DEFAULT_MAX_AGE_HOURS)
    args = parser.parse_args(argv)
    try:
        if args.command == "publish-daily":
            print(publish_daily_ready(args.dist, args.date,
                                      repository=args.repository, run_id=args.run_id,
                                      run_attempt=args.run_attempt,
                                      commit_sha=args.sha))
        else:
            manifest = validate_daily_ready(
                _load_object(args.manifest, "readiness manifest"),
                expected_date=args.date, max_age_hours=args.max_age_hours)
            for reason in degraded_reasons(manifest):
                print(f"::warning::degraded source: {reason}")
            print(f"ready: {args.date}")
    except ReadinessError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
