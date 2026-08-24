"""Congress.gov legislative activity — parsing, link construction, failure taxonomy.

Every fixture below is a real response body captured from api.congress.gov on
2026-08-24, trimmed to the fields the parser reads. Nothing here touches the
network; the one live test skips unless WORLDSCOPE_LIVE is set.

The two failure modes these tests exist for:

  1. **Auth rejection.** An `error` envelope must raise UpstreamAuthError,
     whether it arrives under a 403 (what the API actually does today) or under
     a 200 (what the sibling APIs in this repo do, and what this one is not
     trusted to keep not doing).

  2. **The dropped sort parameter.** `/v3/bill` without `sort=updateDate desc`
     returns HTTP 200, a schema-valid body, and bills from the 110th Congress.
     There is no error signal. `assert_current_congress` is the only thing
     standing between that response and a brief that reports 2007 legislation
     as today's news, so it is tested against the real ancient body.
"""
from __future__ import annotations

import json
import os
from datetime import date

import pytest

from worldscope.sections import (
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
    UpstreamSchemaError,
)
from worldscope.sections import congress as cg


TODAY = date(2026, 8, 24)


# --------------------------------------------------------------------------- #
# Captured bodies
# --------------------------------------------------------------------------- #

# GET /v3/bill?format=json&limit=5&sort=updateDate+desc — first three bills.
BILLS_OK = """
{
    "bills": [
        {
            "congress": 119,
            "introducedDate": "2026-06-04",
            "latestAction": {
                "actionDate": "2026-08-06",
                "text": "Placed on Senate Legislative Calendar under General Orders. Calendar No. 549."
            },
            "number": "4689",
            "originChamber": "Senate",
            "originChamberCode": "S",
            "title": "READ Act",
            "type": "S",
            "updateDate": "2026-08-24",
            "updateDateIncludingText": "2026-08-24",
            "url": "https://api.congress.gov/v3/bill/119/s/4689?format=json"
        },
        {
            "congress": 119,
            "introducedDate": "2026-08-20",
            "latestAction": {
                "actionDate": "2026-08-24",
                "text": "Referred to the House Committee on Energy and Commerce."
            },
            "number": "10134",
            "originChamber": "House",
            "originChamberCode": "H",
            "title": "Local Health Care Protection Act of 2026",
            "type": "HR",
            "updateDate": "2026-08-24",
            "updateDateIncludingText": "2026-08-24",
            "url": "https://api.congress.gov/v3/bill/119/hr/10134?format=json"
        },
        {
            "congress": 119,
            "introducedDate": "2026-08-20",
            "latestAction": {
                "actionDate": "2026-08-23",
                "text": "Referred to the House Committee on House Administration."
            },
            "number": "10118",
            "originChamber": "House",
            "originChamberCode": "H",
            "title": "No Data Center NDAs Act",
            "type": "HR",
            "updateDate": "2026-08-24",
            "updateDateIncludingText": "2026-08-24",
            "url": "https://api.congress.gov/v3/bill/119/hr/10118?format=json"
        }
    ],
    "pagination": {"count": 600000},
    "request": {"contentType": "application/json", "format": "json"}
}
"""

# GET /v3/bill?format=json&limit=3&api_key=DEMO_KEY — i.e. the SAME endpoint
# with the sort parameter omitted. HTTP 200. Bills from 2007. This is the body
# that would have shipped daily if the sort had ever been dropped or typo'd.
BILLS_UNSORTED_HTTP_200 = """
{
    "bills": [
        {
            "congress": 110,
            "latestAction": {"actionDate": "2007-01-24", "text": "Referred to the Subcommittee on Health."},
            "number": "20",
            "originChamber": "House",
            "originChamberCode": "H",
            "title": "Expressing the sense of Congress regarding health care.",
            "type": "HCONRES",
            "updateDate": "2025-04-07",
            "url": "https://api.congress.gov/v3/bill/110/hconres/20?format=json"
        },
        {
            "congress": 110,
            "latestAction": {"actionDate": "2007-01-05", "text": "Referred to the Committee on Rules."},
            "number": "10",
            "originChamber": "House",
            "originChamberCode": "H",
            "title": "Providing for a joint session of Congress.",
            "type": "HCONRES",
            "updateDate": "2024-02-07",
            "url": "https://api.congress.gov/v3/bill/110/hconres/10?format=json"
        }
    ],
    "pagination": {"count": 600000},
    "request": {"contentType": "application/json", "format": "json"}
}
"""

# GET /v3/summaries?format=json&limit=3&sort=updateDate+desc
SUMMARIES_OK = """
{
    "pagination": {"count": 5},
    "request": {"contentType": "application/json", "format": "json"},
    "summaries": [
        {
            "actionDate": "2026-04-29",
            "actionDesc": "Introduced in House",
            "bill": {
                "congress": 119,
                "number": "1230",
                "originChamber": "House",
                "originChamberCode": "H",
                "title": "Addressing the politicization of war crimes allegations against allied Special Operations Forces.",
                "type": "HRES",
                "updateDateIncludingText": "2026-08-24",
                "url": "https://api.congress.gov/v3/bill/119/hres/1230?format=json"
            },
            "currentChamber": "House",
            "currentChamberCode": "H",
            "lastSummaryUpdateDate": "2026-08-24T15:00:19Z",
            "text": "<p>This resolution honors the service and sacrifice of the&nbsp;armed forces of the United Kingdom, Australia, and other partner nations that fought alongside U.S. troops in Afghanistan, Iraq, and other collective security and counterterrorism missions.</p>",
            "updateDate": "2026-08-24T15:01:02Z",
            "versionCode": "00"
        },
        {
            "actionDate": "2026-05-11",
            "actionDesc": "Passed Senate",
            "bill": {
                "congress": 119,
                "number": "4689",
                "originChamber": "Senate",
                "originChamberCode": "S",
                "title": "READ Act",
                "type": "S",
                "updateDateIncludingText": "2026-08-24",
                "url": "https://api.congress.gov/v3/bill/119/s/4689?format=json"
            },
            "currentChamber": "Senate",
            "currentChamberCode": "S",
            "lastSummaryUpdateDate": "2026-08-24T14:04:34Z",
            "text": "<p>This bill directs the Department of State to report on&nbsp;basic education programs.</p>",
            "updateDate": "2026-08-24T14:05:56Z",
            "versionCode": "55"
        }
    ]
}
"""

# Same shape as BILLS_OK, with the identities and action text altered so both
# arms of the escalation filter fall inside the recency window on TODAY. Bill
# numbers are synthetic; the action strings are verbatim Congress.gov phrasings.
BILLS_PASSAGE = """
{
    "bills": [
        {
            "congress": 119,
            "introducedDate": "2026-07-01",
            "latestAction": {
                "actionDate": "2026-08-24",
                "text": "Passed House by the Yeas and Nays: 241 - 187 (Roll no. 412)."
            },
            "number": "9001",
            "originChamber": "House",
            "originChamberCode": "H",
            "title": "A bill that cleared the floor.",
            "type": "HR",
            "updateDate": "2026-08-24",
            "url": "https://api.congress.gov/v3/bill/119/hr/9001?format=json"
        },
        {
            "congress": 119,
            "introducedDate": "2026-08-23",
            "latestAction": {
                "actionDate": "2026-08-23",
                "text": "Referred to the Committee on Ways and Means."
            },
            "number": "9002",
            "originChamber": "House",
            "originChamberCode": "H",
            "title": "A bill that went to committee.",
            "type": "HR",
            "updateDate": "2026-08-24",
            "url": "https://api.congress.gov/v3/bill/119/hr/9002?format=json"
        }
    ],
    "pagination": {"count": 600000}
}
"""

# --- error bodies, all captured verbatim ---------------------------------- #

# api_key=NOT_A_REAL_KEY_12345 -> HTTP 403
AUTH_INVALID = """
{
  "error": {
    "code": "API_KEY_INVALID",
    "message": "An invalid api_key was supplied. Get one at https://api.congress.gov:443"
  }
}
"""

# no api_key param at all, and api_key= (empty) -> HTTP 403, identical body
AUTH_MISSING = """
{
  "error": {
    "code": "API_KEY_MISSING",
    "message": "No api_key was supplied. Get one at https://api.congress.gov:443"
  }
}
"""

# GET /v3/billzzz -> HTTP 404. Note `error` is a STRING here, not an object:
# a parser that assumes the umbrella's dict shape raises TypeError on this and
# never reports the real failure.
UNKNOWN_RESOURCE = """
{
    "error": "Unknown resource: billzzz"
}
"""

# An exhausted DEMO_KEY -> HTTP 429. Captured live on 2026-08-24 by running the
# section's own pull() until the quota ran out. The response also carried
# `x-ratelimit-limit: 10`, `x-ratelimit-remaining: 0` and `retry-after: 31714`
# — nearly nine hours, so DEMO_KEY is a one-run-a-day credential, not a
# reduced-rate one.
OVER_RATE_LIMIT = """
{
  "error": {
    "code": "OVER_RATE_LIMIT",
    "message": "You have exceeded your rate limit. Try again later or contact us for assistance: https://api.congress.gov:443"
  }
}
"""


# --------------------------------------------------------------------------- #
# 1. Auth rejection must raise — under 403 AND under 200
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body", [AUTH_INVALID, AUTH_MISSING])
def test_auth_error_body_raises_upstream_auth_error(body):
    with pytest.raises(UpstreamAuthError):
        cg.check_response(403, body, "https://api.congress.gov/v3/bill")


@pytest.mark.parametrize("body", [AUTH_INVALID, AUTH_MISSING])
def test_auth_error_raises_even_under_http_200(body):
    """The case resp.raise_for_status() cannot see.

    api.congress.gov answers 403 today, but the sibling feeds in this repo
    answer 200 on a rejected key, and a section that trusts the status code is
    one provider change away from reporting zero items as a quiet day.
    """
    with pytest.raises(UpstreamAuthError):
        cg.check_response(200, body, "https://api.congress.gov/v3/bill")


def test_auth_error_message_names_the_provider_code():
    """The operator has to be able to tell a bad key from an absent one."""
    with pytest.raises(UpstreamAuthError, match="API_KEY_INVALID"):
        cg.check_response(403, AUTH_INVALID)
    with pytest.raises(UpstreamAuthError, match="API_KEY_MISSING"):
        cg.check_response(403, AUTH_MISSING)


def test_rate_limit_is_http_error_not_auth_error():
    """An exhausted DEMO_KEY is a throttle, not a credential problem, and the
    remedy named in the message differs."""
    with pytest.raises(UpstreamHTTPError) as exc:
        cg.check_response(429, OVER_RATE_LIMIT)
    assert not isinstance(exc.value, UpstreamAuthError)
    assert "CONGRESS_GOV_API_KEY" in str(exc.value)


def test_string_shaped_error_body_does_not_crash_the_classifier():
    """`error` is a dict on umbrella failures and a bare string on the API's own
    404. Both must produce a typed failure, not a TypeError."""
    with pytest.raises(UpstreamHTTPError) as exc:
        cg.check_response(404, UNKNOWN_RESOURCE)
    assert "Unknown resource" in str(exc.value)


def test_non_json_body_raises_parse_error_at_200():
    with pytest.raises(UpstreamParseError):
        cg.check_response(200, "<html>502 Bad Gateway</html>")


def test_non_json_body_raises_http_error_at_502():
    with pytest.raises(UpstreamHTTPError):
        cg.check_response(502, "<html>502 Bad Gateway</html>")


def test_json_array_body_is_a_schema_error():
    with pytest.raises(UpstreamSchemaError):
        cg.check_response(200, "[]")


def test_clean_body_is_returned():
    body = cg.check_response(200, BILLS_OK)
    assert len(body["bills"]) == 3


# --------------------------------------------------------------------------- #
# 2. The dropped-sort silent failure
# --------------------------------------------------------------------------- #

def test_unsorted_response_is_rejected_despite_http_200():
    """The headline guard. This body is real, arrived under HTTP 200, parses
    cleanly, and is from 2007."""
    body = cg.check_response(200, BILLS_UNSORTED_HTTP_200)
    rows = cg.parse_bills(body, today=TODAY)
    assert rows, "the ancient body parses fine — that is exactly the problem"
    with pytest.raises(UpstreamSchemaError, match="not sorted by updateDate"):
        cg.assert_current_congress(
            [r["congress"] for r in rows], cg.expected_congress(TODAY), "bill"
        )


def test_current_congress_passes_the_guard():
    rows = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    cg.assert_current_congress(
        [r["congress"] for r in rows], cg.expected_congress(TODAY), "bill"
    )


def test_guard_tolerates_one_congress_behind():
    """A run in the first days of January expects the incoming Congress while
    the API still serves the outgoing one. One behind is fine; nine is not."""
    cg.assert_current_congress([119], 120, "bill")
    with pytest.raises(UpstreamSchemaError):
        cg.assert_current_congress([119], 121, "bill")


def test_guard_is_silent_on_an_empty_list():
    """Emptiness is diagnosed by the parsers, not by the freshness guard."""
    cg.assert_current_congress([], 119, "bill")


def test_expected_congress_tracks_the_calendar():
    assert cg.expected_congress(date(2025, 3, 1)) == 119
    assert cg.expected_congress(date(2026, 8, 24)) == 119
    assert cg.expected_congress(date(2027, 3, 1)) == 120
    assert cg.expected_congress(date(2029, 3, 1)) == 121


# --------------------------------------------------------------------------- #
# 3. Empty vs broken
# --------------------------------------------------------------------------- #

def test_missing_bills_array_is_a_schema_error():
    with pytest.raises(UpstreamSchemaError):
        cg.parse_bills({"pagination": {}}, today=TODAY)


def test_empty_bills_array_is_a_failure_not_a_quiet_day():
    """`/v3/bill` lists the whole corpus ordered by update date. Zero rows is
    not a slow news day in Congress; it is a broken query."""
    with pytest.raises(UpstreamSchemaError, match="never"):
        cg.parse_bills({"bills": []}, today=TODAY)


def test_missing_summaries_array_is_a_schema_error():
    with pytest.raises(UpstreamSchemaError):
        cg.parse_summaries({"pagination": {}}, today=TODAY)


def test_empty_summaries_array_is_accepted():
    """CRS publishes in bursts; an empty recent window is genuinely possible."""
    assert cg.parse_summaries({"summaries": []}, today=TODAY) == []


def test_recess_returns_a_flagged_floor_not_a_fabricated_window():
    """When nothing was acted on inside the window, the floor items must be
    marked so nothing three weeks old is passed off as today's activity."""
    rows = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    picked = cg.select_recent(rows, today=date(2026, 12, 25),
                              window_days=3, minimum=2, cap=40)
    assert len(picked) == 2
    assert all(p["within_window"] is False for p in picked)


# --------------------------------------------------------------------------- #
# 4. Parsing
# --------------------------------------------------------------------------- #

def test_bill_row_carries_the_fields_the_brief_renders():
    rows = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    hr = next(r for r in rows if r["number"] == "10134")

    assert hr["id"] == "bill:119-hr-10134"
    assert hr["designation"] == "H.R. 10134"
    assert hr["title"] == "[H.R. 10134] Local Health Care Protection Act of 2026"
    assert hr["chamber"] == "House"
    assert hr["introduced_date"] == "2026-08-20"
    assert hr["latest_action_date"] == "2026-08-24"
    assert hr["latest_action"].startswith("Referred to the House Committee")
    assert hr["date"] == "2026-08-24"
    assert hr["event_date"] == "2026-08-24"
    assert hr["event_kind"] == "latest_action"
    assert hr["event_days_ago"] == 0
    assert "introduced 2026-08-20" in hr["summary"]


def test_bill_url_is_the_human_page_not_the_api_endpoint():
    """The API's `url` field needs a key and renders as JSON; a reader needs
    the congress.gov page."""
    rows = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    hr = next(r for r in rows if r["number"] == "10134")
    assert hr["url"] == "https://www.congress.gov/bill/119th-congress/house-bill/10134"
    assert hr["api_url"].startswith("https://api.congress.gov/")


def test_public_url_covers_every_bill_type():
    """Slug scheme verified live on 2026-08-24 for senate-resolution/690,
    house-bill/10134, house-joint-resolution/1, senate-concurrent-resolution/1."""
    assert cg.public_url(119, "SRES", 690) == \
        "https://www.congress.gov/bill/119th-congress/senate-resolution/690"
    assert cg.public_url(119, "HJRES", 1) == \
        "https://www.congress.gov/bill/119th-congress/house-joint-resolution/1"
    assert cg.public_url(119, "SCONRES", 1) == \
        "https://www.congress.gov/bill/119th-congress/senate-concurrent-resolution/1"
    assert cg.public_url(119, "s", 4689) == \
        "https://www.congress.gov/bill/119th-congress/senate-bill/4689"


def test_unknown_bill_type_falls_back_to_the_api_url():
    """A new bill type is a degraded link, not a failed pull."""
    api = "https://api.congress.gov/v3/bill/119/xres/1?format=json"
    assert cg.public_url(119, "XRES", 1, api) == api


def test_ordinal_survives_the_121st_congress():
    """The obvious '<n>th-congress' template produces a dead link in 2029."""
    assert cg.ordinal(119) == "119th"
    assert cg.ordinal(120) == "120th"
    assert cg.ordinal(121) == "121st"
    assert cg.ordinal(122) == "122nd"
    assert cg.ordinal(123) == "123rd"
    assert cg.ordinal(111) == "111th"   # not '111st'
    assert cg.ordinal(112) == "112th"
    assert cg.ordinal(113) == "113th"


def test_summary_html_and_entities_are_stripped():
    rows = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    hres = next(r for r in rows if r["number"] == "1230")
    assert "<p>" not in hres["crs_summary"]
    assert "&nbsp;" not in hres["crs_summary"]
    assert hres["crs_summary"].startswith("This resolution honors the service")
    assert hres["summary_stage"] == "Introduced in House"


def test_summary_row_ids_include_the_version_code():
    """CRS revises summaries in place; keying on the bill alone would make a
    revision invisible to the day-over-day delta."""
    rows = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    assert {r["id"] for r in rows} == {
        "summary:119-hres-1230:00", "summary:119-s-4689:55"
    }


def test_rows_without_a_bill_identity_are_dropped_not_faked():
    body = {"bills": [
        {"congress": 119, "type": "HR", "number": "1", "title": "Real",
         "latestAction": {"actionDate": "2026-08-24", "text": "Introduced."}},
        {"congress": 119, "title": "No type or number"},
    ]}
    rows = cg.parse_bills(body, today=TODAY)
    assert [r["number"] for r in rows] == ["1"]


# --------------------------------------------------------------------------- #
# 5. Merge + selection
# --------------------------------------------------------------------------- #

def test_matching_summary_is_attached_to_its_bill():
    bills = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    summaries = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    merged = cg.merge(bills, summaries)

    s4689 = next(m for m in merged if m["id"] == "bill:119-s-4689")
    assert s4689["crs_summary"].startswith("This bill directs the Department")
    assert "CRS:" in s4689["summary"]
    # S.4689 appears in both feeds and must not be emitted twice.
    assert sum(1 for m in merged if m.get("number") == "4689") == 1


def test_orphan_summary_survives_as_its_own_item():
    """H.Res.1230 is in the summaries feed but not the bill feed — dropping it
    would discard the more informative of the two records."""
    bills = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    summaries = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    merged = cg.merge(bills, summaries)
    orphan = next(m for m in merged if m["number"] == "1230")
    assert orphan["kind"] == "summary"


def test_selection_ranks_by_action_date_not_update_date():
    """All three fixture bills share updateDate 2026-08-24; only their action
    dates differ. Ordering by update date would put the 2026-08-06 action on
    top of two bills acted on this week."""
    rows = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    picked = cg.select_recent(rows, today=TODAY, window_days=3,
                              minimum=0, cap=40)
    assert [p["number"] for p in picked] == ["10134", "10118"]
    assert all(p["within_window"] for p in picked)


def test_summaries_cannot_starve_the_bill_feed():
    """Regression guard for what the first live pull actually did.

    On 2026-08-24 (August recess) every bill's latest action was ≥4 days old
    while five CRS summaries had been published that morning. Pooled into one
    ranking, the summaries took every slot and the section returned zero bills —
    a legislative feed with no legislation in it, reported as a clean success.
    Selection now runs per kind, so the bill floor holds during a recess.

    `today` here is well past every fixture action date: the recess case,
    exaggerated so the assertion does not depend on when the suite runs.
    """
    bills = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    summaries = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    items = cg.compose(bills, summaries, date(2026, 12, 25),
                       window_days=7, min_bills=5,
                       max_bills=30, max_summaries=10)

    kept = [i for i in items if i["kind"] == "bill"]
    assert len(kept) == 3, "the bill floor must survive a stale-bill day"
    assert all(b["within_window"] is False for b in kept)
    # Summaries get no floor of their own — they are enrichment, and a stale
    # one has nothing to add. Only the bill feed is guaranteed a presence.
    assert not [i for i in items if i["kind"] == "summary"]


def test_a_revised_summary_is_attached_once_not_appended_twice():
    """CRS revises summaries in place, so one bill can appear twice in the
    summaries feed. Attaching both would concatenate the CRS text onto the same
    bill's summary line repeatedly."""
    bills = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    summaries = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    revision = dict(summaries[1])          # S.4689 again, an older revision
    revision["id"] = revision["id"] + "-old"
    revision["crs_summary"] = "Superseded text."

    merged = cg.merge(bills, summaries + [revision])
    s4689 = next(m for m in merged if m["id"] == "bill:119-s-4689")
    assert s4689["summary"].count("CRS:") == 1
    assert "Superseded text." not in s4689["summary"]


def test_guard_does_not_crash_on_a_string_congress_number():
    """A schema drift must surface as the diagnosis, not as a TypeError from
    inside the guard that exists to diagnose it."""
    with pytest.raises(UpstreamSchemaError):
        cg.assert_current_congress(["110", 110], 119, "bill")
    cg.assert_current_congress([None, "not-a-number"], 119, "bill")


def test_unparseable_congress_number_degrades_the_link_not_the_pull():
    assert cg.public_url("not-a-number", "HR", 1, "api://x") == "api://x"
    assert cg.public_url(None, "HR", 1) == "https://www.congress.gov/legislation"


def test_compose_orders_the_combined_feed_by_event_date():
    bills = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    summaries = cg.parse_summaries(cg.check_response(200, SUMMARIES_OK), today=TODAY)
    items = cg.compose(bills, summaries, TODAY, window_days=7, min_bills=5,
                       max_bills=30, max_summaries=10)
    dates = [i["event_date"] for i in items]
    assert dates == sorted(dates, reverse=True)
    # S.4689 is in both feeds; merge must not re-emit it as a separate item.
    assert sum(1 for i in items if i.get("number") == "4689") == 1


def test_selection_honours_the_cap():
    rows = cg.parse_bills(cg.check_response(200, BILLS_OK), today=TODAY)
    assert len(cg.select_recent(rows, today=TODAY, window_days=3650,
                                minimum=0, cap=2)) == 2


# --------------------------------------------------------------------------- #
# 6. Capability contract + end-to-end through resolve()
# --------------------------------------------------------------------------- #

def test_key_is_optional_not_required():
    """DEMO_KEY works, so an absent key degrades rather than kills — but it must
    still be declared, or the static capability test cannot see it."""
    assert cg.CongressSection.requires_env == ()
    assert "CONGRESS_GOV_API_KEY" in cg.CongressSection.optional_env


def test_demo_mode_is_labelled_on_every_item(monkeypatch, tmp_path):
    """The degraded mode has to be visible in the output, not just the docstring."""
    monkeypatch.delenv("CONGRESS_GOV_API_KEY", raising=False)
    from worldscope.store import SnapshotStore

    section = cg.CongressSection(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    monkeypatch.setattr(section, "_get", lambda path, **kw: json.loads(
        BILLS_OK if path == "bill" else SUMMARIES_OK
    ))
    items = section.pull()
    assert items and all(i["key_mode"] == "demo" for i in items)


def test_pull_failure_is_recorded_as_broken_not_quiet(monkeypatch, tmp_path):
    """The trust rule, end to end: an auth rejection must land as a failed
    source with no prior snapshot, never as fresh_empty."""
    from worldscope.sections import STATE_NO_DATA
    from worldscope.store import SnapshotStore

    section = cg.CongressSection(store=SnapshotStore(path=tmp_path / "s.sqlite"))

    def _reject(path, **kw):
        return cg.check_response(403, AUTH_INVALID, path)

    monkeypatch.setattr(section, "_get", _reject)
    state = section.resolve(today=TODAY)

    assert state.state == STATE_NO_DATA
    assert state.error_type == "UpstreamAuthError"
    assert state.items == []


def test_unsorted_upstream_fails_the_section(monkeypatch, tmp_path):
    """End to end for the headline gotcha: a 200 full of 2007 bills must fail
    the section rather than populate the brief."""
    from worldscope.sections import STATE_NO_DATA
    from worldscope.store import SnapshotStore

    section = cg.CongressSection(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    monkeypatch.setattr(section, "_get", lambda path, **kw: json.loads(
        BILLS_UNSORTED_HTTP_200 if path == "bill" else SUMMARIES_OK
    ))
    state = section.resolve(today=TODAY)

    assert state.state == STATE_NO_DATA
    assert state.error_type == "UpstreamSchemaError"


def _section_over(monkeypatch, tmp_path, bill_body):
    from worldscope.store import SnapshotStore
    section = cg.CongressSection(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    monkeypatch.setattr(section, "_get", lambda path, **kw: json.loads(
        bill_body if path == "bill" else SUMMARIES_OK
    ))
    return section


def test_stage_escalation_is_flagged_committee_referral_is_not(monkeypatch, tmp_path):
    """Referral is the modal outcome and carries no signal; passage does."""
    monkeypatch.delenv("CONGRESS_GOV_API_KEY", raising=False)
    section = _section_over(monkeypatch, tmp_path, BILLS_PASSAGE)
    structured = section.emit_structured(section.resolve(today=TODAY))

    assert structured["degraded"] is True
    flagged = {a["description"].split(" —")[0] for a in structured["anomalies"]}
    assert "H.R. 9001" in flagged        # "Passed House..."
    assert "H.R. 9002" not in flagged    # "Referred to the Committee on..."
    assert all(a["category"] == "legislative-stage-change"
               for a in structured["anomalies"])


def test_escalation_outside_the_window_is_not_flagged(monkeypatch, tmp_path):
    """S.4689 was placed on the Senate calendar on 2026-08-06 and only appears
    at all because of the recess floor. A stage change from eighteen days ago is
    not an event to raise today, and flagging it would make every out-of-window
    floor item look like breaking news."""
    monkeypatch.delenv("CONGRESS_GOV_API_KEY", raising=False)
    section = _section_over(monkeypatch, tmp_path, BILLS_OK)
    state = section.resolve(today=TODAY)

    s4689 = next(i for i in state.items if i.get("number") == "4689")
    assert s4689["within_window"] is False
    assert "placed on" in s4689["latest_action"].lower()
    assert section.emit_structured(state)["anomalies"] == []


# --------------------------------------------------------------------------- #
# 7. Live smoke — opt-in, skips cleanly without a key or a network
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not os.environ.get("WORLDSCOPE_LIVE"),
    reason="live smoke test; set WORLDSCOPE_LIVE=1 to run "
           "(DEMO_KEY is capped at 10 req/hr, so this is opt-in)",
)
def test_live_pull_returns_current_congress():
    section = cg.CongressSection()
    items = section.pull()
    assert items
    expected = cg.expected_congress()
    assert max(i["congress"] for i in items) >= expected - 1
    assert all(i["url"].startswith("https://www.congress.gov/bill/")
               or i["url"].startswith("https://api.congress.gov/")
               for i in items)
