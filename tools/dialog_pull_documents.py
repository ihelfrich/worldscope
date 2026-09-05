#!/usr/bin/env python3
"""
dialog_pull_documents.py — one-shot public-record document harvester for the
"Dialog retreat members" research report.

For every principal (person) and every key company / fund / org / PAC in the
network, fire the WORLDSCOPE research pipeline (EDGAR full-text, CourtListener
opinions, Federal Register, OpenSanctions) and write a bundle under
research_reports/dialog-retreat-members/entities/<slug>/.

Public figures + public records only. GDELT is skipped (rate-limited 429s) to
keep the run polite and fast; the other four sources carry the document load.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from worldscope import research as R  # noqa: E402

OUT = REPO / "research_reports" / "dialog-retreat-members" / "entities"

# (query, type) — type drives which sources fire (entity adds sanctions).
PEOPLE = [
    "Peter Thiel", "Elon Musk", "Joe Lonsdale", "Reid Hoffman", "Greg Brockman",
    "Scott Bessent", "Ted Cruz", "Cory Booker", "Jim Himes", "Lisa Monaco",
    "Wes Moore", "Mike Novogratz", "Barry Sternlicht", "Robert Rubin",
    "Nicolas Berggruen", "Barry Silbert", "Wences Casares", "Marcos Galperin",
    "Chamath Palihapitiya", "Bryan Johnson", "Neal Mohan", "Vas Narasimhan",
    "Mario Schlosser", "Scott Cook", "Mike Cannon-Brookes", "Stanley McChrystal",
    "Gaurav Kapadia", "Grover Norquist", "Leonard Leo", "Rachel Brand",
    "Anne-Marie Slaughter", "Susan Athey", "Jonathan Levin", "Alex Bores",
    "Ritchie Torres", "Reema bint Bandar Al Saud",
]

ENTITIES = [
    "Palantir Technologies", "Founders Fund", "8VC", "Addepar", "OpenAI",
    "Anthropic", "Tesla Inc", "SpaceX", "Galaxy Digital Holdings",
    "Starwood Capital Group", "Digital Currency Group", "Grayscale Investments",
    "Key Square Capital Management", "Oscar Health", "Intuit Inc",
    "Atlassian Corp", "Novartis AG", "MercadoLibre Inc", "Social Capital",
    "Berggruen Holdings", "Xapo Bank", "Americans for Tax Reform",
    "Marble Freedom Trust", "McChrystal Group", "XN LP", "Trendyol Group",
    "Kernel Blueprint", "Galaxy Digital", "Stripe Inc", "Rigetti Computing",
]


def slugify(q: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:50]


def run(query: str, qtype: str) -> None:
    slug = slugify(query)
    try:
        res = R.ResearchResult(
            query=query, query_type=qtype,
            pulled_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
        )
        res.sections["federal_register"] = R._safe("fedreg", R.pull_federal_register, query) or []
        res.sections["edgar"] = R._safe("edgar", R.pull_edgar, query) or []
        res.sections["courtlistener"] = R._safe("court", R.pull_courtlistener, query) or []
        res.sections["sanctions"] = R._safe("sanctions", R.pull_opensanctions, query) or []
        R.write_bundle(res, OUT)
        counts = {k: len(v) for k, v in res.sections.items()}
        print(f"[{slug}] {counts}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[{slug}] FAILED: {type(exc).__name__}: {exc}", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    work = [(p, "entity") for p in PEOPLE] + [(e, "entity") for e in ENTITIES]
    print(f"[dialog-pull] {len(work)} entities → {OUT}", flush=True)
    for i, (q, t) in enumerate(work, 1):
        print(f"--- {i}/{len(work)}: {q}", flush=True)
        run(q, t)
        time.sleep(1.2)  # polite to SEC / CourtListener
    print("[dialog-pull] DONE", flush=True)


if __name__ == "__main__":
    main()
