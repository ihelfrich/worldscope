"""
fec.py — recent SEC-equivalent for politics: top campaign finance activity
from the FEC (Federal Election Commission) OpenFEC API.

Two sub-pulls combined:
  1. Top candidates by cycle receipts — who's raising the most this cycle.
  2. Recently filed forms — Form 3 (candidate quarterly), Form 3X (PAC),
     Form 5 (independent expenditure) — most recent 25.

API: https://api.open.fec.gov/v1
Requires an API key. DEMO_KEY works at low volume (~30 req/hr). For
production-cadence use, register at https://api.data.gov/signup/ and
set OPENFEC_API_KEY in env.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import requests

from . import Section

API = "https://api.open.fec.gov/v1"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _api_key() -> str:
    return os.environ.get("OPENFEC_API_KEY") or "DEMO_KEY"


class FECSection(Section):
    id = "fec"
    title = "Campaign finance (FEC: top fundraisers + recent filings)"
    emoji = "🗳️"

    TOP_CANDIDATES = 12
    RECENT_FILINGS = 15
    CURRENT_CYCLE = 2026  # update when the next cycle's data is current

    def pull(self) -> list[dict]:
        session = requests.Session()
        session.headers["User-Agent"] = UA
        session.headers["Accept"] = "application/json"
        items: list[dict] = []

        # --- 1. Top candidates by cycle receipts -------------------------
        try:
            r = session.get(
                f"{API}/candidates/totals/",
                params={
                    "api_key": _api_key(),
                    "cycle": self.CURRENT_CYCLE,
                    "election_full": "true",
                    "sort": "-receipts",
                    "per_page": self.TOP_CANDIDATES,
                },
                timeout=20,
            )
            r.raise_for_status()
            top = r.json()
        except Exception:
            top = {"results": []}

        for c in (top.get("results") or [])[: self.TOP_CANDIDATES]:
            name = c.get("name", "(unknown)")
            office = c.get("office_full", "")
            state = c.get("state", "")
            party = c.get("party_full", "")
            receipts = c.get("receipts") or 0
            disbursements = c.get("disbursements") or 0
            cid = c.get("candidate_id", "")
            items.append({
                "id": f"cand:{cid}",
                "date": date.today().isoformat(),
                "title": f"[Top] {name} ({party[:3]}, {office} {state}): ${receipts/1e6:.2f}M raised",
                "url": f"https://www.fec.gov/data/candidate/{cid}/" if cid else "https://www.fec.gov/data/candidates/",
                "summary": (
                    f"cycle {self.CURRENT_CYCLE} receipts ${receipts/1e6:.2f}M · "
                    f"disbursements ${disbursements/1e6:.2f}M · "
                    f"net ${(receipts-disbursements)/1e6:+.2f}M"
                ),
                "candidate_id": cid,
                "office": office,
                "state": state,
                "party": party,
                "receipts": receipts,
                "kind": "candidate",
            })

        # --- 2. Most recent filings --------------------------------------
        try:
            r = session.get(
                f"{API}/filings/",
                params={
                    "api_key": _api_key(),
                    "sort": "-receipt_date",
                    "per_page": self.RECENT_FILINGS,
                },
                timeout=20,
            )
            r.raise_for_status()
            filings = r.json()
        except Exception:
            filings = {"results": []}

        for f in (filings.get("results") or [])[: self.RECENT_FILINGS]:
            committee = f.get("committee_name") or f.get("candidate_name") or "(unknown)"
            form = f.get("form_type") or ""
            receipt_date = f.get("receipt_date") or ""
            total_receipts = f.get("total_receipts") or 0
            file_number = f.get("file_number") or ""
            pdf_url = f.get("pdf_url") or ""
            cycle = f.get("cycle") or ""
            items.append({
                "id": f"filing:{file_number}",
                "date": (receipt_date or "")[:10],
                "title": f"[{form}] {committee[:70]}",
                "url": pdf_url or f"https://www.fec.gov/data/filing/{file_number}/",
                "summary": (
                    f"cycle {cycle} · "
                    f"filing #{file_number}"
                    + (f" · receipts ${total_receipts/1e6:.2f}M" if total_receipts else "")
                ),
                "form_type": form,
                "committee": committee,
                "cycle": cycle,
                "kind": "filing",
            })

        return items
