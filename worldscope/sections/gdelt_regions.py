"""
gdelt_regions.py — top news stories from GDELT, stratified by country.

For each country in the watchlist, pulls the most-recent N articles whose
source country matches (per GDELT's FIPS-ish code). Normalizes the result
to the standard Section schema.

GDELT DOC 2.0 is multilingual and updates every 15 minutes. No API key.
The free tier is rate-limited: a 429 comes back as plain text "Please limit
requests to one every 5 seconds ..." (observed live 2026-10-09, where a
shared egress IP was throttled on every call and each 429 took ~11s to be
served). That latency × 19 sequential countries is what used to push this
section past its 90s deadline.

Design now (2026-11-12): every request goes through worldscope.lib.gdelt,
one process-wide spacer shared with gdelt_gkg and conflict, so the three
sections stop rate-limiting each other. Two parallel workers here used to
guarantee self-inflicted 429s; from 2026-11-05 to 11-12 this section got
0-12 of a possible ~114 stories a day. Countries are queried one at a time
inside a wall-clock budget (BUDGET_S); the starting country rotates daily
so a short budget does not always drop the same ones. Partial results are
fine; only when *no* country answered is the pull a failure.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from ..lib import gdelt
from . import Section, UpstreamHTTPError


# (FIPS-ish country code GDELT recognizes, display name).
# China appears twice — once as the party-state outlet view (CH), once paired
# with HK/MO when those happen to surface — for now we just use CH and let
# the editorial framing show through whatever GDELT returns.
WATCHLIST: list[tuple[str, str]] = [
    ("CH", "China"),
    ("JA", "Japan"),
    ("KS", "South Korea"),
    ("UP", "Ukraine"),
    ("GM", "Germany"),
    ("IT", "Italy"),
    ("IS", "Israel"),
    ("IR", "Iran"),
    ("SA", "Saudi Arabia"),
    ("TU", "Turkey"),
    ("NI", "Nigeria"),
    ("EC", "Ecuador"),
    ("CO", "Colombia"),
    ("PO", "Portugal"),
    ("CA", "Canada"),
    ("MX", "Mexico"),
    ("UK", "United Kingdom"),
    ("BR", "Brazil"),
    ("IN", "India"),
]


class GdeltRegionsSection(Section):
    id = "gdelt_regions"
    title = "World News (by country, top stories)"
    emoji = "🌍"

    PER_COUNTRY = 6
    REQUEST_TIMEOUT_S = 15
    PULL_TIMEOUT_S = 150
    BUDGET_S = 125           # stop issuing queries; in-flight one may add REQUEST_TIMEOUT_S

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._deadline: float = float("inf")
        self.last_error: str = ""

    def _fetch_one(self, code: str, params: dict) -> dict | None:
        """One country's artlist; None on any failure (never raises)."""
        try:
            return gdelt.doc_get(params, timeout=self.REQUEST_TIMEOUT_S,
                                 deadline=self._deadline)
        except gdelt.GdeltUnavailable as exc:
            self.last_error = str(exc)
            return None

    @staticmethod
    def _normalize(name: str, art: dict) -> dict:
        # GDELT returns seendate like "20260525T180000Z"
        seen = art.get("seendate", "")
        try:
            dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = None
        return {
            "id": art.get("url", "") + "|" + seen,
            "date": dt.date().isoformat() if dt else "",
            "title": f"[{name}] {art.get('title','(no title)')}",
            "url": art.get("url", ""),
            "summary": art.get("domain", "") + " · " + art.get("language", ""),
            "country": name,
            "domain": art.get("domain", ""),
            "tone": art.get("tone", ""),
            "language": art.get("language", ""),
        }

    def pull(self) -> list[dict]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=36)
        base = {
            "mode": "artlist",
            "format": "json",
            "maxrecords": self.PER_COUNTRY,
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end.strftime("%Y%m%d%H%M%S"),
            "sort": "datedesc",
        }
        self._deadline = time.monotonic() + self.BUDGET_S
        items: list[dict] = []
        got_any = False
        shift = datetime.now(timezone.utc).toordinal() % len(WATCHLIST)
        for code, name in WATCHLIST[shift:] + WATCHLIST[:shift]:
            if time.monotonic() >= self._deadline:
                break  # budget spent — return partial instead of overrunning
            params = {"query": f"sourcecountry:{code} sourcelang:english", **base}
            data = self._fetch_one(code, params)
            if data is None:
                continue
            got_any = True
            for art in (data.get("articles") or [])[: self.PER_COUNTRY]:
                items.append(self._normalize(name, art))
        # Sort newest first for the briefing
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        if not items and not got_any:
            raise UpstreamHTTPError(
                f"GDELT returned nothing for any country ({self.last_error or 'rate-limited / blocked'})")
        return items
