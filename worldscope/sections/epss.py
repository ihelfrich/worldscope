"""epss.py — EPSS exploit-prediction scores (FIRST.org).

The Exploit Prediction Scoring System publishes a daily 0–1 probability that
each CVE will be exploited in the wild within the next 30 days. It complements
CISA KEV: KEV is *confirmed* exploitation (late by design), EPSS is the
*forecast*. Surfacing the highest-probability CVEs gives an early, prioritized
cyber-risk layer; extreme scores are emitted as lake anomalies.

API: https://api.first.org/data/v1/epss  (JSON, no key)
"""
from __future__ import annotations

import requests

from . import Section, UpstreamHTTPError, UpstreamParseError

API = "https://api.first.org/data/v1/epss"
NVD = "https://nvd.nist.gov/vuln/detail/{cve}"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"


class EpssSection(Section):
    id = "epss"
    title = "EPSS — highest exploit-probability CVEs"
    emoji = "🛡️"

    source_id = "epss"
    source_name = "Exploit Prediction Scoring System (FIRST.org)"
    source_url = "https://www.first.org/epss/"
    source_tier = "primary_document"
    source_license = "CC-BY-4.0"
    attribution_required = True
    attribution_text = "EPSS, FIRST.org"
    source_country = None
    source_language = "en"
    PULL_TIMEOUT_S = 30
    LIMIT = 40
    EXTREME = 0.90   # EPSS at/above this is flagged as an anomaly

    def pull(self) -> list[dict]:
        params = {"order": "!epss", "limit": self.LIMIT}
        try:
            resp = requests.get(API, params=params, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise UpstreamHTTPError(f"EPSS request failed: {e}") from e
        try:
            payload = resp.json()
        except ValueError as e:
            raise UpstreamParseError(f"EPSS returned non-JSON: {e}") from e
        if payload.get("status") != "OK" or not payload.get("data"):
            raise UpstreamHTTPError(
                f"EPSS returned no data (status={payload.get('status')})")

        items: list[dict] = []
        for r in payload["data"]:
            cve = r.get("cve") or ""
            try:
                score = float(r.get("epss") or 0.0)
                pct = float(r.get("percentile") or 0.0)
            except (TypeError, ValueError):
                continue
            items.append({
                "id": cve,
                "date": r.get("date", ""),
                "title": f"{cve}: {score:.0%} exploit probability (p{pct*100:.1f})",
                "url": NVD.format(cve=cve),
                "summary": f"EPSS {score:.3f} · percentile {pct:.3f}",
                "epss": score,
                "percentile": pct,
            })
        return items

    def emit_structured(self, state: "SectionState") -> dict:
        base = super().emit_structured(state)
        for it in state.items:
            if it.get("epss", 0) >= self.EXTREME:
                base["anomalies"].append({
                    "category": "cyber-epss-extreme",
                    "z_score": round(2.0 + it["epss"], 2),
                    "description": f"High exploit probability · {it.get('title', '')}",
                    "evidence": [it["id"]],
                })
        return base
