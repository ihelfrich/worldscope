"""
gdelt_regions.py — top news stories from GDELT, stratified by country.

For each country in the watchlist, pulls the most-recent N articles whose
source country matches (per GDELT's FIPS-ish code). Normalizes the result
to the standard Section schema.

GDELT DOC 2.0 is multilingual and updates every 15 minutes. No API key.
The free tier is rate-limited (~1 call/sec sustained, throttled on bursts).
This section makes one call per country per run; with a 15-country
watchlist that's about 30 seconds. To keep the daily run cheap, we cap to
~8 hits per country.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests

from . import Section

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

    # Articles per country to keep, and pause between calls so we're polite.
    # GDELT throttles aggressively from shared IPs (Github Actions runners
    # especially). We use a higher baseline throttle and retry on 429 with
    # exponential backoff. The watchlist is long enough that worst-case
    # 429-backoff can exceed 90s, so we budget more.
    PER_COUNTRY = 6
    THROTTLE_S = 2.0
    MAX_RETRIES = 3
    PULL_TIMEOUT_S = 240   # 19 countries × ~3s throttle + retries on 429

    def _fetch_one(self, code: str, params: dict) -> tuple[dict | None, str | None]:
        """Returns (data, error). data=None + error=None means "tried all
        retries, gave up." data=None + error="..." means a hard failure.
        data set means success."""
        backoff = 4.0
        last_err: str | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(DOC_API, params=params,
                                    headers={"User-Agent": UA}, timeout=25)
                if resp.status_code == 429:
                    last_err = f"429 attempt {attempt+1}"
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                return resp.json(), None
            except requests.exceptions.HTTPError as e:
                last_err = f"HTTP {e}"
                time.sleep(backoff)
                backoff *= 2
            except Exception as e:
                # Connection resets, DNS issues, JSON decode errors, etc.
                return None, f"{type(e).__name__}: {e}"
        return None, last_err or "exhausted retries"

    def pull(self) -> list[dict]:
        items: list[dict] = []
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=36)
        successes = 0
        failures: list[str] = []
        for code, name in WATCHLIST:
            params = {
                "query": f"sourcecountry:{code} sourcelang:english",
                "mode": "artlist",
                "format": "json",
                "maxrecords": self.PER_COUNTRY,
                "startdatetime": start.strftime("%Y%m%d%H%M%S"),
                "enddatetime": end.strftime("%Y%m%d%H%M%S"),
                "sort": "datedesc",
            }
            data, err = self._fetch_one(code, params)
            if data is None:
                failures.append(f"{name}({code}):{err}")
                time.sleep(self.THROTTLE_S)
                continue
            successes += 1
            for art in (data.get("articles") or [])[: self.PER_COUNTRY]:
                # GDELT returns seendate like "20260525T180000Z"
                seen = art.get("seendate", "")
                try:
                    dt = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    dt = None
                items.append({
                    "id": art.get("url", "") + "|" + seen,
                    "date": dt.date().isoformat() if dt else "",
                    "title": f"[{name}] {art.get('title','(no title)')}",
                    "url": art.get("url", ""),
                    "summary": art.get("domain", "") + " · " + art.get("language", ""),
                    "country": name,
                    "domain": art.get("domain", ""),
                    "tone": art.get("tone", ""),
                    "language": art.get("language", ""),
                })
            time.sleep(self.THROTTLE_S)

        # Surface per-country failures so they're visible in CI logs.
        if failures:
            print(f"[{self.id}] {len(failures)}/{len(WATCHLIST)} countries failed: "
                  + "; ".join(failures[:6])
                  + (f" (+{len(failures)-6} more)" if len(failures) > 6 else ""))

        # If EVERY country failed, raise — the state machine should mark
        # this section stale_after_failure rather than fresh_empty.
        if successes == 0 and failures:
            raise RuntimeError(
                f"All {len(failures)} GDELT country fetches failed; first error: "
                f"{failures[0]}"
            )

        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
