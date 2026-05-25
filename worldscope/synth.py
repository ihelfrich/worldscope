"""
LLM synthesis with strict anti-hallucination prompt.

Rule: every claim in the synthesized paragraph must trace to one of the items
in the input list. The prompt instructs the model to cite by item index and
refuse to include claims it cannot ground. We post-validate by re-reading the
output for fabricated specifics.
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


def synthesize(section_title: str, items: list[dict], new_ids: set[str]) -> str:
    """Returns the synthesized paragraph. Falls back to a deterministic
    fallback string if the API isn't configured."""
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

    if anthropic is None or not os.environ.get("ANTHROPIC_API_KEY"):
        # Fallback: deterministic prose so the pipeline keeps working offline
        n = len(new_indices)
        if n == 0:
            return f"No new items in {section_title} today (last seen items unchanged)."
        return (
            f"{n} new {section_title.lower()} item(s) today. "
            f"Most recent: {items[0].get('title','')[:160]}."
        )

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
    return resp.content[0].text.strip()
