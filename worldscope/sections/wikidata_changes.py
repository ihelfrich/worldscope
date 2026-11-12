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
from . import Section, UpstreamHTTPError, UpstreamParseError

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
        """For each QID, its latest revision if it falls inside the window.

        MediaWiki rejects rvlimit/rvstart/rvend when several titles are queried
        at once (HTTP 200 with an "error" body), so ask for each page's latest
        revision only and filter by timestamp here.
        """
        self._batches_ok, self._batch_errors = 0, []
        out: dict[str, list[dict]] = {}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.HOURS_BACK)
        for i in range(0, len(qids), self.BATCH):
            batch = qids[i:i + self.BATCH]
            params = {
                "action": "query",
                "format": "json",
                "prop": "revisions",
                "titles": "|".join(batch),
                "rvprop": "timestamp|user|comment|ids",
                "formatversion": 2,
            }
            try:
                resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data or "query" not in data:
                    err = data.get("error") or {}
                    raise ValueError(f"API error {err.get('code', '?')}: {err.get('info', '')}")
            except Exception as exc:
                self._batch_errors.append(str(exc)[:120])
                time.sleep(1.0)
                continue
            self._batches_ok += 1
            for page in data["query"].get("pages", []):
                title = page.get("title")
                recent = []
                for rev in page.get("revisions") or []:
                    try:
                        ts = datetime.fromisoformat(rev.get("timestamp", "").replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts >= cutoff:
                        recent.append(rev)
                if title and recent:
                    out[title] = recent
            time.sleep(0.5)
        return out

    def pull(self) -> list[dict]:
        try:
            hos = current_heads_of_state()
            hog = current_heads_of_government()
        except Exception as exc:
            raise UpstreamHTTPError(f"Wikidata leader roster query failed: {exc}") from exc
        roster = {}
        for e in hos + hog:
            qid = (e.get("leader_qid") or e.get("qid") or "").strip()
            if qid.startswith("Q"):
                roster[qid] = e
        if not roster:
            raise UpstreamParseError(
                f"Wikidata leader roster had no usable QIDs ({len(hos + hog)} rows)")
        qids = list(roster.keys())
        revisions = self._recent_revisions(qids)
        if self._batches_ok == 0:
            raise UpstreamHTTPError(
                "Wikidata revisions API failed for every batch: "
                + (self._batch_errors[0] if self._batch_errors else "unknown"))
        items: list[dict] = []
        for qid, revs in revisions.items():
            entry = roster.get(qid, {})
            name = entry.get("leader_name") or entry.get("personLabel") or entry.get("name") or qid
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
