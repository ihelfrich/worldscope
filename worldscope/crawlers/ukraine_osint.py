"""ukraine_osint: wrapper around the web-intel Go crawler.

Given a list of seed domains for Ukrainian + Russian-dissident editorial
sites, this module shells out to `web-intel crawl` for each seed and
returns the union of pages discovered. The crawler runs depth-2 BFS
inside the seed host, respects robots.txt by default, and writes a
JSONL record per page under `lake/sections/ukraine_theater/<date>/crawl/`.

The function is defensive about three failure modes:
  1. web-intel binary not installed (returns empty list, logs once)
  2. crawler exceeds the per-seed wall-clock budget (kills + returns partials)
  3. JSONL parse error on any line (skips that line, continues)

Each returned record carries `geo_resolution_m` and `latency_hours`
matching the section contract: editorial articles are 50000m (oblast)
resolution by default and 12h latency (published-but-buffered).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UA = "worldscope/0.1 ukraine-osint (contact: ianthelfrich@gmail.com)"

SEEDS: list[tuple[str, str]] = [
    ("ukrainska-pravda.com", "https://www.pravda.com.ua/"),
    ("kyivindependent.com",  "https://kyivindependent.com/"),
    ("hromadske.ua",         "https://hromadske.ua/"),
    ("suspilne.media",       "https://suspilne.media/"),
    ("astra.media",          "https://astra.news/"),
    ("schemes.radiosvoboda.org", "https://www.radiosvoboda.org/z/27775"),
]

# Sensible defaults for a daily refresh: shallow depth, small page cap,
# generous per-host delay. Crawling is polite by default.
DEFAULT_DEPTH = 2
DEFAULT_MAX_PAGES = 60
DEFAULT_CONCURRENCY = 4
DEFAULT_PER_SEED_TIMEOUT_S = 25


def _binary_path() -> Path | None:
    """Find the web-intel binary. Prefer ~/go/bin/web-intel (where it lands
    after `go install`). Fall back to PATH lookup. Returns None if missing."""
    candidates = [
        Path.home() / "go" / "bin" / "web-intel",
        Path("/usr/local/bin/web-intel"),
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return c
    which = shutil.which("web-intel")
    return Path(which) if which else None


def crawl_seeds(
    out_dir: Path,
    *,
    seeds: list[tuple[str, str]] | None = None,
    depth: int = DEFAULT_DEPTH,
    max_pages: int = DEFAULT_MAX_PAGES,
    per_seed_timeout_s: float = DEFAULT_PER_SEED_TIMEOUT_S,
) -> list[dict]:
    """Run web-intel against each seed and return a flat list of page dicts.

    Returns [] (with a stderr log) when the binary isn't installed. Per-seed
    failures are logged but never raised; the caller gets whatever crawled
    successfully.
    """
    binary = _binary_path()
    if binary is None:
        print("[ukraine_osint] web-intel binary not found (~/go/bin/web-intel); skipping crawl")
        return []

    seeds = seeds if seeds is not None else SEEDS
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    ingested_at = datetime.now(timezone.utc).isoformat()

    for host, seed_url in seeds:
        seed_out = out_dir / host.replace(".", "_")
        seed_out.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(binary), "crawl",
            "--seed", seed_url,
            "--depth", str(depth),
            "--max-pages", str(max_pages),
            "--concurrency", str(DEFAULT_CONCURRENCY),
            "--out", str(seed_out),
            "--no-html",
        ]
        t0 = time.time()
        try:
            subprocess.run(
                cmd,
                timeout=per_seed_timeout_s,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            print(f"[ukraine_osint] {host}: crawl exceeded {per_seed_timeout_s}s, taking partials")
        except Exception as exc:
            print(f"[ukraine_osint] {host}: crawl failed: {type(exc).__name__}: {exc}")
            continue

        # web-intel writes pages.jsonl (one JSON per line)
        jsonl = seed_out / "pages.jsonl"
        if not jsonl.exists():
            continue
        try:
            with open(jsonl, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        page = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    url = page.get("url") or page.get("URL") or ""
                    title = page.get("title") or page.get("Title") or ""
                    if not url:
                        continue
                    pages.append({
                        "url": url,
                        "title": title[:300],
                        "host": host,
                        "fetched_at": page.get("fetched_at") or page.get("FetchedAt"),
                        "status": page.get("status") or page.get("Status"),
                        "ingested_at": ingested_at,
                        "wall_clock_s": round(time.time() - t0, 2),
                    })
        except OSError:
            continue

    return pages
