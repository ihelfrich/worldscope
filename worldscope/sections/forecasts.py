"""
forecasts.py — Polymarket prediction markets section.

Pulls the most-traded open prediction markets on Polymarket and surfaces
each market's current "yes" price (= community-implied probability) plus
24-hour volume.

Polymarket switched from a free anonymous API to authenticated-only after
many forecasting tools relied on it; Polymarket's gamma-api is the
remaining no-auth option as of 2026-05. Metaculus is now auth-only.

API: https://gamma-api.polymarket.com/markets
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from . import Section

API = "https://gamma-api.polymarket.com/markets"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


class ForecastsSection(Section):
    id = "forecasts"
    title = "Prediction Markets — most-traded (Polymarket)"
    emoji = "🔮"

    LIMIT = 20

    def pull(self) -> list[dict]:
        params = {
            "closed": "false",
            "active": "true",
            "order": "volume24hr",
            "ascending": "false",
            "limit": self.LIMIT,
        }
        try:
            resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        items: list[dict] = []
        for m in data[: self.LIMIT]:
            slug = m.get("slug", "")
            question = m.get("question") or m.get("title") or "(unnamed)"
            volume_24h = float(m.get("volume24hr") or 0)
            outcome_prices = m.get("outcomePrices") or m.get("outcomes_prices") or []
            # Binary markets typically: ["Yes price", "No price"] as JSON-stringified list
            yes_price = None
            if isinstance(outcome_prices, str):
                try:
                    import json as _j
                    parsed = _j.loads(outcome_prices)
                    if isinstance(parsed, list) and parsed:
                        yes_price = float(parsed[0])
                except Exception:
                    pass
            elif isinstance(outcome_prices, list) and outcome_prices:
                try:
                    yes_price = float(outcome_prices[0])
                except (TypeError, ValueError):
                    yes_price = None

            end_date = m.get("endDate") or m.get("end_date") or ""
            updated = m.get("updatedAt") or m.get("createdAt") or ""
            try:
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None
            except (ValueError, AttributeError):
                dt = None

            yes_str = f"{yes_price*100:.0f}%" if yes_price is not None else "—"
            items.append({
                "id": slug or m.get("id"),
                "date": dt.date().isoformat() if dt else "",
                "title": question,
                "url": f"https://polymarket.com/event/{slug}" if slug else "",
                "summary": (
                    f"yes price: {yes_str} · "
                    f"24h volume: ${volume_24h:,.0f}"
                    + (f" · resolves {end_date[:10]}" if end_date else "")
                ),
                "yes_price": yes_price,
                "volume_24h": volume_24h,
                "end_date": end_date,
            })
        return items
