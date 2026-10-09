"""Validated Pushover delivery and post-acceptance notification receipts."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping
from urllib import error, parse, request


API_URL = "https://api.pushover.net/1/messages.json"


class PushoverDeliveryError(RuntimeError):
    """The message was not accepted for an active Pushover destination."""


Transport = Callable[[Mapping[str, str]], Mapping[str, Any]]


def _post(payload: Mapping[str, str]) -> Mapping[str, Any]:
    req = request.Request(
        API_URL,
        data=parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read()
    except error.HTTPError as exc:
        raise PushoverDeliveryError(f"Pushover HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise PushoverDeliveryError(f"Pushover request failed: {exc.reason}") from exc
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PushoverDeliveryError("Pushover returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise PushoverDeliveryError("Pushover returned a non-object response")
    return value


def _validated_request_id(response: Mapping[str, Any]) -> str:
    errors = response.get("errors")
    if errors:
        detail = "; ".join(str(item) for item in errors) \
            if isinstance(errors, list) else str(errors)
        raise PushoverDeliveryError(detail)
    if response.get("info"):
        # Includes the HTTP-200 soft failure "no active devices to send to".
        raise PushoverDeliveryError(str(response["info"]))
    request_id = response.get("request")
    if response.get("status") != 1 or not isinstance(request_id, str) or not request_id:
        raise PushoverDeliveryError("Pushover did not accept the message")
    return request_id


def deliver(*, user_key: str, app_token: str, title: str, message: str,
            url: str = "", priority: int = 0,
            transport: Transport | None = None) -> str:
    if not user_key or not app_token:
        raise PushoverDeliveryError("Pushover credentials missing")
    payload = {"token": app_token, "user": user_key, "title": title,
               "message": message, "priority": str(priority)}
    if url:
        payload.update(url=url, url_title="Full brief")
    return _validated_request_id((transport or _post)(payload))


def _load_sent(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PushoverDeliveryError(f"invalid notification marker: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PushoverDeliveryError(f"invalid notification marker: {path}")
    return value


def _write_sent(path: Path, sent: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as handle:
        json.dump(sent, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def send_and_record(*, brief: str, sent_file: Path, user_key: str,
                    app_token: str, title: str, message: str, url: str = "",
                    priority: int = 0, transport: Transport | None = None) -> str:
    """Record a brief only after validated Pushover acceptance."""
    sent_file = Path(sent_file)
    sent = _load_sent(sent_file)
    if brief in sent:
        return "already-recorded"
    request_id = deliver(user_key=user_key, app_token=app_token, title=title,
                         message=message, url=url, priority=priority,
                         transport=transport)
    sent.append(brief)
    _write_sent(sent_file, sent)
    return request_id



# ---------------------------------------------------------------------------
# Brief picker (used by .github/workflows/pushover-brief.yml, step "pick").
#
# CI checkouts give every file the checkout time as mtime, so `ls -t` is
# meaningless there. The newest brief is chosen by the date embedded in the
# filename instead: briefings/YYYY-MM-DD.md (daily) and
# weekly_briefings/YYYY-Www.md (weekly). A daily brief dated today or
# yesterday (UTC) beats a weekly; a daily older than two days is never
# notified so a stale brief can't be re-announced by the scheduled fallback.
# ---------------------------------------------------------------------------

_DAILY_RE = re.compile(r"^briefings/(\d{4}-\d{2}-\d{2})\.md$")
_WEEKLY_RE = re.compile(r"^weekly_briefings/(\d{4})-W(\d{2})\.md$")

MAX_DAILY_AGE_DAYS = 2
MAX_WEEKLY_AGE_WEEKS = 1
BODY_LIMIT_BYTES = 900


def brief_date(path: str) -> tuple[str, date] | None:
    """Return ("daily"|"weekly", date) for a brief path, or None if the
    filename doesn't carry a date. Weekly briefs are dated by the Friday of
    their ISO week (the day the weekly routine publishes)."""
    name = str(path).replace("\\", "/")
    m = _DAILY_RE.match(name)
    if m:
        try:
            return "daily", date.fromisoformat(m.group(1))
        except ValueError:
            return None
    m = _WEEKLY_RE.match(name)
    if m:
        try:
            return "weekly", date.fromisocalendar(int(m.group(1)), int(m.group(2)), 5)
        except ValueError:
            return None
    return None


def pick_brief(candidates: list[str], *, today: date,
               sent: set[str] | frozenset[str] = frozenset()) -> str | None:
    """Choose the brief to notify on, by filename date (never by mtime).

    Preference order: newest daily dated today/yesterday, then a weekly from
    the current or previous ISO week, then a daily exactly two days old.
    Anything already in `sent`, undated, future-dated or older is ignored.
    Returns None when nothing fresh is left.
    """
    fresh_daily: list[tuple[date, str]] = []
    older_daily: list[tuple[date, str]] = []
    weekly: list[tuple[date, str]] = []
    for cand in candidates:
        cand = str(cand).replace("\\", "/")
        if cand in sent:
            continue
        parsed = brief_date(cand)
        if parsed is None:
            continue
        kind, d = parsed
        age = (today - d).days
        if kind == "daily":
            if age < 0 or age > MAX_DAILY_AGE_DAYS:
                continue
            (fresh_daily if age <= 1 else older_daily).append((d, cand))
        else:
            this_week = today.isocalendar()[:2]
            that_week = d.isocalendar()[:2]
            weeks_ago = ((date.fromisocalendar(*this_week, 1)
                          - date.fromisocalendar(*that_week, 1)).days // 7)
            if weeks_ago < 0 or weeks_ago > MAX_WEEKLY_AGE_WEEKS:
                continue
            weekly.append((d, cand))
    for bucket in (fresh_daily, weekly, older_daily):
        if bucket:
            return max(bucket)[1]
    return None


def brief_headline(md_text: str) -> str:
    """Notification title: the first H1 if there is one, else the first
    non-empty line (the shell version used `head -1 | sed 's/^# *//'`)."""
    lines = md_text.splitlines()
    for line in lines:
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()
    for line in lines:
        if line.strip():
            return line.strip()
    return ""


def brief_body(md_text: str, limit: int = BODY_LIMIT_BYTES) -> str:
    """Pushover body: drop the H1, strip markdown noise, truncate to `limit`
    bytes on a UTF-8 boundary (the shell did this with `head -c`, which
    closes the pipe early and SIGPIPEs `sed` under `set -o pipefail`)."""
    lines = md_text.splitlines(keepends=True)
    rest = "".join(lines[1:])
    rest = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", rest)
    rest = re.sub(r"[*_`#>]+", "", rest)
    return rest.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _github_output_lines(brief: str, headline: str) -> list[str]:
    kind, _ = brief_date(brief) or ("daily" if brief.startswith("briefings/") else "weekly", None)
    stem = Path(brief).stem
    if kind == "daily":
        return [f"file={brief}", "kind=Daily", f"url_path=briefings/{stem}.html",
                f"headline={headline}"]
    return [f"file={brief}", "kind=Weekly", f"url_path=weekly_briefings/{stem}.html",
            f"headline={headline}"]


def pick_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pushover_delivery pick",
                                     description="Pick the brief to notify on")
    parser.add_argument("--sent-file", type=Path, required=True)
    parser.add_argument("--manual", default="",
                        help="explicit brief path (workflow_dispatch input); "
                             "bypasses the freshness rule, not the sent marker")
    parser.add_argument("--today", default=None, help="ISO date (default: UTC today)")
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    root = args.root
    sent = set(_load_sent(args.sent_file)) if args.sent_file.exists() else set()
    today = date.fromisoformat(args.today) if args.today else _utc_today()
    outputs = ["file="]

    manual = args.manual.strip().replace("\\", "/")
    chosen: str | None = None
    if manual and (root / manual).is_file():
        chosen = manual
    else:
        candidates = sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for pattern in ("briefings/*.md", "weekly_briefings/*.md")
            for p in root.glob(pattern)
        )
        chosen = pick_brief(candidates, today=today, sent=sent)
        if chosen is None:
            print(f"no fresh brief (today={today.isoformat()}, "
                  f"{len(candidates)} candidates, {len(sent)} already notified)")

    if chosen is not None and chosen in sent:
        print(f"already notified for {chosen}, skipping")
        chosen = None

    if chosen is not None:
        md_text = (root / chosen).read_text(encoding="utf-8")
        headline = brief_headline(md_text)
        args.body_file.write_text(brief_body(md_text), encoding="utf-8")
        outputs = _github_output_lines(chosen, headline)
        print(f"picked {chosen}: {headline}")

    text = "\n".join(outputs) + "\n"
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text, end="")
    return 0


def _utc_today() -> date:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date()


def main(argv: list[str] | None = None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["pick"]:
        return pick_main(argv[1:])
    parser = argparse.ArgumentParser(description="Send a validated Pushover message")
    parser.add_argument("--title", required=True)
    body = parser.add_mutually_exclusive_group(required=True)
    body.add_argument("--message")
    body.add_argument("--message-file", type=Path)
    parser.add_argument("--url", default="")
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--brief")
    parser.add_argument("--sent-file", type=Path)
    args = parser.parse_args(argv)
    if bool(args.brief) != bool(args.sent_file):
        parser.error("--brief and --sent-file must be supplied together")
    message = args.message_file.read_text(encoding="utf-8") \
        if args.message_file else (args.message or "")
    common = {
        "user_key": os.environ.get("PUSHOVER_USER_KEY", ""),
        "app_token": os.environ.get("PUSHOVER_APP_TOKEN", ""),
        "title": args.title, "message": message, "url": args.url,
        "priority": args.priority,
    }
    try:
        request_id = send_and_record(brief=args.brief, sent_file=args.sent_file,
                                     **common) if args.brief else deliver(**common)
    except PushoverDeliveryError as exc:
        print(f"::error::{exc}")
        return 1
    print(f"Pushover accepted request {request_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
