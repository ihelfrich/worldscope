"""Query U.S. government documents — at a moment's notice.

Two modes:

  - **lake** (default): search everything GovScope has already ingested into the
    WorldScope data lake (fast, offline, full history).
  - **live** (`--live`): fetch fresh across every branch right now and search
    that (use when you need the absolute latest and don't mind the network).

Examples
--------
    # everything DOE did this week, from the lake
    python -m worldscope.gov.query --org "Department of Energy" --since 2026-06-10

    # live, all branches, mentioning "tariff"
    python -m worldscope.gov.query --live --query tariff

    # all judicial-branch docs, as JSON, for piping
    python -m worldscope.gov.query --branch judicial --json

    # the latest 20 presidential documents
    python -m worldscope.gov.query --doc-type "Presidential Document" --limit 20
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Optional

from .fetch import GovDoc, gather_all

GOV_SECTIONS = ("gov_us", "federal_register")


# --------------------------------------------------------------------------- #
# pure filter (unit-tested) — works on any list of GovDoc dicts
# --------------------------------------------------------------------------- #
def filter_docs(docs: list[GovDoc], *,
                query: Optional[str] = None,
                branch: Optional[str] = None,
                org: Optional[str] = None,
                doc_type: Optional[str] = None,
                since: Optional[str] = None,
                limit: Optional[int] = None) -> list[GovDoc]:
    q = (query or "").lower().strip()
    br = (branch or "").lower().strip()
    og = (org or "").lower().strip()
    dt = (doc_type or "").lower().strip()
    out: list[GovDoc] = []
    for d in docs:
        if q and q not in (d.get("title", "") + " " + d.get("summary", "")).lower():
            continue
        if br and br != (d.get("branch", "") or "").lower():
            continue
        if og and og not in (d.get("org", "") or "").lower():
            continue
        if dt and dt not in (d.get("doc_type", "") or "").lower():
            continue
        if since and (d.get("date") or "") and d["date"][:10] < since:
            continue
        out.append(d)
    out.sort(key=lambda d: (d.get("date") or "", d.get("org") or ""), reverse=True)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- #
# lake-backed search
# --------------------------------------------------------------------------- #
def _row_to_doc(row) -> GovDoc:
    try:
        extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    except Exception:
        extra = {}
    text = row["original_text"] or ""
    title, _, summary = text.partition(" — ")
    return {
        "id": row["record_id"],
        "date": (row["record_date"] or "")[:10] if row["record_date"] else "",
        "title": title.strip() or text[:120],
        "url": row["original_url"] or "",
        "summary": (extra.get("summary") or summary).strip(),
        "branch": extra.get("branch", ""),
        "org": extra.get("org", ""),
        "doc_type": extra.get("doc_type", ""),
        "source": extra.get("source", row["section_id"]),
    }


def search_lake(db_path=None, **filters) -> list[GovDoc]:
    """Read GovScope records out of the lake and filter them. Never raises on a
    missing/empty lake — returns []."""
    try:
        from ..lake import Lake
        lake = Lake.open(db_path) if db_path else Lake.open()
        conn = lake._ensure_open()
        placeholders = ",".join("?" for _ in GOV_SECTIONS)
        rows = conn.execute(
            f"SELECT id AS record_id, source_id, section_id, original_url, "
            f"original_text, record_date, extra_json FROM records "
            f"WHERE section_id IN ({placeholders}) "
            f"ORDER BY record_date DESC LIMIT 5000",
            GOV_SECTIONS,
        ).fetchall()
    except Exception as exc:  # missing table / db -> empty, not fatal
        print(f"[gov.query] lake unavailable: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return []
    docs = [_row_to_doc(r) for r in rows]
    return filter_docs(docs, **filters)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_markdown(docs: list[GovDoc]) -> str:
    if not docs:
        return "_no matching government documents._"
    lines = [f"# {len(docs)} government document(s)", ""]
    for d in docs:
        lines.append(
            f"- **{d.get('date','')}** · _{d.get('branch','')}_ · "
            f"{d.get('org','')} · {d.get('doc_type','')}\n"
            f"  [{d.get('title','(no title)')}]({d.get('url','#')})"
        )
        if d.get("summary"):
            lines.append(f"  > {d['summary'][:240]}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m worldscope.gov.query",
        description="Search U.S. government documents across all branches.")
    p.add_argument("--query", "-q", help="substring match in title+summary")
    p.add_argument("--branch", help="executive|legislative|judicial|independent|state")
    p.add_argument("--org", help="organization substring, e.g. 'Department of Energy'")
    p.add_argument("--doc-type", dest="doc_type",
                   help="Rule | Proposed Rule | Presidential Document | Press Release | Bill | Opinion")
    p.add_argument("--since", help="ISO date floor, e.g. 2026-06-01")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--days", type=int, default=2, help="(live mode) lookback window")
    p.add_argument("--live", action="store_true",
                   help="fetch fresh from sources instead of reading the lake")
    p.add_argument("--json", action="store_true", help="emit JSON")
    args = p.parse_args(argv)

    filters = dict(query=args.query, branch=args.branch, org=args.org,
                   doc_type=args.doc_type, since=args.since, limit=args.limit)

    if args.live:
        docs = gather_all(days=args.days, raise_on_total_failure=False)
        docs = filter_docs(docs, **filters)
    else:
        docs = search_lake(**filters)

    if args.json:
        print(json.dumps(docs, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
