"""Build the 30-day per-section source health ledger."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any


HEALTH_STATES = ("fresh", "empty_ok", "carry_forward", "stale_after_failure", "no_data")


def _today_iso(as_of: date | None = None) -> str:
    return (as_of or date.today()).isoformat()


def _load_payload(raw: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _snapshot_state(payload: dict[str, Any] | None, previous_good: tuple[str, dict[str, Any]] | None) -> str:
    if not payload:
        return "carry_forward" if previous_good else "no_data"
    status = payload.get("status")
    items = payload.get("items") or []
    if status == "ok":
        return "fresh" if items else "empty_ok"
    if status == "empty_ok":
        return "empty_ok"
    if status == "failed":
        return "stale_after_failure" if previous_good else "no_data"
    return "no_data"


def _normalize_current_state(state: str) -> str:
    if state == "fresh_empty":
        return "empty_ok"
    if state in HEALTH_STATES:
        return state
    return "no_data"


def _source_tier(section_id: str, source_tiers: dict[str, str],
                 payload: dict[str, Any] | None) -> str:
    if source_tiers.get(section_id):
        return source_tiers[section_id]
    for item in (payload or {}).get("items") or []:
        if isinstance(item, dict) and item.get("source_tier"):
            return str(item["source_tier"])
    return "unknown"


def _snapshots_by_section(store_path: Path, start: date, as_of: date) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not store_path.exists():
        return out
    conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT section_id, snapshot_date, payload FROM snapshots "
            "WHERE snapshot_date <= ? ORDER BY section_id, snapshot_date",
            (as_of.isoformat(),),
        ).fetchall()
        for sid, snap_date, payload_json in rows:
            out.setdefault(sid, {})[snap_date] = _load_payload(payload_json)
    finally:
        conn.close()
    return out


def _current_entry(section_state: Any, day_iso: str) -> dict[str, Any]:
    state = _normalize_current_state(getattr(section_state, "state", "no_data") or "no_data")
    source_date = getattr(section_state, "source_date", None)
    carried_from = source_date if source_date and source_date != day_iso else None
    return {
        "date": day_iso,
        "state": state,
        "items": len(getattr(section_state, "items", []) or []),
        "error": getattr(section_state, "error", None),
        "carried_from": carried_from,
    }


def build_source_health(
    store_path: Path,
    *,
    as_of: date | None = None,
    days: int = 30,
    source_tiers: dict[str, str] | None = None,
    current_states: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the source_health.json document."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=days - 1)
    tiers = source_tiers or {}
    current_states = current_states or {}
    snapshots = _snapshots_by_section(Path(store_path), start, as_of)
    section_ids = sorted(set(tiers) | set(snapshots) | set(current_states))
    day_list = [start + timedelta(days=i) for i in range(days)]
    sections: list[dict[str, Any]] = []

    for sid in section_ids:
        by_day = snapshots.get(sid, {})
        previous_good: tuple[str, dict[str, Any]] | None = None
        last_fresh_at = ""
        for snap_date, payload in sorted(by_day.items()):
            if snap_date >= start.isoformat():
                break
            if _snapshot_state(payload, previous_good) in {"fresh", "empty_ok"}:
                previous_good = (snap_date, payload)
                last_fresh_at = str(payload.get("pulled_at") or last_fresh_at)
        history: list[dict[str, Any]] = []

        for day in day_list:
            day_iso = day.isoformat()
            payload = by_day.get(day_iso)
            if sid in current_states and day == as_of:
                entry = _current_entry(current_states[sid], day_iso)
                if entry["state"] in {"fresh", "empty_ok"}:
                    payload = by_day.get(day_iso) or {}
                    previous_good = (day_iso, {"items": getattr(current_states[sid], "items", []) or []})
                    last_fresh_at = str(payload.get("pulled_at") or last_fresh_at)
                history.append(entry)
                continue

            state = _snapshot_state(payload, previous_good)
            active_payload = payload if state in {"fresh", "empty_ok"} else (previous_good or ("", {}))[1]
            carried_from = None
            if state == "carry_forward" and previous_good:
                carried_from = previous_good[0]
            elif state == "stale_after_failure" and previous_good:
                carried_from = previous_good[0]
            entry = {
                "date": day_iso,
                "state": state,
                "items": len((active_payload or {}).get("items") or []),
                "error": str((payload or {}).get("error") or "") if state == "stale_after_failure" else "",
                "carried_from": carried_from,
            }
            history.append(entry)
            if state in {"fresh", "empty_ok"}:
                previous_good = (day_iso, payload or {"items": []})
                last_fresh_at = str((payload or {}).get("pulled_at") or last_fresh_at)

        consecutive_fresh = 0
        consecutive_failure = 0
        for entry in reversed(history):
            if entry["state"] in {"fresh", "empty_ok"}:
                if consecutive_failure == 0:
                    consecutive_fresh += 1
                else:
                    break
            elif entry["state"] in {"stale_after_failure", "no_data"}:
                if consecutive_fresh == 0:
                    consecutive_failure += 1
                else:
                    break
            else:
                break

        latest_payload = None
        for payload in reversed([by_day.get(d.isoformat()) for d in day_list]):
            if payload:
                latest_payload = payload
                break
        sections.append({
            "section_id": sid,
            "source_tier": _source_tier(sid, tiers, latest_payload),
            "history": history,
            "consecutive_fresh_days": consecutive_fresh,
            "consecutive_failure_days": consecutive_failure,
            "last_fresh_at": last_fresh_at,
        })

    return {"as_of": as_of.isoformat(), "sections": sections}


def write_source_health(out_dir: Path, doc: dict[str, Any]) -> Path:
    data_dir = Path(out_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "source_health.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_from_repo(
    repo_root: Path,
    out_dir: Path,
    *,
    today: date | None = None,
    store_path: Path | None = None,
    source_tiers: dict[str, str] | None = None,
    current_states: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path]:
    store = store_path or Path(repo_root) / "data" / "store.sqlite"
    doc = build_source_health(
        store,
        as_of=today,
        source_tiers=source_tiers,
        current_states=current_states,
    )
    return doc, write_source_health(out_dir, doc)
