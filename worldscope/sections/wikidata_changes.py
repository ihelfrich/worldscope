"""
wikidata_changes.py — recent changes to politically-significant Wikidata
entities (heads of state, ministers, central bankers, generals,
opposition leaders).

We don't replay every minor edit. We watch a roster (built from
lib/wikidata.py canned queries) and ask Wikidata's RecentChanges API
for edits to those QIDs in the last 24-48h, then categorize:

  - Date of death added → leader died
  - Position held (P39) changed → role change
  - Sanctions (P3884) added → sanctioned
  - Replaces (P1365) added → succession event

This is a cheap proxy for "who in global leadership moved this week"
that's faster than waiting for news sentiment to converge on the fact.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from ..lib.wikidata import current_heads_of_government, current_heads_of_state
from . import Section

API = "https://www.wikidata.org/w/api.php"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"

# Properties worth flagging
INTERESTING_PROPS = {
    "P570": "date of death",
    "P39": "position held",
    "P3884": "sanctioned",
    "P1365": "replaces",
    "P1366": "replaced by",
    "P102": "party affiliation",
    "P512": "academic degree",  # rarely interesting; included for low-noise context
}


class WikidataChangesSection(Section):
    id = "wikidata_changes"
    title = "Wikidata leader-roster changes (48h)"
    emoji = "👤"

    PULL_TIMEOUT_S = 90
    HOURS_BACK = 48
    BATCH = 50

    def _recent_revisions(self, qids: list[str]) -> dict[str, list[dict]]:
        """For each QID, list recent revisions in window."""
        out: dict[str, list[dict]] = {}
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=self.HOURS_BACK)
        # Wikidata expects YYYYMMDDHHMMSS for rvend/rvstart
        rvstart = end.strftime("%Y%m%d%H%M%S")
        rvend = start.strftime("%Y%m%d%H%M%S")
        for i in range(0, len(qids), self.BATCH):
            batch = qids[i:i + self.BATCH]
            params = {
                "action": "query",
                "format": "json",
                "prop": "revisions",
                "titles": "|".join(batch),
                "rvlimit": 5,
                "rvstart": rvstart,
                "rvend": rvend,
                "rvprop": "timestamp|user|comment|ids",
                "formatversion": 2,
            }
            try:
                resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                time.sleep(1.0)
                continue
            for page in (data.get("query") or {}).get("pages", []):
                title = page.get("title")
                revs = page.get("revisions") or []
                if title and revs:
                    out[title] = revs
            time.sleep(0.5)
        return out

    def pull(self) -> list[dict]:
        try:
            hos = current_heads_of_state()
            hog = current_heads_of_government()
        except Exception as exc:
            raise RuntimeError(
                f"[{self.id}] failed to load Wikidata heads-of-state/government "
                f"roster: {type(exc).__name__}: {exc}"
            ) from exc
        roster = {(e.get("qid") or "").strip(): e for e in (hos + hog) if e.get("qid")}
        if not roster:
            raise RuntimeError(
                f"[{self.id}] Wikidata returned no heads of state or government; "
                "upstream SPARQL likely failed"
            )
        qids = list(roster.keys())
        revisions = self._recent_revisions(qids)
        items: list[dict] = []
        for qid, revs in revisions.items():
            entry = roster.get(qid, {})
            name = entry.get("personLabel") or entry.get("name") or qid
            country = entry.get("countryLabel") or entry.get("country") or ""
            for rev in revs:
                comment = (rev.get("comment") or "").strip()
                ts = rev.get("timestamp", "")
                try:
                    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
                except ValueError:
                    d = ""
                # Flag if comment mentions an interesting property
                flag = None
                for prop, label in INTERESTING_PROPS.items():
                    if prop in comment:
                        flag = label
                        break
                items.append({
                    "id": f"wd-{qid}-{rev.get('revid','')}",
                    "date": d,
                    "title": f"[{country}] {name} ({qid}) edited" + (f" — {flag}" if flag else ""),
                    "url": f"https://www.wikidata.org/wiki/{qid}",
                    "summary": comment[:240],
                    "country": country,
                    "person": name,
                    "qid": qid,
                    "topics": ["leadership", "people"],
                    "entities": [qid],
                    "_source": self.id,
                })
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
