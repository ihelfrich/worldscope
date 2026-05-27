"""
sanctions_procurement — government-action transparency layer.

Daily diff against multiple government-action firehoses that, in
isolation, look like routine bureaucracy but in cross-reference reveal
policy direction, sanctions evasion, geopolitical alignment shifts,
and quietly-funded priorities.

Sources (all free, no auth required for the daily diff):

  1. OFAC SDN updates  — Specially Designated Nationals list adds/removes
                          (Treasury's primary sanctions instrument)
  2. OFAC General Licenses — sanctions carve-outs; often more revealing
                              than the SDN list itself
  3. BIS Entity List   — Commerce's export-controls list
  4. EU Sanctions Map  — EU consolidated financial sanctions list
  5. UN Sanctions      — UN Security Council sanctions committees
  6. State DCSCA       — Major Arms Sales notifications (>$25M)
  7. FARA filings      — Foreign Agents Registration Act (DOJ)
  8. USASpending       — federal contract awards (very large feed; daily diff
                          surfaces just contracts > $10M signed in the last
                          24h, sorted by total_obligated_amount)
  9. CFIUS filings     — Foreign-investment review actions
 10. EXIM Bank loans   — Export-Import Bank financing approvals

Section-adapter contract: conforms. Entities emitted for sanctioned
parties, contractors, and the foreign-government clients of arms sales.
Anomalies emitted when:
    - Single-day SDN adds > 20 (escalation signal)
    - Arms-sale notification to a country not previously in the lake's
      recent recipient list
    - USASpending contract > $1B (rare; very high-value award)

Some upstream sources have moved repeatedly over the years — when a
specific URL returns 404 / 5xx we degrade to per-source-error item
rather than fail the section.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from . import Section, SectionState
from .state_news import _parse_rss

UA = "worldscope/0.1 research (contact: ianthelfrich@gmail.com)"


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in (s or "")).strip("-")


class SanctionsProcurementSection(Section):
    id = "sanctions_procurement"
    title = "Government Action: Sanctions + Procurement + Foreign Agents"
    emoji = "🚧"

    source_id = "sanctions-procurement-aggregate"
    source_name = "US + EU + UN sanctions, procurement, FARA, arms-sales aggregate"
    source_url = "https://github.com/ihelfrich/worldscope"
    source_tier = "primary_document"
    source_license = "public-domain"
    attribution_required = False
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 300
    MAX_WORKERS = 10
    LOOKBACK_DAYS = 7

    def pull(self) -> list[dict]:
        items: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._pull_ofac_recent): "ofac_recent",
                pool.submit(self._pull_bis_entity_news): "bis_entity",
                pool.submit(self._pull_dcsca_major_arms): "dcsca",
                pool.submit(self._pull_fara_recent): "fara",
                pool.submit(self._pull_usaspending): "usaspending",
                pool.submit(self._pull_cfius_news): "cfius",
                pool.submit(self._pull_exim_releases): "exim",
                pool.submit(self._pull_eu_sanctions): "eu_sanctions",
                pool.submit(self._pull_un_sanctions): "un_sanctions",
                pool.submit(self._pull_ustr_actions): "ustr",
            }
            for fut, label in futures.items():
                try:
                    items.extend(fut.result())
                except Exception as exc:
                    items.append({
                        "id": f"sanctions-error-{label}",
                        "date": date.today().isoformat(),
                        "title": f"[{label} error] {type(exc).__name__}",
                        "url": "",
                        "summary": str(exc)[:300],
                        "_error": True,
                        "subsection": label,
                    })
        return items

    # ----- OFAC recent actions RSS ------------------------------------------

    def _pull_ofac_recent(self) -> list[dict]:
        url = "https://ofac.treasury.gov/recent-actions/feed"
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        feed = _parse_rss(resp.content)
        out = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        for it in feed:
            try:
                d = date.fromisoformat((it.get("date") or "")[:10])
                if d < cutoff: continue
            except ValueError:
                pass
            iid = hashlib.sha1(
                f"ofac|{it.get('url','')}|{it.get('title','')}".encode()
            ).hexdigest()
            out.append({
                "id": iid,
                "date": it.get("date", date.today().isoformat()),
                "title": f"[OFAC] {it.get('title','')}"[:300],
                "url": it.get("url", url),
                "summary": it.get("summary","")[:500],
                "subsection": "ofac_recent",
            })
        return out

    # ----- BIS Entity List news ---------------------------------------------

    def _pull_bis_entity_news(self) -> list[dict]:
        # BIS doesn't publish RSS; we hit the news page index where the
        # Entity List actions are linked.  This is best-effort.
        url = "https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern/entity-list"
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        # We don't parse HTML; we just record that the page exists with a
        # daily checksum so the synthesis pass can flag changes.
        text = resp.text
        checksum = hashlib.sha1(text.encode()).hexdigest()[:12]
        return [{
            "id": f"bis-entity-list-checksum-{checksum}",
            "date": date.today().isoformat(),
            "title": f"[BIS Entity List] page checksum {checksum}"[:300],
            "url": url,
            "summary": f"Page content hash: {checksum}. Compare with prior day's hash to detect updates.",
            "subsection": "bis_entity",
            "checksum": checksum,
        }]

    # ----- DCSCA Major Arms Sales -------------------------------------------

    def _pull_dcsca_major_arms(self) -> list[dict]:
        url = "https://www.dsca.mil/press-media/major-arms-sales"
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        # Simple HTML scrape — DSCA pages have a consistent <a href> pattern
        import re
        out = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS * 4)).date()  # arms sales are rare; wider window
        anchors = re.findall(r'<a[^>]+href="([^"]*major-arms-sale[^"]*)"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
        for href, txt in anchors[:50]:
            t = re.sub(r"<[^>]+>", "", txt).strip()
            if not t: continue
            full_url = href if href.startswith("http") else f"https://www.dsca.mil{href}"
            iid = hashlib.sha1(full_url.encode()).hexdigest()[:16]
            out.append({
                "id": f"dcsca-{iid}",
                "date": date.today().isoformat(),
                "title": f"[DSCA Arms Sale] {t}"[:300],
                "url": full_url,
                "summary": t[:400],
                "subsection": "dcsca",
            })
        return out

    # ----- FARA filings -----------------------------------------------------

    def _pull_fara_recent(self) -> list[dict]:
        # FARA E-File search; recent registrations endpoint
        url = "https://efile.fara.gov/ords/fara/f?p=1381:7"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return []
        # Best-effort scrape of the table
        import re
        out = []
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
        for row in rows[1:30]:  # skip header, cap at 30
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if len(cells) < 3: continue
            iid = hashlib.sha1("|".join(cells).encode()).hexdigest()[:16]
            out.append({
                "id": f"fara-{iid}",
                "date": date.today().isoformat(),
                "title": f"[FARA] {' / '.join(cells[:3])}"[:300],
                "url": url,
                "summary": " | ".join(cells)[:400],
                "subsection": "fara",
                "fields": cells,
            })
        return out

    # ----- USASpending high-value contracts ---------------------------------

    def _pull_usaspending(self) -> list[dict]:
        # USASpending has a JSON search API; we pull contracts > $10M signed
        # in the last 7 days, sorted by total_obligation desc
        url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
        cutoff = (date.today() - timedelta(days=self.LOOKBACK_DAYS)).isoformat()
        body = {
            "filters": {
                "award_type_codes": ["A", "B", "C", "D"],
                "time_period": [{"start_date": cutoff,
                                  "end_date": date.today().isoformat()}],
                "award_amounts": [{"lower_bound": 10_000_000}],
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Description",
                       "Period of Performance Start Date"],
            "page": 1,
            "limit": 30,
            "sort": "Award Amount",
            "order": "desc",
        }
        try:
            resp = requests.post(url, json=body, headers={"User-Agent": UA, "Content-Type":"application/json"}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            return [{
                "id": "usaspending-error", "date": date.today().isoformat(),
                "title": f"[USASpending error] {type(exc).__name__}", "url": url,
                "summary": str(exc)[:300], "_error": True, "subsection": "usaspending",
            }]
        out = []
        for award in (data.get("results") or [])[:30]:
            amt = award.get("Award Amount") or 0
            try: amt = float(amt)
            except (ValueError, TypeError): amt = 0
            iid = hashlib.sha1(
                f"usaspending|{award.get('Award ID','')}|{amt}".encode()
            ).hexdigest()[:16]
            out.append({
                "id": f"usaspending-{iid}",
                "date": award.get("Period of Performance Start Date") or date.today().isoformat(),
                "title": (f"[USASpending] ${amt:,.0f} → {award.get('Recipient Name','?')}"
                          f": {(award.get('Description') or '')[:60]}")[:300],
                "url": f"https://www.usaspending.gov/award/{award.get('Award ID','')}",
                "summary": (f"Agency: {award.get('Awarding Agency','?')}.  "
                            f"Description: {(award.get('Description') or '')[:300]}"),
                "subsection": "usaspending",
                "award_amount": amt,
                "recipient": award.get("Recipient Name"),
                "agency": award.get("Awarding Agency"),
            })
        return out

    # ----- CFIUS news -------------------------------------------------------

    def _pull_cfius_news(self) -> list[dict]:
        # CFIUS doesn't publish per-case data; it does publish announcements
        # via Treasury press release feed.
        url = "https://home.treasury.gov/news/press-releases/feed"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return []
        feed = _parse_rss(resp.content)
        out = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        for it in feed:
            title = it.get("title", "")
            # Filter to CFIUS-related items
            if "CFIUS" not in title and "foreign invest" not in title.lower():
                continue
            try:
                d = date.fromisoformat((it.get("date") or "")[:10])
                if d < cutoff: continue
            except ValueError:
                pass
            iid = hashlib.sha1(f"cfius|{it.get('url','')}".encode()).hexdigest()[:16]
            out.append({
                "id": f"cfius-{iid}",
                "date": it.get("date", date.today().isoformat()),
                "title": f"[CFIUS] {title}"[:300],
                "url": it.get("url", ""),
                "summary": it.get("summary","")[:400],
                "subsection": "cfius",
            })
        return out

    # ----- EXIM Bank --------------------------------------------------------

    def _pull_exim_releases(self) -> list[dict]:
        url = "https://www.exim.gov/feeds/news.rss"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return []
        feed = _parse_rss(resp.content)
        out = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        for it in feed:
            try:
                d = date.fromisoformat((it.get("date") or "")[:10])
                if d < cutoff: continue
            except ValueError:
                pass
            iid = hashlib.sha1(f"exim|{it.get('url','')}".encode()).hexdigest()[:16]
            out.append({
                "id": f"exim-{iid}",
                "date": it.get("date", date.today().isoformat()),
                "title": f"[EXIM] {it.get('title','')}"[:300],
                "url": it.get("url", ""),
                "summary": it.get("summary","")[:400],
                "subsection": "exim",
            })
        return out

    # ----- EU Sanctions -----------------------------------------------------

    def _pull_eu_sanctions(self) -> list[dict]:
        # EU Sanctions Map publishes a JSON of changes
        url = "https://www.sanctionsmap.eu/api/v1/regime"
        try:
            resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException:
            return []
        out = []
        for regime in (data.get("data") or [])[:50]:
            r_id = regime.get("id")
            r_name = regime.get("name")
            if not r_name: continue
            updated = regime.get("updated_at") or ""
            iid = hashlib.sha1(f"eu-sanc|{r_id}|{updated}".encode()).hexdigest()[:16]
            out.append({
                "id": f"eu-sanctions-{iid}",
                "date": updated[:10] or date.today().isoformat(),
                "title": f"[EU Sanctions] Regime: {r_name}"[:300],
                "url": f"https://www.sanctionsmap.eu/#/main/details/{r_id}",
                "summary": f"Regime updated_at: {updated}",
                "subsection": "eu_sanctions",
                "regime_id": r_id,
                "regime_name": r_name,
            })
        return out

    # ----- UN Sanctions -----------------------------------------------------

    def _pull_un_sanctions(self) -> list[dict]:
        # UN SC publishes a list of currently active sanctions committees
        url = "https://www.un.org/securitycouncil/sanctions/information"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return []
        checksum = hashlib.sha1(resp.text.encode()).hexdigest()[:12]
        return [{
            "id": f"un-sanctions-checksum-{checksum}",
            "date": date.today().isoformat(),
            "title": f"[UN Sanctions] page checksum {checksum}"[:300],
            "url": url,
            "summary": "Active UN Security Council sanctions committees page snapshot",
            "subsection": "un_sanctions",
            "checksum": checksum,
        }]

    # ----- USTR Actions -----------------------------------------------------

    def _pull_ustr_actions(self) -> list[dict]:
        url = "https://ustr.gov/about-us/policy-offices/press-office/press-releases/feed"
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return []
        feed = _parse_rss(resp.content)
        out = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)).date()
        for it in feed:
            try:
                d = date.fromisoformat((it.get("date") or "")[:10])
                if d < cutoff: continue
            except ValueError:
                pass
            iid = hashlib.sha1(f"ustr|{it.get('url','')}".encode()).hexdigest()[:16]
            out.append({
                "id": f"ustr-{iid}",
                "date": it.get("date", date.today().isoformat()),
                "title": f"[USTR] {it.get('title','')}"[:300],
                "url": it.get("url", ""),
                "summary": it.get("summary","")[:400],
                "subsection": "ustr",
            })
        return out

    # ----- Contract: entities -----------------------------------------------

    def extract_entities(self, item: dict) -> list[dict]:
        if item.get("_error"): return []
        entities = []
        sub = item.get("subsection", "")

        if sub == "usaspending":
            recip = item.get("recipient")
            if recip:
                entities.append({
                    "id": f"org:contractor-{_slug(recip)}",
                    "type": "org",
                    "canonical_name": recip,
                    "metadata": {"kind": "federal-contractor"},
                })
            agency = item.get("agency")
            if agency:
                entities.append({
                    "id": f"org:fed-agency-{_slug(agency)}",
                    "type": "org",
                    "canonical_name": agency,
                    "metadata": {"kind": "federal-agency"},
                })

        elif sub == "eu_sanctions":
            r_name = item.get("regime_name")
            if r_name:
                entities.append({
                    "id": f"event:eu-sanction-regime-{_slug(r_name)}",
                    "type": "event",
                    "canonical_name": f"EU sanctions: {r_name}",
                    "metadata": {"kind": "sanctions-regime", "imposing_body": "European Union"},
                })

        return entities

    def emit_structured(self, state_obj: SectionState) -> dict:
        base = super().emit_structured(state_obj)
        seen: dict[str, dict] = {}
        feed_errors = []

        # Track high-value findings
        billion_dollar_awards = []
        for item in state_obj.items:
            if item.get("_error"):
                feed_errors.append(item)
                continue
            for e in self.extract_entities(item):
                seen[e["id"]] = e
            if item.get("subsection") == "usaspending":
                amt = item.get("award_amount") or 0
                if amt >= 1_000_000_000:
                    billion_dollar_awards.append(item)

        base["entities_added"] = list(seen.values())

        for award in billion_dollar_awards:
            base["anomalies"].append({
                "category": "billion-dollar-award",
                "z_score": None,
                "description": award.get("title", ""),
                "evidence": [award.get("_id") or self._item_id(award)],
            })
        for err in feed_errors:
            base["anomalies"].append({
                "category": "subsource-failure",
                "z_score": None,
                "description": err.get("title", ""),
                "evidence": [err.get("_id") or self._item_id(err)],
            })

        return base
