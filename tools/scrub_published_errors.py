#!/usr/bin/env python3
"""Remove an account address that old error messages wrote into published files.

Until 2026-11 the ACLED adapter named the account in its "rejected
credentials" error. That text was copied into section pages, the run report,
the lake's per-day files and the lake's source-health tables, all of which are
committed and served. The adapter no longer does this and
``sections.redact_error`` strips addresses from every new error; this rewrites
what was already written. It is idempotent and safe to run on every build.

Usage:  python tools/scrub_published_errors.py [--dry-run] [PATH ...]
Default paths: dist lake data status
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = ("dist", "lake", "data", "status")
TEXT_SUFFIXES = {".html", ".json", ".jsonl", ".md", ".txt", ".xml", ".geojson"}
NEEDLE = "rejected credentials for "
PATTERN = re.compile(r"rejected credentials for [^\s:<\"\\]+")
REPLACEMENT = "rejected the configured credentials"


def scrub_text(text: str) -> tuple[str, int]:
    return PATTERN.subn(REPLACEMENT, text)


def scrub_file(path: Path, dry_run: bool) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    if NEEDLE not in text:
        return 0
    cleaned, count = scrub_text(text)
    if count and not dry_run:
        path.write_text(cleaned, encoding="utf-8")
    return count


def scrub_sqlite(path: Path, dry_run: bool) -> int:
    """Rewrite matching values in every text column of every table."""
    total = 0
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            for column in columns:
                try:
                    rows = conn.execute(
                        f'SELECT rowid, "{column}" FROM "{table}" '
                        f'WHERE typeof("{column}") = \'text\' AND "{column}" LIKE ?',
                        (f"%{NEEDLE}%",)).fetchall()
                except sqlite3.Error:
                    continue  # WITHOUT ROWID tables and virtual tables
                for rowid, value in rows:
                    cleaned, count = scrub_text(value)
                    if not count:
                        continue
                    total += count
                    if not dry_run:
                        conn.execute(
                            f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                            (cleaned, rowid))
        if total and not dry_run:
            conn.commit()
    finally:
        conn.close()
    return total


def scrub(paths: list[Path], dry_run: bool = False) -> tuple[int, int]:
    """Return (files changed, replacements made)."""
    files = replacements = 0
    for base in paths:
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            if not path.is_file():
                continue
            if path.suffix == ".sqlite":
                count = scrub_sqlite(path, dry_run)
            elif path.suffix in TEXT_SUFFIXES:
                count = scrub_file(path, dry_run)
            else:
                continue
            if count:
                files += 1
                replacements += count
    return files, replacements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files or directories to scrub")
    parser.add_argument("--dry-run", action="store_true", help="count without writing")
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.paths] or [ROOT / p for p in DEFAULT_PATHS]
    files, replacements = scrub(paths, dry_run=args.dry_run)
    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"scrub_published_errors: {verb} {replacements} occurrence(s) in {files} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
