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


_SAFE_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def safe_href(url) -> str:
    """Return `url` if it is an absolute http(s) URL, else "#".

    `html.escape` alone does not make an href safe: `javascript:alert(1)` and
    `data:text/html;base64,...` contain nothing to escape and execute on
    click. Feed/scraper URLs are untrusted, so only http(s) is allowed through;
    leading whitespace/control characters (a classic filter bypass) are
    stripped before the scheme check. Call this BEFORE html.escape.
    """
    if not url:
        return "#"
    cleaned = re.sub(r"^[\s\x00-\x1f\x7f]+", "", str(url)).strip()
    if not cleaned or re.search(r"[\x00-\x1f\x7f]", cleaned):
        return "#"
    if not _SAFE_SCHEME_RE.match(cleaned):
        return "#"
    return cleaned
