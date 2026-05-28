"""
form4.py — SEC EDGAR Form 4 insider-transaction filings (recent).

Source: the EDGAR "browse current filings" ATOM feed filtered to Form 4.
    https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&output=atom

The feed returns the ~100 most recent Form 4 filings (officers/directors/10%
holders disclosing changes in their company holdings). We filter to the last
48 hours so a missed run doesn't blow the window, and cap at 50 items.

SEC requires a real, identifying User-Agent. A generic "worldscope/0.1" UA
gets bounced to the "Undeclared Automated Tool" page.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import feedparser

from . import Section

FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&output=atom"
UA = "Ian Helfrich worldscope/0.1 ianthelfrich@gmail.com"

# Accession numbers in EDGAR URLs look like 0001234567-26-000001
ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")

# Typical entry title from this feed:
#   "4 - SMITH JOHN A (0001234567) (Reporting)"
# or with an issuer prefix in some variants:
#   "4 - ACME CORP (0000012345) (Filer)"
TITLE_RE = re.compile(
    r"^\s*(?P<form>4(?:/A)?)\s*-\s*(?P<name>.+?)\s*\((?P<cik>\d{10})\)\s*\((?P<role>[^)]+)\)\s*$"
)


class Form4Section(Section):
    id = "form4"
    title = "SEC Form 4 insider transactions (recent)"
    emoji = "👁️"

    # 96h (4 days) so a Monday pull sees Friday's filings — SEC doesn't accept
    # Form 4s on weekends or federal holidays.
    WINDOW_HOURS = 96
    MAX_ITEMS = 50

    def pull(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.WINDOW_HOURS)
        try:
            feed = feedparser.parse(FEED, agent=UA)
        except Exception as exc:
            print(f"[{self.id}] SEC Form 4 feed fetch failed: {type(exc).__name__}: {exc}")
            raise

        # feedparser does NOT raise on HTTP/feed errors — it sets .bozo
        # and .bozo_exception, or returns a non-2xx .status, or returns
        # empty .entries. We have to inspect all of those.
        status = getattr(feed, "status", None)
        if status is not None and not (200 <= int(status) < 300):
            raise RuntimeError(
                f"[{self.id}] SEC Form 4 feed returned HTTP {status}"
            )
        if getattr(feed, "bozo", 0):
            bozo_exc = getattr(feed, "bozo_exception", None)
            # bozo=1 with CharacterEncodingOverride is benign; raise on
            # everything else (XML parse error, HTTP failure, etc).
            from xml.sax import SAXException  # type: ignore
            tolerable = ("CharacterEncodingOverride",
                          "NonXMLContentType",
                          "FeedparserDict")
            if bozo_exc and type(bozo_exc).__name__ not in tolerable:
                raise RuntimeError(
                    f"[{self.id}] SEC Form 4 feed parse failed: "
                    f"{type(bozo_exc).__name__}: {bozo_exc}"
                )
        entries = getattr(feed, "entries", []) or []
        if not entries:
            raise RuntimeError(
                f"[{self.id}] SEC Form 4 feed returned zero entries "
                f"(status={status}); upstream may be down"
            )

        items: list[dict] = []
        for e in entries:
            # --- timestamp ---------------------------------------------------
            dt = None
            for attr in ("updated_parsed", "published_parsed"):
                tt = getattr(e, attr, None)
                if tt:
                    try:
                        dt = datetime(*tt[:6], tzinfo=timezone.utc)
                        break
                    except (TypeError, ValueError):
                        dt = None
            if dt is None:
                # Skip entries without a parseable timestamp — can't window them.
                continue
            if dt < cutoff:
                continue

            raw_title = (getattr(e, "title", "") or "").strip()
            link = (getattr(e, "link", "") or "").strip()
            raw_summary = (getattr(e, "summary", "") or "").strip()
            # Strip HTML tags from the summary blob
            summary = re.sub(r"<[^>]+>", " ", raw_summary)
            summary = re.sub(r"\s+", " ", summary).strip()[:400]

            # --- accession id ------------------------------------------------
            acc_match = ACCESSION_RE.search(link) or ACCESSION_RE.search(raw_summary)
            accession = acc_match.group(1) if acc_match else None

            # --- parse the title ---------------------------------------------
            form_type = "4"
            filer_name = None
            cik = None
            role = None
            m = TITLE_RE.match(raw_title)
            if m:
                form_type = m.group("form")
                filer_name = m.group("name").strip()
                cik = m.group("cik")
                role = m.group("role").strip()

            # Build the human-readable title
            if filer_name:
                pretty_title = f"[{filer_name}] Form {form_type}"
                if role:
                    pretty_title += f" ({role})"
            else:
                pretty_title = raw_title

            # Add an "amended" hint to the summary when applicable
            amended = form_type.endswith("/A")
            time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            extra_bits = [f"filed {time_str}"]
            if amended:
                extra_bits.append("amended")
            if role:
                extra_bits.append(f"role: {role}")
            prefix = " · ".join(extra_bits)
            final_summary = (prefix + (" — " + summary if summary else "")).strip()

            item = {
                "id": accession or (link or raw_title),
                "date": dt.date().isoformat(),
                "title": pretty_title,
                "url": link,
                "summary": final_summary,
                "form_type": form_type,
                "filer_name": filer_name,
                "cik": cik,
                "role": role,
                "amended": amended,
            }
            items.append(item)
            if len(items) >= self.MAX_ITEMS:
                break

        # Newest first
        items.sort(key=lambda it: it.get("date", ""), reverse=True)
        return items
