"""One self-maintaining issue that lists what a person must do for the sources.

The run report already says which sections failed and why, but the fixes that
only the owner can make (a secret to set, a rejected login to renew, a feed
that no longer exists) were scattered across daily logs and chat summaries.
This turns them into a single checklist issue:

- the issue body is rewritten in place each run, so it is always current;
- a comment is added only when the list changes, so a condition that stays
  broken does not notify every day;
- the issue closes itself when nothing is left to do.

Transient failures (an upstream outage younger than ``STALE_DAYS``) are left to
the next run and are not listed.

    python -m worldscope.source_actions --report dist/run_report.json
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from . import alerting

KEY = "source-actions"
TITLE = "Source fixes only you can make"
MARKER = "<!-- worldscope:source-actions -->"
STALE_DAYS = 3
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET_RE = re.compile(r"\b([A-Z][A-Z0-9]*_(?:[A-Z0-9]+_)*(?:KEY|TOKEN|PASSWORD|EMAIL|APPNAME|SECRET))\b")


@dataclass(frozen=True)
class Action:
    section: str
    kind: str      # credential | auth | retired | outage
    remedy: str
    detail: str


def _days_old(source_date: Any, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(source_date)[:10])).days
    except ValueError:
        return None


def _secrets(text: str) -> list[str]:
    return list(dict.fromkeys(_SECRET_RE.findall(text or "")))


def actions_from_report(report: Mapping[str, Any]) -> list[Action]:
    today = date.fromisoformat(str(report.get("date"))[:10])
    out: list[Action] = []
    for sid, entry in sorted((report.get("sections") or {}).items()):
        if not isinstance(entry, Mapping):
            continue
        etype = entry.get("error_type") or ""
        error = str(entry.get("error") or "")
        detail = error.split(": ", 1)[1] if error.startswith(etype + ": ") else error
        detail = _EMAIL_RE.sub("<email>", detail)
        secrets = _secrets(error)
        if etype == "MissingCredential":
            remedy = (f"Add repository secret {', '.join(f'`{s}`' for s in secrets)}"
                      if secrets else "Provide the missing credential")
            out.append(Action(sid, "credential", remedy, detail))
        elif etype == "UpstreamAuthError":
            remedy = (f"Renew or replace {', '.join(f'`{s}`' for s in secrets)}"
                      if secrets else "Renew the credentials this source rejected")
            out.append(Action(sid, "auth", remedy, detail))
        elif etype == "FeedRetired":
            out.append(Action(sid, "retired",
                              "Upstream retired this feed: pick a replacement or drop the section",
                              detail))
        elif etype:
            age = _days_old(entry.get("source_date"), today)
            if age is None or age > STALE_DAYS:
                since = f"no good data for {age} days" if age is not None else "never had good data"
                out.append(Action(sid, "outage", f"Investigate: {since}", detail))
    return out


_HEADINGS = {
    "credential": "Missing credentials",
    "auth": "Credentials the source rejected",
    "retired": "Feeds that no longer exist",
    "outage": "Long outages",
}


def render(actions: list[Action], report_date: str) -> str:
    lines = [MARKER, f"Status as of the **{report_date}** daily run. "
             "This list is rewritten by every run and the issue closes itself "
             "when it is empty.", ""]
    for kind, heading in _HEADINGS.items():
        group = [a for a in actions if a.kind == kind]
        if not group:
            continue
        lines.append(f"### {heading}")
        for a in group:
            lines.append(f"- [ ] **{a.section}**: {a.remedy}")
            if a.detail:
                lines.append(f"  <sub>{a.detail[:220]}</sub>")
        lines.append("")
    lines.append("Secrets go in Settings → Secrets and variables → Actions.")
    return "\n".join(lines).rstrip() + "\n"


def _listed_sections(body: str) -> set[str]:
    return set(re.findall(r"^- \[[ x]\] \*\*([^*]+)\*\*", body or "", flags=re.M))


def sync(repo: str, report: Mapping[str, Any], *, owner: str,
         transport: alerting.Transport) -> str:
    """Bring the tracked issue in line with the report. Returns what happened."""
    actions = actions_from_report(report)
    report_date = str(report.get("date"))
    if not actions:
        number = alerting.resolve_alert(
            repo, KEY, f"Resolved by the {report_date} run: every source has what it needs.",
            transport=transport)
        return f"closed #{number}" if number else "nothing to do"
    body = render(actions, report_date)
    existing = alerting.find_open(repo, KEY, transport)
    if existing is None:
        number, _ = alerting.raise_alert(repo, KEY, TITLE, body, owner=owner,
                                         transport=transport)
        return f"opened #{number} ({len(actions)} items)"
    number = int(existing["number"])
    before = _listed_sections(existing.get("body") or "")
    after = {a.section for a in actions}
    if existing.get("body") != body:
        transport("PATCH", f"/repos/{repo}/issues/{number}", {"body": body})
    if before != after:
        added, fixed = sorted(after - before), sorted(before - after)
        parts = []
        if fixed:
            parts.append("Fixed: " + ", ".join(fixed))
        if added:
            parts.append("New: " + ", ".join(added))
        transport("POST", f"/repos/{repo}/issues/{number}/comments",
                  {"body": f"{report_date}: " + ". ".join(parts) + "."})
        return f"updated #{number} ({'; '.join(parts)})"
    return f"refreshed #{number} (unchanged list)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", default="dist/run_report.json")
    parser.add_argument("--dry-run", action="store_true", help="print the issue body only")
    args = parser.parse_args(argv)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if args.dry_run:
        actions = actions_from_report(report)
        print(render(actions, str(report.get("date"))) if actions else "(no actions)")
        return 0
    try:
        repo, owner, transport = alerting.env_transport()
        print(sync(repo, report, owner=owner, transport=transport))
    except alerting.AlertError as exc:
        print(f"::warning::source actions not synced: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
