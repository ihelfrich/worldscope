"""Positions ledger — who is on what side of every issue, over time.

The "evolution of government and who is on what position" is, at root, derived
from the record: **roll-call votes**, **bill sponsorship/cosponsorship**, and
**formal documents** (an EO advances a position; an amicus brief takes a side).
This module is the ledger that accumulates those datapoints into a queryable,
append-only history so a position can be tracked as it shifts.

v1 implements the highest-signal, fully-structured source — **congressional
roll-call votes** — plus a generic `Position` record and a JSONL store. Each
member's vote on each bill is one position datapoint, tagged with the bill's
policy area as the "issue". Sponsorship and document-derived stances slot into
the same `Position` shape as later populators.

Everything is key-guarded and offline-safe: with no `CONGRESS_API_KEY` the
populator is a no-op; the store + query path work entirely offline.

Store: ``lake/gov/positions/positions.jsonl`` (one JSON Position per line).

CLI
---
    # who voted how on a bill / issue
    python -m worldscope.gov.positions query --issue "agriculture"
    python -m worldscope.gov.positions query --entity "Thompson" --json

    # populate from recent House/Senate votes (needs CONGRESS_API_KEY)
    python -m worldscope.gov.positions populate --congress 119 --days 7
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

CONGRESS_API = "https://api.congress.gov/v3"
UA = "worldscope-govscope/0.1 (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


@dataclass
class Position:
    entity_id: str          # e.g. person:rep-glenn-thompson
    entity_name: str        # display name
    role: str               # Representative | Senator | President | Agency | ...
    party: str              # R | D | I | ""
    state: str              # postal abbr or ""
    issue: str              # policy area / topic label
    subject_id: str         # e.g. bill-119-hr-7567 / eo-14110
    stance: str             # support | oppose | present | advance
    value: str              # raw value (Yea/Nay/Present/sponsor/signed/...)
    date: str               # ISO date
    source: str             # provenance label
    evidence_url: str = ""  # link to the record


# --------------------------------------------------------------------------- #
# pure mappers (unit-tested)
# --------------------------------------------------------------------------- #
_STANCE = {
    "yea": "support", "yes": "support", "aye": "support",
    "nay": "oppose", "no": "oppose",
    "present": "present", "not voting": "present",
}


def stance_for_vote(value: str) -> str:
    return _STANCE.get((value or "").strip().lower(), "present")


def positions_from_vote(vote: dict) -> list[Position]:
    """Pure mapper: one roll-call vote record -> Position rows (one per member).

    Expected (documented) shape — the relevant subset of the Congress.gov vote
    payload, also produced by our fixtures::

        {
          "congress": 119, "chamber": "House",
          "rollNumber": 123, "date": "2026-04-30",
          "bill": {"type": "HR", "number": "7567", "title": "...",
                   "policyArea": "Agriculture and Food"},
          "url": "https://...",
          "members": [
            {"name": "Thompson, Glenn", "bioguideId": "T000467",
             "party": "R", "state": "PA", "vote": "Yea"},
            ...
          ]
        }
    """
    bill = vote.get("bill") or {}
    btype = (bill.get("type") or "").upper()
    bnum = bill.get("number") or ""
    congress = vote.get("congress") or ""
    subject_id = f"bill-{congress}-{btype}-{bnum}".lower().strip("-")
    issue = (bill.get("policyArea") or bill.get("title") or "uncategorized")
    vdate = (vote.get("date") or "")[:10]
    chamber = vote.get("chamber") or ""
    role = "Senator" if chamber.lower().startswith("s") else "Representative"
    src = f"{chamber} Roll Call {vote.get('rollNumber','')}".strip()
    url = vote.get("url", "")
    rows: list[Position] = []
    for m in vote.get("members", []) or []:
        name = m.get("name") or m.get("bioguideId") or "unknown"
        eid = "person:" + _slug(f"{role}-{m.get('bioguideId') or name}")
        rows.append(Position(
            entity_id=eid, entity_name=name, role=role,
            party=m.get("party", ""), state=m.get("state", ""),
            issue=issue, subject_id=subject_id,
            stance=stance_for_vote(m.get("vote", "")),
            value=m.get("vote", ""), date=vdate, source=src, evidence_url=url,
        ))
    return rows


# --------------------------------------------------------------------------- #
# store (JSONL, append-only) + query
# --------------------------------------------------------------------------- #
def default_store() -> Path:
    return Path(__file__).resolve().parent.parent / "lake" / "gov" / "positions" / "positions.jsonl"


def record_positions(rows: list[Position], store: Optional[Path] = None) -> int:
    store = store or default_store()
    store.parent.mkdir(parents=True, exist_ok=True)
    # de-dupe against existing (entity_id, subject_id, date) keys
    existing: set[tuple] = set()
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                existing.add((d["entity_id"], d["subject_id"], d["date"]))
            except Exception:
                continue
    written = 0
    with store.open("a", encoding="utf-8") as fh:
        for r in rows:
            key = (r.entity_id, r.subject_id, r.date)
            if key in existing:
                continue
            existing.add(key)
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            written += 1
    return written


def load_positions(store: Optional[Path] = None) -> list[dict]:
    store = store or default_store()
    if not store.exists():
        return []
    out = []
    for line in store.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def query_positions(rows: list[dict], *, entity: Optional[str] = None,
                    issue: Optional[str] = None, subject: Optional[str] = None,
                    stance: Optional[str] = None, party: Optional[str] = None,
                    limit: Optional[int] = None) -> list[dict]:
    e = (entity or "").lower(); i = (issue or "").lower()
    s = (subject or "").lower(); st = (stance or "").lower(); pa = (party or "").upper()
    out = []
    for r in rows:
        if e and e not in (r.get("entity_name", "") + " " + r.get("entity_id", "")).lower():
            continue
        if i and i not in (r.get("issue", "") or "").lower():
            continue
        if s and s not in (r.get("subject_id", "") or "").lower():
            continue
        if st and st != (r.get("stance", "") or "").lower():
            continue
        if pa and pa != (r.get("party", "") or "").upper():
            continue
        out.append(r)
    out.sort(key=lambda r: (r.get("date", ""), r.get("entity_name", "")), reverse=True)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- #
# populate from Congress.gov (key-guarded)
# --------------------------------------------------------------------------- #
def populate_from_congress(congress: int = 119, days: int = 7,
                           api_key: Optional[str] = None,
                           store: Optional[Path] = None) -> int:
    """Pull recent House+Senate roll-call votes and append Position rows.
    Returns count written. No-op (0) without a key."""
    api_key = api_key or os.environ.get("CONGRESS_API_KEY")
    if not api_key:
        print("[positions] CONGRESS_API_KEY not set; skipping populate.")
        return 0
    since = (date.today() - timedelta(days=days)).isoformat()
    total = 0
    for chamber in ("house", "senate"):
        try:
            resp = requests.get(
                f"{CONGRESS_API}/{chamber}-vote/{congress}",
                params={"api_key": api_key, "fromDate": since,
                        "limit": 100, "format": "json"},
                headers={"User-Agent": UA}, timeout=30)
            resp.raise_for_status()
            votes = resp.json().get("votes", []) or resp.json().get("houseRollCallVotes", [])
            for v in votes:
                rows = positions_from_vote(v)
                total += record_positions(rows, store=store)
        except Exception as exc:
            print(f"[positions] {chamber} votes failed: {type(exc).__name__}: {exc}")
    return total


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m worldscope.gov.positions",
                                description="Positions ledger: who is on what side.")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="search the ledger")
    q.add_argument("--entity"); q.add_argument("--issue")
    q.add_argument("--subject"); q.add_argument("--stance")
    q.add_argument("--party"); q.add_argument("--limit", type=int, default=100)
    q.add_argument("--json", action="store_true")

    pop = sub.add_parser("populate", help="ingest recent congressional votes")
    pop.add_argument("--congress", type=int, default=119)
    pop.add_argument("--days", type=int, default=7)

    args = p.parse_args(argv)
    if args.cmd == "populate":
        n = populate_from_congress(congress=args.congress, days=args.days)
        print(f"recorded {n} position datapoint(s).")
        return 0

    rows = query_positions(load_positions(), entity=args.entity, issue=args.issue,
                           subject=args.subject, stance=args.stance,
                           party=args.party, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        if not rows:
            print("_no matching positions in the ledger._")
        for r in rows:
            print(f"{r.get('date','')}  {r.get('entity_name',''):<28} "
                  f"[{r.get('party','')}/{r.get('state','')}]  "
                  f"{r.get('stance','').upper():<8} {r.get('subject_id','')}  "
                  f"({r.get('issue','')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
