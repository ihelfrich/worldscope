"""Tracked pipeline alerts as GitHub issues.

Pushover was the only alert channel, and for weeks it answered "no active
devices to send to" while the desk-officer brief was missing, so nothing
reached a human. An issue is a second channel that needs no device and that
also records state: one open ``[pipeline-alert:<key>]`` issue means that
condition is broken right now, and the next healthy check closes it.

Standard library only, so alerting workflows need no install step.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Callable, Mapping
from urllib import error, request


API_ROOT = "https://api.github.com"
LABEL = "pipeline-alert"
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


class AlertError(RuntimeError):
    """The alert could not be recorded on GitHub."""


# (method, path, json body or None) -> decoded JSON response
Transport = Callable[[str, str, Mapping[str, Any] | None], Any]


def _http_transport(token: str) -> Transport:
    def call(method: str, path: str, body: Mapping[str, Any] | None) -> Any:
        req = request.Request(
            API_ROOT + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "worldscope-alerting",
            },
            method=method,
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raise AlertError(f"GitHub HTTP {exc.code} on {method} {path}") from exc
        except error.URLError as exc:
            raise AlertError(f"GitHub request failed: {exc.reason}") from exc
        return json.loads(raw) if raw else None
    return call


def _check(repo: str, key: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        raise AlertError(f"invalid repository: {repo!r}")
    if not _KEY_RE.fullmatch(key or ""):
        raise AlertError(f"invalid alert key: {key!r}")


def _prefix(key: str) -> str:
    return f"[{LABEL}:{key}]"


def find_open(repo: str, key: str, transport: Transport) -> dict | None:
    """Return the open alert issue for ``key``, if any (pull requests excluded)."""
    _check(repo, key)
    items = transport("GET", f"/repos/{repo}/issues?state=open&per_page=100", None) or []
    for item in items:
        if "pull_request" in item:
            continue
        if str(item.get("title", "")).startswith(_prefix(key)):
            return item
    return None


def raise_alert(repo: str, key: str, title: str, message: str, *,
                url: str = "", owner: str = "",
                transport: Transport) -> tuple[int, bool]:
    """Open the alert issue for ``key`` or note a repeat on the open one.

    Returns (issue number, created). The owner is mentioned only when the
    issue is created, so a condition that stays broken does not page daily
    through mentions; the repeat is a plain comment on the same issue.
    """
    existing = find_open(repo, key, transport)
    detail = message.strip() or "(no detail)"
    if url:
        detail += f"\n\n{url}"
    if existing is not None:
        number = int(existing["number"])
        transport("POST", f"/repos/{repo}/issues/{number}/comments",
                  {"body": f"Still failing: {title}\n\n{detail}"})
        return number, False
    mention = f"@{owner} " if re.fullmatch(r"[A-Za-z0-9-]{1,39}", owner or "") else ""
    body = (f"{mention}{detail}\n\n"
            "This issue is opened by the pipeline when an alert fires and is "
            "closed automatically by the next healthy check.")
    created = transport("POST", f"/repos/{repo}/issues",
                        {"title": f"{_prefix(key)} {title}"[:240],
                         "body": body, "labels": [LABEL]})
    return int(created["number"]), True


def resolve_alert(repo: str, key: str, note: str = "", *,
                  transport: Transport) -> int | None:
    """Close the open alert issue for ``key``. Returns its number, or None."""
    existing = find_open(repo, key, transport)
    if existing is None:
        return None
    number = int(existing["number"])
    transport("POST", f"/repos/{repo}/issues/{number}/comments",
              {"body": note.strip() or "Recovered: the check passed."})
    transport("PATCH", f"/repos/{repo}/issues/{number}",
              {"state": "closed", "state_reason": "completed"})
    return number


def env_transport() -> tuple[str, str, Transport]:
    """(repo, owner, transport) from the Actions environment."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        raise AlertError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")
    return repo, os.environ.get("GITHUB_REPOSITORY_OWNER", ""), _http_transport(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open or close a tracked pipeline alert")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("raise")
    up.add_argument("--key", required=True)
    up.add_argument("--title", required=True)
    up.add_argument("--message", default="")
    up.add_argument("--url", default="")
    down = sub.add_parser("resolve")
    down.add_argument("--key", required=True)
    down.add_argument("--note", default="")
    args = parser.parse_args(argv)
    try:
        repo, owner, transport = env_transport()
        if args.command == "raise":
            number, created = raise_alert(repo, args.key, args.title, args.message,
                                          url=args.url, owner=owner, transport=transport)
            print(f"alert issue #{number} {'opened' if created else 'updated'}")
        else:
            number = resolve_alert(repo, args.key, args.note, transport=transport)
            print(f"alert issue #{number} closed" if number else "no open alert")
    except AlertError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
