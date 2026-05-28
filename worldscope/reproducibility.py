"""Build provenance data for the per-brief reproducibility page."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


def _today_iso(today: date | None = None) -> str:
    return (today or date.today()).isoformat()


def _git(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "unknown"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _load_payload(raw: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _state_from_snapshot(payload: dict[str, Any] | None, carried: bool) -> str:
    if carried:
        return "carried"
    if not payload:
        return "no_data"
    status = payload.get("status")
    items = payload.get("items") or []
    if status == "ok":
        return "fresh" if items else "empty_ok"
    if status in {"empty_ok", "failed"}:
        return status
    return str(status or "no_data")


def _tier_for(section_id: str, payload: dict[str, Any] | None,
              source_tiers: dict[str, str]) -> str:
    if source_tiers.get(section_id):
        return source_tiers[section_id]
    if payload and payload.get("source_tier"):
        return str(payload["source_tier"])
    for item in (payload or {}).get("items") or []:
        if isinstance(item, dict) and item.get("source_tier"):
            return str(item["source_tier"])
    return "unknown"


def source_pull_rows(store_path: Path, today: date | None = None, *,
                     source_tiers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return one reproducibility row per known section in the snapshot store."""
    iso = _today_iso(today)
    tiers = source_tiers or {}
    section_ids = set(tiers)
    if not store_path.exists():
        return [
            {
                "section_id": sid,
                "state": "no_data",
                "items_today": 0,
                "items_yesterday": 0,
                "pulled_at": "",
                "error": "",
                "source_tier": tier or "unknown",
            }
            for sid, tier in sorted(tiers.items())
        ]

    conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT section_id FROM snapshots").fetchall()
        section_ids.update(r[0] for r in rows)
        out: list[dict[str, Any]] = []
        for sid in sorted(section_ids):
            today_row = conn.execute(
                "SELECT payload FROM snapshots WHERE section_id = ? AND snapshot_date = ?",
                (sid, iso),
            ).fetchone()
            prev_row = conn.execute(
                "SELECT snapshot_date, payload FROM snapshots "
                "WHERE section_id = ? AND snapshot_date < ? "
                "ORDER BY snapshot_date DESC LIMIT 1",
                (sid, iso),
            ).fetchone()
            today_payload = _load_payload(today_row[0]) if today_row else None
            prev_payload = _load_payload(prev_row[1]) if prev_row else None
            carried = today_payload is None and prev_payload is not None
            active_payload = prev_payload if carried else today_payload
            out.append({
                "section_id": sid,
                "state": _state_from_snapshot(today_payload, carried),
                "items_today": len((active_payload or {}).get("items") or []),
                "items_yesterday": len((prev_payload or {}).get("items") or []),
                "pulled_at": str((active_payload or {}).get("pulled_at") or ""),
                "error": str((active_payload or {}).get("error") or ""),
                "source_tier": _tier_for(sid, active_payload, tiers),
            })
        return out
    finally:
        conn.close()


def fact_check_summary(out_dir: Path) -> dict[str, Any]:
    claims = _read_json(out_dir / "data" / "claims.json", {})
    summary = claims.get("summary") if isinstance(claims, dict) else None
    if not isinstance(summary, dict):
        summary = {"total": 0, "verified": 0, "divergent": 0, "unverified": 0, "skipped": 0}
    return {
        "summary": {
            "total": int(summary.get("total") or 0),
            "verified": int(summary.get("verified") or 0),
            "divergent": int(summary.get("divergent") or 0),
            "unverified": int(summary.get("unverified") or 0),
            "skipped": int(summary.get("skipped") or 0),
        },
        "generator_version": str(claims.get("generator_version") or "unknown") if isinstance(claims, dict) else "unknown",
    }


def lake_stats(repo_root: Path, out_dir: Path, today: date | None = None) -> dict[str, int]:
    iso = _today_iso(today)
    db = repo_root / "lake" / "db" / "worldscope.sqlite"
    stats = {"records": 0, "entities": 0, "relationships": 0, "cross_section_signals": 0}
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for table in ("records", "entities", "relationships"):
                try:
                    stats[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.Error:
                    stats[table] = 0
        finally:
            conn.close()
    else:
        today_doc = _read_json(out_dir / "data" / "today.json", {})
        entities_doc = _read_json(out_dir / "data" / "entities.json", {})
        stats["records"] = int((today_doc or {}).get("exported_records") or 0)
        stats["entities"] = len((entities_doc or {}).get("entities") or [])

    signals = _read_json(out_dir / "data" / "signals.json", {})
    if isinstance(signals, dict):
        if isinstance(signals.get("entities"), list):
            stats["cross_section_signals"] = len(signals["entities"])
        else:
            by_conf = signals.get("by_confidence") or {}
            stats["cross_section_signals"] = sum(len(by_conf.get(k) or []) for k in ("high", "medium", "low"))
    return stats


def _sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 128), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def artifact_rows(repo_root: Path, out_dir: Path, today: date | None = None) -> list[dict[str, Any]]:
    iso = _today_iso(today)
    candidates: list[Path] = []
    if out_dir.exists():
        for path in out_dir.rglob("*"):
            if path.is_file() and (
                iso in path.name
                or path.name in {
                    "index.html",
                    "claims.json",
                    "today.json",
                    "entities.json",
                    "signals.json",
                    "figures.json",
                    "graph.json",
                    "threads.json",
                }
            ):
                candidates.append(path)

    explicit = [
        out_dir / f"{iso}.html",
        out_dir / "index.html",
        out_dir / "briefings" / f"{iso}.html",
        out_dir / "zips" / f"{iso}.zip",
        out_dir / "data" / "claims.json",
        out_dir / "data" / "today.json",
        out_dir / "data" / "figures.json",
        out_dir / "data" / "graph.json",
        out_dir / "data" / "threads.json",
        repo_root / "briefings" / f"{iso}.md",
    ]
    seen: set[Path] = set()
    rows: list[dict[str, Any]] = []
    for path in candidates + explicit:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        exists = path.exists()
        try:
            rel = path.relative_to(out_dir)
            link = "../../" + rel.as_posix()
            display = "dist/" + rel.as_posix()
        except ValueError:
            rel_repo = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
            commit = _git(repo_root, "rev-parse", "HEAD")
            link = f"https://github.com/ihelfrich/worldscope/blob/{commit}/{rel_repo.as_posix()}"
            display = rel_repo.as_posix()
        rows.append({
            "path": display,
            "href": link,
            "exists": exists,
            "bytes": path.stat().st_size if exists else 0,
            "sha256": _sha256_short(path) if exists else "",
        })
    return sorted(rows, key=lambda r: r["path"])


def build_from_repo(
    repo_root: Path,
    out_dir: Path,
    *,
    today: date | None = None,
    store_path: Path | None = None,
    source_tiers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the reproducibility document consumed by the HTML page."""
    iso = _today_iso(today)
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    store = store_path or repo_root / "data" / "store.sqlite"
    commit = _git(repo_root, "rev-parse", "HEAD")
    commit_time = _git(repo_root, "log", "-1", "--format=%ai")
    platform_text = platform.platform()
    fc = fact_check_summary(out_dir)
    try:
        package_version = importlib.metadata.version("worldscope")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"
    return {
        "brief_date": iso,
        "generated_at": _git(repo_root, "log", "-1", "--format=%aI") if commit != "unknown" else "",
        "commit": {
            "sha": commit,
            "short": commit[:7] if commit != "unknown" else "unknown",
            "time": commit_time,
        },
        "environment": {
            "worldscope_version": package_version,
            "python_version": sys.version.split()[0],
            "platform": platform_text,
            "os_hash": hashlib.sha256(platform_text.encode("utf-8")).hexdigest()[:16],
        },
        "generator_version": fc["generator_version"],
        "source_pulls": source_pull_rows(store, today, source_tiers=source_tiers),
        "fact_check": fc["summary"],
        "lake_stats": lake_stats(repo_root, out_dir, today),
        "artifacts": artifact_rows(repo_root, out_dir, today),
    }
