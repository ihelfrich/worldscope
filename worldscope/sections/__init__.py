"""Sections are the unit of composition in a briefing.

Each section subclass implements:
    pull()    -> list[dict]    fresh items from upstream sources
    delta()   -> list[dict]    items new since the previous snapshot
    synth()   -> str           LLM-synthesized prose paragraph
    render() -> str            HTML block for the daily page

The base class wires the snapshot store and the diff machinery so a section
author only writes the pull and the render — diffing is automatic.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from ..store import SnapshotStore


class Section(ABC):
    id: str = ""        # short stable id; used as snapshot key
    title: str = ""      # human-readable title for the briefing
    emoji: str = "📌"

    def __init__(self, store: SnapshotStore | None = None) -> None:
        self.store = store or SnapshotStore()

    # ---- to implement -------------------------------------------------------

    @abstractmethod
    def pull(self) -> list[dict]:
        """Pull all currently-fresh items from upstream. Return a list of
        normalized dicts with at minimum:
            {"id": str, "date": str, "title": str, "url": str, "summary": str}
        """

    # ---- machinery (don't override unless needed) ---------------------------

    @staticmethod
    def _item_id(item: dict) -> str:
        # Stable fingerprint for delta detection: prefer explicit id, else hash url+title.
        if item.get("id"):
            return str(item["id"])
        h = hashlib.sha1()
        h.update((item.get("url", "") + "|" + item.get("title", "")).encode("utf-8"))
        return h.hexdigest()

    def delta(self, *, today: date | None = None) -> dict:
        """Return {'new': [...], 'all': [...]}. 'new' = items absent from
        the most-recent prior snapshot."""
        items = self.pull()
        prior = self.store.previous(self.id, before=today)
        prior_ids = {p.get("_id") for p in (prior or [])}
        tagged = []
        for it in items:
            it = dict(it)
            it["_id"] = self._item_id(it)
            tagged.append(it)
        # Write today's snapshot so tomorrow's diff sees this
        self.store.put(self.id, tagged, when=today)
        new = [it for it in tagged if it["_id"] not in prior_ids]
        return {"new": new, "all": tagged}

    def render_html(self, delta_result: dict, synth: str | None = None) -> str:
        """Default HTML for a section: title, synthesis paragraph, list of items
        with NEW badges on the diff."""
        new_ids = {it["_id"] for it in delta_result.get("new", [])}
        items = delta_result.get("all", [])
        synth_html = f"<p class='synth'>{synth}</p>" if synth else ""
        items_html = []
        for it in items[:50]:
            new_badge = "<span class='new-badge'>NEW</span>" if it["_id"] in new_ids else ""
            items_html.append(
                f"<li>{new_badge}<a href='{it.get('url','#')}'>{it.get('title','(no title)')}</a>"
                f"<span class='meta'> · {it.get('date','')}</span>"
                f"<div class='abs'>{it.get('summary','')[:280]}</div></li>"
            )
        return (
            f"<section class='section'>"
            f"<h2>{self.emoji} {self.title} <span class='count'>· {len(new_ids)} new / {len(items)} total</span></h2>"
            f"{synth_html}"
            f"<ul class='items'>{''.join(items_html)}</ul>"
            f"</section>"
        )
