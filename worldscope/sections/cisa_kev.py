"""
cisa_kev.py — CISA Known Exploited Vulnerabilities catalog.

The KEV catalog lists CVEs that CISA has evidence are being actively
exploited in the wild. New entries are a strong signal of state-actor
or organized-crime activity. Federal civilian agencies must patch within
the listed remediation deadline.

Feed: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json

We surface entries added or updated in the last 14 days, with the CVE,
vendor/product, deadline, and ransomware-usage flag.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

from . import Section

FEED = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"


class CisaKevSection(Section):
    id = "cisa_kev"
    title = "CISA Known Exploited Vulnerabilities (last 14d)"
    emoji = "🛡️"

    PULL_TIMEOUT_S = 30
    DAYS_BACK = 14

    def pull(self) -> list[dict]:
        try:
            resp = requests.get(FEED, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[{self.id}] CISA KEV feed fetch failed: {type(exc).__name__}: {exc}")
            raise
        cutoff = date.today() - timedelta(days=self.DAYS_BACK)
        items: list[dict] = []
        for v in data.get("vulnerabilities", []):
            added = v.get("dateAdded", "")
            try:
                added_d = datetime.strptime(added, "%Y-%m-%d").date()
            except ValueError:
                continue
            if added_d < cutoff:
                continue
            cve = v.get("cveID", "")
            vendor = v.get("vendorProject", "")
            product = v.get("product", "")
            name = v.get("vulnerabilityName", "")
            deadline = v.get("dueDate", "")
            ransom = v.get("knownRansomwareCampaignUse", "")
            items.append({
                "id": f"kev-{cve}",
                "date": added,
                "title": f"{cve} · {vendor} {product}: {name}" + (" [RANSOMWARE-LINKED]" if ransom == "Known" else ""),
                "url": f"https://nvd.nist.gov/vuln/detail/{cve}",
                "summary": f"vendor: {vendor} · product: {product} · CISA remediation by {deadline}",
                "cve": cve,
                "vendor": vendor,
                "product": product,
                "deadline": deadline,
                "ransomware_linked": ransom == "Known",
                "topics": ["cyber", "vulnerabilities"],
                "_source": self.id,
            })
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
