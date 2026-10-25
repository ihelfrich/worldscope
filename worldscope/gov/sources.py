"""Registry of official U.S. government RSS/Atom feeds, by branch and organ.

This is the curated backbone for the branches/organs the Federal Register API
does NOT cover (the FR API covers all executive departments + presidential
documents; it is pulled separately in `fetch.fetch_federal_register`).

Each entry is a `GovSource`. Feeds are fetched defensively and in parallel; a
feed that 404s, moves, or returns junk is skipped and logged, never fatal —
so the registry can be broad and aspirational without risking the brief. Add a
feed by appending one line; remove a dead one by deleting it.

`branch` is one of: executive, legislative, judicial, independent, state.
`tier` follows the WorldScope source-tier vocabulary (these are all
primary_document — official government sources speaking for themselves).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GovSource:
    branch: str          # executive | legislative | judicial | independent | state
    org: str             # canonical organization name
    url: str             # RSS/Atom feed URL
    label: str           # human label shown in the brief
    kind: str = "rss"    # rss | atom (both handled by the same parser)
    tier: str = "primary_document"


# --------------------------------------------------------------------------- #
# The registry. Grouped by branch for readability; order does not matter.
# --------------------------------------------------------------------------- #
GOV_SOURCES: list[GovSource] = [
    # ===================== EXECUTIVE — White House ========================= #
    GovSource("executive", "The White House",
              "https://www.whitehouse.gov/presidential-actions/feed/",
              "White House — Presidential Actions"),
    GovSource("executive", "The White House",
              "https://www.whitehouse.gov/briefing-room/feed/",
              "White House — Briefing Room"),
    GovSource("executive", "The White House",
              "https://www.whitehouse.gov/news/feed/",
              "White House — News"),

    # ===================== EXECUTIVE — Departments (press) ================= #
    # (The Federal Register API covers these depts' *rules/notices*; these RSS
    #  feeds add their *press releases / statements*, which the FR does not.)
    GovSource("executive", "Department of the Treasury",
              "https://home.treasury.gov/system/files/126/ofac.xml",
              "Treasury — OFAC Recent Actions"),
    GovSource("executive", "Department of the Treasury",
              "https://home.treasury.gov/rss/press.xml",
              "Treasury — Press Releases"),
    GovSource("executive", "Department of Defense",
              "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=30",
              "DoD — Releases"),
    GovSource("executive", "Department of Defense",
              "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=30",
              "DoD — Contracts"),
    GovSource("executive", "Department of Justice",
              "https://www.justice.gov/news/rss?type=press_release",
              "DOJ — Office of Public Affairs (AG)"),
    GovSource("executive", "Department of State",
              "https://www.state.gov/rss-feed/press-releases/feed/",
              "State Department — Press Releases"),
    GovSource("executive", "Department of Energy",
              "https://www.energy.gov/rss/articles.xml",
              "Department of Energy — News"),
    GovSource("executive", "Department of Transportation",
              "https://www.transportation.gov/rss/briefing-room.xml",
              "Department of Transportation — Briefing Room"),
    GovSource("executive", "Department of Homeland Security",
              "https://www.dhs.gov/news-releases/all/rss.xml",
              "DHS — News Releases"),
    GovSource("executive", "Department of Education",
              "https://www.ed.gov/feed",
              "Department of Education — Press"),
    GovSource("executive", "Department of Health and Human Services",
              "https://www.hhs.gov/about/news/rss.xml",
              "HHS — News"),
    GovSource("executive", "Department of Commerce",
              "https://www.commerce.gov/feeds/news",
              "Department of Commerce — News"),
    GovSource("executive", "Department of Labor",
              "https://www.dol.gov/rss/releases.xml",
              "Department of Labor — Releases"),
    GovSource("executive", "Department of the Interior",
              "https://www.doi.gov/feeds/rss/pressreleases.xml",
              "Department of the Interior — Press"),
    GovSource("executive", "Department of Agriculture",
              "https://www.usda.gov/rss/home.xml",
              "USDA — News"),
    GovSource("executive", "Environmental Protection Agency",
              "https://www.epa.gov/newsreleases/search/rss",
              "EPA — News Releases"),

    # ===================== EXECUTIVE — Security / Intelligence ============= #
    GovSource("executive", "Office of the Director of National Intelligence",
              "https://www.dni.gov/index.php/newsroom/press-releases?format=feed&type=rss",
              "ODNI — Press Releases"),
    GovSource("executive", "Central Intelligence Agency",
              "https://www.cia.gov/stories/feed/",
              "CIA — Stories / Press"),
    GovSource("executive", "Federal Bureau of Investigation",
              "https://www.fbi.gov/feeds/national-press-releases/rss.xml",
              "FBI — National Press Releases"),
    GovSource("executive", "Cybersecurity and Infrastructure Security Agency",
              "https://www.cisa.gov/cybersecurity-advisories/all.xml",
              "CISA — Advisories"),

    # ===================== INDEPENDENT — Fed / regulators ================== #
    GovSource("independent", "Federal Reserve",
              "https://www.federalreserve.gov/feeds/press_all.xml",
              "Federal Reserve — All Press"),
    GovSource("independent", "Federal Reserve",
              "https://www.federalreserve.gov/feeds/press_monetary.xml",
              "Federal Reserve — Monetary Policy"),
    GovSource("independent", "Securities and Exchange Commission",
              "https://www.sec.gov/news/pressreleases.rss",
              "SEC — Press Releases"),

    # ===================== LEGISLATIVE — Congress ========================== #
    # (Bills/laws/votes come primarily through the Congress.gov API in fetch.py;
    #  these feeds add floor/news color. CBO is the analytic arm.)
    GovSource("legislative", "Congressional Budget Office",
              "https://www.cbo.gov/publications/all/rss.xml",
              "CBO — Publications"),
    GovSource("legislative", "Government Accountability Office",
              "https://www.gao.gov/rss/reports.xml",
              "GAO — Reports"),

    # ===================== JUDICIAL ======================================== #
    # (SCOTUS opinions come through CourtListener in fetch.py when a token is
    #  present; this feed adds the daily orders/press where available.)
    GovSource("judicial", "U.S. Courts",
              "https://www.uscourts.gov/news/rss.xml",
              "U.S. Courts — News"),

    # ===================== STATE — Attorneys General (seed) ================ #
    GovSource("state", "California Attorney General",
              "https://oag.ca.gov/rss/media",
              "California AG — Press"),
    GovSource("state", "New York Attorney General",
              "https://ag.ny.gov/rss/press-releases.xml",
              "New York AG — Press"),
    GovSource("state", "Texas Attorney General",
              "https://www.texasattorneygeneral.gov/rss.xml",
              "Texas AG — Press"),
    GovSource("state", "Florida Attorney General",
              "http://www.myfloridalegal.com/newsrel.nsf/newsreleases?openview&rss",
              "Florida AG — Press"),
]


def sources_for_branch(branch: str) -> list[GovSource]:
    """All registry feeds for a given branch (executive/legislative/…)."""
    b = (branch or "").lower().strip()
    return [s for s in GOV_SOURCES if s.branch == b]


# Canonical branch list, for CLI validation / docs.
BRANCHES = ("executive", "legislative", "judicial", "independent", "state")
