"""Shared GDELT DOC 2.0 client with a process-wide request spacer.

GDELT's free tier allows one request every 5 seconds per IP and answers
anything faster with a plain-text 429. Three sections (gdelt_gkg,
gdelt_regions, conflict) query it in the same run; each used to pace itself
(0.7 s apart, or two workers in parallel) without regard to the others, so on
most days only one or two of them got through. Every DOC request in the
process now takes a slot from one spacer, including requests from a section
thread abandoned at its timeout that is still running.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "worldscope/0.1 (+https://github.com/ihelfrich/worldscope)"
MIN_INTERVAL_S = 5.5
PENALTY_S = 6.0  # extra spacing after a 429, on top of MIN_INTERVAL_S

_lock = threading.Lock()
_next_slot = 0.0


class GdeltUnavailable(Exception):
    """A DOC request did not produce JSON; the message says why."""


def _reserve(deadline: float) -> float | None:
    """Claim the next free slot; None if it would start after ``deadline``."""
    global _next_slot
    with _lock:
        slot = max(time.monotonic(), _next_slot)
        if slot > deadline:
            return None
        _next_slot = slot + MIN_INTERVAL_S
        return slot


def _penalise() -> None:
    global _next_slot
    with _lock:
        _next_slot = max(_next_slot, time.monotonic() + MIN_INTERVAL_S + PENALTY_S)


def reset() -> None:
    """Forget the spacer state (tests)."""
    global _next_slot
    with _lock:
        _next_slot = 0.0


def doc_get(params: dict[str, Any], *, timeout: float = 15.0,
            deadline: float = float("inf"), attempts: int = 2) -> dict:
    """GET the DOC API, spaced against every other caller in the process.

    Returns the decoded JSON. Raises GdeltUnavailable when the deadline leaves
    no slot, when every attempt is rate-limited, or on an HTTP/parse error.
    """
    last = "no attempt made"
    for _ in range(max(1, attempts)):
        slot = _reserve(deadline)
        if slot is None:
            raise GdeltUnavailable(f"out of time before a GDELT slot was free ({last})")
        time.sleep(max(0.0, slot - time.monotonic()))
        try:
            resp = requests.get(DOC_API, params=params, headers={"User-Agent": UA},
                                timeout=timeout)
        except requests.RequestException as exc:
            raise GdeltUnavailable(f"GDELT unreachable: {exc}") from exc
        if resp.status_code == 429:
            last = "HTTP 429 (rate limited)"
            _penalise()
            continue
        if resp.status_code != 200:
            raise GdeltUnavailable(f"GDELT answered HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            snippet = (resp.text or "").strip().replace("\n", " ")[:120]
            raise GdeltUnavailable(f"GDELT returned non-JSON: {snippet}") from exc
    raise GdeltUnavailable(last)
