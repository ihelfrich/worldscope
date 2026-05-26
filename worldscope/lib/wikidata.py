"""
wikidata.py — thin SPARQL client + queries for political-figure data.

No external SPARQL library; just `requests` against the public endpoint.
Results cached to ~/.worldscope/wikidata_cache.sqlite with a 24h TTL so
we don't re-hit Wikidata on every brief run.

Wikidata's SPARQL endpoint is at https://query.wikidata.org/sparql.
Rate limit: 5 requests/second per IP, plus a query timeout of 60s.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com; +https://github.com/ihelfrich/worldscope)"
CACHE_PATH = Path.home() / ".worldscope" / "wikidata_cache.sqlite"


def _cache_conn():
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(CACHE_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS q (key TEXT PRIMARY KEY, ts TEXT, body TEXT)")
    c.commit()
    return c


def sparql(query: str, *, max_age_hours: float = 24) -> dict:
    """Execute a SPARQL query. Returns the parsed JSON results. Cached."""
    key = hashlib.sha1(query.encode("utf-8")).hexdigest()
    c = _cache_conn()
    row = c.execute("SELECT ts, body FROM q WHERE key = ?", (key,)).fetchone()
    if row:
        ts_str, body = row
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts_str)).total_seconds() / 3600
        if age_h < max_age_hours:
            return json.loads(body)
    # Polite throttle
    time.sleep(0.2)
    r = requests.get(
        ENDPOINT,
        params={"query": query, "format": "json"},
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    c.execute("INSERT OR REPLACE INTO q VALUES (?, ?, ?)",
              (key, datetime.now(timezone.utc).isoformat(), json.dumps(data)))
    c.commit()
    return data


# --- canned queries -------------------------------------------------------

# All current heads of state for sovereign states (Q6256).
# wdt:P35 = "head of state"; P39 captures the position they hold; P580/P582 are start/end.
HEADS_OF_STATE_Q = """
SELECT DISTINCT ?country ?countryLabel ?iso ?leader ?leaderLabel ?positionLabel WHERE {
  ?country wdt:P31 wd:Q6256 .                  # sovereign state
  ?country wdt:P35 ?leader .                   # head of state
  OPTIONAL { ?country wdt:P298 ?iso . }        # ISO 3-letter
  OPTIONAL {
    ?leader p:P39 ?st .
    ?st ps:P39 ?position .
    ?st pq:P642 ?country .
    FILTER NOT EXISTS { ?st pq:P582 ?endDate . }   # currently held
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?countryLabel
"""

# All current heads of government (Prime Ministers, etc.)
HEADS_OF_GOVT_Q = """
SELECT DISTINCT ?country ?countryLabel ?iso ?leader ?leaderLabel WHERE {
  ?country wdt:P31 wd:Q6256 .
  ?country wdt:P6 ?leader .                    # head of government
  OPTIONAL { ?country wdt:P298 ?iso . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?countryLabel
"""


def current_heads_of_state() -> list[dict]:
    """Return the current head of state for every sovereign country."""
    data = sparql(HEADS_OF_STATE_Q)
    out: list[dict] = []
    seen = set()
    for b in data["results"]["bindings"]:
        country = b.get("countryLabel", {}).get("value", "")
        leader = b.get("leaderLabel", {}).get("value", "")
        key = (country, leader)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "country": country,
            "country_qid": b.get("country", {}).get("value", "").split("/")[-1],
            "iso": b.get("iso", {}).get("value", ""),
            "leader_name": leader,
            "leader_qid": b.get("leader", {}).get("value", "").split("/")[-1],
            "position": b.get("positionLabel", {}).get("value", "Head of state"),
        })
    return out


def current_heads_of_government() -> list[dict]:
    """Return the current head of government for every sovereign country
    (Prime Minister where applicable; may be same person as HoS in presidential
    systems)."""
    data = sparql(HEADS_OF_GOVT_Q)
    out: list[dict] = []
    seen = set()
    for b in data["results"]["bindings"]:
        country = b.get("countryLabel", {}).get("value", "")
        leader = b.get("leaderLabel", {}).get("value", "")
        key = (country, leader)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "country": country,
            "country_qid": b.get("country", {}).get("value", "").split("/")[-1],
            "iso": b.get("iso", {}).get("value", ""),
            "leader_name": leader,
            "leader_qid": b.get("leader", {}).get("value", "").split("/")[-1],
            "position": "Head of government",
        })
    return out


if __name__ == "__main__":
    hos = current_heads_of_state()
    print(f"Heads of state: {len(hos)}")
    for h in hos[:5]:
        print(f"  {h['country']:25s} → {h['leader_name']}  ({h['position']})")
    hog = current_heads_of_government()
    print(f"\nHeads of government: {len(hog)}")
    for h in hog[:5]:
        print(f"  {h['country']:25s} → {h['leader_name']}")
