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

Design now:
  - the per-country queries run on a small ThreadPoolExecutor (2 workers)
    with a 15s per-request timeout and at most one 429 retry;
  - the whole pull has a wall-clock budget (BUDGET_S) well inside the
    section deadline; whatever has completed by then is returned and the
    rest is abandoned (partial results are acceptable — every country is an
    independent query);
  - only when *no* country succeeded is the pull a failure.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone

import requests

from . import Section, UpstreamHTTPError

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

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
    MAX_WORKERS = 2
    REQUEST_TIMEOUT_S = 15
    MAX_RETRIES = 2          # one retry after a 429
    RETRY_SLEEP_S = 5.0      # what GDELT's 429 text asks for
    PULL_TIMEOUT_S = 75
    BUDGET_S = 50            # stop collecting with margin; return partial

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._deadline: float = float("inf")

    def _fetch_one(self, code: str, params: dict) -> dict | None:
        """One country's artlist; None on any failure (never raises).
        A 429 is retried once after RETRY_SLEEP_S if the budget allows."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(DOC_API, params=params, headers={"User-Agent": UA},
                                    timeout=self.REQUEST_TIMEOUT_S)
                if resp.status_code == 429:
                    if (attempt + 1 < self.MAX_RETRIES
                            and time.monotonic() + self.RETRY_SLEEP_S < self._deadline):
                        time.sleep(self.RETRY_SLEEP_S)
                        continue
                    return None
                resp.raise_for_status()
                return resp.json()
            except Exception:
                return None
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
        pool = ThreadPoolExecutor(max_workers=self.MAX_WORKERS, thread_name_prefix="gdelt")
        try:
            futures = {}
            for code, name in WATCHLIST:
                params = {"query": f"sourcecountry:{code} sourcelang:english", **base}
                futures[pool.submit(self._fetch_one, code, params)] = name
            pending = set(futures)
            while pending:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    break  # budget spent — return partial instead of overrunning
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                for fut in done:
                    data = fut.result()
                    if data is None:
                        continue
                    got_any = True
                    name = futures[fut]
                    for art in (data.get("articles") or [])[: self.PER_COUNTRY]:
                        items.append(self._normalize(name, art))
        finally:
            # Don't wait for in-flight requests; they time out on their own.
            pool.shutdown(wait=False, cancel_futures=True)
        # Sort newest first for the briefing
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        if not items and not got_any:
            raise UpstreamHTTPError(
                "GDELT returned nothing for any country (rate-limited / blocked)")
        return items
