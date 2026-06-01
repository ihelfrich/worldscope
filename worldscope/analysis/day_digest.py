"""day_digest.py — deterministic "what resolved and moved today" roll-up.

A forward-looking morning brief says what's happening and what's coming. The
backward-looking complement — an evening debrief, or a glanceable always-on
panel — needs the day's *settled facts*: which paper bets resolved and at what
P&L, which of the system's own predictions came due and whether they were
right, and what anomalies fired today.

This module computes exactly that from the lake, deterministically, with no
network and no LLM. It is the factual substrate a debrief narrates; the prose
layer can sit on top later. Each query guards its own table so a fresh lake
(missing tables) yields an empty-but-valid digest rather than an error.

Mirrors the analysis-package conventions (pure sqlite3, dependency-free,
idempotent). Pure `build_day_digest(conn, date_iso)` for testing/MCP; tables
are filtered on the date portion of their ISO timestamps.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def _date_rows(conn: sqlite3.Connection, sql: str, params: tuple) -> list[sqlite3.Row]:
    """Run a query, returning [] if the underlying table doesn't exist yet."""
    try:
        cur = conn.execute(sql, params)
        return cur.fetchall()
    except sqlite3.OperationalError:
        return []


def _resolved_bets(conn, date_iso: str) -> dict:
    rows = _date_rows(
        conn,
        """
        SELECT b.market_question, b.side, b.market_platform,
               r.final_outcome, r.final_pnl, r.holding_period_days
          FROM paper_bet_resolutions r
          JOIN paper_bets b ON b.id = r.bet_id
         WHERE substr(r.resolved_at, 1, 10) = ?
         ORDER BY ABS(r.final_pnl) DESC
        """,
        (date_iso,),
    )
    items = [{
        "question": r[0], "side": r[1], "platform": r[2],
        "outcome": r[3], "pnl": float(r[4] or 0.0),
        "holding_days": r[5],
    } for r in rows]
    net = sum(it["pnl"] for it in items)
    wins = sum(1 for it in items if it["pnl"] > 0)
    return {
        "n": len(items), "net_pnl": round(net, 2), "wins": wins,
        "losses": len(items) - wins, "items": items,
    }


def _resolved_predictions(conn, date_iso: str) -> dict:
    rows = _date_rows(
        conn,
        """
        SELECT resolution_criteria, predicted_outcome, actual_outcome, confidence
          FROM predictions
         WHERE actual_outcome IS NOT NULL AND actual_outcome != ''
           AND substr(resolved_at, 1, 10) = ?
         ORDER BY confidence DESC
        """,
        (date_iso,),
    )
    items = []
    n_correct = 0
    for r in rows:
        correct = str(r[1] or "").strip().lower() == str(r[2] or "").strip().lower()
        n_correct += 1 if correct else 0
        items.append({
            "criteria": r[0], "predicted": r[1], "actual": r[2],
            "confidence": float(r[3]) if r[3] is not None else None,
            "correct": correct,
        })
    return {"n": len(items), "n_correct": n_correct, "items": items}


def _anomalies(conn, date_iso: str, limit: int = 10) -> dict:
    rows = _date_rows(
        conn,
        """
        SELECT category, z_score, description, section_id
          FROM anomalies
         WHERE substr(detected_at, 1, 10) = ?
         ORDER BY ABS(COALESCE(z_score, 0)) DESC
         LIMIT ?
        """,
        (date_iso, limit),
    )
    items = [{
        "category": r[0], "z_score": float(r[1]) if r[1] is not None else None,
        "description": r[2], "section": r[3],
    } for r in rows]
    return {"n": len(items), "items": items}


def build_day_digest(conn: sqlite3.Connection, date_iso: str) -> dict:
    """Assemble the day's settled facts for `date_iso` (YYYY-MM-DD).

    Returns a dict with `bets_resolved`, `predictions_resolved`, and
    `anomalies` blocks plus a compact `headline` count line. Safe on an empty
    or partial lake.
    """
    bets = _resolved_bets(conn, date_iso)
    preds = _resolved_predictions(conn, date_iso)
    anoms = _anomalies(conn, date_iso)
    headline = {
        "bets_resolved": bets["n"],
        "net_pnl": bets["net_pnl"],
        "predictions_resolved": preds["n"],
        "predictions_correct": preds["n_correct"],
        "anomalies": anoms["n"],
    }
    return {
        "date": date_iso,
        "headline": headline,
        "bets_resolved": bets,
        "predictions_resolved": preds,
        "anomalies": anoms,
    }
