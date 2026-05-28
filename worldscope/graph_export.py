"""graph_export — entity co-occurrence graph for the /graph/ view.

Computes nodes + edges from the lake's record_entities table, capped to
keep the force-directed view interactive (≤150 nodes, edges between
selected nodes only with co-occurrence ≥ 2).

Node selection priority:

  1. Pin every cross-section recurrence entity (these are the day's
     signal — they MUST appear in the graph).
  2. Backfill with top-mention single-section entities up to the cap.

Edge construction:

  For every record mentioning ≥2 selected entities today, increment
  edges between every pair (re1.entity_id < re2.entity_id). Track which
  sections produced each edge so the chat can drill in.

Output: dist/data/graph.json, ~30-80 KB depending on density.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any


MAX_NODES        = 150
MIN_EDGE_WEIGHT  = 2
MAX_EDGES        = 600


def build_graph(
    *,
    lake_db: Path,
    meta_dir: Path,
    out_dir: Path,
    today: _date | None = None,
) -> Path:
    today = today or _date.today()
    iso = today.isoformat()
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / "graph.json"

    doc: dict[str, Any] = {
        "date": iso,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes": [], "edges": [],
        "node_count": 0, "edge_count": 0,
    }
    if not lake_db.exists():
        out_path.write_text(json.dumps(doc), encoding="utf-8")
        return out_path

    conn = sqlite3.connect(f"file:{lake_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # --- 1. Select nodes -----------------------------------------------
        pinned_ids: set[str] = set()
        cs_path = meta_dir / iso / "cross_section.json"
        if cs_path.exists():
            cs = json.loads(cs_path.read_text(encoding="utf-8"))
            for band in ("high", "medium", "low"):
                for ent in (cs.get("by_confidence", {}).get(band) or []):
                    eid = ent.get("entity_id")
                    name = ent.get("canonical_name")
                    if not eid and name:
                        row = conn.execute(
                            "SELECT id FROM entities WHERE lower(canonical_name) = ? LIMIT 1",
                            (name.lower(),),
                        ).fetchone()
                        eid = row["id"] if row else None
                    if eid:
                        pinned_ids.add(eid)

        # Heaviest-mention entities for today, excluding pinned set
        placeholders = ",".join("?" * len(pinned_ids)) if pinned_ids else "''"
        backfill_n = max(0, MAX_NODES - len(pinned_ids))
        bf_rows = conn.execute(
            f"""
            SELECT e.id, e.canonical_name, e.type, COUNT(*) AS mentions
              FROM entities e
              JOIN record_entities re ON e.id = re.entity_id
              JOIN records r          ON re.record_id = r.id
             WHERE substr(r.ingested_at, 1, 10) = ?
               {"AND e.id NOT IN (" + placeholders + ")" if pinned_ids else ""}
             GROUP BY e.id
             ORDER BY mentions DESC, e.canonical_name
             LIMIT ?
            """,
            [iso] + list(pinned_ids) + [backfill_n],
        ).fetchall()

        # Look up pinned-entity attributes
        pinned_rows: list[dict] = []
        if pinned_ids:
            q = "SELECT id, canonical_name, type FROM entities WHERE id IN (" + placeholders + ")"
            for r in conn.execute(q, list(pinned_ids)).fetchall():
                # mentions count for pinned
                m = conn.execute(
                    """SELECT COUNT(*) FROM record_entities re
                            JOIN records r ON r.id = re.record_id
                       WHERE re.entity_id = ? AND substr(r.ingested_at,1,10) = ?""",
                    (r["id"], iso),
                ).fetchone()[0]
                pinned_rows.append({"id": r["id"], "canonical_name": r["canonical_name"],
                                    "type": r["type"], "mentions": m})

        selected_ids: set[str] = set()
        nodes: list[dict] = []
        for row in (pinned_rows + [dict(r) for r in bf_rows]):
            if row["id"] in selected_ids or len(nodes) >= MAX_NODES:
                continue
            selected_ids.add(row["id"])
            # How many distinct sections mention this entity today?
            n_sections = conn.execute(
                """SELECT COUNT(DISTINCT r.section_id)
                     FROM record_entities re JOIN records r ON r.id = re.record_id
                    WHERE re.entity_id = ? AND substr(r.ingested_at,1,10) = ?""",
                (row["id"], iso),
            ).fetchone()[0]
            nodes.append({
                "id":       row["id"],
                "name":     row["canonical_name"],
                "type":     (row["type"] or "other").split(":")[0],
                "mentions": int(row["mentions"]),
                "sections": int(n_sections),
                "pinned":   row["id"] in pinned_ids,
            })

        # --- 2. Build edges over selected nodes only -----------------------
        if selected_ids:
            sel_placeholders = ",".join("?" * len(selected_ids))
            edge_counter: dict[tuple[str, str], dict] = {}
            rows = conn.execute(
                f"""
                SELECT re1.entity_id AS a, re2.entity_id AS b, r.section_id
                  FROM record_entities re1
                  JOIN record_entities re2
                    ON re1.record_id = re2.record_id AND re1.entity_id < re2.entity_id
                  JOIN records r ON r.id = re1.record_id
                 WHERE substr(r.ingested_at, 1, 10) = ?
                   AND re1.entity_id IN ({sel_placeholders})
                   AND re2.entity_id IN ({sel_placeholders})
                """,
                [iso] + list(selected_ids) * 2,
            ).fetchall()
            for r in rows:
                key = (r["a"], r["b"])
                bucket = edge_counter.setdefault(key, {"weight": 0, "sections": set()})
                bucket["weight"] += 1
                bucket["sections"].add(r["section_id"])

            edges: list[dict] = []
            for (a, b), v in edge_counter.items():
                if v["weight"] < MIN_EDGE_WEIGHT:
                    continue
                edges.append({
                    "source":   a,
                    "target":   b,
                    "weight":   v["weight"],
                    "sections": sorted(v["sections"]),
                })
            edges.sort(key=lambda e: -e["weight"])
            edges = edges[:MAX_EDGES]
        else:
            edges = []
    finally:
        conn.close()

    doc["nodes"]      = nodes
    doc["edges"]      = edges
    doc["node_count"] = len(nodes)
    doc["edge_count"] = len(edges)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
    return out_path


def build_from_repo(repo: Path, out_dir: Path, today: _date | None = None) -> Path:
    return build_graph(
        lake_db=repo / "lake" / "db" / "worldscope.sqlite",
        meta_dir=repo / "lake" / "sections" / "_meta",
        out_dir=Path(out_dir),
        today=today,
    )
