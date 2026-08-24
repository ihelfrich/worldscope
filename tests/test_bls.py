"""BLS section: response validation, delta semantics, and the keyed/keyless mode.

Three things are being defended here.

**The 200-on-auth-failure trap.** api.bls.gov answers a rejected registration
key with HTTP 200 and a JSON body whose ``status`` is ``REQUEST_NOT_PROCESSED``.
``raise_for_status()`` passes, ``Results`` is ``{}``, and a parser that trusts
the status code yields zero rows — which the brief would record as a quiet day
in U.S. price and labor statistics. Every non-success body below must raise.

**The silent downgrade.** BLS_API_KEY is optional (v1 is keyless), so the
section can run in two modes. It must say which one it ran in, and a key that
is present-and-rejected must fail rather than quietly fall back to v1.

**Delta semantics.** A percent change of the unemployment rate is a category
error: 4.2 to 4.1 is −0.1 percentage points, not −2.4 percent.

Every fixture except ``QUOTA_BODY`` is a real response body captured from
api.bls.gov on 2026-08-24; the successful ones are trimmed to the most recent
13 (resp. 31) observations and re-indented, with no values altered. QUOTA_BODY
is reconstructed from BLS's documented daily-threshold wording and is labelled
as such at its definition — it exists only to prove that a rate-limit message
is not misclassified as a rejected credential.

No network, no key. The one live test skips unless WORLDSCOPE_LIVE_TESTS is set.
"""
from __future__ import annotations

import copy
import json
import os

import pytest
import requests

from worldscope.sections import (
    SectionState,
    SourceUnavailable,
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
    UpstreamSchemaError,
)
from worldscope.sections import bls


# --------------------------------------------------------------------------- #
# Captured bodies
# --------------------------------------------------------------------------- #

# POST /publicAPI/v1/timeseries/data/ with the five watchlist series,
# startyear=2025 endyear=2026. Note two real quirks preserved verbatim: the
# 2025-M10 value of "-" (the lapse-in-appropriations gap) in CPI and the
# unemployment rate, and the "P" preliminary footnotes on the CES/PPI series.
V1_BODY = json.loads(r"""
{
  "status": "REQUEST_SUCCEEDED",
  "responseTime": 91,
  "message": [],
  "Results": {"series": [
    {"seriesID": "CUUR0000SA0", "data": [
      {"year":"2026","period":"M07","periodName":"July","latest":"true","value":"333.918","footnotes":[{}]},
      {"year":"2026","period":"M06","periodName":"June","value":"333.952","footnotes":[{}]},
      {"year":"2026","period":"M05","periodName":"May","value":"335.123","footnotes":[{}]},
      {"year":"2026","period":"M04","periodName":"April","value":"333.020","footnotes":[{}]},
      {"year":"2026","period":"M03","periodName":"March","value":"330.213","footnotes":[{}]},
      {"year":"2026","period":"M02","periodName":"February","value":"326.785","footnotes":[{}]},
      {"year":"2026","period":"M01","periodName":"January","value":"325.252","footnotes":[{}]},
      {"year":"2025","period":"M12","periodName":"December","value":"324.054","footnotes":[{}]},
      {"year":"2025","period":"M11","periodName":"November","value":"324.122","footnotes":[{}]},
      {"year":"2025","period":"M10","periodName":"October","value":"-","footnotes":[{"code":"X","text":"Data unavailable due to the 2025 lapse in appropriations"}]},
      {"year":"2025","period":"M09","periodName":"September","value":"324.800","footnotes":[{}]},
      {"year":"2025","period":"M08","periodName":"August","value":"323.976","footnotes":[{}]},
      {"year":"2025","period":"M07","periodName":"July","value":"323.048","footnotes":[{}]}
    ]},
    {"seriesID": "LNS14000000", "data": [
      {"year":"2026","period":"M07","periodName":"July","latest":"true","value":"4.1","footnotes":[{}]},
      {"year":"2026","period":"M06","periodName":"June","value":"4.2","footnotes":[{}]},
      {"year":"2026","period":"M05","periodName":"May","value":"4.3","footnotes":[{}]},
      {"year":"2026","period":"M04","periodName":"April","value":"4.3","footnotes":[{}]},
      {"year":"2026","period":"M03","periodName":"March","value":"4.3","footnotes":[{}]},
      {"year":"2026","period":"M02","periodName":"February","value":"4.4","footnotes":[{}]},
      {"year":"2026","period":"M01","periodName":"January","value":"4.3","footnotes":[{"code":"12","text":"January 2026 estimates were revised to incorporate updated population controls. For more information, see www.bls.gov/cps/documentation.htm#pop."}]},
      {"year":"2025","period":"M12","periodName":"December","value":"4.4","footnotes":[{}]},
      {"year":"2025","period":"M11","periodName":"November","value":"4.5","footnotes":[{}]},
      {"year":"2025","period":"M10","periodName":"October","value":"-","footnotes":[{"code":"9","text":"Data unavailable due to the 2025 lapse in appropriations."}]},
      {"year":"2025","period":"M09","periodName":"September","value":"4.4","footnotes":[{}]},
      {"year":"2025","period":"M08","periodName":"August","value":"4.3","footnotes":[{}]},
      {"year":"2025","period":"M07","periodName":"July","value":"4.3","footnotes":[{}]}
    ]},
    {"seriesID": "CES0000000001", "data": [
      {"year":"2026","period":"M07","periodName":"July","latest":"true","value":"158858","footnotes":[{"code":"P","text":"preliminary"}]},
      {"year":"2026","period":"M06","periodName":"June","value":"158881","footnotes":[{"code":"P","text":"preliminary"}]},
      {"year":"2026","period":"M05","periodName":"May","value":"158861","footnotes":[{}]},
      {"year":"2026","period":"M04","periodName":"April","value":"158798","footnotes":[{}]},
      {"year":"2026","period":"M03","periodName":"March","value":"158650","footnotes":[{}]},
      {"year":"2026","period":"M02","periodName":"February","value":"158436","footnotes":[{}]},
      {"year":"2026","period":"M01","periodName":"January","value":"158592","footnotes":[{}]},
      {"year":"2025","period":"M12","periodName":"December","value":"158432","footnotes":[{}]},
      {"year":"2025","period":"M11","periodName":"November","value":"158449","footnotes":[{}]},
      {"year":"2025","period":"M10","periodName":"October","value":"158408","footnotes":[{}]},
      {"year":"2025","period":"M09","periodName":"September","value":"158548","footnotes":[{}]},
      {"year":"2025","period":"M08","periodName":"August","value":"158472","footnotes":[{}]},
      {"year":"2025","period":"M07","periodName":"July","value":"158542","footnotes":[{}]}
    ]},
    {"seriesID": "CES0500000003", "data": [
      {"year":"2026","period":"M07","periodName":"July","latest":"true","value":"37.62","footnotes":[{"code":"P","text":"preliminary"}]},
      {"year":"2026","period":"M06","periodName":"June","value":"37.60","footnotes":[{"code":"P","text":"preliminary"}]},
      {"year":"2026","period":"M05","periodName":"May","value":"37.49","footnotes":[{}]},
      {"year":"2026","period":"M04","periodName":"April","value":"37.41","footnotes":[{}]},
      {"year":"2026","period":"M03","periodName":"March","value":"37.35","footnotes":[{}]},
      {"year":"2026","period":"M02","periodName":"February","value":"37.27","footnotes":[{}]},
      {"year":"2026","period":"M01","periodName":"January","value":"37.15","footnotes":[{}]},
      {"year":"2025","period":"M12","periodName":"December","value":"37.02","footnotes":[{}]},
      {"year":"2025","period":"M11","periodName":"November","value":"37.00","footnotes":[{}]},
      {"year":"2025","period":"M10","periodName":"October","value":"36.85","footnotes":[{}]},
      {"year":"2025","period":"M09","periodName":"September","value":"36.70","footnotes":[{}]},
      {"year":"2025","period":"M08","periodName":"August","value":"36.62","footnotes":[{}]},
      {"year":"2025","period":"M07","periodName":"July","value":"36.47","footnotes":[{}]}
    ]},
    {"seriesID": "WPUFD4", "data": [
      {"year":"2026","period":"M07","periodName":"July","latest":"true","value":"156.927","footnotes":[{"code":"P","text":"Preliminary. All indexes are subject to monthly revisions up to four months after original publication."}]},
      {"year":"2026","period":"M06","periodName":"June","value":"157.083","footnotes":[{"code":"P","text":"Preliminary. All indexes are subject to monthly revisions up to four months after original publication."}]},
      {"year":"2026","period":"M05","periodName":"May","value":"157.128","footnotes":[{"code":"P","text":"Preliminary. All indexes are subject to monthly revisions up to four months after original publication."}]},
      {"year":"2026","period":"M04","periodName":"April","value":"156.458","footnotes":[{"code":"P","text":"Preliminary. All indexes are subject to monthly revisions up to four months after original publication."}]},
      {"year":"2026","period":"M03","periodName":"March","value":"154.675","footnotes":[{}]},
      {"year":"2026","period":"M02","periodName":"February","value":"153.156","footnotes":[{}]},
      {"year":"2026","period":"M01","periodName":"January","value":"152.145","footnotes":[{}]},
      {"year":"2025","period":"M12","periodName":"December","value":"150.773","footnotes":[{}]},
      {"year":"2025","period":"M11","periodName":"November","value":"150.580","footnotes":[{}]},
      {"year":"2025","period":"M10","periodName":"October","value":"150.439","footnotes":[{}]},
      {"year":"2025","period":"M09","periodName":"September","value":"150.100","footnotes":[{}]},
      {"year":"2025","period":"M08","periodName":"August","value":"149.466","footnotes":[{}]},
      {"year":"2025","period":"M07","periodName":"July","value":"149.898","footnotes":[{}]}
    ]}
  ]}
}
""")

# Same endpoint, LNS14000000 alone over 2024-2026 — enough monthly changes to
# exercise the z path, which needs more history than a 13-month window gives.
V1_UNEMPLOYMENT_3Y = json.loads(r"""
{
  "status": "REQUEST_SUCCEEDED",
  "message": [],
  "Results": {"series": [
    {"seriesID": "LNS14000000", "data": [
      {"year":"2026","period":"M07","periodName":"July","latest":"true","value":"4.1","footnotes":[{}]},
      {"year":"2026","period":"M06","periodName":"June","value":"4.2","footnotes":[{}]},
      {"year":"2026","period":"M05","periodName":"May","value":"4.3","footnotes":[{}]},
      {"year":"2026","period":"M04","periodName":"April","value":"4.3","footnotes":[{}]},
      {"year":"2026","period":"M03","periodName":"March","value":"4.3","footnotes":[{}]},
      {"year":"2026","period":"M02","periodName":"February","value":"4.4","footnotes":[{}]},
      {"year":"2026","period":"M01","periodName":"January","value":"4.3","footnotes":[{"code":"12","text":"January 2026 estimates were revised to incorporate updated population controls."}]},
      {"year":"2025","period":"M12","periodName":"December","value":"4.4","footnotes":[{}]},
      {"year":"2025","period":"M11","periodName":"November","value":"4.5","footnotes":[{}]},
      {"year":"2025","period":"M10","periodName":"October","value":"-","footnotes":[{"code":"9","text":"Data unavailable due to the 2025 lapse in appropriations."}]},
      {"year":"2025","period":"M09","periodName":"September","value":"4.4","footnotes":[{}]},
      {"year":"2025","period":"M08","periodName":"August","value":"4.3","footnotes":[{}]},
      {"year":"2025","period":"M07","periodName":"July","value":"4.3","footnotes":[{}]},
      {"year":"2025","period":"M06","periodName":"June","value":"4.1","footnotes":[{}]},
      {"year":"2025","period":"M05","periodName":"May","value":"4.3","footnotes":[{}]},
      {"year":"2025","period":"M04","periodName":"April","value":"4.2","footnotes":[{}]},
      {"year":"2025","period":"M03","periodName":"March","value":"4.2","footnotes":[{}]},
      {"year":"2025","period":"M02","periodName":"February","value":"4.2","footnotes":[{}]},
      {"year":"2025","period":"M01","periodName":"January","value":"4.0","footnotes":[{}]},
      {"year":"2024","period":"M12","periodName":"December","value":"4.1","footnotes":[{}]},
      {"year":"2024","period":"M11","periodName":"November","value":"4.2","footnotes":[{}]},
      {"year":"2024","period":"M10","periodName":"October","value":"4.1","footnotes":[{}]},
      {"year":"2024","period":"M09","periodName":"September","value":"4.1","footnotes":[{}]},
      {"year":"2024","period":"M08","periodName":"August","value":"4.2","footnotes":[{}]},
      {"year":"2024","period":"M07","periodName":"July","value":"4.2","footnotes":[{}]},
      {"year":"2024","period":"M06","periodName":"June","value":"4.1","footnotes":[{}]},
      {"year":"2024","period":"M05","periodName":"May","value":"3.9","footnotes":[{}]},
      {"year":"2024","period":"M04","periodName":"April","value":"3.9","footnotes":[{}]},
      {"year":"2024","period":"M03","periodName":"March","value":"3.9","footnotes":[{}]},
      {"year":"2024","period":"M02","periodName":"February","value":"3.9","footnotes":[{}]},
      {"year":"2024","period":"M01","periodName":"January","value":"3.7","footnotes":[{}]}
    ]}
  ]}
}
""")

# HTTP 200. This is the whole reason the section status-checks every body:
# nothing at the transport layer distinguishes this from a successful call.
AUTH_FAILURE_BODY = json.loads(r"""
{"status": "REQUEST_NOT_PROCESSED", "responseTime": 0,
 "message": ["The key:INVALID provided by the User is invalid. Please provide a proper key for the operation to be successful"],
 "Results": {}}
""")

# HTTP 200, and note the status: BLS calls a rejected series id a *success* and
# hides the rejection in message[] with an empty data array.
INVALID_SERIES_BODY = json.loads(r"""
{"status": "REQUEST_SUCCEEDED", "responseTime": 70,
 "message": ["Invalid Series for Series NOTASERIES1"],
 "Results": {"series": [{"seriesID": "NOTASERIES1", "data": []}]}}
""")

# HTTP 200 for a request BLS could not interpret at all — a success with an
# empty universe, which is precisely the shape a broken adapter would emit.
EMPTY_RESULTS_BODY = json.loads(r"""
{"status": "REQUEST_SUCCEEDED", "responseTime": 73, "message": [],
 "Results": {"series": []}}
""")

# NOT CAPTURED. Reconstructed from BLS's documented daily-threshold response
# (v1 allows 25 queries/day/IP) rather than by exhausting the quota against the
# live service. Its only job is to prove the quota/auth discrimination: the
# message names a key, so a naive "does it mention a key" check would call this
# a rejected credential and send an operator hunting for a secret that is fine.
QUOTA_BODY = {
    "status": "REQUEST_NOT_PROCESSED",
    "responseTime": 0,
    "message": ["The daily threshold for total requests has been exceeded for "
                "the key: 0123456789abcdef. Please try again tomorrow."],
    "Results": {},
}


def _items_by_series(payload=V1_BODY, **kwargs) -> dict:
    return {it["series_id"]: it for it in bls.parse_response(payload, **kwargs)}


# --------------------------------------------------------------------------- #
# 1. The 200-on-failure bodies must raise, never parse to []
# --------------------------------------------------------------------------- #

def test_auth_failure_body_raises_upstream_auth_error():
    """The mandatory one. HTTP 200, status REQUEST_NOT_PROCESSED, empty Results."""
    with pytest.raises(UpstreamAuthError) as exc:
        bls.check_status(AUTH_FAILURE_BODY)
    assert "REQUEST_NOT_PROCESSED" in str(exc.value)


def test_auth_failure_never_reaches_the_parser():
    """parse_response validates before it reads, so a rejected key cannot come
    back as a well-formed empty day."""
    with pytest.raises(UpstreamAuthError):
        bls.parse_response(AUTH_FAILURE_BODY)


def test_auth_error_is_a_source_unavailable():
    """The base state machine keys off SourceUnavailable to record the run as
    failed rather than fresh_empty."""
    assert issubclass(UpstreamAuthError, SourceUnavailable)


def test_quota_message_is_a_rate_limit_not_a_rejected_credential():
    with pytest.raises(UpstreamHTTPError) as exc:
        bls.check_status(QUOTA_BODY)
    assert not isinstance(exc.value, UpstreamAuthError)
    assert "rate limited" in str(exc.value)


def test_unrecognized_failure_status_still_raises():
    body = {"status": "REQUEST_NOT_PROCESSED", "message": ["something new"],
            "Results": {}}
    with pytest.raises(UpstreamHTTPError):
        bls.check_status(body)


def test_invalid_series_inside_a_succeeded_body_raises_schema_error():
    with pytest.raises(UpstreamSchemaError) as exc:
        bls.check_status(INVALID_SERIES_BODY)
    assert "NOTASERIES1" in str(exc.value)


def test_empty_series_list_raises_rather_than_returning_no_items():
    with pytest.raises(UpstreamSchemaError):
        bls.parse_response(EMPTY_RESULTS_BODY)


def test_missing_series_is_named_in_the_error():
    body = copy.deepcopy(V1_BODY)
    body["Results"]["series"] = [
        s for s in body["Results"]["series"] if s["seriesID"] != "WPUFD4"
    ]
    with pytest.raises(UpstreamSchemaError) as exc:
        bls.parse_response(body)
    assert "WPUFD4" in str(exc.value)


def test_series_with_every_value_suppressed_raises():
    """A gap is fine; an all-gap series is a broken source, not a quiet month."""
    body = copy.deepcopy(V1_BODY)
    for s in body["Results"]["series"]:
        if s["seriesID"] == "CES0000000001":
            for point in s["data"]:
                point["value"] = "-"
    with pytest.raises(UpstreamSchemaError) as exc:
        bls.parse_response(body)
    assert "CES0000000001" in str(exc.value)


def test_reshaped_envelope_raises_schema_error():
    with pytest.raises(UpstreamSchemaError):
        bls.series_blocks({"status": "REQUEST_SUCCEEDED", "Results": []})
    with pytest.raises(UpstreamSchemaError):
        bls.series_blocks({"status": "REQUEST_SUCCEEDED",
                           "Results": {"series": "not-a-list"}})


# --------------------------------------------------------------------------- #
# 2. Transport failures
# --------------------------------------------------------------------------- #

class _FakeResponse:
    def __init__(self, status_code=200, body="", payload=None):
        self.status_code = status_code
        self.text = body
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def test_non_json_body_raises_parse_error(monkeypatch):
    monkeypatch.setattr(
        bls.requests, "post",
        lambda *a, **k: _FakeResponse(200, "<html>maintenance</html>"))
    with pytest.raises(UpstreamParseError) as exc:
        bls.BlsSection()._post(bls.API_V1, {})
    assert "maintenance" in str(exc.value)


def test_non_200_raises_http_error(monkeypatch):
    monkeypatch.setattr(bls.requests, "post",
                        lambda *a, **k: _FakeResponse(503, "unavailable"))
    with pytest.raises(UpstreamHTTPError) as exc:
        bls.BlsSection()._post(bls.API_V1, {})
    assert "503" in str(exc.value)


def test_connection_failure_raises_http_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("name resolution failed")
    monkeypatch.setattr(bls.requests, "post", boom)
    with pytest.raises(UpstreamHTTPError) as exc:
        bls.BlsSection()._post(bls.API_V1, {})
    assert "ConnectionError" in str(exc.value)


# --------------------------------------------------------------------------- #
# 3. Levels and deltas
# --------------------------------------------------------------------------- #

def test_every_watchlist_series_yields_exactly_one_item():
    items = bls.parse_response(V1_BODY)
    assert len(items) == len(bls.WATCHLIST)
    assert {it["series_id"] for it in items} == {s.series_id for s in bls.WATCHLIST}


def test_cpi_carries_latest_level_and_both_deltas():
    cpi = _items_by_series()["CUUR0000SA0"]
    assert cpi["period"] == "2026-07"
    assert cpi["value"] == pytest.approx(333.918)
    # 333.918 / 333.952 - 1 and 333.918 / 323.048 - 1
    assert cpi["mom_pct"] == pytest.approx(-0.0102, abs=1e-3)
    assert cpi["yoy_pct"] == pytest.approx(3.3648, abs=1e-3)
    assert cpi["delta_kind"] == bls.DELTA_PCT
    assert "+3.36%" in cpi["summary"]


def test_unemployment_moves_in_percentage_points_not_percent():
    """4.2 -> 4.1 is -0.1 pp. Reporting it as -2.38% is the category error this
    watchlist's delta_kind exists to prevent."""
    ur = _items_by_series()["LNS14000000"]
    assert ur["delta_kind"] == bls.DELTA_PP
    assert ur["mom_change"] == pytest.approx(-0.1)
    assert ur["yoy_change"] == pytest.approx(-0.2)
    # Deliberately withheld so no downstream consumer can quote it.
    assert ur["mom_pct"] is None and ur["yoy_pct"] is None
    assert "-0.1 pp" in ur["summary"]
    assert "-2.3" not in ur["summary"]


def test_payrolls_lead_with_the_level_change_in_thousands():
    jobs = _items_by_series()["CES0000000001"]
    assert jobs["delta_kind"] == bls.DELTA_LEVEL
    assert jobs["mom_change"] == pytest.approx(-23.0)     # 158858 - 158881
    assert jobs["yoy_change"] == pytest.approx(316.0)     # 158858 - 158542
    assert "-23 thousands of jobs" in jobs["summary"]
    # The percent change is still available, just not the headline.
    assert jobs["mom_pct"] == pytest.approx(-0.0145, abs=1e-3)


def test_a_level_alone_is_never_emitted_without_a_delta():
    for it in bls.parse_response(V1_BODY):
        assert it["mom_change"] is not None, it["series_id"]
        assert it["yoy_change"] is not None, it["series_id"]


def test_preliminary_footnote_is_surfaced():
    items = _items_by_series()
    assert items["CES0000000001"]["preliminary"] is True
    assert "preliminary" in items["CES0000000001"]["summary"]
    assert items["CUUR0000SA0"]["preliminary"] is False


def test_seasonal_adjustment_status_is_stated_on_every_item():
    items = _items_by_series()
    cpi = items["CUUR0000SA0"]
    assert cpi["seasonally_adjusted"] is False
    # The caveat must be explicit: a NSA month-over-month is not the figure the
    # CPI release leads with.
    assert "NOT seasonally adjusted" in cpi["summary"]
    assert "seasonal movement" in cpi["summary"]
    ur = items["LNS14000000"]
    assert ur["seasonally_adjusted"] is True
    assert "NOT seasonally adjusted" not in ur["summary"]


def test_monthly_datum_is_dated_to_its_reference_month():
    assert _items_by_series()["CUUR0000SA0"]["date"] == "2026-07-01"


# --------------------------------------------------------------------------- #
# 4. Period handling: gaps, annual averages, ordering
# --------------------------------------------------------------------------- #

def test_appropriations_gap_is_dropped_not_parsed_as_zero():
    block = {s["seriesID"]: s for s in V1_BODY["Results"]["series"]}["LNS14000000"]
    obs = bls.monthly_observations(block)
    assert len(obs) == 12                      # 13 rows, one of them "-"
    assert (2025, 10) not in {(o.year, o.month) for o in obs}
    assert all(o.value > 0 for o in obs)


def test_gap_is_skipped_rather_than_bridged():
    """September -> November must not be counted as a month-over-month step."""
    block = {s["seriesID"]: s for s in V1_BODY["Results"]["series"]}["LNS14000000"]
    obs = bls.monthly_observations(block)
    changes = bls.change_series(obs, 1, bls.DELTA_PP)
    # 12 observations spanning 13 calendar months: 10 adjacent pairs survive,
    # not 11 — the two pairs touching the missing October are both dropped.
    assert len(changes) == 10


def test_annual_average_period_is_excluded():
    """M13 is the annual average. Sorted in among the months it would be read
    as a 13th month and become 'latest'."""
    body = copy.deepcopy(V1_BODY)
    for s in body["Results"]["series"]:
        if s["seriesID"] == "CUUR0000SA0":
            s["data"].insert(0, {"year": "2026", "period": "M13",
                                 "periodName": "Annual", "value": "999.999",
                                 "footnotes": [{}]})
    cpi = _items_by_series(body)["CUUR0000SA0"]
    assert cpi["period"] == "2026-07"
    assert cpi["value"] == pytest.approx(333.918)


def test_observations_are_sorted_not_assumed():
    """BLS happens to return newest-first; that ordering is not documented."""
    body = copy.deepcopy(V1_BODY)
    for s in body["Results"]["series"]:
        s["data"].reverse()
    assert _items_by_series(body)["CUUR0000SA0"]["period"] == "2026-07"


def test_month_shift_crosses_the_year_boundary():
    assert bls._shift(2026, 1, 1) == (2025, 12)
    assert bls._shift(2026, 1, 12) == (2025, 1)
    assert bls._shift(2026, 7, 12) == (2025, 7)


# --------------------------------------------------------------------------- #
# 5. change_z — a standardization, explicitly not a test statistic
# --------------------------------------------------------------------------- #

def test_z_is_withheld_when_history_is_too_short():
    """One year of data gives one YoY change; reporting a z off that would be
    theatre."""
    cpi = _items_by_series()["CUUR0000SA0"]
    assert cpi["change_z_basis"] == "yoy"     # NSA series standardize on YoY
    assert cpi["change_z"] is None


def test_z_is_computed_over_three_years_of_real_data():
    item = bls.parse_response(V1_UNEMPLOYMENT_3Y, (bls.WATCHLIST[2],))[0]
    assert item["change_z_basis"] == "mom"    # SA series standardize on MoM
    assert item["change_z_n"] == 28
    assert item["change_z"] == pytest.approx(-1.04, abs=0.01)
    assert "z=" in item["summary"]


def test_zscore_excludes_the_current_observation_and_flat_history():
    assert bls.zscore([0.0] * 13) is None            # no dispersion
    assert bls.zscore([1.0, 2.0]) is None            # not enough history
    changes = [0.0] * 12 + [1.0]
    assert bls.zscore(changes) is None               # sd of prior is 0
    changes = [0.0, 1.0] * 6 + [10.0]
    assert bls.zscore(changes) > 2.0


def test_anomaly_is_emitted_only_beyond_the_threshold():
    section = bls.BlsSection.__new__(bls.BlsSection)   # no SnapshotStore needed
    section.store = None

    def _state(z):
        return SectionState(
            section_id="bls", title="t", emoji="🧮", state="fresh",
            items=[{"id": "bls:X:2026-07", "label": "Test series", "period": "2026-07",
                    "change_z": z, "change_z_basis": "mom", "change_z_n": 28,
                    "api_mode": "keyless — BLS_API_KEY not set"}],
            new=[], comparison_date=None, source_date="2026-07-01")

    assert section.emit_structured(_state(1.4))["anomalies"] == []
    anomalies = section.emit_structured(_state(-3.1))["anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["z_score"] == pytest.approx(3.1)
    # The description must not overclaim significance.
    assert "not a test" in anomalies[0]["description"]


# --------------------------------------------------------------------------- #
# 6. Keyed vs keyless — the mode must never be silent
# --------------------------------------------------------------------------- #

class _Recorder:
    """Stands in for BlsSection._post, capturing what was sent where."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, body):
        self.calls.append((url, body))
        return self.payload


def test_without_a_key_the_section_uses_v1_and_says_so(monkeypatch):
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    rec = _Recorder(V1_BODY)
    monkeypatch.setattr(bls.BlsSection, "_post", lambda self, url, body: rec(url, body))

    items = bls.BlsSection.pull(bls.BlsSection.__new__(bls.BlsSection))

    url, body = rec.calls[0]
    assert url == bls.API_V1
    assert "registrationkey" not in body
    assert all(it["api_version"] == "v1" for it in items)
    assert all("keyless" in it["summary"] for it in items)
    assert all("BLS_API_KEY not set" in it["api_mode"] for it in items)


def test_with_a_key_the_section_uses_v2_and_sends_it(monkeypatch):
    monkeypatch.setenv("BLS_API_KEY", "deadbeefdeadbeefdeadbeefdeadbeef")
    rec = _Recorder(V1_BODY)
    monkeypatch.setattr(bls.BlsSection, "_post", lambda self, url, body: rec(url, body))

    items = bls.BlsSection.pull(bls.BlsSection.__new__(bls.BlsSection))

    url, body = rec.calls[0]
    assert url == bls.API_V2
    assert body["registrationkey"] == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert all(it["api_version"] == "v2" for it in items)
    assert all("keyed" in it["summary"] for it in items)


def test_a_rejected_key_fails_instead_of_falling_back_to_v1(monkeypatch):
    """The downgrade this repo exists to prevent: answering from the keyless
    endpoint would report success while the configured credential was dead."""
    monkeypatch.setenv("BLS_API_KEY", "INVALID")
    rec = _Recorder(AUTH_FAILURE_BODY)
    monkeypatch.setattr(bls.BlsSection, "_post", lambda self, url, body: rec(url, body))

    with pytest.raises(UpstreamAuthError):
        bls.BlsSection.pull(bls.BlsSection.__new__(bls.BlsSection))

    assert len(rec.calls) == 1
    assert rec.calls[0][0] == bls.API_V2


def test_pull_requests_three_calendar_years(monkeypatch):
    """Year-over-year needs 13 months, and in January the newest published month
    can still fall in the year before last."""
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    rec = _Recorder(V1_BODY)
    monkeypatch.setattr(bls.BlsSection, "_post", lambda self, url, body: rec(url, body))

    bls.BlsSection.pull(bls.BlsSection.__new__(bls.BlsSection))

    _, body = rec.calls[0]
    assert int(body["endyear"]) - int(body["startyear"]) == 2
    assert body["seriesid"] == [s.series_id for s in bls.WATCHLIST]


def test_structured_payload_carries_the_mode(monkeypatch):
    section = bls.BlsSection.__new__(bls.BlsSection)
    state = SectionState(
        section_id="bls", title="t", emoji="🧮", state="fresh",
        items=[{"id": "bls:X:2026-07", "api_mode": "keyless — BLS_API_KEY not set"}],
        new=[], comparison_date=None, source_date="2026-07-01")
    assert "keyless" in section.emit_structured(state)["api_mode"]


# --------------------------------------------------------------------------- #
# 7. Capability contract
# --------------------------------------------------------------------------- #

def test_key_is_optional_not_required():
    """v1 is genuinely keyless, so declaring BLS_API_KEY as requires_env would
    disable a working source. optional_env is what makes tests/test_capabilities
    accept the os.environ.get in pull()."""
    assert bls.BlsSection.optional_env == ("BLS_API_KEY",)
    assert bls.BlsSection.requires_env == ()
    assert bls.BlsSection.requires_packages == ()


# --------------------------------------------------------------------------- #
# 8. Live smoke (opt-in)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not os.environ.get("WORLDSCOPE_LIVE_TESTS"),
                    reason="set WORLDSCOPE_LIVE_TESTS=1 to hit api.bls.gov")
def test_live_keyless_v1_returns_every_watchlist_series():
    """Runs against the real keyless endpoint. Also the canary for a watchlist
    entry BLS has retired — parse_response raises rather than shrinking."""
    items = bls.BlsSection().pull()
    assert {it["series_id"] for it in items} == {s.series_id for s in bls.WATCHLIST}
    assert all(it["value"] is not None for it in items)
