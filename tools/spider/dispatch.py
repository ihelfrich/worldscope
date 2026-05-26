"""
dispatch.py — watch-area-driven spider dispatcher.

Reads watchareas.yaml + tools/spider/targets/<slug>.yaml, expands each
configured watch area into a target URL list, then invokes the Go
`web-intel` crawler with appropriate depth and politeness flags. Results
are normalized and inserted into ~/.worldscope/spider.sqlite.

Each target catalog YAML looks like:

  area: russia-oil-sanctions-perimeter
  publications:
    - https://www.kommersant.ru/rubric/4
    - https://www.vedomosti.ru/economics
  government:
    - https://home.treasury.gov/policy-issues/financial-sanctions/recent-actions
    - https://www.consilium.europa.eu/en/policies/sanctions-against-russia/
  courts:
    - https://www.cit.uscourts.gov/SlipOpinions.html

Run:  python tools/spider/dispatch.py [--area NAME] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
WATCH = REPO / "watchareas.yaml"
TARGETS_DIR = REPO / "tools" / "spider" / "targets"
STORE = Path.home() / ".worldscope" / "spider.sqlite"
WEB_INTEL = os.environ.get("WEB_INTEL_BIN", str(Path.home() / "go" / "bin" / "web-intel"))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def _ensure_store() -> sqlite3.Connection:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STORE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            watch_area TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status_code INTEGER,
            title TEXT,
            body_text TEXT,
            UNIQUE (url, fetched_at)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_area ON pages(watch_area, fetched_at DESC)")
    conn.commit()
    return conn


def _load_targets(area_slug: str) -> dict:
    p = TARGETS_DIR / f"{area_slug}.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _crawl_one(url: str, depth: int = 1, max_pages: int = 20) -> list[dict]:
    """Invoke web-intel crawl <url> and parse JSONL output."""
    if not Path(WEB_INTEL).exists():
        print(f"  [skip] web-intel binary not at {WEB_INTEL}", file=sys.stderr)
        return []
    try:
        result = subprocess.run(
            [WEB_INTEL, "crawl", url, "--depth", str(depth), "--max", str(max_pages), "--format", "jsonl"],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return []
    out: list[dict] = []
    for line in result.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def dispatch(area_name: str | None = None, dry_run: bool = False) -> dict[str, int]:
    if not WATCH.exists():
        print("no watchareas.yaml", file=sys.stderr)
        return {}
    raw = yaml.safe_load(WATCH.read_text(encoding="utf-8")) or {}
    areas = raw.get("watch_areas") if isinstance(raw, dict) else raw
    areas = areas or []
    if area_name:
        areas = [a for a in areas if a.get("name") == area_name]
    conn = None if dry_run else _ensure_store()
    counts: dict[str, int] = {}
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for area in areas:
        name = area.get("name")
        if not name:
            continue
        slug = _slug(name)
        targets = _load_targets(slug)
        if not targets:
            counts[name] = 0
            continue
        n_pages = 0
        for kind in ("publications", "government", "courts"):
            for url in (targets.get(kind) or []):
                if dry_run:
                    print(f"  [{name}] {kind:>13s} → {url}")
                    n_pages += 1
                    continue
                pages = _crawl_one(url)
                for p in pages:
                    purl = p.get("url") or url
                    title = (p.get("title") or "")[:300]
                    body = (p.get("text") or "")[:200000]  # cap at 200K
                    code = p.get("status_code")
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO pages "
                            "(watch_area, target_kind, url, fetched_at, status_code, title, body_text) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (name, kind, purl, now, code, title, body),
                        )
                    except sqlite3.OperationalError as e:
                        print(f"  [warn] {e}", file=sys.stderr)
                n_pages += len(pages)
        if conn:
            conn.commit()
        counts[name] = n_pages
        print(f"[{name}] {n_pages} pages crawled")
    if conn:
        conn.close()
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--area")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    dispatch(area_name=args.area, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
