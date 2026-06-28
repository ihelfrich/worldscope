#!/usr/bin/env python3
"""
dialog_pull_documents_wave2.py — deepen the public-record document layer for the
Dialog network with INVESTMENT, BUSINESS, LEGAL, and (public) TAX records.

Covers the ~80 second-degree leak-roster people and their companies/funds that
wave 1 did not, then adds nonprofit Form 990 financials for the foundations in
the network (public by statute, via ProPublica Nonprofit Explorer).

Sources, all public:
  - SEC EDGAR full-text  → investment & business filings (13D/13F/Form 4/S-1/8-K)
  - CourtListener        → legal filings / opinions
  - Federal Register     → rules/notices naming the entity
  - OpenSanctions (local)→ sanctions/PEP hits
  - ProPublica 990       → nonprofit tax filings (revenue/assets/officers)

NOT collected: private individual tax returns (confidential under 26 U.S.C.
§6103) — only public 990s and tax-related litigation are in scope.
"""
from __future__ import annotations

import datetime
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import requests  # noqa: E402
from worldscope import research as R  # noqa: E402

OUT = REPO / "research_reports" / "dialog-retreat-members" / "entities"
FOUND_OUT = REPO / "research_reports" / "dialog-retreat-members" / "foundations"
UA = "worldscope/0.1 dialog-wave2 (ianthelfrich@gmail.com)"

# --- new people (second-degree roster) with plausible filing footprints ------
PEOPLE = [
    "Henry Kravis", "John Arnold", "Peter Briger", "Peter Brown", "Robert Jain",
    "Karen Karniol-Tambour", "Scott Stephenson", "Thasunda Brown Duckett",
    "Marc Andreessen", "Jim Breyer", "Randall Kroszner", "Eric Schmidt",
    "Adam D'Angelo", "Howie Liu", "Severin Hacker", "Jonathan Ross",
    "Cesar Carvalho", "Matt Cohler", "Palmer Luckey", "Shivon Zilis",
    "Daniel Schulman", "Strauss Zelnick", "Jared Kushner", "Will Scharf",
    "Julian Castro", "Mitch Daniels", "Daniel Driscoll", "Jim O'Neill",
    "Robert Hur", "Neal Katyal", "Preet Bharara", "Tom Goldstein",
    "Jared Polis", "Lawrence Summers", "Garry Kasparov", "Niall Ferguson",
    "Tim Ferriss", "Peter Attia", "Marie-Josee Kravis", "Michael Novogratz",
]

# --- companies / funds named in the expansion --------------------------------
ENTITIES = [
    "KKR & Co", "Fortress Investment Group", "Renaissance Technologies",
    "Millennium Management", "Jain Global", "TIAA", "Breyer Capital",
    "Blackstone Inc", "Circle Internet Group", "MicroStrategy",
    "Verisk Analytics", "Relativity Space", "Quora", "Airtable",
    "Duolingo", "Groq", "PsiQuantum", "Wellhub", "Anduril Industries",
    "Affinity Partners", "QXO Inc", "Verizon Communications",
    "Take-Two Interactive", "Robinhood Markets", "Coinbase Global",
    "Block Inc", "Andreessen Horowitz", "Ribbit Capital", "Bridgewater Associates",
    "Galaxy Digital", "Arnold Ventures", "Schmidt Futures", "Relativity",
]

# --- foundations / nonprofits → public 990s ----------------------------------
FOUNDATIONS = [
    "Thiel Foundation", "Marble Freedom Trust", "Berggruen Institute",
    "Anti-Defamation League", "Cato Institute", "Charles Koch Foundation",
    "Renew Democracy Initiative", "Human Rights Foundation",
    "Americans for Tax Reform", "Americans for Tax Reform Foundation",
    "New America Foundation", "Robin Hood Foundation", "Latino Community Foundation",
    "King Faisal Foundation", "Lifebox Foundation", "Math for America",
    "Simons Foundation", "Federalist Society", "Hudson Institute",
    "Atlantic Council", "Council on Foreign Relations", "Heterodox Academy",
    "Saisei Foundation", "Knight Foundation", "Stand Together Foundation",
]


def slugify(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:50]


def pull_propublica_990(name: str) -> list[dict]:
    """Public nonprofit 990 data: latest filing financials + officers."""
    r = requests.get(
        "https://projects.propublica.org/nonprofits/api/v2/search.json",
        params={"q": name}, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    orgs = (r.json().get("organizations") or [])
    if not orgs:
        return []
    out = []
    for org in orgs[:1]:  # top match only
        ein = org.get("ein")
        try:
            d = requests.get(
                f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json",
                headers={"User-Agent": UA}, timeout=25).json()
        except Exception:  # noqa: BLE001
            continue
        o = d.get("organization", {})
        filings = d.get("filings_with_data") or []
        for f in filings[:5]:
            out.append({
                "date": str(f.get("tax_prd_yr") or ""),
                "title": f"{o.get('name','')} (EIN {ein}) — FY{f.get('tax_prd_yr','')} Form 990",
                "url": f"https://projects.propublica.org/nonprofits/organizations/{ein}",
                "snippet": (f"revenue=${f.get('totrevenue','?'):,} "
                            f"expenses=${f.get('totfuncexpns','?'):,} "
                            f"assets_eoy=${f.get('totassetsend','?'):,} "
                            if isinstance(f.get('totrevenue'), int) else
                            "financials n/a"),
                "totrevenue": f.get("totrevenue"),
                "totfuncexpns": f.get("totfuncexpns"),
                "totassetsend": f.get("totassetsend"),
                "pdf_url": f.get("pdf_url"),
            })
        if not filings:
            out.append({
                "date": "", "title": f"{o.get('name','')} (EIN {ein}) — registered, no machine-readable 990",
                "url": f"https://projects.propublica.org/nonprofits/organizations/{ein}",
                "snippet": f"{o.get('city','')}, {o.get('state','')} · ruling {o.get('ruling_date','?')}",
            })
    return out


def run_records(query: str) -> None:
    slug = slugify(query)
    res = R.ResearchResult(
        query=query, query_type="entity",
        pulled_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    res.sections["federal_register"] = R._safe("fedreg", R.pull_federal_register, query) or []
    res.sections["edgar"] = R._safe("edgar", R.pull_edgar, query) or []
    res.sections["courtlistener"] = R._safe("court", R.pull_courtlistener, query) or []
    res.sections["sanctions"] = R._safe("sanctions", R.pull_opensanctions, query) or []
    R.write_bundle(res, OUT)
    print(f"[{slug}] " + str({k: len(v) for k, v in res.sections.items()}), flush=True)


def run_foundation(query: str) -> None:
    slug = slugify(query)
    res = R.ResearchResult(
        query=query, query_type="entity",
        pulled_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    res.sections["form990"] = R._safe("990", pull_propublica_990, query) or []
    res.sections["courtlistener"] = R._safe("court", R.pull_courtlistener, query) or []
    R.write_bundle(res, FOUND_OUT)
    print(f"[990:{slug}] " + str({k: len(v) for k, v in res.sections.items()}), flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FOUND_OUT.mkdir(parents=True, exist_ok=True)
    work = [(q, "rec") for q in PEOPLE + ENTITIES] + [(q, "found") for q in FOUNDATIONS]
    print(f"[wave2] {len(work)} entities ({len(PEOPLE)} people, "
          f"{len(ENTITIES)} cos, {len(FOUNDATIONS)} foundations)", flush=True)
    for i, (q, kind) in enumerate(work, 1):
        print(f"--- {i}/{len(work)}: {q}", flush=True)
        try:
            if kind == "found":
                run_foundation(q)
            else:
                run_records(q)
        except Exception as exc:  # noqa: BLE001
            print(f"[{slugify(q)}] FAILED: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(1.2)
    print("[wave2] DONE", flush=True)


if __name__ == "__main__":
    main()
