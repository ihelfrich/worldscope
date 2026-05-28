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
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from ..lib.content_filter import filter_items as _filter_junk
from ..store import SnapshotStore


class PullTimeout(Exception):
    """Raised when a section's pull() exceeds its deadline."""


def _run_with_timeout(fn, seconds: float):
    """Run `fn()` with a hard wall-clock deadline. If `seconds` elapse before
    fn returns, raise PullTimeout. Uses a daemon thread so a wedged network
    call doesn't block the whole interpreter (the thread keeps running in
    the background until process exit — Python has no clean way to cancel
    a blocking socket recv on the main thread, and signal-based timeouts
    don't work inside helper threads, so this is the pragmatic option)."""
    box: list = [None, None]   # [result, exception]
    def runner():
        try:
            box[0] = fn()
        except BaseException as exc:
            box[1] = exc
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise PullTimeout(f"pull() exceeded {seconds}s deadline (still running)")
    if box[1] is not None:
        raise box[1]
    return box[0]


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

    # Hard wall-clock deadline for pull(). A section can override (e.g.
    # SanctionsSection sets 180 for the 2.6 GB FtM scan). When exceeded the
    # section degrades to STATE_STALE with a timeout error and the previous
    # snapshot carries forward, instead of wedging the whole brief.
    PULL_TIMEOUT_S: float = 75

    # Apply the adult/scam/clickbait content filter (lib.content_filter).
    # Default on for every section so junk that leaks in through open feeds
    # (Google News proxies, GDELT, MediaCloud, regional RSS) gets dropped
    # before snapshot + render. Sections that legitimately surface these
    # terms (sanctions actions against adult platforms, court cases on
    # crypto fraud) can opt out by setting this to False.
    FILTER_ADULT_SCAM: bool = True

    # --- Section-adapter contract (see docs/SECTION_ADAPTER_CONTRACT.md) ---
    # Subclasses SHOULD override these for proper attribution + trust signaling.
    # Defaults below match the existing pre-contract sections so no migration
    # is required to keep working; contract artifacts will be empty/minimal
    # until each section sets these explicitly.
    source_id: str = ""                   # e.g. "federal-register"
    source_name: str = ""                 # e.g. "U.S. Federal Register"
    source_url: str = ""                  # canonical homepage / API root
    source_tier: str = "primary_document" # primary_document | mainstream_independent | ...
    source_license: str = "public-domain"
    attribution_required: bool = False
    attribution_text: Optional[str] = None
    source_country: Optional[str] = "US"
    source_language: str = "en"

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

    def _apply_content_filter(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """Drop adult/scam/clickbait items unless the section opts out."""
        if not items or not self.FILTER_ADULT_SCAM:
            return items, []
        return _filter_junk(items)

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
                # Filter previously-snapshotted junk so old caches don't
                # surface OnlyFans/scam content the filter would now drop.
                items, dropped = self._apply_content_filter(items)
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
                    extras={"filtered_count": len(dropped)} if dropped else {},
                )
            # Skipped but no prior to carry forward.
            return SectionState(
                section_id=self.id, title=self.title, emoji=self.emoji,
                state=STATE_NO_DATA, items=[], new=[],
                comparison_date=None, source_date=None,
                error="skipped and no prior snapshot to carry forward",
            )

        # Normal path: try to pull, with a hard deadline. Distinguish failure
        # from empty-ok.
        items: list[dict] = []
        dropped: list[dict] = []
        error: Optional[str] = None
        try:
            raw = _run_with_timeout(self.pull, self.PULL_TIMEOUT_S)
            items = self._tag_ids(raw or [])
            items, dropped = self._apply_content_filter(items)
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
                stale_items = most_recent.get("items") or []
                stale_items, stale_dropped = self._apply_content_filter(stale_items)
                return SectionState(
                    section_id=self.id, title=self.title, emoji=self.emoji,
                    state=STATE_STALE,
                    items=stale_items, new=[],
                    comparison_date=(prior or {}).get("snapshot_date"),
                    source_date=src_date,
                    error=error,
                    extras={"filtered_count": len(stale_dropped)} if stale_dropped else {},
                )
            return SectionState(
                section_id=self.id, title=self.title, emoji=self.emoji,
                state=STATE_NO_DATA, items=[], new=[],
                comparison_date=None, source_date=None,
                error=error,
            )

        # SUCCESSFUL pull. Write today's snapshot with the right status.
        status = "ok" if items else "empty_ok"
        wrote = self.store.put(self.id, items, status=status, when=today)

        # If the store refused the write (empty-can't-replace-non-empty
        # invariant), reload the retained same-day snapshot so the
        # downstream render + lake mirror see the morning's items rather
        # than this rerun's empty result.
        if not wrote:
            retained = self.store.get(self.id, when=today)
            if retained is not None:
                items = retained.get("items") or items
                # New vs the snapshot strictly before today (not "before today's
                # write" — there is no today write to compare against).
                prior = self.store.previous(self.id, before=today)
                new = self._compute_new(items, prior)
                state = STATE_FRESH if items else STATE_FRESH_EMPTY
                return SectionState(
                    section_id=self.id, title=self.title, emoji=self.emoji,
                    state=state, items=items, new=new,
                    comparison_date=(prior or {}).get("snapshot_date"),
                    source_date=today.isoformat(),
                    extras={"refused_empty_write": True,
                            **({"filtered_count": len(dropped)} if dropped else {})},
                )

        # Compute new vs. previous snapshot (the one before today's write).
        prior = self.store.previous(self.id, before=today)
        new = self._compute_new(items, prior)

        state = STATE_FRESH if items else STATE_FRESH_EMPTY
        return SectionState(
            section_id=self.id, title=self.title, emoji=self.emoji,
            state=state, items=items, new=new,
            comparison_date=(prior or {}).get("snapshot_date"),
            source_date=today.isoformat(),
            extras={"filtered_count": len(dropped)} if dropped else {},
        )

    @staticmethod
    def _compute_new(items: list[dict], prior: Optional[dict]) -> list[dict]:
        if not prior:
            return list(items)
        prior_ids = {it.get("_id") for it in (prior.get("items") or [])}
        return [it for it in items if it.get("_id") not in prior_ids]

    # ---- render -------------------------------------------------------------

    def render_html(self, state: SectionState, synth: Optional[str] = None) -> str:
        """HTML block for the section. Used by the legacy weekly renderer.

        Every interpolated item field is HTML-escaped to defend against XSS
        from upstream feeds (feed titles, GDELT article titles, etc. can
        and do contain malformed HTML/JS). URLs are scheme-filtered: only
        http(s) survives; javascript:/data:/file: become '#'.
        """
        import html as _html
        from urllib.parse import urlparse as _urlparse

        def _safe_url(u: str) -> str:
            if not u: return "#"
            try:
                scheme = _urlparse(u).scheme.lower()
            except Exception:
                return "#"
            return u if scheme in ("http", "https") else "#"

        badge = self._staleness_badge(state)
        new_ids = {it.get("_id") for it in state.new}
        items_html = []
        for it in state.items[:50]:
            is_new = it.get("_id") in new_ids
            new_marker = "<span class='new-badge'>NEW</span>" if is_new else ""
            url     = _html.escape(_safe_url(it.get("url") or ""), quote=True)
            title_  = _html.escape(it.get("title")   or "(no title)")
            date_s  = _html.escape(it.get("date")    or "")
            summary = _html.escape((it.get("summary") or "")[:280])
            items_html.append(
                f"<li>{new_marker}<a href='{url}'>{title_}</a>"
                f"<span class='meta'> · {date_s}</span>"
                f"<div class='abs'>{summary}</div></li>"
            )
        if not state.items:
            items_html.append("<li class='empty'>no items in this section.</li>")
        synth_html = (
            f"<p class='synth'>{_html.escape(synth)}</p>" if synth else ""
        )
        title  = _html.escape(self.title or "")
        emoji  = _html.escape(self.emoji or "")
        return (
            f"<section class='section'>"
            f"<h2>{emoji} {title} "
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

    # ---- Section-adapter contract artifacts ----------------------------------
    # The orchestrator calls these AFTER resolve(). Each method has a
    # sensible default so existing sections work unchanged; subclasses
    # override to add entity extraction, predictions, paper bets, etc.

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        """Map one in-memory item to a contract-shaped record_dict for the lake.
        Subclasses MAY override to enrich (e.g. detect language, add metadata).
        The dict returned here becomes one line in raw.jsonl AND one row in
        the lake's `records` table."""
        return {
            "id": item.get("_id") or self._item_id(item),
            "source_id": self.source_id or self.id,
            "section_id": self.id,
            "ingested_at_utc": today_iso,
            "original_url": item.get("url"),
            "original_text": (item.get("title", "") + " — " + (item.get("summary", "") or ""))[:500],
            "original_lang": self.source_language,
            "record_date": item.get("date"),
            "license": self.source_license,
            "entities": [],   # subclasses override extract_entities() to populate
            "extra": {k: v for k, v in item.items() if k not in (
                "_id", "id", "url", "title", "summary", "date"
            )},
        }

    def extract_entities(self, item: dict) -> list[dict]:
        """Return a list of entity-payload dicts mentioned in this item.
        Each entry: {id, type, canonical_name, aliases?, metadata?}.
        Default: empty. Subclasses override for entity-rich sources
        (federal register, courtlistener, OpenStates, etc.)."""
        return []

    def synthesize_summary(self, state: "SectionState") -> str:
        """Default markdown summary. Subclasses can override to do LLM-driven
        synthesis with section-specific prompts; the orchestrator's parallel
        sub-agents typically replace this whole method via an LLM Task call."""
        new_count = len(state.new)
        total = len(state.items)
        lines = [
            f"---",
            f"section: {self.id}",
            f"title: {self.title}",
            f"date: {state.source_date or ''}",
            f"record_count: {total}",
            f"new_today: {new_count}",
            f"state: {state.state}",
            f"---",
            f"",
            f"## {self.title}",
            f"",
            f"{new_count} new of {total} total items today.",
            f"",
        ]
        for it in state.items[:25]:
            is_new = it.get("_id") in {n.get("_id") for n in state.new}
            marker = "**NEW**  " if is_new else ""
            lines.append(
                f"- {marker}[{it.get('title','(no title)')}]({it.get('url','#')}) "
                f"— *{it.get('date','')}*"
            )
            summary = (it.get("summary") or "").strip()
            if summary:
                lines.append(f"  > {summary[:280]}")
        if total > 25:
            lines.append(f"")
            lines.append(f"_({total - 25} additional items in raw.jsonl)_")
        return "\n".join(lines) + "\n"

    def emit_structured(self, state: "SectionState") -> dict:
        """Produce the structured.json payload for the graph + predictions
        + paper-bets + anomalies tables. Default returns an empty shell;
        subclasses override to actually emit entity/relationship updates."""
        return {
            "section": self.id,
            "date": state.source_date or "",
            "record_count": len(state.items),
            "new_count": len(state.new),
            "entities_added": [],
            "entities_updated": [],
            "relationships": [],
            "predictions": [],
            "paper_bets": [],
            "anomalies": [],
        }

    def to_lake(self, state: "SectionState", lake=None) -> "ArtifactSet":
        """Write the three-artifact set under lake/sections/<section>/<date>/
        AND mirror the raw records into the lake's `records` table. The
        orchestrator calls this once per section after resolve() returns."""
        from datetime import datetime, timezone
        from ..lake import Lake, ArtifactSet

        lake = lake or Lake.open()
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        section_date = state.source_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Register the source row + record health
        lake.register_source(
            source_id=self.source_id or self.id,
            name=self.source_name or self.title or self.id,
            url=self.source_url or None,
            license=self.source_license,
            tier=self.source_tier,
            country=self.source_country,
            language=self.source_language,
            attribution_required=self.attribution_required,
            attribution_text=self.attribution_text,
        )

        raw_records: list[dict] = []
        if state.error:
            lake.record_source_health(
                self.source_id or self.id, success=False, error=state.error,
            )
        else:
            for item in state.items:
                record = self.to_raw_record(item, today_iso=today_iso)
                raw_records.append(record)
                # Mirror into the records table for queryability
                try:
                    lake.upsert_record(
                        record_id=record["id"],
                        source_id=record["source_id"],
                        section_id=record["section_id"],
                        original_url=record.get("original_url"),
                        original_text=record.get("original_text"),
                        original_lang=record.get("original_lang", "en"),
                        record_date=record.get("record_date"),
                        license=record.get("license"),
                        extra=record.get("extra"),
                    )
                except Exception as exc:
                    lake.add_to_quarantine(
                        q_id=record["id"],
                        source_id=record["source_id"],
                        section_id=record["section_id"],
                        raw_json=record,
                        validation_error=f"upsert_record: {type(exc).__name__}: {exc}",
                    )
            from ..lake import schema_hash_of
            lake.record_source_health(
                self.source_id or self.id,
                success=True,
                record_count=len(raw_records),
                schema_hash=schema_hash_of(state.items),
            )

        summary_md = self.synthesize_summary(state)
        structured = self.emit_structured(state)

        # Push the structured.json payload into the graph tables so the
        # MCP server's lookup_entity / graph_path / query_relationships
        # tools can see them. (The artifact files are useful but the
        # SQLite-backed graph is what makes ad-hoc queries fast.)
        try:
            for ent in structured.get("entities_added", []):
                lake.upsert_entity(
                    entity_id=ent["id"],
                    type=ent["type"],
                    canonical_name=ent["canonical_name"],
                    aliases=ent.get("aliases"),
                    metadata=ent.get("metadata"),
                )
            for ent in structured.get("entities_updated", []):
                lake.upsert_entity(
                    entity_id=ent["id"],
                    type=ent["type"],
                    canonical_name=ent["canonical_name"],
                    aliases=ent.get("aliases"),
                    metadata=ent.get("metadata"),
                )
            for rel in structured.get("relationships", []):
                lake.upsert_relationship(
                    from_id=rel["from"],
                    to_id=rel["to"],
                    type=rel["type"],
                    weight=rel.get("weight", 1.0),
                    evidence=rel.get("evidence"),
                )
            # Link records to their mentioned entities for the
            # record_entities M:N table.
            for rec in raw_records:
                for entity_id in rec.get("entities", []) or []:
                    lake.link_record_entity(rec["id"], entity_id)
            # Persist anomalies + predictions + paper bets the section emitted.
            import hashlib
            for anom in structured.get("anomalies", []):
                # Deterministic ID so re-runs idempotent
                aid = hashlib.sha1(
                    f"{self.id}|{anom.get('category','')}|{anom.get('description','')}|{section_date}".encode()
                ).hexdigest()
                lake.add_anomaly(
                    anomaly_id=aid,
                    section_id=self.id,
                    category=anom.get("category", "unknown"),
                    z_score=anom.get("z_score"),
                    description=anom.get("description", ""),
                    evidence=anom.get("evidence", []),
                )
            for pred in structured.get("predictions", []):
                pid = pred.get("id") or hashlib.sha1(
                    f"{self.id}|{pred.get('claim','')}|{section_date}".encode()
                ).hexdigest()
                lake.add_prediction(
                    prediction_id=pid,
                    target_date=pred.get("target_date"),
                    resolution_criteria=pred.get("resolution_criteria", ""),
                    predicted_outcome=pred.get("predicted_outcome", ""),
                    confidence=pred.get("confidence", 0.5),
                    training_window_days=pred.get("training_window_days"),
                    indicators_used=pred.get("indicators_used", []),
                    method=pred.get("method", "unspecified"),
                    evidence=pred.get("evidence", []),
                    section_id=self.id,
                )
            for bet in structured.get("paper_bets", []):
                bid = bet.get("id") or hashlib.sha1(
                    f"{self.id}|{bet.get('market_id','')}|{bet.get('side','')}|{section_date}".encode()
                ).hexdigest()
                lake.add_paper_bet(
                    bet_id=bid,
                    market_platform=bet.get("market_platform", "unknown"),
                    market_id=bet.get("market_id", ""),
                    market_url=bet.get("market_url"),
                    market_question=bet.get("market_question", ""),
                    market_resolves_at=bet.get("market_resolves_at"),
                    side=bet.get("side", "YES"),
                    size_usd=bet.get("size_usd", 0.0),
                    price_at_bet=bet.get("price_at_bet", 0.5),
                    rationale=bet.get("rationale", ""),
                    evidence=bet.get("evidence", []),
                    model_version=bet.get("model_version", "unspecified"),
                    confidence_band=bet.get("confidence_band", "medium"),
                    section_id=self.id,
                )
        except Exception as exc:
            # Don't let graph-population errors fail the artifact write.
            # The raw + summary + structured files are still safe on disk
            # and a subsequent run can re-attempt the graph population.
            import sys
            print(f"[{self.id}] graph population failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)

        artifacts = ArtifactSet(
            section_id=self.id,
            date=section_date,
            raw=raw_records,
            summary_md=summary_md,
            structured=structured,
        )
        lake.write_artifacts(artifacts)
        return artifacts
