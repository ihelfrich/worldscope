"""
paper_bets — the system's daily simulated prediction-market trades.

Each day this section does three things:
  1. Pulls the current state of every active market from Polymarket, Kalshi,
     PredictIt, and Manifold (markets with > $10K cumulative volume).
  2. Marks-to-market every open paper bet at its 1/5/14/30/60/90-day
     milestone (whichever applies for today's date relative to the
     bet's open date).
  3. Resolves any bets whose markets have closed and records final P&L.

What it does NOT do here: PLACE new bets. New-bet decisions are made
by a separate sub-agent in the orchestrator that reads the day's
section summaries and decides where its credence differs sharply
enough from market prices to merit a simulated trade. That sub-agent
calls Lake.add_paper_bet() directly.

This section's job is the bookkeeping layer: pull market state, mark
the open positions, surface a scorecard. It conforms to the contract
but emits no entities or relationships — paper bets are tracked in
their own dedicated tables (paper_bets, paper_bet_marks,
paper_bet_resolutions).

For each market platform we use:
  - Polymarket:  https://gamma-api.polymarket.com  (free, no auth)
  - Kalshi:      https://api.elections.kalshi.com   (free public endpoint
                                                       for active markets)
  - PredictIt:   https://www.predictit.org/api/marketdata/all/  (deprecated
                                                       in 2023, may still work)
  - Manifold:    https://api.manifold.markets/v0    (free, no auth)

If any platform returns failure, the section degrades gracefully — the
scorecard is computed from whatever marks succeeded.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from . import Section, SectionState

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Days-since-bet milestones for mark-to-market. After 90 we mark weekly.
MARK_MILESTONES = [1, 5, 14, 30, 60, 90]


class PaperBetsSection(Section):
    id = "paper_bets"
    title = "Paper Trading Scorecard"
    emoji = "🎯"

    source_id = "prediction-markets-aggregate"
    source_name = "Polymarket + Kalshi + PredictIt + Manifold"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "prediction_market"
    source_license = "varies-per-platform"
    attribution_required = True
    attribution_text = (
        "Market state from Polymarket gamma API, Kalshi public API, "
        "PredictIt market-data endpoint, and Manifold v0 API. Paper bets "
        "are simulations only; no real money is staked."
    )
    source_country = None
    source_language = "en"
    PULL_TIMEOUT_S = 90

    def pull(self) -> list[dict]:
        """Fetch current market state. Returns a list with one item per
        active market we know about. The mark-to-market and resolve passes
        are wired in resolve_paper_bets_today() which the orchestrator
        calls AFTER this pull populates the lake."""
        items: list[dict] = []
        items.extend(self._pull_polymarket())
        items.extend(self._pull_kalshi())
        items.extend(self._pull_manifold())
        # PredictIt deprecated 2023; we still try opportunistically.
        items.extend(self._pull_predictit())
        return items

    # ---- platform pulls -------------------------------------------------

    def _pull_polymarket(self) -> list[dict]:
        # Gamma API: /markets returns active markets with current price.
        url = "https://gamma-api.polymarket.com/markets"
        params = {"active": "true", "closed": "false", "limit": 100,
                  "order": "volume", "ascending": "false"}
        try:
            resp = requests.get(url, params=params,
                                headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "paper-bets-error-polymarket",
                "date": date.today().isoformat(),
                "title": f"[Polymarket error] {type(exc).__name__}",
                "url": url,
                "summary": str(exc)[:300],
                "platform": "polymarket",
                "_error": True,
            }]
        out = []
        markets = data if isinstance(data, list) else (data.get("data") or [])
        for m in markets:
            mid = m.get("id") or m.get("conditionId") or m.get("slug")
            if not mid: continue
            question = m.get("question") or m.get("title") or ""
            outcomes = m.get("outcomes") or []
            outcome_prices = m.get("outcomePrices") or []
            yes_price: Optional[float] = None
            try:
                if outcome_prices:
                    parsed = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                    if isinstance(parsed, list) and len(parsed) >= 1:
                        yes_price = float(parsed[0])
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
            volume = m.get("volume") or 0
            try:
                volume = float(volume)
            except (ValueError, TypeError):
                volume = 0
            if volume < 10_000:   # Skip thinly-traded markets
                continue
            out.append({
                "id": f"polymarket:{mid}",
                "date": date.today().isoformat(),
                "title": f"[Polymarket] {question}"[:300],
                "url": f"https://polymarket.com/event/{m.get('slug', mid)}",
                "summary": (m.get("description") or "")[:400],
                "platform": "polymarket",
                "market_id": str(mid),
                "question": question,
                "yes_price": yes_price,
                "volume_usd": volume,
                "end_date": m.get("endDate"),
                "outcomes": outcomes,
            })
        return out

    def _pull_kalshi(self) -> list[dict]:
        # Kalshi public elections API. The general markets API requires
        # auth; the elections subdomain has a public endpoint.
        url = "https://api.elections.kalshi.com/trade-api/v2/events"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20,
                                params={"status": "open", "limit": 50})
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "paper-bets-error-kalshi",
                "date": date.today().isoformat(),
                "title": f"[Kalshi error] {type(exc).__name__}",
                "url": url,
                "summary": str(exc)[:300],
                "platform": "kalshi",
                "_error": True,
            }]
        out = []
        for ev in (data.get("events") or []):
            ticker = ev.get("event_ticker")
            if not ticker: continue
            title = ev.get("title") or ""
            out.append({
                "id": f"kalshi:{ticker}",
                "date": date.today().isoformat(),
                "title": f"[Kalshi] {title}"[:300],
                "url": f"https://kalshi.com/events/{ticker}",
                "summary": (ev.get("sub_title") or "")[:400],
                "platform": "kalshi",
                "market_id": ticker,
                "question": title,
                "category": ev.get("category"),
            })
        return out

    def _pull_manifold(self) -> list[dict]:
        url = "https://api.manifold.markets/v0/markets"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20,
                                params={"limit": 100})
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "paper-bets-error-manifold",
                "date": date.today().isoformat(),
                "title": f"[Manifold error] {type(exc).__name__}",
                "url": url,
                "summary": str(exc)[:300],
                "platform": "manifold",
                "_error": True,
            }]
        out = []
        markets = data if isinstance(data, list) else []
        for m in markets[:100]:
            if m.get("isResolved"): continue
            if m.get("outcomeType") != "BINARY": continue
            mid = m.get("id")
            if not mid: continue
            volume = float(m.get("volume", 0) or 0)
            if volume < 100:   # Manifold markets are in mana; lower threshold
                continue
            out.append({
                "id": f"manifold:{mid}",
                "date": date.today().isoformat(),
                "title": f"[Manifold] {m.get('question','')}"[:300],
                "url": m.get("url") or f"https://manifold.markets/market/{m.get('slug', mid)}",
                "summary": (m.get("description") or "")[:400] if isinstance(m.get("description"), str) else "",
                "platform": "manifold",
                "market_id": mid,
                "question": m.get("question", ""),
                "yes_price": m.get("probability"),
                "volume_mana": volume,
                "end_date": (datetime.fromtimestamp(m["closeTime"]/1000, tz=timezone.utc).isoformat()
                             if m.get("closeTime") else None),
            })
        return out

    def _pull_predictit(self) -> list[dict]:
        url = "https://www.predictit.org/api/marketdata/all/"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            # PredictIt was wound down in 2023; failures are expected.
            return []
        out = []
        for m in (data.get("markets") or [])[:50]:
            mid = m.get("id")
            if not mid: continue
            out.append({
                "id": f"predictit:{mid}",
                "date": date.today().isoformat(),
                "title": f"[PredictIt] {m.get('name','')}"[:300],
                "url": m.get("url") or f"https://www.predictit.org/markets/detail/{mid}",
                "summary": "",
                "platform": "predictit",
                "market_id": str(mid),
                "question": m.get("name", ""),
                "contracts": [
                    {"name": c.get("name"), "lastTradePrice": c.get("lastTradePrice")}
                    for c in (m.get("contracts") or [])
                ],
            })
        return out

    # ---- mark-to-market + resolutions -----------------------------------

    def mark_open_bets(self, lake=None) -> dict:
        """For every open paper bet, check current market price (from this
        section's just-pulled raw data) and add a paper_bet_marks row if
        we've hit a milestone. Returns a count of marks made + bets resolved."""
        from ..lake import Lake
        lake = lake or Lake.open()

        # Build a price index from the current pull
        conn = lake._ensure_open()

        # Get all open bets
        open_bets = conn.execute(
            """
            SELECT b.id, b.market_platform, b.market_id, b.timestamp_bet, b.side, b.price_at_bet
              FROM paper_bets b
             LEFT JOIN paper_bet_resolutions r ON b.id = r.bet_id
             WHERE r.bet_id IS NULL
            """
        ).fetchall()

        marks_made = 0
        for bet in open_bets:
            bet_dt = datetime.fromisoformat(bet["timestamp_bet"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            days_since = (now - bet_dt).days
            if days_since <= 0: continue

            # Find next milestone we haven't hit yet
            existing = conn.execute(
                "SELECT days_since_bet FROM paper_bet_marks WHERE bet_id = ?",
                (bet["id"],),
            ).fetchall()
            hit = {r["days_since_bet"] for r in existing}
            due = sorted([m for m in MARK_MILESTONES if m <= days_since and m not in hit])
            if not due: continue

            # Look up the current market price from this pull's records
            # (the platform:market_id key matches our raw item id)
            target_key = f"{bet['market_platform']}:{bet['market_id']}"
            price_row = conn.execute(
                """
                SELECT extra_json FROM records
                 WHERE section_id = 'paper_bets' AND id = ?
                 ORDER BY ingested_at DESC LIMIT 1
                """,
                (target_key,),
            ).fetchone()
            if not price_row:
                continue
            try:
                extra = json.loads(price_row["extra_json"] or "{}")
                current_price = extra.get("yes_price")
                if current_price is None:
                    continue
                current_price = float(current_price)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

            for milestone in due:
                lake.mark_paper_bet(
                    bet_id=bet["id"],
                    mark_date=now.strftime("%Y-%m-%d"),
                    days_since_bet=milestone,
                    mark_price=current_price,
                )
                marks_made += 1

        return {"marks_made": marks_made, "open_bets_count": len(open_bets)}

    # ---- Contract artifacts ---------------------------------------------

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)

        # Paper bets section emits NO entity-graph payload itself — paper
        # bets are tracked in their own tables. But it DOES emit anomalies
        # for any platform that failed, so a daily brief reader sees it.
        for item in state_obj.items:
            if item.get("_error"):
                base["anomalies"].append({
                    "category": "platform-failure",
                    "z_score": None,
                    "description": item.get("title", ""),
                    "evidence": [item.get("_id") or self._item_id(item)],
                })
        return base

    def synthesize_summary(self, state_obj: SectionState) -> str:
        """A scorecard-style summary showing open positions, recent resolutions,
        and rolling P&L. Reads the paper_bets tables directly because that's
        where the actual book lives, not in the per-pull items list."""
        from ..lake import Lake

        lake = Lake.open()
        conn = lake._ensure_open()

        # Counts
        open_count = conn.execute(
            """
            SELECT COUNT(*) FROM paper_bets b
             LEFT JOIN paper_bet_resolutions r ON b.id = r.bet_id
             WHERE r.bet_id IS NULL
            """
        ).fetchone()[0]
        resolved_count = conn.execute(
            "SELECT COUNT(*) FROM paper_bet_resolutions"
        ).fetchone()[0]

        # Rolling P&L (last 30 days resolved)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rolling = conn.execute(
            "SELECT SUM(final_pnl), COUNT(*), SUM(CASE WHEN final_pnl > 0 THEN 1 ELSE 0 END)"
            " FROM paper_bet_resolutions WHERE resolved_at >= ?",
            (cutoff,),
        ).fetchone()
        rolling_pnl = rolling[0] or 0.0
        rolling_n = rolling[1] or 0
        rolling_wins = rolling[2] or 0

        # All-time totals
        alltime = conn.execute(
            "SELECT SUM(final_pnl), COUNT(*), SUM(CASE WHEN final_pnl > 0 THEN 1 ELSE 0 END)"
            " FROM paper_bet_resolutions"
        ).fetchone()
        alltime_pnl = alltime[0] or 0.0
        alltime_n = alltime[1] or 0
        alltime_wins = alltime[2] or 0

        lines = [
            "---",
            f"section: {self.id}",
            f"title: {self.title}",
            f"date: {state_obj.source_date or date.today().isoformat()}",
            "---",
            "",
            f"## {self.emoji} {self.title}",
            "",
            f"**Open positions:** {open_count}",
            f"**Resolved positions all-time:** {resolved_count}",
            "",
            "### Rolling 30-day scorecard",
            f"- Bets resolved: **{rolling_n}**",
            f"- Net P&L: **${rolling_pnl:+,.2f}**",
            f"- Hit rate: **{(rolling_wins/rolling_n*100 if rolling_n else 0):.1f}%**"
            f" ({rolling_wins} wins of {rolling_n})" if rolling_n else "- Hit rate: insufficient data",
            "",
            "### All-time",
            f"- Bets resolved: **{alltime_n}**",
            f"- Net P&L: **${alltime_pnl:+,.2f}**",
            f"- Hit rate: **{(alltime_wins/alltime_n*100 if alltime_n else 0):.1f}%**"
            f" ({alltime_wins} wins of {alltime_n})" if alltime_n else "- Hit rate: insufficient data",
            "",
        ]

        # Top 5 open positions by current unrealized P&L
        wins_open = conn.execute(
            """
            SELECT b.market_question, b.side, b.size_usd, b.price_at_bet,
                   m.mark_price, m.unrealized_pnl, m.days_since_bet,
                   b.market_platform
              FROM paper_bets b
              JOIN paper_bet_marks m ON b.id = m.bet_id
             LEFT JOIN paper_bet_resolutions r ON b.id = r.bet_id
             WHERE r.bet_id IS NULL
             ORDER BY m.unrealized_pnl DESC
             LIMIT 5
            """
        ).fetchall()
        losers_open = conn.execute(
            """
            SELECT b.market_question, b.side, b.size_usd, b.price_at_bet,
                   m.mark_price, m.unrealized_pnl, m.days_since_bet,
                   b.market_platform
              FROM paper_bets b
              JOIN paper_bet_marks m ON b.id = m.bet_id
             LEFT JOIN paper_bet_resolutions r ON b.id = r.bet_id
             WHERE r.bet_id IS NULL
             ORDER BY m.unrealized_pnl ASC
             LIMIT 5
            """
        ).fetchall()

        if wins_open:
            lines.append("### Top open positions (best mark-to-market)")
            for w in wins_open:
                lines.append(
                    f"- [{w['market_platform']}] **{w['side']}** @ ${w['price_at_bet']:.3f} "
                    f"-> ${w['mark_price']:.3f} = **${w['unrealized_pnl']:+,.2f}** "
                    f"({w['days_since_bet']}d) — {w['market_question'][:80]}"
                )
            lines.append("")

        if losers_open:
            lines.append("### Worst open positions")
            for L in losers_open:
                lines.append(
                    f"- [{L['market_platform']}] **{L['side']}** @ ${L['price_at_bet']:.3f} "
                    f"-> ${L['mark_price']:.3f} = **${L['unrealized_pnl']:+,.2f}** "
                    f"({L['days_since_bet']}d) — {L['market_question'][:80]}"
                )
            lines.append("")

        # Market state pulled today
        live_markets = len([it for it in state_obj.items if not it.get("_error")])
        platform_counts: dict[str, int] = {}
        for it in state_obj.items:
            if not it.get("_error"):
                platform_counts[it.get("platform","unknown")] = platform_counts.get(it.get("platform","unknown"), 0) + 1
        lines.append(f"### Today's market state")
        lines.append(f"- Active markets indexed: **{live_markets}**")
        for p, n in platform_counts.items():
            lines.append(f"  - {p}: {n}")

        return "\n".join(lines) + "\n"

    def to_raw_record(self, item: dict, *, today_iso: str) -> dict:
        record = super().to_raw_record(item, today_iso=today_iso)
        record["source_id"] = f"prediction-market:{item.get('platform','unknown')}"
        # Stash market-specific fields in extra for the MCP server's
        # recent_market_state-style queries.
        extra = dict(record.get("extra") or {})
        for k in ("platform", "market_id", "question", "yes_price",
                  "volume_usd", "volume_mana", "end_date", "category",
                  "outcomes", "contracts"):
            if k in item:
                extra[k] = item[k]
        record["extra"] = extra
        return record
