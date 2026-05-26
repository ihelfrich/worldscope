"""Sections are the unit of composition in a briefing.

Each section subclass implements one method:

    pull() -> list[dict]    fresh items from upstream sources

The base class handles everything else:

  - calling pull() and catching exceptions cleanly
  - distinguishing a clean-empty pull from a failed pull
  - storing snapshots with status metadata
  - honoring WORLDSCOPE_SKIP=<id> by reading the most-recent snapshot
    instead of re-pulling (carry-forward mode)
  - computing the day-over-day delta only when both today's and a prior
    snapshot exist
  - rendering an HTML block with visible staleness markers when a section
    is carried-forward or stale-after-failure

The result returned from resolve() is a SectionState that the orchestrator
uses for synthesis, rendering, and bundling.
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..store import SnapshotStore


# --- the state machine ----------------------------------------------------

# fresh                — pulled cleanly today, has items
# fresh_empty          — pulled cleanly today, returned zero items
# carry_forward        — explicitly skipped (WORLDSCOPE_SKIP); previous snapshot reused
# stale_after_failure  — pull threw; previous snapshot reused with stale marker
# no_data              — no prior snapshot AND today's pull empty/failed
STATE_FRESH = "fresh"
STATE_FRESH_EMPTY = "fresh_empty"
STATE_CARRY_FORWARD = "carry_forward"
STATE_STALE = "stale_after_failure"
STATE_NO_DATA = "no_data"


@dataclass
class SectionState:
    """What resolve() returns. The orchestrator uses these fields directly."""
    section_id: str
    title: str
    emoji: str
    state: str                       # one of the STATE_* constants
    items: list[dict]                # tagged with _id; possibly carried-forward
    new: list[dict]                  # items new since the comparison snapshot
    comparison_date: Optional[str]   # the date 'items' is being compared against
    source_date: Optional[str]       # the date 'items' actually came from
    error: Optional[str] = None
    extras: dict = field(default_factory=dict)


class Section(ABC):
    id: str = ""
    title: str = ""
    emoji: str = "📌"

    def __init__(self, store: Optional[SnapshotStore] = None) -> None:
        self.store = store or SnapshotStore()

    # ---- to implement -------------------------------------------------------

    @abstractmethod
    def pull(self) -> list[dict]:
        """Pull all currently-fresh items from upstream.
        Return a list of dicts with at minimum:
            {"id": str (optional; falls back to url+title hash),
             "date": str,
             "title": str,
             "url": str,
             "summary": str}
        Raise on hard failure; the base class catches and records it.
        """

    # ---- machinery (don't override unless needed) ---------------------------

    @staticmethod
    def _item_id(item: dict) -> str:
        if item.get("id"):
            return str(item["id"])
        h = hashlib.sha1()
        h.update((item.get("url", "") + "|" + item.get("title", "")).encode("utf-8"))
        return h.hexdigest()

    def _tag_ids(self, items: list[dict]) -> list[dict]:
        out = []
        for it in items:
            tagged = dict(it)
            tagged["_id"] = self._item_id(it)
            out.append(tagged)
        return out

    def _is_skipped(self) -> bool:
        skip_set = {s.strip() for s in (os.environ.get("WORLDSCOPE_SKIP") or "").split(",") if s.strip()}
        return self.id in skip_set

    def resolve(self, *, today: Optional[date] = None) -> SectionState:
        """Run the section through its state machine. Returns SectionState."""
        today = today or date.today()
        skipped = self._is_skipped()
        most_recent = self.store.most_recent(self.id)

        # CARRY-FORWARD: skip flag set AND we have a prior snapshot
        # (even an empty one — an empty prior is a valid state to carry forward).
        if skipped:
            if most_recent is not None:
                src_date = most_recent.get("snapshot_date")
                items = most_recent.get("items") or []
                # Comparison: the snapshot one step before the most-recent
                prior = self.store.previous(
                    self.id, before=date.fromisoformat(src_date)
                )
                new = self._compute_new(items, prior)
                return SectionState(
                    section_id=self.id, title=self.title, emoji=self.emoji,
                    state=STATE_CARRY_FORWARD,
                    items=items, new=new,
                    comparison_date=(prior or {}).get("snapshot_date"),
                    source_date=src_date,
                )
            # Skipped but no prior to carry forward.
            return SectionState(
                section_id=self.id, title=self.title, emoji=self.emoji,
                state=STATE_NO_DATA, items=[], new=[],
                comparison_date=None, source_date=None,
                error="skipped and no prior snapshot to carry forward",
            )

        # Normal path: try to pull. Distinguish failure from empty-ok.
        items: list[dict] = []
        error: Optional[str] = None
        try:
            raw = self.pull()
            items = self._tag_ids(raw or [])
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        # FAILED pull → keep last known good if it exists, mark stale.
        # Empty prior is still valid ("yesterday had 0 items").
        if error is not None:
            if most_recent is not None:
                src_date = most_recent.get("snapshot_date")
                prior = self.store.previous(
                    self.id, before=date.fromisoformat(src_date)
                )
                return SectionState(
                    section_id=self.id, title=self.title, emoji=self.emoji,
                    state=STATE_STALE,
                    items=most_recent.get("items") or [], new=[],
                    comparison_date=(prior or {}).get("snapshot_date"),
                    source_date=src_date,
                    error=error,
                )
            return SectionState(
                section_id=self.id, title=self.title, emoji=self.emoji,
                state=STATE_NO_DATA, items=[], new=[],
                comparison_date=None, source_date=None,
                error=error,
            )

        # SUCCESSFUL pull. Write today's snapshot with the right status.
        status = "ok" if items else "empty_ok"
        self.store.put(self.id, items, status=status, when=today)

        # Compute new vs. previous snapshot (the one before today's write).
        prior = self.store.previous(self.id, before=today)
        new = self._compute_new(items, prior)

        state = STATE_FRESH if items else STATE_FRESH_EMPTY
        return SectionState(
            section_id=self.id, title=self.title, emoji=self.emoji,
            state=state, items=items, new=new,
            comparison_date=(prior or {}).get("snapshot_date"),
            source_date=today.isoformat(),
        )

    @staticmethod
    def _compute_new(items: list[dict], prior: Optional[dict]) -> list[dict]:
        if not prior:
            return list(items)
        prior_ids = {it.get("_id") for it in (prior.get("items") or [])}
        return [it for it in items if it.get("_id") not in prior_ids]

    # ---- render -------------------------------------------------------------

    def render_html(self, state: SectionState, synth: Optional[str] = None) -> str:
        """HTML block for the section. Includes a visible staleness badge
        when state is carry_forward or stale_after_failure."""
        badge = self._staleness_badge(state)
        new_ids = {it.get("_id") for it in state.new}
        items_html = []
        for it in state.items[:50]:
            is_new = it.get("_id") in new_ids
            new_marker = "<span class='new-badge'>NEW</span>" if is_new else ""
            items_html.append(
                f"<li>{new_marker}<a href='{it.get('url','#')}'>"
                f"{it.get('title','(no title)')}</a>"
                f"<span class='meta'> · {it.get('date','')}</span>"
                f"<div class='abs'>{(it.get('summary','') or '')[:280]}</div></li>"
            )
        if not state.items:
            items_html.append("<li class='empty'>no items in this section.</li>")
        synth_html = f"<p class='synth'>{synth}</p>" if synth else ""
        return (
            f"<section class='section'>"
            f"<h2>{self.emoji} {self.title} "
            f"<span class='count'>· {len(new_ids)} new / {len(state.items)} total</span>"
            f"{badge}</h2>"
            f"{synth_html}"
            f"<ul class='items'>{''.join(items_html)}</ul>"
            f"</section>"
        )

    @staticmethod
    def _staleness_badge(state: SectionState) -> str:
        today = date.today()
        if state.state == STATE_CARRY_FORWARD and state.source_date:
            days_ago = (today - date.fromisoformat(state.source_date)).days
            label = f"carried from {state.source_date}" + (f" · {days_ago} day{'s' if days_ago != 1 else ''} ago" if days_ago > 0 else " · today")
            return (
                f"<span class='stale-badge stale-carry'>{label}</span>"
            )
        if state.state == STATE_STALE and state.source_date:
            days_ago = (today - date.fromisoformat(state.source_date)).days
            reason = state.error or "pull failed"
            return (
                f"<span class='stale-badge stale-failed' title='{reason}'>"
                f"stale · last good from {state.source_date} ({days_ago} day{'s' if days_ago != 1 else ''} ago)"
                f"</span>"
            )
        if state.state == STATE_NO_DATA:
            return "<span class='stale-badge stale-none'>section unavailable today</span>"
        return ""
