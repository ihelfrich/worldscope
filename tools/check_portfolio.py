#!/usr/bin/env python3
"""Nightly health check for every public site listed in data/portfolio.json.

Fifty-odd sites were deployed from separate repositories with nothing
watching them: three served 404 for months and nobody knew. This asks two
questions of each registry entry:

  1. Does the site answer 200 with a real page?
  2. For repositories with scheduled data refreshes, did the named workflow
     last complete successfully, and recently?

It prints one line per failure and exits 1 if there is any, so the workflow
can raise a single tracked alert. Transient errors are retried; a site is
only reported after every attempt failed. Standard library only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Callable
from urllib import error, request

REGISTRY = Path(__file__).resolve().parents[1] / "data" / "portfolio.json"
MIN_BYTES = 400          # a GitHub Pages 404 served as 200 would still be larger; an empty shell is not
ATTEMPTS = 3
_REPO = re.compile(r"^[A-Za-z0-9_.-]+$")

Fetch = Callable[[str, dict], tuple[int, bytes]]


def _fetch(url: str, headers: dict) -> tuple[int, bytes]:
    req = request.Request(url, headers={"User-Agent": "portfolio-health", **headers})
    try:
        with request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, b""
    except (error.URLError, TimeoutError, OSError):
        return 0, b""


def load_registry(path: Path = REGISTRY) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    owner = doc.get("owner", "")
    if not _REPO.fullmatch(owner):
        raise ValueError("registry owner is missing or invalid")
    seen: set[str] = set()
    for site in doc.get("sites", []):
        repo, url = site.get("repo", ""), site.get("url", "")
        if not _REPO.fullmatch(repo):
            raise ValueError(f"invalid repo in registry: {repo!r}")
        if repo in seen:
            raise ValueError(f"duplicate repo in registry: {repo}")
        seen.add(repo)
        if not url.startswith(f"https://{owner}.github.io/"):
            raise ValueError(f"{repo}: url must be on https://{owner}.github.io/")
        for wf in site.get("workflows", []):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", wf.get("file", "")):
                raise ValueError(f"{repo}: invalid workflow file")
    return doc


def check_site(site: dict, fetch: Fetch = _fetch, *, pause: float = 5.0) -> str | None:
    """None if the site is up, else a one-line reason."""
    status, body = 0, b""
    for attempt in range(ATTEMPTS):
        status, body = fetch(site["url"], {})
        if status == 200 and len(body) >= MIN_BYTES:
            return None
        if status == 404:
            break                      # definite; do not wait on it
        if attempt + 1 < ATTEMPTS:
            time.sleep(pause)
    if status == 200:
        return f"{site['repo']}: page is only {len(body)} bytes ({site['url']})"
    return f"{site['repo']}: HTTP {status or 'no response'} ({site['url']})"


def check_workflow(owner: str, repo: str, wf: dict, now: datetime,
                   fetch: Fetch = _fetch, token: str = "") -> str | None:
    """None if the workflow's latest completed run is green and recent."""
    url = (f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
           f"{wf['file']}/runs?status=completed&per_page=1")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, body = fetch(url, headers)
    if status != 200:
        return f"{repo}: could not read runs of {wf['file']} (HTTP {status or 'no response'})"
    try:
        runs = json.loads(body).get("workflow_runs", [])
    except (ValueError, AttributeError):
        return f"{repo}: unreadable run list for {wf['file']}"
    if not runs:
        return f"{repo}: {wf['file']} has never completed a run"
    run = runs[0]
    conclusion = run.get("conclusion")
    if conclusion not in ("success", "skipped"):
        return f"{repo}: {wf['file']} last run {conclusion} ({run.get('html_url', '')})"
    try:
        finished = datetime.fromisoformat(str(run.get("updated_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return f"{repo}: {wf['file']} has no readable finish time"
    max_age = timedelta(days=float(wf.get("max_age_days", 3)))
    if now - finished > max_age:
        days = (now - finished).days
        return f"{repo}: {wf['file']} has not run for {days} days"
    return None


def run(doc: dict, *, fetch: Fetch = _fetch, token: str = "",
        now: datetime | None = None, pause: float = 5.0) -> list[str]:
    now = now or datetime.now(timezone.utc)
    failures: list[str] = []
    for site in doc["sites"]:
        problem = check_site(site, fetch, pause=pause)
        if problem:
            failures.append(problem)
        for wf in site.get("workflows", []):
            problem = check_workflow(doc["owner"], site["repo"], wf, now, fetch, token)
            if problem:
                failures.append(problem)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    args = parser.parse_args(argv)
    doc = load_registry(args.registry)
    failures = run(doc, token=os.environ.get("GITHUB_TOKEN", ""))
    print(f"checked {len(doc['sites'])} sites; {len(failures)} problem(s)")
    for line in failures:
        print(f"FAIL {line}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        summary = "; ".join(failures)[:900]
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"count={len(failures)}\n")
            handle.write(f"failures={summary}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
