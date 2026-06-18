"""Fetch + normalize U.S. government activity from every branch.

Public surface:
  - `GovDoc`                         normalized document record (a plain dict shape)
  - `fetch_federal_register(...)`    executive-branch backbone (all agencies + POTUS)
  - `fetch_rss_sources(...)`         curated RSS registry (the other branches)
  - `fetch_congress(...)`            recent bills/laws (needs CONGRESS_API_KEY)
  - `fetch_courtlistener(...)`       recent SCOTUS opinions (needs COURTLISTENER_API_TOKEN)
  - `gather_all(...)`                merge + dedup everything into one list

Every fetcher is defensive: a single dead source is skipped (and noted in the
returned `errors` list when present), never raised. `gather_all` raises only if
*every* requested source failed — that is a real "the government feed is down"
signal the trust layer should see, not a quiet empty day.

A `GovDoc` is a dict (not a class) so it drops straight into the WorldScope
Section/lake pipeline:

    {
      "id":        stable id,
      "date":      ISO-8601 date (YYYY-MM-DD) or "",
      "title":     str,
      "url":       str,
      "summary":   str,
      "branch":    executive|legislative|judicial|independent|state,
      "org":       organization name,
      "doc_type":  Rule | Proposed Rule | Presidential Document | Press Release | Bill | Opinion | ...
      "source":    feed/source label,
    }
"""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from ..sections.state_news import _parse_rss, _normalize_feed_date
from .sources import GovSource, GOV_SOURCES

UA = "worldscope-govscope/0.1 (contact: ianthelfrich@gmail.com)"
FR_API = "https://www.federalregister.gov/api/v1/documents.json"
CONGRESS_API = "https://api.congress.gov/v3"
COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v4/opinions/"

GovDoc = dict  # documented shape above; alias kept for readability/typing


# --------------------------------------------------------------------------- #
# small pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def _doc_id(branch: str, url: str, title: str) -> str:
    h = hashlib.sha1()
    h.update(f"{branch}|{url}|{title}".encode("utf-8"))
    return "gov-" + h.hexdigest()[:16]


def _within_days(doc_date: str, days: int, today: Optional[date] = None) -> bool:
    """True if `doc_date` (ISO) is within the last `days` (inclusive). An empty
    or unparseable date is kept (we'd rather show a freshly-pulled item with a
    missing date than silently drop it)."""
    if not doc_date:
        return True
    today = today or date.today()
    try:
        d = date.fromisoformat(doc_date[:10])
    except ValueError:
        return True
    return (today - d).days <= days and d <= today + timedelta(days=1)


def dedup(docs: list[GovDoc]) -> list[GovDoc]:
    """Collapse duplicates by normalized URL, then by (org, normalized title).
    First occurrence wins. Pure; safe to unit-test."""
    seen_url: set[str] = set()
    seen_tt: set[str] = set()
    out: list[GovDoc] = []
    for d in docs:
        url = (d.get("url") or "").strip().lower().rstrip("/").split("?", 1)[0]
        tt = (d.get("org", "") + "|" +
              "".join(c for c in (d.get("title") or "").lower() if c.isalnum()))
        if url and url in seen_url:
            continue
        if tt and tt in seen_tt:
            continue
        if url:
            seen_url.add(url)
        if tt:
            seen_tt.add(tt)
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Federal Register — executive backbone (all departments + presidential docs)
# --------------------------------------------------------------------------- #
FR_TYPE_LABEL = {
    "Rule": "Rule",
    "Proposed Rule": "Proposed Rule",
    "Notice": "Notice",
    "Presidential Document": "Presidential Document",
}


def map_fr_result(d: dict) -> GovDoc:
    """Pure mapper: one Federal Register API result -> GovDoc. Unit-tested."""
    agencies = [a.get("name", "") for a in (d.get("agencies") or []) if a.get("name")]
    org = agencies[0] if agencies else "Federal Register"
    pres = d.get("president")
    pres_name = pres.get("name") if isinstance(pres, dict) else None
    doc_type = d.get("type") or "Notice"
    title = (d.get("title") or "").strip()
    url = d.get("html_url", "") or ""
    if doc_type == "Presidential Document" and pres_name:
        org = f"President {pres_name}"
    return {
        "id": "fr-" + str(d.get("document_number", "")),
        "date": (d.get("publication_date") or "")[:10],
        "title": title,
        "url": url,
        "summary": (d.get("abstract") or "")[:600],
        "branch": "executive",
        "org": org,
        "doc_type": doc_type,
        "source": "U.S. Federal Register",
        "agencies": agencies,
        "president": pres_name if doc_type == "Presidential Document" else None,
    }


def fetch_federal_register(days: int = 2,
                           types: Optional[set[str]] = None,
                           timeout: int = 30) -> list[GovDoc]:
    """All executive-branch rules/proposed rules/notices/presidential docs in
    the last `days`. One key-free call covering every agency. Raises
    requests.RequestException on transport/HTTP failure (caller decides)."""
    types = types or {"Rule", "Proposed Rule", "Presidential Document"}
    start = (date.today() - timedelta(days=days)).isoformat()
    params: list[tuple[str, str]] = [
        ("conditions[publication_date][gte]", start),
        ("per_page", "200"),
        ("order", "newest"),
    ]
    for f in ("document_number", "title", "type", "publication_date",
              "html_url", "abstract", "agencies", "president",
              "executive_order_number"):
        params.append(("fields[]", f))
    resp = requests.get(FR_API, params=params, headers={"User-Agent": UA},
                        timeout=timeout)
    resp.raise_for_status()
    out: list[GovDoc] = []
    for d in resp.json().get("results", []):
        if d.get("type") not in types:
            continue
        out.append(map_fr_result(d))
    return out


# --------------------------------------------------------------------------- #
# RSS registry — every other branch/organ
# --------------------------------------------------------------------------- #
def tag_rss_items(src: GovSource, items: list[dict], days: int,
                  today: Optional[date] = None) -> list[GovDoc]:
    """Pure: turn parsed RSS items into branch-tagged GovDocs, filtered to the
    recency window. Unit-tested."""
    out: list[GovDoc] = []
    for it in items:
        d_iso = (it.get("date") or "")[:10]
        if not _within_days(d_iso, days, today):
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        url = it.get("url") or src.url
        out.append({
            "id": _doc_id(src.branch, url, title),
            "date": d_iso,
            "title": title[:500],
            "url": url,
            "summary": (it.get("summary") or "")[:600],
            "branch": src.branch,
            "org": src.org,
            "doc_type": "Press Release",
            "source": src.label,
        })
    return out


def _fetch_one_rss(src: GovSource, days: int, timeout: int) -> list[GovDoc]:
    resp = requests.get(src.url, headers={"User-Agent": UA}, timeout=timeout)
    resp.raise_for_status()
    items = _parse_rss(resp.content)
    return tag_rss_items(src, items, days)


def fetch_rss_sources(sources: Optional[list[GovSource]] = None,
                      days: int = 2, max_workers: int = 12,
                      timeout: int = 20,
                      errors: Optional[list[str]] = None) -> list[GovDoc]:
    """Fetch the RSS registry in parallel. Dead feeds are skipped; their error
    strings are appended to `errors` if a list is supplied."""
    sources = sources if sources is not None else GOV_SOURCES
    out: list[GovDoc] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_fetch_one_rss, s, days, timeout): s for s in sources}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                out.extend(fut.result())
            except Exception as exc:  # one dead feed must not sink the rest
                if errors is not None:
                    errors.append(f"{s.label}: {type(exc).__name__}: {exc}")
    return out


# --------------------------------------------------------------------------- #
# Congress.gov API (optional — needs CONGRESS_API_KEY)
# --------------------------------------------------------------------------- #
def map_congress_bill(b: dict, congress: int) -> GovDoc:
    """Pure mapper: one Congress.gov bill record -> GovDoc. Unit-tested."""
    num = b.get("number", "")
    btype = (b.get("type", "") or "").upper()
    title = b.get("title", "") or f"{btype} {num}"
    latest = b.get("latestAction") or {}
    return {
        "id": f"bill-{congress}-{btype}-{num}".lower(),
        "date": (latest.get("actionDate") or b.get("updateDate") or "")[:10],
        "title": f"{btype} {num}: {title}"[:500],
        "url": b.get("url") or f"https://www.congress.gov/bill/{congress}th-congress/{btype}/{num}",
        "summary": (latest.get("text") or "")[:600],
        "branch": "legislative",
        "org": "U.S. Congress",
        "doc_type": "Bill",
        "source": "Congress.gov API",
    }


def fetch_congress(days: int = 2, api_key: Optional[str] = None,
                   congress: int = 119, timeout: int = 30,
                   errors: Optional[list[str]] = None) -> list[GovDoc]:
    """Recent bills with action in the window. No-op (returns []) without a key."""
    api_key = api_key or os.environ.get("CONGRESS_API_KEY")
    if not api_key:
        return []
    since = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00Z"
    try:
        resp = requests.get(
            f"{CONGRESS_API}/bill/{congress}",
            params={"api_key": api_key, "fromDateTime": since,
                    "sort": "updateDate+desc", "limit": 250, "format": "json"},
            headers={"User-Agent": UA}, timeout=timeout,
        )
        resp.raise_for_status()
        bills = resp.json().get("bills", [])
        return [map_congress_bill(b, congress) for b in bills]
    except Exception as exc:
        if errors is not None:
            errors.append(f"Congress.gov: {type(exc).__name__}: {exc}")
        return []


# --------------------------------------------------------------------------- #
# CourtListener (optional — needs COURTLISTENER_API_TOKEN) — SCOTUS opinions
# --------------------------------------------------------------------------- #
def map_courtlistener_opinion(o: dict) -> GovDoc:
    """Pure mapper: one CourtListener opinion -> GovDoc. Unit-tested."""
    cluster = o.get("cluster") or {}
    title = (cluster.get("case_name") or o.get("case_name")
             or "Supreme Court opinion")
    url = o.get("absolute_url") or ""
    if url and url.startswith("/"):
        url = "https://www.courtlistener.com" + url
    return {
        "id": "scotus-" + str(o.get("id", "")),
        "date": (o.get("date_created") or cluster.get("date_filed") or "")[:10],
        "title": str(title)[:500],
        "url": url,
        "summary": (o.get("snippet") or "")[:600],
        "branch": "judicial",
        "org": "Supreme Court of the United States",
        "doc_type": "Opinion",
        "source": "CourtListener",
    }


def fetch_courtlistener(days: int = 7, token: Optional[str] = None,
                        timeout: int = 30,
                        errors: Optional[list[str]] = None) -> list[GovDoc]:
    """Recent SCOTUS opinions. No-op without COURTLISTENER_API_TOKEN."""
    token = token or os.environ.get("COURTLISTENER_API_TOKEN")
    if not token:
        return []
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        resp = requests.get(
            COURTLISTENER_API,
            params={"cluster__docket__court": "scotus",
                    "date_created__gte": since, "order_by": "-date_created"},
            headers={"User-Agent": UA, "Authorization": f"Token {token}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return [map_courtlistener_opinion(o)
                for o in resp.json().get("results", [])]
    except Exception as exc:
        if errors is not None:
            errors.append(f"CourtListener: {type(exc).__name__}: {exc}")
        return []


# --------------------------------------------------------------------------- #
# gather_all — the single call the Section + query CLI use
# --------------------------------------------------------------------------- #
def gather_all(days: int = 2, *,
               federal_register: bool = True,
               rss: bool = True,
               congress: bool = True,
               scotus: bool = True,
               sources: Optional[list[GovSource]] = None,
               raise_on_total_failure: bool = True) -> list[GovDoc]:
    """Merge every enabled government source into one deduped, date-sorted list.

    Raises `RuntimeError` only if *every* enabled fetcher failed (a genuine
    blackout). Partial failure returns whatever succeeded."""
    errors: list[str] = []
    docs: list[GovDoc] = []
    attempted = 0
    succeeded = 0

    if federal_register:
        attempted += 1
        try:
            docs.extend(fetch_federal_register(days=days))
            succeeded += 1
        except Exception as exc:
            errors.append(f"FederalRegister: {type(exc).__name__}: {exc}")

    if rss:
        attempted += 1
        before = len(errors)
        rss_docs = fetch_rss_sources(sources=sources, days=days, errors=errors)
        docs.extend(rss_docs)
        # RSS "succeeds" if at least one feed returned or no new errors at all.
        if rss_docs or len(errors) == before:
            succeeded += 1

    if congress:
        attempted += 1
        before = len(errors)
        cdocs = fetch_congress(days=days, errors=errors)
        docs.extend(cdocs)
        if cdocs or len(errors) == before:
            succeeded += 1

    if scotus:
        attempted += 1
        before = len(errors)
        sdocs = fetch_courtlistener(days=max(days, 7), errors=errors)
        docs.extend(sdocs)
        if sdocs or len(errors) == before:
            succeeded += 1

    if raise_on_total_failure and attempted > 0 and succeeded == 0:
        raise RuntimeError(
            "all government sources failed: " + " | ".join(errors[:6]))

    docs = dedup(docs)
    docs.sort(key=lambda d: (d.get("date") or "", d.get("org") or ""), reverse=True)
    return docs
