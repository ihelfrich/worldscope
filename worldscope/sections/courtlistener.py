"""
courtlistener.py — recent court opinions of consequence, federal AND state.

Pulls from CourtListener v4 search across high-signal courts:
  - SCOTUS               (US Supreme Court)
  - CIT                  (US Court of International Trade — tariffs/trade)
  - All US Courts of Appeals (1st-11th Circuits, DC, Federal)
  - Every state court of last resort (all 50 state supreme courts, plus the
    DC Court of Appeals and the separate criminal high courts of Texas and
    Oklahoma) — so state-level judicial action is covered, not just federal.

Courts are fetched concurrently (CourtListener is one HTTP round-trip per
court and there are ~65 of them; sequential fetching would blow the section
pull deadline). Results are deduped by cluster, volume-balanced per court so
a single busy docket can't crowd everything else out, then sorted by recency.

Uses the COURTLISTENER_API_TOKEN already provisioned in econscope/.env.
Free tier; authenticated calls lift rate limits. Each per-court fetch is
best-effort: an unknown court id or a transient error degrades to zero
results for that court rather than failing the section.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

from . import Section

API = "https://www.courtlistener.com/api/rest/v4/search/"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"

# Federal courts to watch (CourtListener court_id -> display label).
FEDERAL_COURTS: dict[str, str] = {
    "scotus": "U.S. Supreme Court",
    "cit": "U.S. Court of International Trade",
    "ca1": "U.S. Court of Appeals, 1st Cir.",
    "ca2": "U.S. Court of Appeals, 2nd Cir.",
    "ca3": "U.S. Court of Appeals, 3rd Cir.",
    "ca4": "U.S. Court of Appeals, 4th Cir.",
    "ca5": "U.S. Court of Appeals, 5th Cir.",
    "ca6": "U.S. Court of Appeals, 6th Cir.",
    "ca7": "U.S. Court of Appeals, 7th Cir.",
    "ca8": "U.S. Court of Appeals, 8th Cir.",
    "ca9": "U.S. Court of Appeals, 9th Cir.",
    "ca10": "U.S. Court of Appeals, 10th Cir.",
    "ca11": "U.S. Court of Appeals, 11th Cir.",
    "cadc": "U.S. Court of Appeals, D.C. Cir.",
    "cafc": "U.S. Court of Appeals, Federal Cir.",
}

# State courts of last resort (CourtListener court_id -> display label).
# IDs follow the CourtListener court taxonomy; any that don't resolve simply
# return no rows (see best-effort note above). Texas and Oklahoma split their
# top courts between civil and criminal, so both are listed.
STATE_COURTS: dict[str, str] = {
    "ala": "Supreme Court of Alabama",
    "alaska": "Alaska Supreme Court",
    "ariz": "Arizona Supreme Court",
    "ark": "Arkansas Supreme Court",
    "cal": "Supreme Court of California",
    "colo": "Colorado Supreme Court",
    "conn": "Connecticut Supreme Court",
    "del": "Delaware Supreme Court",
    "dc": "D.C. Court of Appeals",
    "fla": "Florida Supreme Court",
    "ga": "Supreme Court of Georgia",
    "haw": "Hawaii Supreme Court",
    "idaho": "Idaho Supreme Court",
    "ill": "Illinois Supreme Court",
    "ind": "Indiana Supreme Court",
    "iowa": "Iowa Supreme Court",
    "kan": "Kansas Supreme Court",
    "ky": "Kentucky Supreme Court",
    "la": "Louisiana Supreme Court",
    "me": "Maine Supreme Judicial Court",
    "md": "Supreme Court of Maryland",
    "mass": "Massachusetts Supreme Judicial Court",
    "mich": "Michigan Supreme Court",
    "minn": "Minnesota Supreme Court",
    "miss": "Mississippi Supreme Court",
    "mo": "Supreme Court of Missouri",
    "mont": "Montana Supreme Court",
    "neb": "Nebraska Supreme Court",
    "nev": "Nevada Supreme Court",
    "nh": "New Hampshire Supreme Court",
    "nj": "New Jersey Supreme Court",
    "nm": "New Mexico Supreme Court",
    "ny": "New York Court of Appeals",
    "nc": "Supreme Court of North Carolina",
    "nd": "North Dakota Supreme Court",
    "ohio": "Ohio Supreme Court",
    "okla": "Oklahoma Supreme Court",
    "oklacrimapp": "Oklahoma Court of Criminal Appeals",
    "or": "Oregon Supreme Court",
    "pa": "Supreme Court of Pennsylvania",
    "ri": "Rhode Island Supreme Court",
    "sc": "South Carolina Supreme Court",
    "sd": "South Dakota Supreme Court",
    "tenn": "Tennessee Supreme Court",
    "tex": "Supreme Court of Texas",
    "texcrimapp": "Texas Court of Criminal Appeals",
    "utah": "Utah Supreme Court",
    "vt": "Vermont Supreme Court",
    "va": "Supreme Court of Virginia",
    "wash": "Washington Supreme Court",
    "wva": "West Virginia Supreme Court of Appeals",
    "wis": "Wisconsin Supreme Court",
    "wyo": "Wyoming Supreme Court",
}


class CourtListenerSection(Section):
    id = "courtlistener"
    title = "Court opinions of consequence (federal & state)"
    emoji = "⚖️"

    DAYS = 14
    LIMIT = 60                 # overall cap returned to the brief
    PER_FEDERAL = 8            # max rows kept per federal court
    PER_STATE = 4              # max rows kept per state court
    REQUEST_TIMEOUT_S = 12     # per-court HTTP timeout
    MAX_WORKERS = 24           # concurrent court fetches
    PULL_TIMEOUT_S = 90        # section deadline (override base 75: ~65 courts)

    def _fetch_court(self, court: str, label: str, jurisdiction: str,
                     cap: int, headers: dict, start: str) -> list[dict]:
        """Fetch and parse up to `cap` recent opinions for one court.
        Best-effort: any error returns an empty list for this court."""
        try:
            r = requests.get(
                API,
                params={
                    "type": "o",
                    "court": court,
                    "order_by": "dateFiled desc",
                    "filed_after": start,
                },
                headers=headers,
                timeout=self.REQUEST_TIMEOUT_S,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            return []
        out: list[dict] = []
        for res in (data.get("results") or [])[:cap]:
            rid = res.get("cluster_id") or res.get("id") or 0
            date_str = res.get("dateFiled") or res.get("date_filed") or ""
            case = res.get("caseName", "") or "(opinion)"
            out.append({
                "id": str(rid) if rid else (res.get("absolute_url") or ""),
                "date": date_str[:10],
                "title": f"{label}: {case}",
                "url": f"https://www.courtlistener.com{res.get('absolute_url','')}",
                "summary": (res.get("snippet") or "")[:400],
                "court": court,
                "court_label": label,
                "jurisdiction": jurisdiction,
            })
        return out

    def pull(self) -> list[dict]:
        token = os.environ.get("COURTLISTENER_API_TOKEN")
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        start = (datetime.now(timezone.utc) - timedelta(days=self.DAYS)).strftime("%Y-%m-%d")

        # (court_id, label, jurisdiction, per-court cap)
        jobs = [(c, lbl, "federal", self.PER_FEDERAL) for c, lbl in FEDERAL_COURTS.items()]
        jobs += [(c, lbl, "state", self.PER_STATE) for c, lbl in STATE_COURTS.items()]

        gathered: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = [
                pool.submit(self._fetch_court, c, lbl, juris, cap, headers, start)
                for (c, lbl, juris, cap) in jobs
            ]
            for fut in as_completed(futures):
                gathered.extend(fut.result())

        # Dedupe by cluster id (some cases surface under multiple court ids),
        # then sort by filing date (newest first) and cap.
        seen: set[str] = set()
        deduped: list[dict] = []
        for it in gathered:
            key = it["id"]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append(it)
        deduped.sort(key=lambda it: it.get("date") or "", reverse=True)
        return deduped[: self.LIMIT]
