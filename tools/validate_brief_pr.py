#!/usr/bin/env python3
"""Decide what to do with a routine pull request that carries a brief.

The claude.ai desk-officer routine can only push a ``claude/*`` branch, so its
briefs arrived as pull requests that nobody merged (58 were open by
2026-11-11). auto-merge-briefs.yml runs this on each such pull request and
acts on the verdict:

  merge   every file is newly added and belongs to exactly one daily brief
          (briefings/<date>.md plus its PNGs and events GeoJSON) or one weekly
          brief (weekly_briefings/<week>.md plus PNGs), and the Markdown looks
          like a real brief
  close   the pull request only records SOURCE_NOT_READY
  ignore  anything else; a person decides

This file is run from the base branch. It never executes anything from the
pull request: file names and contents are read through the API as data.
Standard library only.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timedelta, timezone
import json
import os
import re
import sys
from urllib import error, parse, request

DAILY_MD = re.compile(r"^briefings/(\d{4}-\d{2}-\d{2})\.md$")
DAILY_ASSET = re.compile(r"^briefings/(\d{4}-\d{2}-\d{2})-[a-z0-9_]{1,60}\.(png|geojson)$")
WEEKLY_MD = re.compile(r"^weekly_briefings/(\d{4}-W\d{2})\.md$")
WEEKLY_ASSET = re.compile(r"^weekly_briefings/(\d{4}-W\d{2})-[a-z0-9_]{1,60}\.png$")
NOT_READY = re.compile(r"^status/source_not_ready_\d{4}-\d{2}-\d{2}\.txt$")

MAX_FILES = 24
MAX_MD_BYTES = 600_000
DAILY_MIN_WORDS = 2500
WEEKLY_MIN_WORDS = 800
MAX_AGE_DAYS = 2


def _titled(text: str, phrase: str) -> bool:
    """A level-one heading naming the product within the first lines.

    Lenient on purpose: routine briefs have opened with a DATA NOTE
    blockquote before the title, and have capitalised it both ways.
    """
    for line in text.splitlines()[:20]:
        if line.startswith("# ") and phrase in line.lower():
            return True
    return False


def classify(files: list[dict], markdown: str | None, today: date) -> tuple[str, str]:
    """Return (verdict, reason) for the changed files of one pull request."""
    if not files:
        return "ignore", "no files"
    names = [str(f.get("filename", "")) for f in files]
    if any(f.get("status") != "added" for f in files):
        return "ignore", "changes or removes existing files"
    if all(NOT_READY.match(n) for n in names):
        return "close", "only records SOURCE_NOT_READY"
    if len(names) > MAX_FILES:
        return "ignore", f"{len(names)} files (limit {MAX_FILES})"

    daily = [m.group(1) for n in names if (m := DAILY_MD.match(n))]
    weekly = [m.group(1) for n in names if (m := WEEKLY_MD.match(n))]
    if len(daily) + len(weekly) != 1:
        return "ignore", "does not carry exactly one brief"

    if daily:
        stamp = daily[0]
        for n in names:
            m = DAILY_MD.match(n) or DAILY_ASSET.match(n)
            if not m or m.group(1) != stamp:
                return "ignore", f"unexpected file for a daily brief: {n}"
        try:
            day = date.fromisoformat(stamp)
        except ValueError:
            return "ignore", f"invalid date {stamp}"
        if not (today - timedelta(days=MAX_AGE_DAYS) <= day <= today + timedelta(days=1)):
            return "ignore", f"brief date {stamp} is not current"
        phrase, floor = "morning brief", DAILY_MIN_WORDS
    else:
        stamp = weekly[0]
        for n in names:
            m = WEEKLY_MD.match(n) or WEEKLY_ASSET.match(n)
            if not m or m.group(1) != stamp:
                return "ignore", f"unexpected file for a weekly brief: {n}"
        year, week = int(stamp[:4]), int(stamp[6:])
        current = today.isocalendar()
        previous = (today - timedelta(days=7)).isocalendar()
        if (year, week) not in {(current[0], current[1]), (previous[0], previous[1])}:
            return "ignore", f"brief week {stamp} is not current"
        phrase, floor = "weekly brief", WEEKLY_MIN_WORDS

    if markdown is None:
        return "ignore", "brief text unavailable"
    if len(markdown.encode("utf-8")) > MAX_MD_BYTES:
        return "ignore", "brief is implausibly large"
    words = len(markdown.split())
    if words < floor:
        return "ignore", f"brief too short ({words} words, floor {floor})"
    if not _titled(markdown, phrase):
        return "ignore", f"no '# ... {phrase}' title"
    return "merge", f"{stamp}: {words} words, {len(names)} files"


def _api(path: str, token: str):
    req = request.Request(
        "https://api.github.com" + path,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "worldscope-brief-pr"})
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr", type=int, required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        print("::error::GH_TOKEN and a valid --repo are required")
        return 1
    try:
        pull = _api(f"/repos/{args.repo}/pulls/{args.pr}", token)
        files = _api(f"/repos/{args.repo}/pulls/{args.pr}/files?per_page=100", token)
        markdown = None
        brief = [f["filename"] for f in files
                 if DAILY_MD.match(f["filename"]) or WEEKLY_MD.match(f["filename"])]
        if len(brief) == 1 and len(files) < 100:
            head = pull["head"]["sha"]
            blob = _api(f"/repos/{args.repo}/contents/{parse.quote(brief[0])}"
                        f"?ref={parse.quote(head)}", token)
            if blob.get("encoding") == "base64" and blob.get("content"):
                markdown = base64.b64decode(blob["content"]).decode("utf-8", "replace")
        if len(files) >= 100:
            verdict, reason = "ignore", "100 or more files"
        else:
            verdict, reason = classify(files, markdown,
                                       datetime.now(timezone.utc).date())
    except (error.URLError, KeyError, ValueError, TypeError) as exc:
        print(f"::error::could not inspect pull request: {exc}")
        return 1
    print(f"verdict={verdict}")
    print(f"reason={reason}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"verdict={verdict}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
