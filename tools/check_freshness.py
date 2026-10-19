"""check_freshness.py — end-to-end PRODUCT freshness watchdog.

Both long outages this system has suffered were invisible to job-level CI:
every workflow was green while the product was broken —
  * Jun 2026: the lake silently stopped committing for 13 days (shallow-clone
    rebase failure, then the DB crossing GitHub's 100 MB file limit);
  * Jun–Oct 2026: the LIVE homepage served the June 22 build for four months
    (regenerated daily but never committed, so hourly deploys of the stale
    committed copy kept clobbering it).

This checker therefore tests OUTCOMES, not jobs:

  live_page       the deployed homepage's own date marker is recent
  committed_page  dist/index.html in the repo is recent (catches the breakage
                  before deploy-clobbering even makes it visible)
  lake_fresh      max(ingested_at) in the lake is recent
  lake_size       the lake has headroom under GitHub's hard 100 MB limit
                  (the June freeze would have been flagged weeks early)
  sections_fresh  enough sections ingested recently (mass source death)

Exit 0 when all pass; exit 1 with a one-line-per-failure summary otherwise
(also written to $GITHUB_OUTPUT as `failures=` for the alert step).
Stdlib only, so it runs in any job with no installs.

    python tools/check_freshness.py                 # full check
    python tools/check_freshness.py --skip-live     # offline/local
    python tools/check_freshness.py --max-age-days 0  # rehearse a failure
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE_URL = "https://ihelfrich.github.io/worldscope/"
DATE_MARKER = re.compile(r"Situation Board · (\d{4}-\d{2}-\d{2})")

MAX_AGE_DAYS = 2          # page/lake may lag a weekend-ish amount, never more
LAKE_SOFT_MB = 90.0       # alert well before GitHub's hard 100 MB reject
MIN_FRESH_SECTIONS = 20   # of ~35 live sources; below this something is wrong


def _age_days(d: date, today: date) -> int:
    return (today - d).days


def check_live_page(url: str, today: date, max_age: int) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "worldscope-watchdog/1.0"})
    try:
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as exc:
        return False, f"live_page: fetch failed ({type(exc).__name__}: {exc})"
    m = DATE_MARKER.search(html)
    if not m:
        return False, "live_page: no 'Situation Board · YYYY-MM-DD' marker found"
    d = date.fromisoformat(m.group(1))
    age = _age_days(d, today)
    if age > max_age:
        return False, f"live_page: deployed homepage is {age}d old ({d})"
    return True, f"live_page: OK ({d}, {age}d old)"


def check_committed_page(root: Path, today: date, max_age: int) -> tuple[bool, str]:
    p = root / "dist" / "index.html"
    if not p.exists():
        return False, "committed_page: dist/index.html missing from the repo"
    m = DATE_MARKER.search(p.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return False, "committed_page: no date marker in dist/index.html"
    d = date.fromisoformat(m.group(1))
    age = _age_days(d, today)
    if age > max_age:
        return False, f"committed_page: committed homepage is {age}d old ({d})"
    return True, f"committed_page: OK ({d}, {age}d old)"


def check_lake(root: Path, today: date, max_age: int,
               soft_mb: float, min_sections: int) -> list[tuple[bool, str]]:
    p = root / "lake" / "db" / "worldscope.sqlite"
    if not p.exists():
        return [(False, "lake_fresh: lake/db/worldscope.sqlite missing")]
    out: list[tuple[bool, str]] = []
    mb = p.stat().st_size / 1e6
    if mb > soft_mb:
        out.append((False, f"lake_size: {mb:.1f}MB — approaching GitHub's 100MB "
                           f"hard limit (push bricks at 100; prune now)"))
    else:
        out.append((True, f"lake_size: OK ({mb:.1f}MB, limit 100MB)"))
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        latest = con.execute("SELECT MAX(ingested_at) FROM records").fetchone()[0]
        cutoff = (datetime.combine(today, datetime.min.time(), timezone.utc)
                  - timedelta(days=max_age)).strftime("%Y-%m-%dT00:00:00Z")
        n_sec = con.execute(
            "SELECT COUNT(DISTINCT section_id) FROM records WHERE ingested_at >= ?",
            (cutoff,)).fetchone()[0]
        con.close()
    except sqlite3.Error as exc:
        return out + [(False, f"lake_fresh: cannot read lake ({exc})")]
    if not latest:
        out.append((False, "lake_fresh: records table is empty"))
    else:
        d = date.fromisoformat(latest[:10])
        age = _age_days(d, today)
        if age > max_age:
            out.append((False, f"lake_fresh: newest record is {age}d old ({latest[:10]})"))
        else:
            out.append((True, f"lake_fresh: OK (newest {latest[:10]}, {age}d old)"))
    if n_sec < min_sections:
        out.append((False, f"sections_fresh: only {n_sec} sections ingested in the "
                           f"last {max_age}d (floor {min_sections})"))
    else:
        out.append((True, f"sections_fresh: OK ({n_sec} sections in last {max_age}d)"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="End-to-end product freshness watchdog")
    ap.add_argument("--url", default=LIVE_URL)
    ap.add_argument("--root", type=Path, default=REPO)
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    ap.add_argument("--lake-soft-mb", type=float, default=LAKE_SOFT_MB)
    ap.add_argument("--min-fresh-sections", type=int, default=MIN_FRESH_SECTIONS)
    ap.add_argument("--skip-live", action="store_true",
                    help="skip the deployed-site fetch (offline/local runs)")
    args = ap.parse_args(argv)
    today = datetime.now(timezone.utc).date()

    results: list[tuple[bool, str]] = []
    if not args.skip_live:
        results.append(check_live_page(args.url, today, args.max_age_days))
    results.append(check_committed_page(args.root, today, args.max_age_days))
    results.extend(check_lake(args.root, today, args.max_age_days,
                              args.lake_soft_mb, args.min_fresh_sections))

    failures = [msg for ok, msg in results if not ok]
    for ok, msg in results:
        print(("PASS  " if ok else "FAIL  ") + msg)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("failures=" + (" | ".join(failures)) + "\n")

    if failures:
        print(f"\n{len(failures)} check(s) FAILED")
        return 1
    print(f"\nall {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
