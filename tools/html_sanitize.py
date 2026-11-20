"""Allowlist HTML sanitizer for rendered briefs (stdlib only).

Markdown passes raw HTML through, and brief text is LLM output derived from
untrusted feeds, so rendered HTML is filtered before it is published.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "a", "p", "br", "hr", "strong", "em", "b", "i", "u", "code", "pre",
    "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "img", "span",
    "div", "sup", "sub", "del", "details", "summary",
}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript"}
VOID = {"br", "hr", "img"}
GLOBAL_ATTRS = {"id", "class", "title", "align"}
TAG_ATTRS = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
SAFE_SCHEMES = ("http://", "https://", "mailto:")


def _safe_url(value: str, *, allow_relative: bool) -> bool:
    v = "".join(ch for ch in value if ch >= " " and ch != "\x7f").strip().lower()
    if v.startswith(SAFE_SCHEMES):
        return True
    if not allow_relative or v.startswith("//"):
        return False
    head = v.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return ":" not in head


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip or tag not in ALLOWED_TAGS:
            return
        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            if value is None or name.startswith("on"):
                continue
            if name in GLOBAL_ATTRS or name in TAG_ATTRS.get(tag, ()):
                if name in ("href", "src") and not _safe_url(value, allow_relative=True):
                    continue
                kept.append(f' {name}="{html.escape(value, quote=True)}"')
        if tag == "a":
            kept.append(' rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag not in ALLOWED_TAGS or tag in VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(html.escape(data, quote=False))


def sanitize_html(fragment: str) -> str:
    parser = _Sanitizer()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.out)
