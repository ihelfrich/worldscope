"""Bounded retention for the dated pages under dist/.

Nothing ever deleted from dist/, so the published tree grew about 4 MB a day
toward GitHub Pages' 1 GB site limit. The section indexes link only to the
newest seven days per section and the landing page links to no dated page at
all, so older dated files are unlinked orphans; the raw records they were
rendered from stay in lake/.

Same shape as lake_maintenance.maintain_store: the cutoff is measured from the
newest dated file (deterministic, independent of the wall clock), a floor of
newest files per section is always kept, and a size ceiling then drops the
oldest remaining whole day until the tree fits.

Pruned, by date:
  dist/sections/<section>/<date>.html
  dist/<date>.html, dist/<date>.md and dist/zips/<date>.zip (together, because
  each daily page links to its own bundle)
Never touched: index pages, briefings, weekly briefings, live, status, assets.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import re

_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(html|md|zip)$")

# site_builder.build_all renders (and each section index links) this many of a
# section's newest days. They must survive however old they are.
SECTION_FLOOR = 7
# Never prune the daily pages of the most recent days, whatever the ceiling.
DAILY_FLOOR = 14


def _day(name: str) -> str | None:
    match = _DATED.match(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def _candidates(dist: Path) -> tuple[dict[str, list[Path]], set[Path]]:
    """Dated files grouped by day, and the subset that must be kept."""
    by_day: dict[str, list[Path]] = {}
    protected: set[Path] = set()

    sections = dist / "sections"
    if sections.is_dir():
        for section_dir in sorted(p for p in sections.iterdir() if p.is_dir()):
            dated = sorted(((d, p) for p in section_dir.iterdir()
                            if p.is_file() and p.suffix == ".html"
                            and (d := _day(p.name))), reverse=True)
            for index, (day, path) in enumerate(dated):
                by_day.setdefault(day, []).append(path)
                if index < SECTION_FLOOR:
                    protected.add(path)

    daily: list[tuple[str, Path]] = []
    for path in dist.iterdir() if dist.is_dir() else ():
        if path.is_file() and path.suffix in (".html", ".md") and (day := _day(path.name)):
            daily.append((day, path))
    zips = dist / "zips"
    if zips.is_dir():
        for path in zips.iterdir():
            if path.is_file() and path.suffix == ".zip" and (day := _day(path.name)):
                daily.append((day, path))
    floor_days = set(sorted({d for d, _ in daily}, reverse=True)[:DAILY_FLOOR])
    for day, path in daily:
        by_day.setdefault(day, []).append(path)
        if day in floor_days:
            protected.add(path)
    return by_day, protected


def _tree_bytes(dist: Path) -> int:
    return sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())


def prune(dist: Path = Path("dist"), *, keep_days: int = 60, max_mb: float = 400.0,
          dry_run: bool = False) -> dict:
    """Delete dated pages older than keep_days, then enforce max_mb."""
    dist = Path(dist)
    if not dist.is_dir():
        print(f"[dist-maint] no dist at {dist}")
        return {"ok": True, "skipped": True}
    if keep_days < 1:
        raise ValueError("keep_days must be at least 1")

    by_day, protected = _candidates(dist)
    before = _tree_bytes(dist)
    if not by_day:
        return {"ok": True, "before": before, "after": before, "deleted": 0, "days": 0}

    newest = max(by_day)
    cutoff = (date.fromisoformat(newest) - timedelta(days=keep_days)).isoformat()
    doomed: list[Path] = []
    remaining = before
    dropped_days = 0

    def drop(day: str) -> None:
        nonlocal remaining, dropped_days
        victims = [p for p in by_day.pop(day) if p not in protected]
        if victims:
            dropped_days += 1
        for path in victims:
            remaining -= path.stat().st_size
            doomed.append(path)

    for day in sorted(d for d in by_day if d < cutoff):
        drop(day)
    # Age was not enough (pages ballooned): drop the oldest whole day at a time.
    while remaining / 1e6 > max_mb:
        droppable = sorted(d for d, paths in by_day.items()
                           if any(p not in protected for p in paths))
        if not droppable:
            print(f"::warning::dist is {remaining/1e6:.0f}MB with only protected "
                  f"pages left; the ceiling of {max_mb:.0f}MB cannot be met by pruning")
            break
        drop(droppable[0])

    if not dry_run:
        for path in doomed:
            path.unlink()
    after = remaining
    print(f"[dist-maint] {'would prune' if dry_run else 'pruned'} {len(doomed)} files "
          f"across {dropped_days} days (cutoff {cutoff}) · "
          f"{before/1e6:.0f}MB -> {after/1e6:.0f}MB")
    return {"ok": True, "before": before, "after": after, "deleted": len(doomed),
            "days": dropped_days, "cutoff": cutoff, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded retention for dated pages in dist/")
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--keep-days", type=int, default=60)
    parser.add_argument("--max-mb", type=float, default=400.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    prune(args.out, keep_days=args.keep_days, max_mb=args.max_mb, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
