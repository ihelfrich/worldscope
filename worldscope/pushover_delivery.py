"""Validated Pushover delivery and post-acceptance notification receipts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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


def main(argv: list[str] | None = None) -> int:
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
