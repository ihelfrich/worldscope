"""worldscope.integrity — the data-integrity layer.

The hardest problem this feed has is not that sections fail — it's that they
fail *silently*. A section that returns empty because its API key is missing,
or its endpoint moved, or the network was blocked, looks exactly like a section
that returned empty because nothing happened. The published brief then either
omits the gap or apologizes for it in hand-written prose (the old "DATA NOTE"),
and a reader cannot tell a quiet day from a broken sensor.

This module makes the gaps **visible, classified, and honest**. It reads what
the pipeline already records — per-section record recency plus the
``source_health`` table — and assigns every section a status with a *reason*:

    FRESH    pulled today with data
    STALE    has recent data but nothing today
    EMPTY    no data in the window and no recorded failure
    FAILED   the last pull errored (carries the error)
    NO_KEY   a required credential is not set in the environment
    SKIPPED  deliberately skipped this run (WORLDSCOPE_SKIP)

From that it generates an auto-written integrity line that *replaces* the
apologetic DATA NOTE with the truth, and a panel for the brief. Run it in CI
(where the network + keys are real) and broken core feeds can no longer hide.

Pure, offline, stdlib-only core: ``classify_section`` takes plain values and is
trivially unit-testable. The lake adapter is a thin wrapper.

    python -m worldscope.integrity                 # today's integrity report
    python -m worldscope.integrity --date 2026-05-31
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
LAKE_META = REPO / "lake" / "sections" / "_meta"

# How recent a record must be to count as "today's" data, and the staleness edge.
FRESH_DAYS = 0
STALE_DAYS = 3

# Sections whose adapters return empty without a credential. Keyed by section id;
# value is the list of env vars that must ALL be present for the section to work.
# (Source: the adapters' own environ.get() calls.)
REQUIRED_KEYS: dict[str, list[str]] = {
    "macro":      ["FRED_API_KEY"],
    # markets falls back to keyless Yahoo, so it has no hard credential need.
    "mediacloud": ["MEDIACLOUD_API_KEY"],
    "acled":      ["ACLED_EMAIL", "ACLED_PASSWORD"],
    "firms":      ["FIRMS_MAP_KEY"],
    "state_bills": ["OPENSTATES_API_KEY"],
}

STATUS_ORDER = ["FAILED", "NO_KEY", "EMPTY", "STALE", "SKIPPED", "FRESH"]


@dataclass
class SectionIntegrity:
    section_id: str
    status: str
    reason: str
    last_record_date: Optional[str] = None
    today_count: int = 0
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id, "status": self.status,
            "reason": self.reason, "last_record_date": self.last_record_date,
            "today_count": self.today_count,
            "consecutive_failures": self.consecutive_failures,
        }


def classify_section(
    section_id: str, *, today: date,
    last_record_date: Optional[str], today_count: int,
    consecutive_failures: int = 0, last_failure_error: Optional[str] = None,
    missing_keys: Optional[list[str]] = None, skipped: bool = False,
) -> SectionIntegrity:
    """Assign a section its integrity status + a human reason. Pure function.

    Precedence is deliberate: a missing credential or a recorded failure is the
    *root cause* of emptiness and must be reported even when stale data lingers,
    so those rank above FRESH/STALE/EMPTY.
    """
    def mk(status: str, reason: str) -> SectionIntegrity:
        return SectionIntegrity(section_id, status, reason,
                                last_record_date=last_record_date,
                                today_count=today_count,
                                consecutive_failures=consecutive_failures)

    if skipped:
        return mk("SKIPPED", "deliberately skipped this run (WORLDSCOPE_SKIP)")
    if today_count > 0:
        # Data is actually flowing — it's working, whatever this shell's env says.
        return mk("FRESH", f"{today_count} records today")
    if missing_keys:
        return mk("NO_KEY", f"required credential not set: {', '.join(missing_keys)}")
    if consecutive_failures > 0:
        err = (last_failure_error or "unknown error").strip().splitlines()[0][:120]
        return mk("FAILED", f"last pull failed ({consecutive_failures}×): {err}")
    if last_record_date:
        try:
            age = (today - date.fromisoformat(last_record_date[:10])).days
        except ValueError:
            age = STALE_DAYS + 1
        if age <= STALE_DAYS:
            return mk("STALE", f"no data today; last good {last_record_date} ({age}d)")
        return mk("EMPTY", f"no data in {STALE_DAYS}d; last ever {last_record_date}")
    return mk("EMPTY", "never produced a record")


def _missing_keys(section_id: str, env: Optional[dict] = None) -> list[str]:
    env = env if env is not None else os.environ
    return [k for k in REQUIRED_KEYS.get(section_id, []) if not env.get(k)]


# ---------------------------------------------------------------------------
# Lake adapter
# ---------------------------------------------------------------------------

def _skip_set(env: Optional[dict] = None) -> set[str]:
    env = env if env is not None else os.environ
    raw = env.get("WORLDSCOPE_SKIP", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def assess(conn, section_ids: list[str], *, today: date,
           env: Optional[dict] = None, store=None) -> list[SectionIntegrity]:
    """Build an integrity report for ``section_ids``.

    Recency/count come from the lake ``records`` table AND — crucially — the
    snapshot store: many news-heavy sections (foreign_news, russian_internal, …)
    write hundreds of items to the snapshot store but no rows to the lake records
    table, so a lake-only view wrongly calls them EMPTY. Failure state comes from
    ``source_health``. Pass ``store`` (a SnapshotStore) to include snapshot
    coverage."""
    env = env if env is not None else os.environ
    skip = _skip_set(env)
    today_iso = today.isoformat()

    # recency + today's count per section
    recency: dict[str, tuple[Optional[str], int]] = {}
    for sid in section_ids:
        row = conn.execute(
            "SELECT MAX(record_date), "
            "SUM(CASE WHEN record_date = ? THEN 1 ELSE 0 END) "
            "FROM records WHERE section_id = ?",
            (today_iso, sid),
        ).fetchone()
        recency[sid] = (row[0] if row else None, int((row[1] or 0)) if row else 0)

    # failure state (source_health keyed by source id; many sections share the id)
    health: dict[str, tuple[int, Optional[str]]] = {}
    try:
        for r in conn.execute(
            "SELECT source_id, consecutive_failures, last_failure_error FROM source_health"
        ):
            health[r[0]] = (int(r[1] or 0), r[2])
    except Exception:
        pass

    out: list[SectionIntegrity] = []
    for sid in section_ids:
        last, cnt = recency.get(sid, (None, 0))
        # Merge snapshot-store coverage so snapshot-only sections aren't EMPTY.
        if store is not None:
            try:
                mr = store.most_recent(sid)
            except Exception:
                mr = None
            if mr:
                sd = mr.get("snapshot_date")
                n = len(mr.get("items") or [])
                if sd and (last is None or sd > last):
                    last = sd
                if cnt == 0 and sd == today_iso:
                    cnt = n
        cf, err = health.get(sid, (0, None))
        out.append(classify_section(
            sid, today=today, last_record_date=last, today_count=cnt,
            consecutive_failures=cf, last_failure_error=err,
            missing_keys=_missing_keys(sid, env), skipped=sid in skip,
        ))
    out.sort(key=lambda s: (STATUS_ORDER.index(s.status)
                            if s.status in STATUS_ORDER else 99, s.section_id))
    return out


def section_ids_from_registry() -> list[str]:
    """Canonical section ids from the brief's registry (so empty sections are
    still assessed). Falls back to an empty list if the import is heavy/fails."""
    try:
        from .brief import SECTION_REGISTRY
        return [c.id for c in SECTION_REGISTRY]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Honest summary + brief panel  (replaces the hand-written DATA NOTE)
# ---------------------------------------------------------------------------

def summary_line(reports: list[SectionIntegrity]) -> str:
    by: dict[str, list[str]] = {}
    for r in reports:
        by.setdefault(r.status, []).append(r.section_id)
    n = len(reports)
    fresh = len(by.get("FRESH", []))
    parts = [f"Data integrity · {fresh}/{n} sections fresh"]
    if by.get("FAILED"):
        parts.append(f"{len(by['FAILED'])} failing ({', '.join(by['FAILED'][:5])})")
    if by.get("NO_KEY"):
        parts.append(f"{len(by['NO_KEY'])} awaiting credentials ({', '.join(by['NO_KEY'][:5])})")
    if by.get("EMPTY"):
        parts.append(f"{len(by['EMPTY'])} empty ({', '.join(by['EMPTY'][:5])})")
    if by.get("STALE"):
        parts.append(f"{len(by['STALE'])} stale")
    if by.get("SKIPPED"):
        parts.append(f"{len(by['SKIPPED'])} skipped")
    return " · ".join(parts) + ". All published data is drawn from the lake; gaps are shown, not hidden."


_BADGE = {"FRESH": "#3B6B43", "STALE": "#9A6B00", "EMPTY": "#7A2A20",
          "FAILED": "#990000", "NO_KEY": "#2B4257", "SKIPPED": "#6F695C"}


def render_integrity_panel(reports: list[SectionIntegrity], *, max_show: int = 14) -> str:
    import html as _html
    flagged = [r for r in reports if r.status not in ("FRESH", "SKIPPED")]
    if not flagged:
        return ""
    rows = []
    for r in flagged[:max_show]:
        color = _BADGE.get(r.status, "#6F695C")
        rows.append(
            "<li>"
            f"<span class='new-badge' style='background:{color}'>{r.status}</span>"
            f"<strong>{_html.escape(r.section_id)}</strong>"
            f"<span class='meta'> · {_html.escape(r.reason)}</span></li>"
        )
    return (
        "<section class='section'>"
        "<h2>🩺 Data integrity "
        f"<span class='count'>· {len(flagged)} sections need attention</span></h2>"
        f"<p class='synth'>{_html.escape(summary_line(reports))}</p>"
        f"<ul class='items'>{''.join(rows)}</ul>"
        "</section>"
    )


def write_artifact(today: date, reports: list[SectionIntegrity], *,
                   out_root: Path = LAKE_META) -> Path:
    out_dir = Path(out_root) / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "integrity.json"
    out_path.write_text(json.dumps({
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary_line(reports),
        "sections": [r.to_dict() for r in reports],
    }, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="WORLDSCOPE data-integrity report.")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--write", action="store_true", help="write the _meta artifact")
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.date)

    from .lake import Lake
    from .store import SnapshotStore
    lake = Lake.open()
    conn = lake._ensure_open()
    sids = section_ids_from_registry()
    if not sids:  # fallback: whatever the lake has seen, plus key-gated sections
        seen = [r[0] for r in conn.execute("SELECT DISTINCT section_id FROM records")]
        sids = sorted(set(seen) | set(REQUIRED_KEYS))
    reports = assess(conn, sids, today=today, store=SnapshotStore())
    print(summary_line(reports), "\n")
    for r in reports:
        if r.status == "FRESH":
            continue
        print(f"  {r.status:8} {r.section_id:24} {r.reason}")
    if args.write:
        print("\n[integrity]", write_artifact(today, reports))
    lake.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
