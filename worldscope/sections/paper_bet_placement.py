"""
paper_bet_placement — the sub-agent that decides where to place paper bets.

Architecturally this is a "derived" section: it reads other sections' output
from today (their summary.md + structured.json files) rather than pulling from
an upstream API. It runs LAST in the daily registry so it can synthesize
across everything.

For each high-volume market in today's paper_bets pull, it asks Claude
(Sonnet) to compare today's evidence base against the current market price.
If the system's credence diverges from the market by >= EDGE_THRESHOLD (8%)
AND the evidence is at least medium-confidence, the section records a paper
bet via lake.add_paper_bet().

Sizing follows Kelly-lite:
    size_usd = BASE_UNIT * min(edge * 5, 1.0) * confidence_multiplier
where edge = |our_credence - market_price|, confidence_multiplier in
{0.5, 1.0, 1.5} for {low, medium, high}.

No real money is ever touched. paper_bets is a SQLite table. No platform
auth, no signed transactions, no order books. Pure simulation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import Section, SectionState

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAKE_SECTIONS = REPO_ROOT / "lake" / "sections"

# Decision thresholds
EDGE_THRESHOLD = 0.08            # Place a bet only if |our - market| >= 8%
BASE_UNIT_USD = 100.0            # Notional unit per bet (paper dollars)
MAX_BET_FRACTION = 0.05          # No single bet > 5% of total notional risked
MAX_NEW_BETS_PER_DAY = 8         # Conservative cap to avoid over-trading
MODEL = "claude-sonnet-4-6"      # Reasoning model for the placement decision


def _confidence_multiplier(band: str) -> float:
    return {"low": 0.5, "medium": 1.0, "high": 1.5}.get(band, 1.0)


def _kelly_lite_size(edge: float, confidence_band: str) -> float:
    """Conservative Kelly-style sizing: scales with edge, capped at base unit."""
    return BASE_UNIT_USD * min(edge * 5.0, 1.0) * _confidence_multiplier(confidence_band)


def _load_section_summaries(target_date: str, exclude: set[str]) -> dict[str, str]:
    """Read today's summary.md from every section EXCEPT the ones we're
    making decisions for (paper_bets) or this very section."""
    out: dict[str, str] = {}
    if not LAKE_SECTIONS.exists():
        return out
    for section_dir in LAKE_SECTIONS.iterdir():
        if not section_dir.is_dir(): continue
        if section_dir.name in exclude: continue
        summary_path = section_dir / target_date / "summary.md"
        if summary_path.exists():
            # Cap each section at 4KB so the prompt stays bounded
            text = summary_path.read_text(encoding="utf-8")
            if len(text) > 4000:
                text = text[:4000] + "\n... [truncated]"
            out[section_dir.name] = text
    return out


def _load_market_state(target_date: str) -> list[dict]:
    """Read the day's paper_bets raw.jsonl to get the market snapshot."""
    market_file = LAKE_SECTIONS / "paper_bets" / target_date / "raw.jsonl"
    if not market_file.exists():
        # Fall back to the most recent date we have
        section_dir = LAKE_SECTIONS / "paper_bets"
        if not section_dir.exists(): return []
        dates = sorted([d.name for d in section_dir.iterdir() if d.is_dir()],
                       reverse=True)
        if not dates: return []
        market_file = section_dir / dates[0] / "raw.jsonl"
    markets = []
    with open(market_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                extra = rec.get("extra") or {}
                # Only include markets with a known yes_price and reasonable volume
                if extra.get("yes_price") is None: continue
                markets.append({
                    "id": rec.get("id"),
                    "platform": extra.get("platform"),
                    "market_id": extra.get("market_id"),
                    "question": extra.get("question") or rec.get("original_text", ""),
                    "url": rec.get("original_url"),
                    "yes_price": extra.get("yes_price"),
                    "end_date": extra.get("end_date"),
                    "volume": extra.get("volume_usd") or extra.get("volume_mana") or 0,
                })
            except json.JSONDecodeError:
                continue
    # Sort by volume desc, cap at 30 to keep prompt bounded
    markets.sort(key=lambda m: -(m.get("volume") or 0))
    return markets[:30]


def _call_claude_for_decisions(summaries: dict[str, str],
                               markets: list[dict]) -> list[dict]:
    """Ask Claude Sonnet which markets are mispriced given today's evidence.
    Returns a list of decision dicts. Empty list on any failure (degraded ok)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.stderr.write("[paper_bet_placement] no ANTHROPIC_API_KEY; skipping placement\n")
        return []
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.stderr.write("[paper_bet_placement] anthropic SDK missing; skipping placement\n")
        return []

    # Compose the evidence brief
    evidence_blocks = []
    for sec, text in summaries.items():
        evidence_blocks.append(f"### {sec}\n\n{text}\n")
    evidence_md = "\n---\n".join(evidence_blocks) if evidence_blocks else "(no section summaries available yet)"

    # Compose the market roster
    market_lines = []
    for i, m in enumerate(markets, start=1):
        price = m.get("yes_price")
        market_lines.append(
            f"{i}. [{m.get('platform')}] {m.get('question','')[:160]}\n"
            f"   market_id={m.get('market_id')}  yes_price={price:.3f}  "
            f"vol={m.get('volume',0):,.0f}  ends={m.get('end_date') or 'n/a'}"
        )
    market_roster = "\n".join(market_lines)

    system_prompt = (
        "You are the paper-bet decision module for an OSINT research platform. "
        "You are given today's evidence summaries across multiple sections plus "
        "a list of active prediction markets with current YES prices.\n\n"
        "Your job: identify markets where the evidence in today's summaries "
        "suggests the true probability is meaningfully different from the "
        "current market price. For each such market, output a decision JSON "
        "specifying side (YES/NO), our internal credence (0-1), confidence "
        "band (low/medium/high), and the rationale citing specific evidence.\n\n"
        "Hard rules:\n"
        f"- Only output decisions where |credence - market_price| >= {EDGE_THRESHOLD}.\n"
        "- Confidence band 'low' for thin evidence, 'medium' for solid single-"
        "  source evidence, 'high' for multi-source converging evidence.\n"
        "- Cite specific section names in the rationale (e.g. 'per state_bills "
        "  section: California AB-2643 just passed Privacy committee').\n"
        f"- Output AT MOST {MAX_NEW_BETS_PER_DAY} decisions, ranked by edge × confidence.\n"
        "- Output VALID JSON only — a single array of objects. No prose, no "
        "  markdown fences, no commentary.\n\n"
        "Schema per decision:\n"
        "{\n"
        '  "market_id": str,\n'
        '  "platform": str,\n'
        '  "side": "YES" | "NO",\n'
        '  "credence": float 0-1,\n'
        '  "confidence_band": "low" | "medium" | "high",\n'
        '  "rationale": str,\n'
        '  "evidence_sections": [str, ...]\n'
        "}"
    )
    user_prompt = (
        f"## Today's evidence (across sections)\n\n{evidence_md}\n\n"
        f"## Active prediction markets (top 30 by volume)\n\n{market_roster}\n\n"
        "Return the JSON array of decisions now."
    )

    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(L for L in lines if not L.startswith("```"))
        decisions = json.loads(text)
        if not isinstance(decisions, list):
            return []
        return decisions
    except Exception as exc:
        sys.stderr.write(f"[paper_bet_placement] Claude call failed: {type(exc).__name__}: {exc}\n")
        return []


class PaperBetPlacementSection(Section):
    id = "paper_bet_placement"
    title = "Paper-Bet Placement (today's new positions)"
    emoji = "🎲"

    source_id = "paper-bet-placement-synth"
    source_name = "Paper-bet placement sub-agent"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "prediction_market"
    source_license = "internal-synthesis"
    attribution_required = False
    source_country = None
    source_language = "en"

    PULL_TIMEOUT_S = 120

    def pull(self) -> list[dict]:
        """Read today's section summaries + market state, ask Claude where to
        place bets, record them via lake.add_paper_bet(). Returns the placed
        bets as items for the standard contract pipeline."""
        from ..lake import Lake

        today = date.today().isoformat()
        # Read today's summaries from every other section
        summaries = _load_section_summaries(
            today,
            exclude={"paper_bets", "paper_bet_placement"},
        )
        markets = _load_market_state(today)

        if not markets:
            return [{
                "id": "paper-bet-placement-no-markets",
                "date": today,
                "title": "[paper_bet_placement] No market state yet — skipping placement",
                "url": "",
                "summary": "Could not read lake/sections/paper_bets/<today>/raw.jsonl. "
                           "Either the paper_bets section hasn't run today, or the "
                           "lake doesn't have the daily snapshot yet.",
                "_skipped": True,
            }]

        decisions = _call_claude_for_decisions(summaries, markets)

        if not decisions:
            return [{
                "id": f"paper-bet-placement-{today}-no-decisions",
                "date": today,
                "title": "[paper_bet_placement] No bets placed today",
                "url": "",
                "summary": "The decision module did not identify any markets "
                           "with sufficient edge today. Either evidence converges "
                           "with market consensus, or Claude API was unavailable.",
                "_skipped": True,
            }]

        # Index markets for lookup by market_id
        market_by_id = {m["market_id"]: m for m in markets if m.get("market_id")}

        lake = Lake.open()
        placed_items: list[dict] = []

        for d in decisions[:MAX_NEW_BETS_PER_DAY]:
            try:
                market_id = d.get("market_id")
                if not market_id or market_id not in market_by_id:
                    continue
                market = market_by_id[market_id]
                price = float(market.get("yes_price") or 0.5)
                credence = float(d.get("credence") or 0.5)
                side = d.get("side", "YES").upper()
                confidence_band = d.get("confidence_band", "medium").lower()
                if confidence_band not in {"low", "medium", "high"}:
                    confidence_band = "medium"
                edge = abs(credence - price)
                if edge < EDGE_THRESHOLD:
                    continue   # safety check; Claude should have filtered

                size_usd = round(_kelly_lite_size(edge, confidence_band), 2)
                bet_id = hashlib.sha1(
                    f"{today}|{market_id}|{side}".encode()
                ).hexdigest()

                lake.add_paper_bet(
                    bet_id=bet_id,
                    market_platform=market.get("platform", "unknown"),
                    market_id=market_id,
                    market_url=market.get("url"),
                    market_question=market.get("question", ""),
                    market_resolves_at=market.get("end_date"),
                    side=side,
                    size_usd=size_usd,
                    price_at_bet=price,
                    rationale=d.get("rationale", "")[:1000],
                    evidence=d.get("evidence_sections", []),
                    model_version=f"placement-v1::{MODEL}",
                    confidence_band=confidence_band,
                    section_id=self.id,
                )

                placed_items.append({
                    "id": bet_id,
                    "date": today,
                    "title": (f"[{market.get('platform','?')}] {side} @ ${price:.3f} "
                              f"(credence {credence:.2f}, edge {edge*100:.1f}%) — "
                              f"{market.get('question','')[:120]}"),
                    "url": market.get("url", ""),
                    "summary": (f"size=${size_usd:.2f}  confidence={confidence_band}  "
                                f"rationale: {d.get('rationale','')[:300]}"),
                    "bet_id": bet_id,
                    "side": side,
                    "size_usd": size_usd,
                    "credence": credence,
                    "market_price": price,
                    "edge": edge,
                    "confidence_band": confidence_band,
                    "evidence_sections": d.get("evidence_sections", []),
                    "rationale": d.get("rationale", ""),
                    "market_platform": market.get("platform"),
                    "market_id": market_id,
                })
            except Exception as exc:
                sys.stderr.write(
                    f"[paper_bet_placement] failed to record bet for "
                    f"market {d.get('market_id')}: {type(exc).__name__}: {exc}\n"
                )

        return placed_items

    def synthesize_summary(self, state_obj: SectionState) -> str:
        # Custom summary that surfaces today's placements specifically
        today = state_obj.source_date or date.today().isoformat()
        bets_today = [it for it in state_obj.items if not it.get("_skipped")]
        skipped = [it for it in state_obj.items if it.get("_skipped")]

        lines = [
            "---",
            f"section: {self.id}",
            f"title: {self.title}",
            f"date: {today}",
            "---",
            "",
            f"## {self.emoji} {self.title}",
            "",
            f"**New paper bets placed today:** {len(bets_today)}",
            "",
        ]
        if skipped:
            for s in skipped:
                lines.append(f"_{s.get('title','')}_")
                lines.append("")
        if not bets_today:
            lines.append("(no positions added today)")
            return "\n".join(lines) + "\n"

        # Sort by edge × size for display
        bets_today.sort(
            key=lambda b: -(b.get("edge", 0) * b.get("size_usd", 0))
        )
        for bet in bets_today:
            lines.append(f"### {bet['side']} @ ${bet['market_price']:.3f}  ({bet['market_platform']})")
            lines.append(f"**Market**: {bet.get('rationale','')[:5]}{bet['title'].split('— ',1)[-1] if '— ' in bet['title'] else ''}")
            lines.append(f"- our credence: **{bet['credence']:.2f}** (edge **{bet['edge']*100:.1f}%**)")
            lines.append(f"- size: **${bet['size_usd']:.2f}**  confidence: **{bet['confidence_band']}**")
            lines.append(f"- evidence cited: {', '.join(bet.get('evidence_sections', []))}")
            lines.append(f"- rationale: {bet.get('rationale','')[:400]}")
            lines.append("")

        # Disclosure footer per the contract
        lines.append("---")
        lines.append("**Disclosure:** these are simulated bets only. "
                     "No real money is staked. Decisions made by "
                     f"{MODEL}; sizing follows Kelly-lite at "
                     f"base ${BASE_UNIT_USD:.0f} × min(edge×5, 1.0) × "
                     f"confidence multiplier ({{0.5,1.0,1.5}}). "
                     f"Track record: see paper_bets section.")
        return "\n".join(lines) + "\n"

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        # Emit each placed bet as a structured.paper_bets entry too (for the
        # graph/lake's record-keeping; lake.add_paper_bet already stored
        # them, but this preserves them in the section artifact too).
        for item in state_obj.items:
            if item.get("_skipped"): continue
            base["paper_bets"].append({
                "id": item.get("bet_id"),
                "market_platform": item.get("market_platform"),
                "market_id": item.get("market_id"),
                "market_url": item.get("url"),
                "market_question": item.get("title", ""),
                "side": item.get("side"),
                "size_usd": item.get("size_usd"),
                "price_at_bet": item.get("market_price"),
                "rationale": item.get("rationale", ""),
                "evidence": item.get("evidence_sections", []),
                "confidence_band": item.get("confidence_band"),
                "model_version": f"placement-v1::{MODEL}",
            })
        return base
