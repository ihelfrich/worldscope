"""congress.py — Congress.gov legislative activity (bills acted on + CRS summaries).

What this section is for
========================
`congressional_record` covers what members *said* on the floor. This covers what
the institution *did*: bills introduced, referred, reported, passed, presented,
enacted. A bill acted on today is forward-looking in a way an archive is not, so
both sub-pulls sort by `updateDate desc` and the output is filtered down to
recent legislative action rather than returning a slice of the corpus.

API: https://api.congress.gov/v3 (fronted by the api.data.gov umbrella).

Two sub-pulls, one request each — the request budget is the binding constraint
(see the DEMO_KEY note below), so this is deliberately not a paginated crawl:

  1. ``/v3/bill``      — the most recently *updated* bill records, filtered to
                         those with recent legislative action.
  2. ``/v3/summaries`` — CRS plain-English summaries. Bill titles are often
                         opaque ("READ Act"); the summary says what the thing
                         actually does. Summaries are matched onto the bills
                         from (1) where they overlap, and emitted as their own
                         items where they do not.

*** The silent-failure mode this section is built around ***
============================================================
`/v3/bill` **without** a recognised ``sort`` value returns HTTP 200, a
schema-valid body, and bills from the **110th Congress (2007)**. Probed
2026-08-24: no sort → 110th; ``sort=bogus desc`` → 107th, also HTTP 200. There
is no error signal anywhere in the response. A section that shipped with a typo
in one query parameter would have returned two-decade-old bills every single
day and looked perfectly healthy — the exact failure this codebase was rebuilt
to eliminate. `assert_current_congress` therefore treats an ancient response as
a source defect and raises `UpstreamSchemaError`.

Auth behaviour — probed, and it contradicts the usual api.data.gov folklore
=========================================================================
Probed 2026-08-24 against api.congress.gov:

  * invalid key            -> HTTP **403** ``{"error":{"code":"API_KEY_INVALID"}}``
  * missing / empty key    -> HTTP **403** ``{"error":{"code":"API_KEY_MISSING"}}``
  * unknown resource path  -> HTTP **404** ``{"error":"Unknown resource: …"}``  (str, not dict)

So on this host, today, `raise_for_status()` *would* have caught an auth
failure — the "200 on auth rejection" behaviour reported for sibling APIs did
not reproduce here. `check_response` still inspects the body on every response
including 200s, because (a) the umbrella's error envelope is cheap to check,
(b) the two error shapes above are not interchangeable and a naive
``body["error"]["code"]`` would crash on the 404 form, and (c) the status-code
contract is the provider's to change and the body check is the one that keeps
this section honest if they do. An error envelope on a 200 raises exactly as it
would on a 403.

Rate limit — measured, and much tighter than assumed
====================================================
`x-ratelimit-limit: 10` on a DEMO_KEY response — ten requests, not the ~30/hr
other api.data.gov properties allow. Measured by exhausting it on 2026-08-24:
the 429 that follows carries ``retry-after: 31714``, i.e. **nearly nine hours**.
So DEMO_KEY is a one-run-a-day credential, not a reduced-rate one. Two requests
per run fits; a backfill, a retry loop or a second run in the same day does not.
Register at https://api.congress.gov/sign-up/ and set ``CONGRESS_GOV_API_KEY``
for anything beyond the single daily run. The degraded mode is stated in each
item's ``key_mode`` field and in the section's structured output rather than
left implicit.

One approximation, stated plainly
=================================
`/v3/bill` carries no sponsor. The item's ``chamber`` is the bill's
**originating** chamber (``originChamber``), which is the sponsor's chamber but
not the sponsor. Naming the individual sponsor would cost one request per bill;
at ten requests an hour that is not on the table. Nothing here claims otherwise.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any, Optional

import requests

from ..textutil import clean_text
from . import (
    Section,
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
    UpstreamSchemaError,
)

__version__ = "0.1.0"

UA = "worldscope/0.1 (contact: ianthelfrich@gmail.com)"
API = "https://api.congress.gov/v3"
PUBLIC = "https://www.congress.gov/bill"

# api.data.gov umbrella error codes that mean "your credential was rejected",
# as distinct from "your request was wrong". Kept separate from the status code
# because the body is the signal that survives a provider changing its statuses.
AUTH_ERROR_CODES = frozenset({
    "API_KEY_INVALID", "API_KEY_MISSING", "API_KEY_DISABLED",
    "API_KEY_UNAUTHORIZED", "API_KEY_UNVERIFIED",
})
RATE_LIMIT_CODES = frozenset({"OVER_RATE_LIMIT"})

# congress.gov URL slug per bill type. Verified 2026-08-24 by resolving live
# pages: senate-resolution/690, house-bill/10134, house-joint-resolution/1 and
# senate-concurrent-resolution/1 all render the expected designation. The four
# not fetched (S, HRES, SJRES, HCONRES) are the same chamber-word + form-word
# scheme with both chambers and all three resolution forms already confirmed.
TYPE_SLUG = {
    "HR": "house-bill",
    "S": "senate-bill",
    "HRES": "house-resolution",
    "SRES": "senate-resolution",
    "HJRES": "house-joint-resolution",
    "SJRES": "senate-joint-resolution",
    "HCONRES": "house-concurrent-resolution",
    "SCONRES": "senate-concurrent-resolution",
}

TYPE_DISPLAY = {
    "HR": "H.R.", "S": "S.", "HRES": "H.Res.", "SRES": "S.Res.",
    "HJRES": "H.J.Res.", "SJRES": "S.J.Res.",
    "HCONRES": "H.Con.Res.", "SCONRES": "S.Con.Res.",
}

# Latest-action phrases that mark a bill clearing a stage rather than sitting in
# committee. Lowercased substring match against latestAction.text.
ESCALATION_MARKERS = (
    ("became public law", 5),
    ("presented to president", 4),
    ("signed by president", 4),
    ("passed senate", 3),
    ("passed house", 3),
    ("agreed to in senate", 3),
    ("agreed to in house", 3),
    ("resolving differences", 3),
    ("reported by", 2),
    ("placed on", 2),
)


def _api_key() -> str:
    """CONGRESS_GOV_API_KEY if set, else DEMO_KEY.

    Declared as optional_env, not requires_env: DEMO_KEY genuinely works, so an
    unset key is a degraded mode rather than a dead sensor. It is not treated as
    a silent one — `key_mode` rides along on every item.
    """
    return os.environ.get("CONGRESS_GOV_API_KEY") or "DEMO_KEY"


def _key_mode() -> str:
    return "registered" if os.environ.get("CONGRESS_GOV_API_KEY") else "demo"


# --------------------------------------------------------------------------- #
# Congress arithmetic + public URLs
# --------------------------------------------------------------------------- #

def expected_congress(on: Optional[date] = None) -> int:
    """The Congress number sitting on `on`.

    The 1st Congress convened in 1789 and each runs two years, so
    ``(year - 1789) // 2 + 1``. 2025 and 2026 both give 119, 2027 gives 120.
    Computed rather than hardcoded because a constant would silently rot at the
    next handover and take the freshness guard down with it.
    """
    year = (on or date.today()).year
    return (year - 1789) // 2 + 1


def ordinal(n: int) -> str:
    """119 -> '119th', 121 -> '121st'. The public URL embeds the ordinal, and
    the naive '<n>th' template breaks at the 121st Congress (2029)."""
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def public_url(congress: Any, bill_type: str, number: Any, api_url: str = "") -> str:
    """Human-readable congress.gov page for a bill.

    The API's own ``url`` field points at the JSON endpoint, which needs a key
    and is useless to a reader, so it is only the fallback for a bill type not
    in TYPE_SLUG — a new type is a link-quality problem, not a reason to fail
    the pull.
    """
    slug = TYPE_SLUG.get((bill_type or "").upper())
    try:
        nth = ordinal(int(congress))
    except (TypeError, ValueError):
        nth = ""
    if not slug or not nth or not number:
        return api_url or "https://www.congress.gov/legislation"
    return f"{PUBLIC}/{nth}-congress/{slug}/{number}"


def designation(bill_type: str, number: Any) -> str:
    """'HR' + 10134 -> 'H.R. 10134'. Unknown types pass through uppercased."""
    t = (bill_type or "").upper()
    return f"{TYPE_DISPLAY.get(t, t)} {number}".strip()


# --------------------------------------------------------------------------- #
# Response inspection — the trust boundary
# --------------------------------------------------------------------------- #

def _classify_error(status: int, err: Any, url: str) -> Exception:
    """Map an api.data.gov error envelope onto a typed source failure.

    `err` is the value of the body's ``error`` key, which is a dict on the
    umbrella's auth/rate errors and a bare string on Congress.gov's own 404.
    Both shapes appear in production; assuming either one crashes on the other.
    """
    if isinstance(err, dict):
        code = str(err.get("code") or "").upper()
        message = str(err.get("message") or "")
    else:
        code, message = "", str(err)

    detail = f"congress: {url} -> HTTP {status}"
    if code:
        detail += f" {code}"
    if message:
        detail += f": {message}"

    # Auth first: UpstreamAuthError subclasses UpstreamHTTPError, so a
    # status-first branch would swallow the more specific diagnosis.
    if code in AUTH_ERROR_CODES or status in (401, 403):
        return UpstreamAuthError(detail)
    if code in RATE_LIMIT_CODES or status == 429:
        return UpstreamHTTPError(
            detail + " (DEMO_KEY allows 10 requests and then locks out for ~9h; "
                     "set CONGRESS_GOV_API_KEY)"
        )
    if status != 200:
        return UpstreamHTTPError(detail)
    # An error envelope under a 200 is the provider disagreeing with itself.
    # Refusing to parse it is the whole point of this function.
    return UpstreamSchemaError(detail + " (error envelope returned under HTTP 200)")


def check_response(status: int, body_text: str, url: str = API) -> dict:
    """Decode a Congress.gov response, or raise the right typed failure.

    Split out from the request so the failure taxonomy is testable without a
    socket: every branch here is exercised against a captured body.
    """
    try:
        body = json.loads(body_text)
    except ValueError as exc:
        if status != 200:
            # Non-JSON on an error status: the status is the real information.
            raise UpstreamHTTPError(
                f"congress: {url} -> HTTP {status} with a non-JSON body"
            ) from exc
        raise UpstreamParseError(
            f"congress: {url} returned HTTP 200 with a non-JSON body: {exc}"
        ) from exc

    if not isinstance(body, dict):
        raise UpstreamSchemaError(
            f"congress: {url} returned a {type(body).__name__}, expected an object"
        )
    if "error" in body:
        raise _classify_error(status, body["error"], url)
    if status != 200:
        raise UpstreamHTTPError(f"congress: {url} -> HTTP {status}")
    return body


def assert_current_congress(congresses: list[Any], expected: int, what: str) -> None:
    """Guard against the dropped/typo'd ``sort`` parameter.

    A response ordered by anything other than ``updateDate desc`` surfaces
    1990s-2000s records under a clean HTTP 200 (see the module docstring). The
    tolerance is one Congress below `expected`, which covers a run in the first
    days of January before the new Congress convenes; the failure being caught
    is off by a *decade*, so a loose threshold costs nothing and cannot fire on
    a real response.
    """
    # Coerced rather than trusted: the guard must not itself blow up on a
    # string-typed congress number, because then a schema drift would surface
    # as a TypeError instead of the diagnosis it is meant to produce.
    numbers = []
    for c in congresses:
        try:
            numbers.append(int(c))
        except (TypeError, ValueError):
            continue
    if not numbers:
        return
    newest = max(numbers)
    if newest < expected - 1:
        raise UpstreamSchemaError(
            f"congress: newest {what} is from the {ordinal(newest)} Congress but "
            f"the {ordinal(expected)} is sitting — the response is not sorted by "
            f"updateDate desc, which Congress.gov serves as a clean HTTP 200. "
            f"Refusing to report a 20-year-old archive slice as today's activity."
        )


# --------------------------------------------------------------------------- #
# Parsers (pure — no network)
# --------------------------------------------------------------------------- #

def _iso_date(value: Any) -> str:
    """Congress.gov mixes bare dates and full timestamps across fields."""
    s = str(value or "")
    return s[:10] if len(s) >= 10 else ""


def _days_ago(iso: str, today: date) -> Optional[int]:
    try:
        return (today - date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return None


def parse_bills(body: dict, today: Optional[date] = None) -> list[dict]:
    """One row per bill from a ``/v3/bill`` body.

    Raises UpstreamSchemaError on a missing or empty ``bills`` array. This
    endpoint lists the entire bill corpus ordered by update date — it is not a
    "today's events" feed that can legitimately be empty. Zero rows can only
    mean the query broke, so it is a failure, not a quiet day. (The *filtered*
    result further down may legitimately be empty; that is a recess.)
    """
    today = today or date.today()
    rows = body.get("bills")
    if rows is None:
        raise UpstreamSchemaError("congress: /bill response has no 'bills' array")
    if not isinstance(rows, list):
        raise UpstreamSchemaError(
            f"congress: /bill 'bills' is a {type(rows).__name__}, expected a list"
        )
    if not rows:
        raise UpstreamSchemaError(
            "congress: /bill returned zero bills; the corpus listing is never "
            "empty, so this is a broken query rather than a quiet day"
        )

    out: list[dict] = []
    for b in rows:
        if not isinstance(b, dict):
            continue
        btype = str(b.get("type") or "").upper()
        number = b.get("number")
        congress = b.get("congress")
        if not (btype and number and congress):
            continue

        action = b.get("latestAction") or {}
        action_date = _iso_date(action.get("actionDate"))
        action_text = clean_text(action.get("text"), 400)
        introduced = _iso_date(b.get("introducedDate"))
        # A bill with no recorded action is one that was only just introduced;
        # its introduction date is the action. This is the row's event clock —
        # deliberately NOT updateDate, which bumps on metadata reprocessing.
        event_date = action_date or introduced
        chamber = b.get("originChamber") or ""
        desig = designation(btype, number)
        title = clean_text(b.get("title"), 240) or desig

        bits = [f"{chamber} bill" if chamber else "bill"]
        if introduced:
            bits.append(f"introduced {introduced}")
        if action_date and action_text:
            bits.append(f"{action_date}: {action_text}")
        elif action_text:
            bits.append(action_text)

        out.append({
            "id": f"bill:{congress}-{btype.lower()}-{number}",
            "date": event_date,
            "title": f"[{desig}] {title}",
            "url": public_url(congress, btype, number, str(b.get("url") or "")),
            "summary": " · ".join(bits),
            "kind": "bill",
            "congress": congress,
            "bill_type": btype,
            "number": str(number),
            "designation": desig,
            "bill_title": title,
            # originChamber is the sponsor's chamber, not the sponsor — the
            # list endpoint carries no sponsor and resolving one costs a
            # request per bill. See the module docstring.
            "chamber": chamber,
            "introduced_date": introduced,
            "latest_action_date": action_date,
            "latest_action": action_text,
            "update_date": _iso_date(b.get("updateDate")),
            "event_date": event_date,
            "event_kind": "latest_action" if action_date else "introduced",
            "event_days_ago": _days_ago(event_date, today),
            "api_url": str(b.get("url") or ""),
        })
    return out


def parse_summaries(body: dict, today: Optional[date] = None) -> list[dict]:
    """One row per CRS summary from a ``/v3/summaries`` body.

    Unlike ``/bill``, an empty ``summaries`` array is accepted: CRS publishes in
    bursts and a genuinely empty recent window is plausible. A *missing* array
    is still a schema failure.
    """
    today = today or date.today()
    rows = body.get("summaries")
    if rows is None:
        raise UpstreamSchemaError(
            "congress: /summaries response has no 'summaries' array"
        )
    if not isinstance(rows, list):
        raise UpstreamSchemaError(
            f"congress: /summaries 'summaries' is a {type(rows).__name__}, "
            f"expected a list"
        )

    out: list[dict] = []
    for s in rows:
        if not isinstance(s, dict):
            continue
        bill = s.get("bill") or {}
        btype = str(bill.get("type") or "").upper()
        number = bill.get("number")
        congress = bill.get("congress")
        if not (btype and number and congress):
            continue

        desig = designation(btype, number)
        title = clean_text(bill.get("title"), 240) or desig
        action_date = _iso_date(s.get("actionDate"))
        # CRS summaries are HTML fragments with entity-encoded spaces. Public
        # domain, so the length cap is for brief density, not licensing.
        text = clean_text(s.get("text"), 500)
        stage = clean_text(s.get("actionDesc"), 80)
        # A summary's event is CRS *publishing* it, not the legislative action
        # it describes — those can be months apart, and the news is that a
        # plain-English description of this bill now exists. Ranking a summary
        # by its underlying action date would bury today's CRS output under
        # bills that merely moved this week.
        event_date = _iso_date(s.get("updateDate")) or action_date

        out.append({
            "id": f"summary:{congress}-{btype.lower()}-{number}:{s.get('versionCode') or ''}",
            "date": event_date,
            "title": f"[{desig}] {title}",
            "url": public_url(congress, btype, number, str(bill.get("url") or "")),
            "summary": text or stage,
            "kind": "summary",
            "congress": congress,
            "bill_type": btype,
            "number": str(number),
            "designation": desig,
            "bill_title": title,
            "chamber": bill.get("originChamber") or "",
            "crs_summary": text,
            "summary_stage": stage,
            "summary_action_date": action_date,
            "summary_updated": _iso_date(s.get("updateDate")),
            "event_date": event_date,
            "event_kind": "summary_published",
            "event_days_ago": _days_ago(event_date, today),
            # Kept separate from event_days_ago so a reader can see that a
            # summary published today may describe an action from months back.
            "action_days_ago": _days_ago(action_date, today),
            "api_url": str(bill.get("url") or ""),
        })
    return out


def _bill_key(row: dict) -> str:
    return f"{row.get('congress')}-{row.get('bill_type')}-{row.get('number')}"


def merge(bills: list[dict], summaries: list[dict]) -> list[dict]:
    """Attach each CRS summary to its bill; emit the orphans as their own items.

    The two endpoints answer different questions ("what moved" vs "what did CRS
    describe") and overlap only incidentally, so an orphan summary is normal and
    carries real signal — it is a plain-English description of a bill that is
    live enough for CRS to have just written about it.
    """
    by_key = {_bill_key(b): b for b in bills}
    orphans: list[dict] = []
    attached: set[str] = set()
    for s in summaries:
        key = _bill_key(s)
        target = by_key.get(key)
        if target is None:
            orphans.append(s)
            continue
        # CRS revises summaries in place, so one bill can appear more than once
        # in the summaries feed. It arrives newest-first, so the first match is
        # the current revision; attaching the rest would append the CRS text to
        # the same bill's summary repeatedly.
        if key in attached:
            continue
        attached.add(key)
        target["crs_summary"] = s.get("crs_summary", "")
        target["summary_stage"] = s.get("summary_stage", "")
        if s.get("crs_summary"):
            target["summary"] = f"{target['summary']} · CRS: {s['crs_summary']}"
    return bills + orphans


def select_recent(rows: list[dict], today: date, window_days: int,
                  minimum: int, cap: int) -> list[dict]:
    """Rank by `event_date`, keep the window, floor it at `minimum`.

    Sorting by ``updateDate desc`` returns the most recently *touched* records,
    which is not the same as the most recently *acted on*: Congress.gov bumps
    updateDate on metadata reprocessing, so a bill last acted on three weeks ago
    can top the list. Ranking on the event date is what makes this a feed of
    legislative activity instead of a feed of database writes.

    When the window is genuinely empty — recess — the floor keeps a few items so
    the brief still carries legislative context, each flagged
    ``within_window: False`` so nothing stale is passed off as today's news.
    """
    ranked = sorted(
        rows,
        key=lambda r: (r.get("event_date") or "", r.get("update_date") or ""),
        reverse=True,
    )
    cutoff = (today - timedelta(days=window_days)).isoformat()
    out: list[dict] = []
    for row in ranked:
        stamp = row.get("event_date") or ""
        in_window = bool(stamp) and stamp >= cutoff
        if in_window or len(out) < minimum:
            row["within_window"] = in_window
            out.append(row)
        if len(out) >= cap:
            break
    return out


def compose(bills: list[dict], summaries: list[dict], today: date, *,
            window_days: int, min_bills: int,
            max_bills: int, max_summaries: int) -> list[dict]:
    """Merge the two feeds and select from each *separately*.

    The kinds run on different clocks — a bill's event is its latest floor
    action, a summary's is CRS publishing it — so a single pooled ranking lets
    whichever feed happens to carry fresher timestamps starve the other. On the
    first live pull (2026-08-24, August recess) that was literal: five same-day
    CRS summaries filled every slot and not one bill reached the output. A
    legislative feed with no legislation in it, reported as a clean success.

    Kept out of pull() so the composition can be tested at an arbitrary date;
    pull() legitimately reads the wall clock and cannot be pinned.
    """
    merged = merge(bills, summaries)
    picked = select_recent(
        [r for r in merged if r["kind"] == "bill"], today=today,
        window_days=window_days, minimum=min_bills, cap=max_bills,
    ) + select_recent(
        [r for r in merged if r["kind"] == "summary"], today=today,
        window_days=window_days, minimum=0, cap=max_summaries,
    )
    picked.sort(key=lambda r: r.get("event_date") or "", reverse=True)
    return picked


# --------------------------------------------------------------------------- #
# Section
# --------------------------------------------------------------------------- #

class CongressSection(Section):
    id = "congress"
    title = "Congress (bills introduced, acted on, summarized)"
    emoji = "🏛️"

    source_id = "congress-gov"
    source_name = "Congress.gov API (Library of Congress)"
    source_url = "https://api.congress.gov/v3"
    source_tier = "primary_document"
    source_license = "public-domain"
    source_country = "US"
    source_language = "en"

    PULL_TIMEOUT_S = 60

    # Capability contract: DEMO_KEY works, so an unset key degrades rather than
    # kills. Measured ceiling is 10 requests followed by a ~9h lockout, which
    # this section's two requests fit at a daily cadence and nothing else does.
    optional_env = ("CONGRESS_GOV_API_KEY",)

    BILL_LIMIT = 250        # the endpoint's documented maximum, one request
    SUMMARY_LIMIT = 50
    # A legislative week, matching congressional_record.LOOKBACK_DAYS. A live
    # pull on 2026-08-24 (August recess) with a 3-day window returned zero
    # bills — the newest floor action was 2026-08-20 — and the section filled
    # entirely with CRS summaries. Congress is in recess a large fraction of
    # the year, so a window shorter than a week makes the bill feed disappear
    # for weeks at a time.
    WINDOW_DAYS = 7
    MIN_BILLS = 5           # recess floor, so the section is never bill-less
    MAX_BILLS = 30
    MAX_SUMMARIES = 10

    def _get(self, path: str, **params) -> dict:
        params.setdefault("format", "json")
        params["api_key"] = _api_key()
        url = f"{API}/{path.lstrip('/')}"
        try:
            resp = requests.get(
                url, params=params,
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=25,
            )
        except requests.RequestException as exc:
            raise UpstreamHTTPError(
                f"congress: {path} request failed: {type(exc).__name__}: {exc}"
            ) from exc
        # Deliberately not raise_for_status(): the body carries the provider's
        # own error code, which is more precise than the status and is the part
        # that keeps working if the status contract changes.
        return check_response(resp.status_code, resp.text, url)

    def pull(self) -> list[dict]:
        today = date.today()
        expected = expected_congress(today)
        mode = _key_mode()

        # sort=updateDate+desc is load-bearing, not a preference. Without it the
        # endpoint serves the 110th Congress under a clean 200. The literal '+'
        # survives requests' percent-encoding — verified by a live pull on
        # 2026-08-24 returning 119th-Congress rows through this exact code path.
        bill_body = self._get(
            "bill", limit=self.BILL_LIMIT, sort="updateDate+desc"
        )
        bills = parse_bills(bill_body, today=today)
        assert_current_congress([b["congress"] for b in bills], expected, "bill")

        summary_body = self._get(
            "summaries", limit=self.SUMMARY_LIMIT, sort="updateDate+desc"
        )
        summaries = parse_summaries(summary_body, today=today)
        assert_current_congress(
            [s["congress"] for s in summaries], expected, "summary"
        )

        picked = compose(
            bills, summaries, today,
            window_days=self.WINDOW_DAYS,
            min_bills=self.MIN_BILLS,
            max_bills=self.MAX_BILLS,
            max_summaries=self.MAX_SUMMARIES,
        )
        for item in picked:
            item["key_mode"] = mode
        return picked

    def emit_structured(self, state: "SectionState") -> dict:
        """Flag bills that cleared a stage rather than sitting in committee.

        Referral to committee is the modal outcome and carries almost no
        information; enactment, presentment and floor passage are the events
        worth surfacing to the graph. The score is an ordinal severity from
        ESCALATION_MARKERS, not a distributional z-score — the field is named
        z_score by the lake schema and the value here is not one, which is
        stated rather than dressed up.
        """
        base = super().emit_structured(state)
        base["key_mode"] = _key_mode()
        base["degraded"] = _key_mode() == "demo"
        for it in state.items:
            if not it.get("within_window", True):
                continue
            text = (it.get("latest_action") or "").lower()
            hit = next(
                ((label, rank) for label, rank in ESCALATION_MARKERS if label in text),
                None,
            )
            if hit is None:
                continue
            label, rank = hit
            base["anomalies"].append({
                "category": "legislative-stage-change",
                "z_score": float(rank),   # ordinal severity, not a z-score
                "description": (
                    f"{it.get('designation', '')} — {label} · "
                    f"{it.get('bill_title', '')}"
                ),
                "evidence": [it.get("id", "")],
            })
        return base
