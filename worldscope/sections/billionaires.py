"""
billionaires.py — Forbes Real-Time Billionaires watchlist + movers.

Forbes publishes a JSON feed of the full Real-Time Billionaires list. Each
entry has rank, net worth (in $M), source companies, industries, country
of citizenship, and a breakdown of public-equity holdings (exchange,
ticker, share count, current share price).

Section output:
  - Top 30 by current net worth
  - "Movers": biggest absolute net-worth shifts since the previous snapshot
    (computed from worldscope's own snapshot store, so day-over-day deltas
    fall out naturally once two days of data exist)

This is the "richest people in the world" running list. No auth required.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from . import Section

API = "https://www.forbes.com/forbesapi/person/rtb/0/-estWorthPrev/true.json"
UA = "Mozilla/5.0 worldscope/0.1 (Ian Helfrich; ianthelfrich@gmail.com)"


def _fmt_b(millions: float) -> str:
    """Format $M as $X.XB."""
    return f"${millions/1000:.2f}B"


class BillionairesSection(Section):
    id = "billionaires"
    title = "Forbes Real-Time Billionaires (top 30 + biggest movers)"
    emoji = "💰"

    PULL_TIMEOUT_S = 90   # Forbes RTB JSON can run slow on big payloads
    TOP_N = 30
    MOVERS_N = 10

    def pull(self) -> list[dict]:
        # Single-source section: if Forbes fails we have nothing. Re-raise
        # (don't return []) so the state machine surfaces a real
        # stale-after-failure pill instead of pretending we cleanly
        # pulled zero billionaires today.
        try:
            resp = requests.get(API, params={"limit": 1500},
                                headers={"User-Agent": UA, "Accept": "application/json"},
                                timeout=25)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[{self.id}] Forbes RTB fetch failed: "
                  f"{type(exc).__name__}: {exc}")
            raise

        ppl = (data.get("personList") or {}).get("personsLists", [])
        if not ppl:
            # Forbes' shape may have shifted. Surface this loudly.
            raise RuntimeError(
                f"[{self.id}] Forbes RTB returned no personsLists "
                f"(top-level keys: {list(data.keys())[:10]})"
            )

        # Normalize every entry to a dict the section uses
        all_ppl: list[dict] = []
        for p in ppl:
            rank = p.get("rank") or p.get("position") or 9999
            name = p.get("personName") or (p.get("person") or {}).get("name") or "(unknown)"
            worth = float(p.get("finalWorth") or 0)
            country = p.get("countryOfCitizenship") or ""
            industries = p.get("industries") or []
            source = p.get("source") or ""
            holdings = p.get("financialAssets") or []
            top_holdings = ", ".join(
                f"{h.get('ticker','?')}({h.get('exchange','?')})"
                for h in holdings[:4] if h.get("ticker")
            )
            all_ppl.append({
                "rank": rank,
                "name": name,
                "worth_m": worth,
                "country": country,
                "industries": industries,
                "source": source,
                "top_holdings": top_holdings,
                "uri": p.get("uri") or (p.get("person") or {}).get("uri", ""),
            })

        # Sort by net worth desc
        all_ppl.sort(key=lambda x: -x["worth_m"])

        items: list[dict] = []

        # Top N by net worth
        for i, p in enumerate(all_ppl[: self.TOP_N], 1):
            items.append({
                "id": f"rank:{p['uri']}",
                "date": datetime.now(timezone.utc).date().isoformat(),
                "title": f"#{i} {p['name']} — {_fmt_b(p['worth_m'])}",
                "url": f"https://www.forbes.com/profile/{p['uri']}/" if p['uri'] else "https://www.forbes.com/real-time-billionaires/",
                "summary": (
                    f"{p['country']} · {', '.join(p['industries']) or '—'} · "
                    f"source: {p['source'][:80]}"
                    + (f" · holdings: {p['top_holdings']}" if p['top_holdings'] else "")
                ),
                "rank": i,
                "worth_m": p["worth_m"],
                "country": p["country"],
                "industries": p["industries"],
                "kind": "top",
            })

        # Movers: pull previous snapshot, compute deltas, attach top mover entries
        prev = self.store.most_recent(self.id)
        if prev and prev.get("items"):
            prev_by_uri: dict[str, dict] = {}
            for it in prev["items"]:
                # ID format from above: 'rank:<uri>' for top entries; we want the URI key
                uri = it.get("id", "").split(":", 1)[-1] if it.get("id", "").startswith("rank:") else None
                if not uri:
                    continue
                prev_by_uri[uri] = {
                    "worth_m": it.get("worth_m", 0),
                    "name": it.get("title", "").split(" — ")[0].strip("# 0123456789"),
                }
            current_by_uri = {p["uri"]: p for p in all_ppl if p["uri"]}
            deltas: list[tuple[float, dict]] = []
            for uri, cur in current_by_uri.items():
                if uri in prev_by_uri:
                    delta = cur["worth_m"] - prev_by_uri[uri]["worth_m"]
                    if abs(delta) > 50:  # threshold: $50M+ move
                        deltas.append((delta, cur))
            deltas.sort(key=lambda t: -abs(t[0]))
            for delta_m, p in deltas[: self.MOVERS_N]:
                arrow = "▲" if delta_m > 0 else "▼"
                items.append({
                    "id": f"mover:{p['uri']}",
                    "date": datetime.now(timezone.utc).date().isoformat(),
                    "title": (f"MOVER {arrow} {p['name']} — "
                              f"{_fmt_b(p['worth_m'])} ({'+' if delta_m>0 else ''}{_fmt_b(delta_m)} since yesterday)"),
                    "url": f"https://www.forbes.com/profile/{p['uri']}/",
                    "summary": (
                        f"{p['country']} · {', '.join(p['industries']) or '—'} · "
                        f"source: {p['source'][:80]}"
                    ),
                    "delta_m": delta_m,
                    "worth_m": p["worth_m"],
                    "country": p["country"],
                    "kind": "mover",
                })
        return items
