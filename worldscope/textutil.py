"""textutil — shared text hygiene for display and prompts.

Feed titles/summaries routinely carry embedded HTML (``<figure><img …>``,
``<a>`` wrappers, entity-encoded markup). Rendered straight, that HTML either
shows up as literal angle-bracket text (when escaped) or breaks layout. These
helpers strip it to clean plain text before display or before it goes into an
LLM prompt.
"""
from __future__ import annotations

import html as _html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# scraper wrapper some sources emit, e.g. "[TITLE: foo | LEDE: bar]"
_WRAP_RE = re.compile(r"\[(?:TITLE|LEDE)\s*[:\]]", re.IGNORECASE)


def strip_html(text) -> str:
    """Return plain text: tags removed, entities decoded, whitespace collapsed.

    Two tag passes bracket the entity-decode so markup that was entity-encoded
    (``&lt;img&gt;``) is also removed rather than revealed."""
    if not text:
        return ""
    t = _TAG_RE.sub(" ", str(text))
    t = _html.unescape(t)
    t = _TAG_RE.sub(" ", t)
    t = _WRAP_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def clean_text(text, maxlen: int | None = None) -> str:
    """strip_html plus optional length cap with an ellipsis."""
    t = strip_html(text)
    if maxlen is not None and len(t) > maxlen:
        t = t[:maxlen].rstrip() + "…"
    return t
