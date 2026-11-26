"""One place for every Claude call the pipeline makes.

Before this module the model IDs were hardcoded in six files, three of them
read ``resp.content[0].text`` (which breaks whenever the first block is a
thinking block), and nothing recorded what a run spent.

``complete()`` returns the text of a response or raises ``LLMUnavailable``;
callers keep their own deterministic fallback. Usage is tallied per purpose
and written into the run report by ``brief.py``.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

try:
    import anthropic  # type: ignore
except ImportError:  # the pipeline runs without the SDK, on its fallbacks
    anthropic = None  # type: ignore

# Prose (section summaries, overview, market decisions) and bulk work
# (translation). Both can be overridden per environment.
SYNTH_MODEL = os.environ.get("WORLDSCOPE_SYNTH_MODEL", "claude-sonnet-5-5")
BULK_MODEL = os.environ.get("WORLDSCOPE_BULK_MODEL", "claude-haiku-5-5")

# USD per million tokens (input, output), for the run report's estimate only.
_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# These models think before they answer and the thinking counts against
# max_tokens, so a request sized for the answer alone comes back truncated.
_THINKING_HEADROOM = 4000

_lock = threading.Lock()
_usage: dict[tuple[str, str], dict] = {}


class LLMUnavailable(RuntimeError):
    """No credential, no SDK, an API error, a refusal or an empty answer."""


def available() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))


def response_text(resp) -> str:
    """All text blocks of a response joined; thinking blocks are skipped."""
    parts = [block.text or "" for block in (getattr(resp, "content", None) or [])
             if getattr(block, "type", None) == "text"]
    return "".join(parts).strip()


def strip_code_fence(text: str) -> str:
    """Drop a Markdown code fence wrapped around a JSON answer."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.split("\n")
                         if not line.startswith("```")).strip()
    return text


def _record(purpose: str, model: str, usage) -> None:
    tokens_in = sum(int(getattr(usage, name, 0) or 0) for name in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    with _lock:
        row = _usage.setdefault((purpose, model), {
            "purpose": purpose, "model": model,
            "calls": 0, "tokens_in": 0, "tokens_out": 0})
        row["calls"] += 1
        row["tokens_in"] += tokens_in
        row["tokens_out"] += tokens_out


def usage_summary() -> dict:
    """Calls and tokens per purpose this process, with an estimated cost."""
    with _lock:
        rows = [dict(row) for row in _usage.values()]
    total = 0.0
    for row in rows:
        price_in, price_out = _PRICES.get(row["model"], (0.0, 0.0))
        row["est_cost_usd"] = round(
            (row["tokens_in"] * price_in + row["tokens_out"] * price_out) / 1e6, 4)
        total += row["est_cost_usd"]
    rows.sort(key=lambda row: (row["purpose"], row["model"]))
    return {"calls": rows, "est_cost_usd": round(total, 4)}


def reset_usage() -> None:
    with _lock:
        _usage.clear()


def complete(prompt: str, *, purpose: str, model: Optional[str] = None,
             system: Optional[str] = None, max_tokens: int = 1000,
             effort: str = "low", timeout: float = 120.0) -> str:
    """Send one user message and return the answer text.

    ``max_tokens`` is the room wanted for the answer; headroom for thinking is
    added on top. Raises ``LLMUnavailable`` on every failure so a caller has
    one thing to catch before using its fallback.
    """
    if not available():
        raise LLMUnavailable("no ANTHROPIC_API_KEY or anthropic SDK")
    model = model or SYNTH_MODEL
    request: dict = {
        "model": model,
        "max_tokens": max_tokens + _THINKING_HEADROOM,
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        request["system"] = [{"type": "text", "text": system,
                              "cache_control": {"type": "ephemeral"}}]
    try:
        client = anthropic.Anthropic(timeout=timeout)
        resp = client.messages.create(**request)
    except Exception as exc:  # SDK errors are typed; all mean "use the fallback"
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
    _record(purpose, model, getattr(resp, "usage", None))
    if getattr(resp, "stop_reason", None) == "refusal":
        raise LLMUnavailable("the model declined the request")
    text = response_text(resp)
    if not text:
        raise LLMUnavailable("empty response")
    return text
