"""api_health — probe every section adapter for upstream health.

Runs each section's pull() in isolation against the live upstream and
reports:
  - duration
  - exception (if any)
  - item count
  - first-record preview
  - tier (so you can spot upstream-dependency failures by tier)

Usage from the repo root:

    python -m tools.api_health                       # all sections
    python -m tools.api_health --only gdelt_gkg      # one section
    python -m tools.api_health --skip ukraine_theater,sanctions
    python -m tools.api_health --timeout 30
    python -m tools.api_health --json health.json    # machine-readable

Exit code:
    0  if every probed section returned items OR raised cleanly
    1  if any returned a silent-empty (likely upstream broken)
    2  if any raised an unhandled exception
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

# Make worldscope importable when run from the repo root.
REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from worldscope.brief import SECTION_REGISTRY  # noqa: E402
from worldscope.store import SnapshotStore     # noqa: E402


def _color(s: str, code: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def _row(verdict: str, color: str, sid: str, tier: str, dur: float,
         items: int, note: str) -> str:
    v = _color(f"{verdict:>10s}", color)
    return f"{v}  {sid:28s}  {tier:24s}  {dur:>6.2f}s  {items:>6d}  {note[:80]}"


def probe_section(cls, *, store: SnapshotStore, timeout_s: float) -> dict[str, Any]:
    """Run one section's pull() once. Never raises (catches everything)."""
    sid = cls.id
    tier = getattr(cls, "source_tier", "unknown")
    name = getattr(cls, "source_name", "") or cls.__name__
    sec = cls(store=store)
    # Honour PULL_TIMEOUT_S override per section, but cap at the caller's
    # --timeout so a stuck section can't hang the audit run.
    sec.PULL_TIMEOUT_S = min(timeout_s, getattr(sec, "PULL_TIMEOUT_S", timeout_s))

    t0 = time.monotonic()
    items: list[dict] = []
    err_repr = None
    err_type = None
    try:
        items = list(sec.pull() or [])
    except Exception as exc:  # noqa: BLE001
        err_type = type(exc).__name__
        err_repr = f"{err_type}: {str(exc)[:160]}"
    dur = time.monotonic() - t0

    # Verdict logic:
    #   OK       — got items
    #   EMPTY    — pull succeeded but returned []. Suspicious unless the
    #              section's source is genuinely sparse (FRED on a non-
    #              release day, ACLED with no watch-area events, FIRMS
    #              with no fires). Flag for the user to triage.
    #   RAISED   — pull threw — the state machine would mark this stale
    #   SLOW     — pull took > 75% of the timeout — at risk of being
    #              killed by PullTimeout in production
    if err_repr:
        verdict, color = "RAISED", "31"   # red
    elif not items:
        verdict, color = "EMPTY", "33"     # yellow
    else:
        verdict, color = "OK", "32"        # green
    if dur > (timeout_s * 0.75) and verdict != "RAISED":
        verdict += "•SLOW"
        color = "35"                       # magenta

    note = err_repr or ""
    if verdict.startswith("OK") and items:
        sample = (items[0].get("title") or items[0].get("name") or "")[:80]
        note = f"first: {sample}"

    return {
        "section_id": sid,
        "tier": tier,
        "source_name": name,
        "duration_s": round(dur, 2),
        "item_count": len(items),
        "verdict": verdict,
        "verdict_color": color,
        "error_type": err_type,
        "error": err_repr,
        "note": note,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools.api_health",
        description="Probe each section adapter for upstream health.",
    )
    ap.add_argument("--only", default="",
                    help="comma-separated section ids to probe (default: all)")
    ap.add_argument("--skip", default="",
                    help="comma-separated section ids to skip")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="per-section timeout cap (seconds, default 60)")
    ap.add_argument("--json", default="",
                    help="also write a machine-readable report to this path")
    ap.add_argument("--use-test-store", action="store_true",
                    help="route all writes to a tmp store so the real "
                         "data/store.sqlite isn't touched")
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    timeout_s = max(5.0, args.timeout)

    # Test-mode store: tmp file so we don't poison the real snapshot store.
    if args.use_test_store:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="ws-api-health-")) / "store.sqlite"
        os.environ["WORLDSCOPE_STORE_PATH"] = str(tmp)
        store = SnapshotStore(path=tmp)
        print(f"# using test store: {tmp}")
    else:
        store = SnapshotStore()

    classes = [
        c for c in SECTION_REGISTRY
        if (not only or c.id in only) and c.id not in skip
    ]

    print()
    header = f"{'verdict':>10s}  {'section':28s}  {'tier':24s}  {'dur':>7s}  {'items':>6s}  note"
    print(header)
    print("-" * len(header))

    results: list[dict[str, Any]] = []
    any_empty = False
    any_raised = False

    for cls in classes:
        r = probe_section(cls, store=store, timeout_s=timeout_s)
        results.append(r)
        print(_row(r["verdict"], r["verdict_color"], r["section_id"],
                   r["tier"], r["duration_s"], r["item_count"], r["note"]))
        if r["verdict"].startswith("EMPTY"): any_empty = True
        if r["verdict"].startswith("RAISED"): any_raised = True

    # Summary
    by_verdict: dict[str, int] = {}
    for r in results:
        v = r["verdict"].split("•")[0]
        by_verdict[v] = by_verdict.get(v, 0) + 1
    print()
    print(f"# summary: {len(results)} probed · "
          + ", ".join(f"{k}={v}" for k, v in sorted(by_verdict.items())))

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "probed_at": date.today().isoformat(),
            "results": results,
            "summary_by_verdict": by_verdict,
        }, indent=2), encoding="utf-8")
        print(f"# json written: {out_path}")

    if any_raised: return 2
    if any_empty: return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
