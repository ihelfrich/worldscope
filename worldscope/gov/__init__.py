"""GovScope — comprehensive U.S. government activity tracking for WorldScope.

This subsystem gives two things:

  1. **Access to any public government document at a moment's notice** —
     `worldscope.gov.query` searches the data lake and/or live-fetches across
     every branch and department (CLI: `python -m worldscope.gov.query ...`).

  2. **A daily briefing of everything the U.S. government did** — the
     `gov_us` WorldScope Section (`worldscope/sections/gov_us.py`) pulls from
     the registry below every build, diffs against yesterday, and renders a
     "U.S. Government Daily" block in the brief.

Design follows WorldScope's Tier-1/primary-document philosophy:

  - The **Federal Register API** is the backbone. A single, key-free, reliable
    endpoint that covers EVERY executive department and agency (Energy,
    Transportation, Homeland Security, Education, HHS, Treasury, EPA, …) plus
    all presidential documents (executive orders, memoranda, proclamations).
  - A curated **RSS registry** (`sources.py`) covers the branches the Federal
    Register does not: the White House press operation, Congress, SCOTUS/courts,
    Defense, Treasury press, the Federal Reserve, DOJ/the Attorney General,
    State, the intelligence community, and a seed set of state AGs/governors.
  - Optional **API integrations** (Congress.gov, CourtListener) activate when
    their keys are present; everything degrades gracefully without them.

Nothing here ever hard-crashes a brief: a dead feed is skipped, a missing key
is a no-op, and a total outage raises a typed `SourceUnavailable` so the trust
layer flags it instead of silently reporting an empty government.
"""
from __future__ import annotations

from .sources import GovSource, GOV_SOURCES, sources_for_branch
from .fetch import GovDoc, gather_all, fetch_federal_register, fetch_rss_sources

__all__ = [
    "GovSource",
    "GOV_SOURCES",
    "sources_for_branch",
    "GovDoc",
    "gather_all",
    "fetch_federal_register",
    "fetch_rss_sources",
]
