"""
LLM synthesis with strict anti-hallucination prompt.

Rule: every claim in the synthesized paragraph must trace to one of the items
in the input list. The prompt instructs the model to cite by item index and
refuse to include claims it cannot ground.

If the API is unconfigured or the call fails for any reason, ``synthesize``
returns a deterministic, fact-derived fallback string rather than raising, so
a single bad response never aborts the surrounding briefing run.
"""
from __future__ import annotations

import os
from typing import Optional

# Optional Anthropic SDK import — gracefully no-op if not configured
try:
    import anthropic  # type: ignore
except ImportError:
    anthropic = None  # type: ignore

SYSTEM = """You are a research-grade desk officer writing a daily intelligence
briefing for an economist. The brief must be:

  - Specific. Names, dates, dollar amounts, statute citations — never vague.
  - Sourced. Every concrete claim must come from one of the numbered items
    provided. If the items do not support a claim, do not make it.
  - Tight. Aim for a single paragraph, 3–6 sentences. No bullet lists.
  - Honest about novelty. If today's items are routine (e.g., scheduled
    agency notices), say so plainly. Do not manufacture importance.

Never invent figures, names, or dates. If you cannot ground a sentence in
the provided items, omit the sentence.
"""

PROMPT = """Section: {section_title}

Today's items (numbered):
{items_text}

Items NEW since the previous run: {new_indices}

Write a single paragraph synthesizing what changed today, prioritizing the
NEW items. If nothing of consequence is new, say so directly in one sentence.
Do not list every item — synthesize. Cite specifics from the items only.
"""


def _first_text(resp) -> str:
    """Extract the first text block from a messages response, defensively.

    Adaptive-thinking models, refusals, and empty completions can yield a
    response whose first content block is not text (or whose content list is
    empty), so indexing ``resp.content[0].text`` directly can raise. Return
    "" when no text block is present.
    """
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return (block.text or "").strip()
    return ""


def synthesize(section_title: str, items: list[dict], new_ids: set[str]) -> str:
    """Returns the synthesized paragraph. Falls back to a deterministic
    fallback string if the API isn't configured or the call fails."""
    if not items:
        return f"No new items in {section_title} today."

    # Render items as a numbered list the model can cite by index
    lines = []
    new_indices = []
    for i, it in enumerate(items[:30], 1):
        is_new = it.get("_id") in new_ids
        if is_new:
            new_indices.append(i)
        tag = " [NEW]" if is_new else ""
        lines.append(
            f"{i}.{tag} ({it.get('date','?')}) {it.get('title','(no title)')}"
            f" — {it.get('summary','')[:300]}"
        )
    items_text = "\n".join(lines)

    def _fallback() -> str:
        # Deterministic prose so the pipeline keeps working offline or on
        # any API failure.
        n = len(new_indices)
        if n == 0:
            return f"No new items in {section_title} today (last seen items unchanged)."
        return (
            f"{n} new {section_title.lower()} item(s) today. "
            f"Most recent: {items[0].get('title','')[:160]}."
        )

    if anthropic is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback()

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=400,
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": PROMPT.format(
                    section_title=section_title,
                    items_text=items_text,
                    new_indices=", ".join(str(i) for i in new_indices) or "none",
                )
            }],
        )
    except Exception as exc:  # network/auth/API errors must not abort the brief
        print(f"[synth] {section_title}: API call failed "
              f"({type(exc).__name__}: {exc}); using deterministic fallback")
        return _fallback()
    text = _first_text(resp)
    return text or _fallback()
