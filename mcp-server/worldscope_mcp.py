"""
worldscope_mcp — Model Context Protocol server exposing the Worldscope lake.

Register in your Claude Code config (typically ~/.claude/config.json or
~/.config/claude-code/mcp_servers.json) with:

    {
      "mcpServers": {
        "worldscope": {
          "command": "python3",
          "args": ["/Users/ian/Projects/worldscope/mcp-server/worldscope_mcp.py"],
          "env": {}
        }
      }
    }

Then in any Claude session you can call worldscope.search_news(),
worldscope.lookup_entity(), worldscope.get_paper_bets(), etc. natively.

Read-only by design. No write operations are exposed; the orchestrator
routines are the only path that mutates the lake.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Locate the lake DB relative to this file
REPO_ROOT = Path(__file__).resolve().parent.parent
LAKE_DB = REPO_ROOT / "lake" / "db" / "worldscope.sqlite"
LAKE_SECTIONS = REPO_ROOT / "lake" / "sections"

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.stderr.write(
        "FATAL: mcp package not installed. Run:\n"
        "    pip install 'mcp[cli]'\n"
    )
    sys.exit(1)


mcp = FastMCP("worldscope")


def _open_db() -> sqlite3.Connection:
    """Open the lake DB read-only. Each tool call opens its own connection
    so we don't carry stale state across queries."""
    if not LAKE_DB.exists():
        raise RuntimeError(
            f"Lake DB not found at {LAKE_DB}. "
            "Run a daily brief first to bootstrap it."
        )
    # SQLite read-only mode via URI
    conn = sqlite3.connect(f"file:{LAKE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------- #
# Tool: search_news
# ----------------------------------------------------------------------- #

@mcp.tool()
def search_news(
    query: str,
    days_back: int = 7,
    section_id: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 30,
) -> dict:
    """Full-text search across the lake's records table.

    Args:
        query: text to match against title/original_text (case-insensitive).
        days_back: how many days of history to search (default 7).
        section_id: filter to a specific section (e.g. "state_bills", "federal_register").
        state: filter to a US state name (matches anywhere in record text).
        limit: max records to return (default 30, hard cap 200).

    Returns:
        {"count": int, "records": [...]}, each record with id, source_id,
        section_id, title-equivalent text, url, date, ingested_at.
    """
    limit = min(max(1, limit), 200)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sql_parts = ["SELECT id, source_id, section_id, original_text, original_url, record_date, ingested_at, license"
                 " FROM records WHERE ingested_at >= ?"]
    params: list[Any] = [cutoff]
    if query:
        sql_parts.append("AND lower(original_text) LIKE ?")
        params.append(f"%{query.lower()}%")
    if section_id:
        sql_parts.append("AND section_id = ?")
        params.append(section_id)
    if state:
        sql_parts.append("AND lower(original_text) LIKE ?")
        params.append(f"%{state.lower()}%")
    sql_parts.append("ORDER BY ingested_at DESC LIMIT ?")
    params.append(limit)

    sql = " ".join(sql_parts)
    with _open_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"count": len(rows), "records": _rows_to_dicts(rows)}


# ----------------------------------------------------------------------- #
# Tool: lookup_entity
# ----------------------------------------------------------------------- #

@mcp.tool()
def lookup_entity(
    name_or_id: str,
    include_records: bool = True,
    include_relationships: bool = True,
    record_limit: int = 20,
) -> dict:
    """Resolve an entity by canonical name or id and return its profile.

    Args:
        name_or_id: either an entity id ("person:warsh-kevin") or a canonical
            name fragment (case-insensitive substring match).
        include_records: also return up to record_limit records that mention this entity.
        include_relationships: also return all relationships involving this entity.
        record_limit: how many records to include (default 20).

    Returns:
        {"entity": {...}, "records": [...], "relationships": [...]}
    """
    record_limit = min(max(1, record_limit), 100)
    with _open_db() as conn:
        # Try exact id match first
        ent_row = conn.execute(
            "SELECT * FROM entities WHERE id = ?", (name_or_id,)
        ).fetchone()
        if ent_row is None:
            # Fallback: substring on canonical_name
            ent_row = conn.execute(
                "SELECT * FROM entities WHERE lower(canonical_name) LIKE ? LIMIT 1",
                (f"%{name_or_id.lower()}%",),
            ).fetchone()
        if ent_row is None:
            return {"entity": None, "error": f"no entity matching {name_or_id!r}"}

        result: dict = {"entity": dict(ent_row)}
        result["entity"]["metadata"] = json.loads(result["entity"].get("metadata_json") or "{}")
        result["entity"]["aliases"] = json.loads(result["entity"].get("aliases_json") or "[]")

        if include_records:
            records = conn.execute(
                """
                SELECT r.* FROM records r
                  JOIN record_entities re ON r.id = re.record_id
                 WHERE re.entity_id = ?
                 ORDER BY r.ingested_at DESC
                 LIMIT ?
                """,
                (ent_row["id"], record_limit),
            ).fetchall()
            result["records"] = _rows_to_dicts(records)

        if include_relationships:
            rels = conn.execute(
                """
                SELECT id, from_entity, to_entity, type, weight, first_seen, last_seen
                  FROM relationships
                 WHERE from_entity = ? OR to_entity = ?
                 ORDER BY last_seen DESC
                """,
                (ent_row["id"], ent_row["id"]),
            ).fetchall()
            result["relationships"] = _rows_to_dicts(rels)

    return result


# ----------------------------------------------------------------------- #
# Tool: recent_state_bills
# ----------------------------------------------------------------------- #

@mcp.tool()
def recent_state_bills(
    state: Optional[str] = None,
    topic: Optional[str] = None,
    days_back: int = 7,
    limit: int = 30,
) -> dict:
    """Query the state-bills slice of the lake.

    Args:
        state: full state name (e.g. "California") or None for all states.
        topic: substring to match against bill title/abstract (e.g. "artificial intelligence").
        days_back: how many days back to look (default 7).
        limit: max bills to return (default 30).

    Returns:
        {"count": int, "bills": [{"state","identifier","title","sponsor",...}, ...]}
    """
    limit = min(max(1, limit), 200)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sql_parts = ["SELECT id, original_text, original_url, record_date, ingested_at, extra_json"
                 " FROM records WHERE section_id = 'state_bills' AND ingested_at >= ?"]
    params: list[Any] = [cutoff]
    if state:
        sql_parts.append("AND lower(original_text) LIKE ?")
        params.append(f"%{state.lower()}%")
    if topic:
        sql_parts.append("AND lower(original_text) LIKE ?")
        params.append(f"%{topic.lower()}%")
    sql_parts.append("ORDER BY ingested_at DESC LIMIT ?")
    params.append(limit)

    sql = " ".join(sql_parts)
    with _open_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    bills = []
    for r in rows:
        extra = json.loads(r["extra_json"] or "{}")
        bills.append({
            "id": r["id"],
            "title": r["original_text"][:200],
            "url": r["original_url"],
            "ingested_at": r["ingested_at"],
            "state": extra.get("state"),
            "identifier": extra.get("identifier"),
            "classification": extra.get("classification"),
            "primary_sponsor": extra.get("primary_sponsor"),
            "last_action_date": extra.get("last_action_date"),
            "last_action_description": extra.get("last_action_description"),
        })
    return {"count": len(bills), "bills": bills}


# ----------------------------------------------------------------------- #
# Tool: get_paper_bets
# ----------------------------------------------------------------------- #

@mcp.tool()
def get_paper_bets(
    status: str = "all",
    days_back: int = 30,
    limit: int = 50,
) -> dict:
    """Paper-trading scorecard.

    Args:
        status: "open" (unresolved), "resolved", or "all" (default).
        days_back: how far back to look (default 30).
        limit: max bets to return (default 50).

    Returns:
        {"summary": {...}, "bets": [...]}
        summary includes: open_count, resolved_count, total_pnl, hit_rate.
    """
    limit = min(max(1, limit), 200)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _open_db() as conn:
        all_bets = conn.execute(
            """
            SELECT b.*, r.final_outcome, r.final_pnl, r.resolved_at, r.holding_period_days
              FROM paper_bets b
              LEFT JOIN paper_bet_resolutions r ON b.id = r.bet_id
             WHERE b.timestamp_bet >= ?
             ORDER BY b.timestamp_bet DESC
             LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()

    bets = []
    open_count = 0
    resolved_count = 0
    total_pnl = 0.0
    wins = 0
    for r in all_bets:
        is_resolved = r["resolved_at"] is not None
        if status == "open" and is_resolved: continue
        if status == "resolved" and not is_resolved: continue
        bet = dict(r)
        bet["evidence"] = json.loads(bet.get("evidence_json") or "[]")
        bet.pop("evidence_json", None)
        bets.append(bet)
        if is_resolved:
            resolved_count += 1
            total_pnl += bet.get("final_pnl") or 0.0
            if (bet.get("final_pnl") or 0) > 0:
                wins += 1
        else:
            open_count += 1

    summary = {
        "open_count": open_count,
        "resolved_count": resolved_count,
        "total_resolved_pnl_usd": round(total_pnl, 2),
        "hit_rate": round(wins / resolved_count, 3) if resolved_count else None,
    }
    return {"summary": summary, "bets": bets}


# ----------------------------------------------------------------------- #
# Tool: get_anomalies
# ----------------------------------------------------------------------- #

@mcp.tool()
def get_anomalies(
    category: Optional[str] = None,
    days_back: int = 7,
    limit: int = 50,
) -> dict:
    """Recent anomaly flags from any section.

    Args:
        category: filter by anomaly category (e.g. "feed-failure", "ingest-failure").
        days_back: how far back to look (default 7).
        limit: max rows (default 50).
    """
    limit = min(max(1, limit), 200)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sql_parts = ["SELECT * FROM anomalies WHERE detected_at >= ?"]
    params: list[Any] = [cutoff]
    if category:
        sql_parts.append("AND category = ?")
        params.append(category)
    sql_parts.append("ORDER BY detected_at DESC LIMIT ?")
    params.append(limit)
    with _open_db() as conn:
        rows = conn.execute(" ".join(sql_parts), params).fetchall()
    return {"count": len(rows), "anomalies": _rows_to_dicts(rows)}


# ----------------------------------------------------------------------- #
# Tool: get_source_health
# ----------------------------------------------------------------------- #

@mcp.tool()
def get_source_health(stale_hours: int = 48) -> dict:
    """Per-source freshness check.

    Args:
        stale_hours: threshold for flagging as stale (default 48h).

    Returns:
        {"fresh": [...], "stale": [...], "failing": [...]}
    """
    with _open_db() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.tier, s.country,
                   h.last_success_at, h.last_record_count, h.consecutive_failures,
                   h.last_failure_at, h.last_failure_error
              FROM sources s LEFT JOIN source_health h ON s.id = h.source_id
            """
        ).fetchall()

    fresh, stale, failing = [], [], []
    now = datetime.now(timezone.utc)
    for r in rows:
        d = dict(r)
        if d["last_success_at"]:
            last_dt = datetime.fromisoformat(d["last_success_at"].replace("Z", "+00:00"))
            d["hours_since_success"] = round((now - last_dt).total_seconds() / 3600, 1)
        else:
            d["hours_since_success"] = None
        if d["consecutive_failures"] and d["consecutive_failures"] > 1:
            failing.append(d)
        elif d["hours_since_success"] is None or d["hours_since_success"] > stale_hours:
            stale.append(d)
        else:
            fresh.append(d)
    return {
        "fresh_count": len(fresh),
        "stale_count": len(stale),
        "failing_count": len(failing),
        "fresh": fresh,
        "stale": stale,
        "failing": failing,
    }


# ----------------------------------------------------------------------- #
# Tool: graph_path
# ----------------------------------------------------------------------- #

@mcp.tool()
def graph_path(entity_a: str, entity_b: str, max_hops: int = 4) -> dict:
    """Find the shortest connection path between two entities via the
    relationship graph. Limited to undirected BFS up to max_hops.

    Args:
        entity_a: entity id or canonical-name substring.
        entity_b: same.
        max_hops: search depth (default 4, capped at 6).

    Returns:
        {"path": [entity_id, ...], "edges": [{from, to, type, ...}, ...]}
        or {"path": None} if no path exists within max_hops.
    """
    max_hops = min(max(1, max_hops), 6)
    with _open_db() as conn:
        # Resolve both endpoints
        def resolve(q: str) -> Optional[str]:
            row = conn.execute("SELECT id FROM entities WHERE id = ?", (q,)).fetchone()
            if row: return row["id"]
            row = conn.execute(
                "SELECT id FROM entities WHERE lower(canonical_name) LIKE ? LIMIT 1",
                (f"%{q.lower()}%",),
            ).fetchone()
            return row["id"] if row else None

        a = resolve(entity_a)
        b = resolve(entity_b)
        if a is None or b is None:
            return {"path": None, "error": f"could not resolve {entity_a!r} or {entity_b!r}"}

        # BFS over relationships (undirected)
        from collections import deque
        queue = deque([(a, [a], [])])  # (node, path_so_far, edges_so_far)
        seen = {a}
        while queue:
            node, path, edges = queue.popleft()
            if len(path) - 1 > max_hops:
                continue
            if node == b:
                return {"path": path, "edges": edges, "length": len(edges)}
            neighbors = conn.execute(
                """
                SELECT from_entity, to_entity, type, weight FROM relationships
                 WHERE from_entity = ? OR to_entity = ?
                """,
                (node, node),
            ).fetchall()
            for r in neighbors:
                other = r["to_entity"] if r["from_entity"] == node else r["from_entity"]
                if other in seen:
                    continue
                seen.add(other)
                queue.append((other, path + [other], edges + [dict(r)]))

    return {"path": None, "length": None, "note": f"no path within {max_hops} hops"}


# ----------------------------------------------------------------------- #
# Tool: get_brief
# ----------------------------------------------------------------------- #

@mcp.tool()
def get_brief(date_iso: Optional[str] = None) -> dict:
    """Fetch a past brief by date (YYYY-MM-DD). None = most recent.

    Returns:
        {"date", "kind", "title", "html_path", "md_path",
         "composed_at", "tokens_in", "tokens_out", "cost_usd"} or None.
    """
    with _open_db() as conn:
        if date_iso:
            row = conn.execute(
                "SELECT * FROM briefs WHERE date = ? ORDER BY composed_at DESC LIMIT 1",
                (date_iso,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM briefs ORDER BY composed_at DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else {"error": "no brief found"}


# ----------------------------------------------------------------------- #
# Tool: get_section_summary
# ----------------------------------------------------------------------- #

@mcp.tool()
def get_section_summary(section_id: str, date_iso: Optional[str] = None) -> dict:
    """Fetch a section's pre-synthesized markdown summary for a given date.

    Args:
        section_id: e.g. "state_bills", "federal_register", "state_news".
        date_iso: YYYY-MM-DD. None = most recent date that has a summary.

    Returns:
        {"section_id", "date", "summary_md", "record_count", "structured": {...}}
    """
    section_dir = LAKE_SECTIONS / section_id
    if not section_dir.exists():
        return {"error": f"no section directory for {section_id!r}"}

    if date_iso:
        target = section_dir / date_iso
        if not target.exists():
            return {"error": f"no artifacts for {section_id}/{date_iso}"}
        date_used = date_iso
    else:
        dates = sorted([d.name for d in section_dir.iterdir() if d.is_dir()], reverse=True)
        if not dates:
            return {"error": f"no artifacts found under {section_id}"}
        target = section_dir / dates[0]
        date_used = dates[0]

    summary_md = (target / "summary.md").read_text(encoding="utf-8") if (target / "summary.md").exists() else ""
    structured: dict = {}
    if (target / "structured.json").exists():
        structured = json.loads((target / "structured.json").read_text(encoding="utf-8"))

    return {
        "section_id": section_id,
        "date": date_used,
        "summary_md": summary_md,
        "structured": structured,
        "record_count": structured.get("record_count", 0),
    }


# ----------------------------------------------------------------------- #
# Tool: query_relationships
# ----------------------------------------------------------------------- #

@mcp.tool()
def query_relationships(
    entity_id: str,
    direction: str = "both",
    type: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Get the relationship neighborhood of an entity.

    Args:
        entity_id: the entity to query.
        direction: "out" (entity is from_entity), "in" (entity is to_entity),
            or "both" (default).
        type: filter by relationship type (e.g. "sponsored-by", "signed-by").
        limit: max relationships (default 50).
    """
    limit = min(max(1, limit), 500)
    clauses = []
    params: list[Any] = []
    if direction == "out":
        clauses.append("from_entity = ?")
        params.append(entity_id)
    elif direction == "in":
        clauses.append("to_entity = ?")
        params.append(entity_id)
    else:
        clauses.append("(from_entity = ? OR to_entity = ?)")
        params.extend([entity_id, entity_id])
    if type:
        clauses.append("type = ?")
        params.append(type)
    sql = f"SELECT * FROM relationships WHERE {' AND '.join(clauses)} ORDER BY last_seen DESC LIMIT ?"
    params.append(limit)
    with _open_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"count": len(rows), "relationships": _rows_to_dicts(rows)}


# ----------------------------------------------------------------------- #
# Tools: semantic_search / find_similar_to / cluster_today
# ----------------------------------------------------------------------- #
# These tools depend on worldscope.embeddings + worldscope.dedup. The
# sentence-transformers import is heavy (~200 MB resident), so we keep the
# EmbeddingIndex bound at module load time but only instantiate the model
# on first call. Each tool catches ImportError so an environment without
# sentence-transformers installed degrades gracefully (returns an error
# payload instead of crashing the whole MCP server).

# Make the worldscope package importable from this script's location.
_PKG_ROOT = REPO_ROOT
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

_emb_index = None  # lazy singleton


def _get_embedding_index():
    """Lazy-load the multilingual embedding index. Raises ImportError if
    sentence-transformers is not installed."""
    global _emb_index
    if _emb_index is None:
        from worldscope.embeddings import EmbeddingIndex
        _emb_index = EmbeddingIndex(lake_db_path=LAKE_DB)
    return _emb_index


@mcp.tool()
def semantic_search(
    query: str,
    days_back: int = 7,
    limit: int = 30,
    min_similarity: float = 0.4,
) -> dict:
    """Semantic / cross-language search using sentence-transformers embeddings.

    Finds records that are semantically similar to the query regardless of
    language. Searching for "Warsh" will surface matching coverage in
    Russian (Уолш), Chinese (沃什), Ukrainian (Уорш), Arabic and others
    because the multilingual model puts cognates near each other in vector
    space.

    Args:
        query: search text in any language.
        days_back: how many days of history to search (default 7).
        limit: max records to return (default 30).
        min_similarity: cosine similarity floor below which hits are dropped
            (default 0.4; below ~0.3 results get noisy).

    Returns:
        {"count": int, "query": str, "results": [...]}
        Each result has: record_id, source_id, section_id, original_text,
        original_url, original_lang, record_date, similarity_score.
    """
    try:
        idx = _get_embedding_index()
        results = idx.search(
            query=query,
            days_back=days_back,
            limit=min(max(1, limit), 200),
            min_similarity=float(min_similarity),
        )
        return {"count": len(results), "query": query, "results": results}
    except ImportError as ex:
        return {"error": f"sentence-transformers not installed: {ex}"}
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}"}


@mcp.tool()
def find_similar_to(record_id: str, top_k: int = 10) -> dict:
    """Given a lake record ID, return the K most semantically-similar
    records within a 30-day window. Useful for "show me everything else
    like this story" across languages.

    Args:
        record_id: the lake record id to anchor on.
        top_k: how many neighbors to return (default 10).

    Returns:
        {"seed_id": str, "count": int, "neighbors": [...]}
    """
    try:
        idx = _get_embedding_index()
        results = idx.find_neighbors(record_id, top_k=min(max(1, top_k), 100))
        return {"seed_id": record_id, "count": len(results), "neighbors": results}
    except ImportError as ex:
        return {"error": f"sentence-transformers not installed: {ex}"}
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}"}


@mcp.tool()
def cluster_today(
    date_iso: Optional[str] = None,
    similarity_threshold: float = 0.78,
    time_window_hours: int = 36,
) -> dict:
    """Return deduplication clusters of a day's records.

    When eight outlets all carry the same Reuters wire about a Fed rate
    decision, this returns ONE cluster with members listing those eight
    record_ids and the highest-tier source as the representative.

    Args:
        date_iso: YYYY-MM-DD. None = today (UTC).
        similarity_threshold: cosine floor for joining a cluster (default 0.78).
        time_window_hours: cap on time between cluster members (default 36h).

    Returns:
        {"date": str, "summary": {...}, "clusters": [...]}
    """
    try:
        from worldscope.dedup import HeadlineDedup
        idx = _get_embedding_index()
        dd = HeadlineDedup(
            embedding_index=idx,
            similarity_threshold=similarity_threshold,
            time_window_hours=time_window_hours,
        )
        clusters = dd.cluster_today(date_iso)
        summary = dd.cluster_summary(clusters)
        return {
            "date": date_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "summary": summary,
            "clusters": clusters,
        }
    except ImportError as ex:
        return {"error": f"sentence-transformers not installed: {ex}"}
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}"}


# ----------------------------------------------------------------------- #
# Tool: cross_section_signals
# ----------------------------------------------------------------------- #
# The cross-section recurrence analyzer (worldscope.analysis.cross_section)
# writes a JSON file each day under lake/sections/_meta/<date>/. This tool
# surfaces it directly so a chat session can ask "what's converging today"
# without needing to read raw JSON from disk.

@mcp.tool()
def cross_section_signals(
    date_iso: Optional[str] = None,
    min_confidence: str = "medium",
    limit: int = 25,
) -> dict:
    """Today's cross-section recurrence signals — entities appearing in 3+
    sections of the briefing. This is the analytical seed the homepage hero
    block uses; surfacing it via MCP lets a chat session steer toward the
    day's most-converged signals.

    Args:
        date_iso: YYYY-MM-DD. None = most recent date that has analyzer output.
        min_confidence: "high", "medium", or "low". "medium" returns
            high+medium bands (default). "low" returns all three.
        limit: max entities to return (default 25).

    Returns:
        {"date": str, "entities": [
            {"canonical_name", "entity_type", "n_sections", "total_mentions",
             "confidence", "sections": [...], "section_counts": {...},
             "evidence_records": {...}},
            ...
        ], "recurrences_found": int}
    """
    limit = min(max(1, limit), 200)
    meta_root = REPO_ROOT / "lake" / "sections" / "_meta"
    if not meta_root.exists():
        return {"error": "no cross-section analyzer output found"}

    if date_iso:
        target = meta_root / date_iso / "cross_section.json"
        if not target.exists():
            return {"error": f"no cross_section.json for {date_iso}"}
        used_date = date_iso
    else:
        candidates = sorted(
            (d.name for d in meta_root.iterdir() if d.is_dir()),
            reverse=True,
        )
        target = None
        for d in candidates:
            p = meta_root / d / "cross_section.json"
            if p.exists():
                target = p
                used_date = d
                break
        if target is None:
            return {"error": "no cross_section.json files found"}

    data = json.loads(target.read_text(encoding="utf-8"))
    bands = ("high", "medium", "low")
    cutoff_idx = bands.index(min_confidence.lower()) if min_confidence.lower() in bands else 1
    keep_bands = bands[:cutoff_idx + 1]
    entities: list[dict] = []
    for band in keep_bands:
        for ent in (data.get("by_confidence", {}).get(band) or []):
            entities.append(ent)
            if len(entities) >= limit:
                break
        if len(entities) >= limit:
            break
    return {
        "date": used_date,
        "recurrences_found": int(data.get("recurrences_found") or 0),
        "min_sections_threshold": int(data.get("min_sections_threshold") or 3),
        "entities": entities,
    }


# ----------------------------------------------------------------------- #
# Tool: today_top_new
# ----------------------------------------------------------------------- #

@mcp.tool()
def today_top_new(
    date_iso: Optional[str] = None,
    per_section: int = 3,
    sections: int = 20,
) -> dict:
    """Top NEW records across all sections for a given date.

    A chat session opening on the homepage often wants a one-shot answer
    to "what's new today" without re-reading every section. This tool
    returns up to `per_section` newest records per section, capped at
    `sections` distinct sections, deterministically ordered for prompt
    cache stability.

    Args:
        date_iso: YYYY-MM-DD. None = most recent ingest date.
        per_section: max records per section (default 3).
        sections: max distinct sections to include (default 20).

    Returns:
        {"date": str, "section_count": int, "by_section": {section_id: [records]}}
    """
    per_section = min(max(1, per_section), 10)
    sections = min(max(1, sections), 60)
    with _open_db() as conn:
        if date_iso is None:
            row = conn.execute(
                "SELECT substr(MAX(ingested_at), 1, 10) FROM records"
            ).fetchone()
            date_iso = row[0] if row and row[0] else None
        if not date_iso:
            return {"error": "no records in the lake"}

        # Pull all records for the day, then partition + cap per section.
        rows = conn.execute(
            """
            SELECT id, section_id, source_id, original_text, original_url,
                   record_date, ingested_at, original_lang
              FROM records
             WHERE substr(ingested_at, 1, 10) = ?
             ORDER BY section_id, ingested_at DESC
            """,
            (date_iso,),
        ).fetchall()

    by_section: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        lst = by_section.setdefault(d["section_id"], [])
        if len(lst) < per_section:
            lst.append(d)
    # Cap section count.
    if len(by_section) > sections:
        keep = sorted(by_section.keys())[:sections]
        by_section = {k: by_section[k] for k in keep}

    return {
        "date": date_iso,
        "section_count": len(by_section),
        "by_section": by_section,
    }


# ----------------------------------------------------------------------- #
# Tool: entity_neighborhood_graph
# ----------------------------------------------------------------------- #

@mcp.tool()
def entity_neighborhood_graph(
    entity_id: str,
    radius: int = 1,
    max_nodes: int = 80,
) -> dict:
    """Entity neighborhood as a graph payload: nodes + edges, ready for
    visualization (D3, sigma.js) or further analysis in chat.

    Args:
        entity_id: entity id or canonical-name substring.
        radius: BFS depth from the seed (1 = direct neighbors only,
            default; capped at 3).
        max_nodes: cap on the number of nodes returned (default 80).

    Returns:
        {"seed_id": str, "nodes": [{"id","canonical_name","entity_type"}, ...],
         "edges": [{"from","to","type","weight","last_seen"}, ...],
         "truncated": bool}
    """
    radius = min(max(1, radius), 3)
    max_nodes = min(max(2, max_nodes), 300)
    _SEED_SQL = "SELECT id, canonical_name, type AS entity_type FROM entities"
    with _open_db() as conn:
        # Resolve seed
        seed_row = conn.execute(f"{_SEED_SQL} WHERE id = ?", (entity_id,)).fetchone()
        if seed_row is None:
            seed_row = conn.execute(
                f"{_SEED_SQL} WHERE lower(canonical_name) LIKE ? LIMIT 1",
                (f"%{entity_id.lower()}%",),
            ).fetchone()
        if seed_row is None:
            return {"error": f"no entity resolved from {entity_id!r}"}
        seed = seed_row["id"]

        # BFS
        nodes: dict[str, dict] = {seed: dict(seed_row)}
        edges: list[dict] = []
        frontier = {seed}
        truncated = False
        for _depth in range(radius):
            next_frontier: set[str] = set()
            for nid in frontier:
                if len(nodes) >= max_nodes:
                    truncated = True
                    break
                rels = conn.execute(
                    """
                    SELECT from_entity, to_entity, type, weight, last_seen
                      FROM relationships
                     WHERE from_entity = ? OR to_entity = ?
                     ORDER BY last_seen DESC
                     LIMIT 60
                    """,
                    (nid, nid),
                ).fetchall()
                for r in rels:
                    other = r["to_entity"] if r["from_entity"] == nid else r["from_entity"]
                    edges.append(dict(r))
                    if other in nodes:
                        continue
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    other_row = conn.execute(
                        "SELECT id, canonical_name, type AS entity_type FROM entities WHERE id = ?",
                        (other,),
                    ).fetchone()
                    if other_row is not None:
                        nodes[other] = dict(other_row)
                        next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

    # Dedupe edges by (from,to,type)
    seen_e: set[tuple] = set()
    deduped: list[dict] = []
    for e in edges:
        key = (e.get("from_entity"), e.get("to_entity"), e.get("type"))
        if key in seen_e:
            continue
        seen_e.add(key)
        deduped.append({
            "from": e.get("from_entity"),
            "to": e.get("to_entity"),
            "type": e.get("type"),
            "weight": e.get("weight"),
            "last_seen": e.get("last_seen"),
        })

    return {
        "seed_id": seed,
        "nodes": [{"id": n["id"],
                   "canonical_name": n.get("canonical_name"),
                   "entity_type": n.get("entity_type")} for n in nodes.values()],
        "edges": deduped,
        "node_count": len(nodes),
        "edge_count": len(deduped),
        "truncated": truncated,
    }


# ----------------------------------------------------------------------- #
# Resource: lake_overview
# ----------------------------------------------------------------------- #

@mcp.resource("worldscope://lake/overview")
def lake_overview() -> str:
    """One-glance summary: row counts per table, latest dates, source tiers."""
    with _open_db() as conn:
        out = {}
        for table in ("sources", "records", "entities", "relationships",
                      "paper_bets", "paper_bet_marks", "anomalies", "briefs",
                      "predictions", "quarantine"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            out[table] = count
        latest_record = conn.execute(
            "SELECT MAX(ingested_at) FROM records"
        ).fetchone()[0]
        latest_brief = conn.execute(
            "SELECT MAX(date) FROM briefs"
        ).fetchone()[0]
        sources = conn.execute(
            "SELECT tier, COUNT(*) FROM sources GROUP BY tier"
        ).fetchall()
    out["latest_record_ingested"] = latest_record
    out["latest_brief"] = latest_brief
    out["sources_by_tier"] = {r[0]: r[1] for r in sources}
    return json.dumps(out, indent=2)


# ----------------------------------------------------------------------- #
# Entrypoint
# ----------------------------------------------------------------------- #

if __name__ == "__main__":
    mcp.run()
