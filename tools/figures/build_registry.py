"""Build worldscope/figures_registry.yaml from authoritative sources.

Sources:
  Senate:  senate.gov contact_information XML (100 senators).
  House:   clerk.house.gov MemberData.xml (435 voting + delegates).
  Cabinet, SCOTUS, Fed Board: hand-curated from verified primary lists.

Run this whenever the membership rolls over (mid-term, post-election, post-
confirmation). The script is idempotent: it regenerates the YAML from scratch.

Critical rule: every entry must have a verifiable bioguide_id or "TODO".
We never fabricate names. If we cannot verify a slot, the entry stays
with name TODO and an explanatory comment.

Usage:
    python3 tools/figures/build_registry.py > worldscope/figures_registry.yaml
"""
from __future__ import annotations

import sys
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

UA = "Ian Helfrich worldscope/0.1 ianthelfrich@gmail.com"

SENATE_XML = "https://www.senate.gov/general/contact_information/senators_cfm.xml"
HOUSE_XML  = "https://clerk.house.gov/xml/lists/MemberData.xml"


# Map party letter to full label for downstream readability.
PARTY = {"D": "Democratic", "R": "Republican", "I": "Independent", "ID": "Independent"}


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (text or "")).strip("-")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_senators() -> list[dict]:
    data = fetch(SENATE_XML)
    root = ET.fromstring(data)
    out = []
    for m in root.findall("member"):
        def t(tag: str) -> str:
            e = m.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        first = t("first_name").rstrip(".")
        last = t("last_name")
        name = f"{first} {last}".strip()
        party_letter = t("party")
        state = t("state")
        bg = t("bioguide_id")
        entry = {
            "id": f"senator-{_slug(last)}-{_slug(first)}-{state.lower()}",
            "name": name,
            "role": "Senator",
            "jurisdiction": state,
            "party": PARTY.get(party_letter, party_letter or "TODO"),
            "bioguide_id": bg,
            "congress_chamber": "senate",
            "committees": [],          # populate via ProPublica when key is provisioned
            "twitter": "TODO",          # ProPublica also surfaces handles
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["senate"],
            "source": "senate.gov contact_information XML",
        }
        out.append(entry)
    return out


def parse_house() -> list[dict]:
    data = fetch(HOUSE_XML)
    root = ET.fromstring(data)
    out = []
    for m in root.findall(".//member"):
        info = m.find("member-info")
        if info is None:
            continue
        def t(tag: str) -> str:
            e = info.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        first = t("firstname")
        last = t("lastname")
        bg = t("bioguideID")
        party_letter = t("party")
        state_e = info.find("state")
        state = state_e.attrib.get("postal-code", "") if state_e is not None else ""
        district = t("district")
        name = f"{first} {last}".strip()
        # District label tightens jurisdiction (e.g. "TX-15", "AK-At Large").
        if district == "At Large":
            jur = f"{state}-AL"
        elif district:
            jur = f"{state}-{district.zfill(2) if district.isdigit() else district}"
        else:
            jur = state
        entry = {
            "id": f"representative-{_slug(last)}-{_slug(first)}-{state.lower()}-{_slug(district)}",
            "name": name,
            "role": "Representative",
            "jurisdiction": jur,
            "party": PARTY.get(party_letter, party_letter or "TODO"),
            "bioguide_id": bg,
            "congress_chamber": "house",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["house"],
            "source": "clerk.house.gov MemberData.xml",
        }
        out.append(entry)
    return out


# ------------------------------------------------------------------ #
# Hand-curated rosters. Each entry is sourced to a public list and
# only added when a verified name is in hand. Where a slot exists
# but the name is in flux (acting heads, vacant chairs), we keep the
# slot with name: TODO and a comment.
# ------------------------------------------------------------------ #

# Trump 2nd-term Cabinet + Cabinet-level (per the Wikipedia Second_cabinet_of_Donald_Trump
# article, "Cabinet-level officials" table, fetched 2026-05-27).
# All names lifted directly from the bolded incumbent rows in that table.
CABINET = [
    ("President", "Donald J. Trump", "United States", "Republican"),
    ("Vice President", "JD Vance", "United States", "Republican"),
    ("Secretary of State", "Marco Rubio", "United States", "Republican"),
    ("Secretary of the Treasury", "Scott Bessent", "United States", "Republican"),
    ("Secretary of Defense", "Pete Hegseth", "United States", "Republican"),
    ("Attorney General (acting)", "Todd Blanche", "United States", "Republican"),
    ("Secretary of the Interior", "Doug Burgum", "United States", "Republican"),
    ("Secretary of Agriculture", "Brooke Rollins", "United States", "Republican"),
    ("Secretary of Commerce", "Howard Lutnick", "United States", "Republican"),
    ("Secretary of Labor (acting)", "Keith Sonderling", "United States", "Republican"),
    ("Secretary of Health and Human Services", "Robert F. Kennedy Jr.", "United States", "Independent"),
    ("Secretary of Housing and Urban Development", "Scott Turner", "United States", "Republican"),
    ("Secretary of Transportation", "Sean Duffy", "United States", "Republican"),
    ("Secretary of Energy", "Chris Wright", "United States", "Republican"),
    ("Secretary of Education", "Linda McMahon", "United States", "Republican"),
    ("Secretary of Veterans Affairs", "Doug Collins", "United States", "Republican"),
    ("Secretary of Homeland Security", "Markwayne Mullin", "United States", "Republican"),
    ("White House Chief of Staff", "Susie Wiles", "United States", "Republican"),
    ("EPA Administrator", "Lee Zeldin", "United States", "Republican"),
    ("OMB Director", "Russell Vought", "United States", "Republican"),
    ("Director of National Intelligence", "Tulsi Gabbard", "United States", "Republican"),
    ("CIA Director", "John Ratcliffe", "United States", "Republican"),
    ("U.S. Trade Representative", "Jamieson Greer", "United States", "Republican"),
    ("SBA Administrator", "Kelly Loeffler", "United States", "Republican"),
]

# Cabinet-rank / WH senior staff slots that exist but whose current incumbent
# is not confirmed against a single primary source as of the build date.
# We keep the slot with name TODO rather than fabricate.
CABINET_TODO = [
    "Council of Economic Advisers Chair",
    "U.N. Ambassador",
    "National Security Advisor",
    "Deputy National Security Advisor",
    "NEC Director",
    "Deputy White House Chief of Staff",
    "White House Press Secretary",
    "White House Counsel",
    "White House Communications Director",
    "Senior Adviser to the President",
    "OPM Director",
    "FHFA Director",
]

# SCOTUS: composition stable since Justice Brown Jackson's 2022 confirmation.
# All nine seated continuously through 2026-05-27.
SCOTUS = [
    ("Chief Justice", "John Roberts"),
    ("Associate Justice", "Clarence Thomas"),
    ("Associate Justice", "Samuel Alito"),
    ("Associate Justice", "Sonia Sotomayor"),
    ("Associate Justice", "Elena Kagan"),
    ("Associate Justice", "Neil Gorsuch"),
    ("Associate Justice", "Brett Kavanaugh"),
    ("Associate Justice", "Amy Coney Barrett"),
    ("Associate Justice", "Ketanji Brown Jackson"),
]

# Federal Reserve Board of Governors: pulled from federalreserve.gov/aboutthefed/bios/board/default.htm
# (HTML scrape of the bio-link slugs, 2026-05-27). Seven seated governors.
# Roles (Chair / Vice Chair) are known from public reporting but only Chair is
# locked here; vice-chair assignments left TODO where uncertain.
FED_BOARD = [
    ("Federal Reserve Chair", "Kevin Warsh"),
    ("Federal Reserve Governor", "Philip Jefferson"),
    ("Federal Reserve Governor", "Michelle Bowman"),
    ("Federal Reserve Governor", "Michael Barr"),
    ("Federal Reserve Governor", "Lisa Cook"),
    ("Federal Reserve Governor", "Jerome Powell"),
    ("Federal Reserve Governor", "Christopher Waller"),
]

# Reserve Bank presidents: 12 slots. Composition turns over frequently and we
# do not lock single-source verified names here. Each slot is a TODO; Ian can
# re-run the build with a verified roster file.
RESERVE_DISTRICTS = [
    "Boston", "New York", "Philadelphia", "Cleveland", "Richmond",
    "Atlanta", "Chicago", "St. Louis", "Minneapolis", "Kansas City",
    "Dallas", "San Francisco",
]

# Independent agency chairs: listed as TODO slots. These rotate enough that
# fabricating would be too risky; Ian provisions verified names.
INDEPENDENT_AGENCY_CHAIRS = [
    ("SEC", "Securities and Exchange Commission Chair"),
    ("CFTC", "Commodity Futures Trading Commission Chair"),
    ("FDIC", "Federal Deposit Insurance Corporation Chair"),
    ("OCC", "Office of the Comptroller of the Currency Chair"),
    ("FTC", "Federal Trade Commission Chair"),
    ("FCC", "Federal Communications Commission Chair"),
    ("NLRB", "National Labor Relations Board Chair"),
    ("NCUA", "National Credit Union Administration Chair"),
]


def make_executive_entries() -> list[dict]:
    """Cabinet + WH senior + SCOTUS + Fed + agency entries."""
    out: list[dict] = []
    # Cabinet
    for role, name, jur, party in CABINET:
        out.append({
            "id": _slug(role) + "-" + _slug(name),
            "name": name,
            "role": role,
            "jurisdiction": jur,
            "party": party,
            "bioguide_id": "TODO",          # most cabinet members have no bioguide unless they served in Congress
            "congress_chamber": "none",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["executive", "cabinet"],
            "source": "Wikipedia Second_cabinet_of_Donald_Trump 2026-05-27",
        })
    # Cabinet-rank slots without verified incumbent
    for role in CABINET_TODO:
        out.append({
            "id": _slug(role) + "-todo",
            "name": "TODO",
            "role": role,
            "jurisdiction": "United States",
            "party": "TODO",
            "bioguide_id": "TODO",
            "congress_chamber": "none",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["executive", "wh-senior"],
            "source": "stub: incumbent not verified against single source",
        })
    # SCOTUS
    for role, name in SCOTUS:
        out.append({
            "id": _slug(role) + "-" + _slug(name),
            "name": name,
            "role": role,
            "jurisdiction": "United States",
            "party": "Nonpartisan",
            "bioguide_id": "TODO",
            "congress_chamber": "none",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["judiciary", "scotus"],
            "source": "supremecourt.gov composition 2026-05-27",
        })
    # Fed Board
    for role, name in FED_BOARD:
        out.append({
            "id": _slug(role) + "-" + _slug(name),
            "name": name,
            "role": role,
            "jurisdiction": "United States",
            "party": "Nonpartisan",
            "bioguide_id": "TODO",
            "congress_chamber": "none",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["monetary-policy", "fed-board"],
            "source": "federalreserve.gov bios/board 2026-05-27",
        })
    # Reserve Bank presidents (all TODO)
    for district in RESERVE_DISTRICTS:
        out.append({
            "id": f"reserve-bank-president-{_slug(district)}-todo",
            "name": "TODO",
            "role": f"Federal Reserve Bank of {district} President",
            "jurisdiction": "United States",
            "party": "Nonpartisan",
            "bioguide_id": "TODO",
            "congress_chamber": "none",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["monetary-policy", "reserve-bank"],
            "source": "stub: incumbent not verified against single source",
        })
    # Independent agency chairs (all TODO)
    for short, full in INDEPENDENT_AGENCY_CHAIRS:
        out.append({
            "id": _slug(short) + "-chair-todo",
            "name": "TODO",
            "role": full,
            "jurisdiction": "United States",
            "party": "TODO",
            "bioguide_id": "TODO",
            "congress_chamber": "none",
            "committees": [],
            "twitter": "TODO",
            "bluesky": "TODO",
            "ogeid": "TODO",
            "cspan_person_id": "TODO",
            "watchlist_tags": ["independent-agency", short.lower()],
            "source": "stub: incumbent not verified against single source",
        })
    return out


def yaml_dump(entries: list[dict]) -> str:
    """Hand-rolled YAML emitter so we don't pull a PyYAML dependency in CI.
    Output is a list of mappings, in stable key order."""
    KEY_ORDER = [
        "id", "name", "role", "jurisdiction", "party", "bioguide_id",
        "congress_chamber", "committees", "twitter", "bluesky", "ogeid",
        "cspan_person_id", "watchlist_tags", "source",
    ]
    lines = [
        "# worldscope/figures_registry.yaml",
        "# Auto-generated by tools/figures/build_registry.py (do not hand-edit).",
        "# Re-run after membership changes (mid-term, confirmation, resignation).",
        "# Entries with name: TODO are real slots whose incumbent has not been",
        "# verified against a primary source on this build's date.",
        "",
    ]
    for entry in entries:
        first = True
        for key in KEY_ORDER:
            value = entry.get(key)
            prefix = "- " if first else "  "
            first = False
            if isinstance(value, list):
                if not value:
                    lines.append(f"{prefix}{key}: []")
                else:
                    inner = ", ".join(_yaml_scalar(v) for v in value)
                    lines.append(f"{prefix}{key}: [{inner}]")
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
        lines.append("")
    return "\n".join(lines)


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    s = str(v)
    needs_quote = any(c in s for c in (":", "#", "[", "]", "{", "}", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"))
    if needs_quote or s.strip() != s or not s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def main() -> None:
    print("# Building from senate.gov + clerk.house.gov + curated Cabinet/SCOTUS/Fed lists.",
          file=sys.stderr)
    senators = parse_senators()
    print(f"# {len(senators)} senators", file=sys.stderr)
    house = parse_house()
    print(f"# {len(house)} house members", file=sys.stderr)
    execs = make_executive_entries()
    print(f"# {len(execs)} executive/judicial/agency entries", file=sys.stderr)
    all_entries = senators + house + execs
    print(f"# total: {len(all_entries)}", file=sys.stderr)
    sys.stdout.write(yaml_dump(all_entries))


if __name__ == "__main__":
    main()
