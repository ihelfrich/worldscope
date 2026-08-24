"""BEA (Bureau of Economic Analysis) NIPA adapter.

The whole reason this file exists in this shape: **BEA returns HTTP 200 on an
authentication failure.** The body carries the rejection, `raise_for_status()`
passes, and `Results["Data"]` is simply missing — so a parser that trusts the
status code yields zero items and the pipeline records a quiet day at the
Bureau of Economic Analysis. That is the exact silent-sensor failure this
codebase was rebuilt to eliminate, so the auth-error body below is a verbatim
capture (probed 2026-08-24) and several tests assert that it *raises* rather
than returning [].

Every test here runs with no network and no BEA_API_KEY. The one live-smoke
test skips cleanly when the key is absent.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import requests

from worldscope.sections import (
    MissingCredential,
    STATE_NO_DATA,
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
    UpstreamSchemaError,
)
from worldscope.sections import bea
from worldscope.store import SnapshotStore


# --------------------------------------------------------------------------- #
# Fixtures — response bodies
# --------------------------------------------------------------------------- #

# VERBATIM capture, 2026-08-24: what BEA sends for a bad UserID, with HTTP 200.
AUTH_ERROR_BODY = """
{"BEAAPI":{"Request":{"RequestParam":[
  {"ParameterName":"USERID","ParameterValue":"BADKEY"},
  {"ParameterName":"METHOD","ParameterValue":"GETDATA"},
  {"ParameterName":"RESULTFORMAT","ParameterValue":"JSON"}]},
 "Results":{"Error":{"APIErrorCode":"1",
   "APIErrorDescription":"Invalid Request - Invalid API UserId."}}}}
"""

# The other place BEA puts errors: request-level, above Results.
TOP_LEVEL_AUTH_BODY = """
{"BEAAPI":{"Request":{"RequestParam":[]},
 "Error":{"APIErrorCode":"1",
   "APIErrorDescription":"Invalid Request - Invalid API UserId.",
   "ErrorDetail":{"Description":"The API UserId provided is not registered."}}}}
"""

# A non-credential API error: same envelope, different meaning.
BAD_TABLE_BODY = """
{"BEAAPI":{"Request":{"RequestParam":[]},
 "Results":{"Error":{"APIErrorCode":"201",
   "APIErrorDescription":"The Table requested is not available."}}}}
"""

CATALOG_BODY = """
{"BEAAPI":{"Request":{"RequestParam":[
  {"ParameterName":"DATASETNAME","ParameterValue":"NIPA"},
  {"ParameterName":"PARAMETERNAME","ParameterValue":"TABLENAME"}]},
 "Results":{"ParamValue":[
  {"TableName":"T10101","Description":"Table 1.1.1. Percent Change From Preceding Period in Real Gross Domestic Product"},
  {"TableName":"T10102","Description":"Table 1.1.2. Contributions to Percent Change in Real Gross Domestic Product"},
  {"TableName":"T10105","Description":"Table 1.1.5. Gross Domestic Product"},
  {"TableName":"T20600","Description":"Table 2.6. Personal Income and Its Disposition, Monthly"},
  {"TableName":"T30100","Description":"Table 3.1. Government Current Receipts and Expenditures"}]}}}
"""

# Quarterly GetData, trimmed to two periods so the latest-period selection is
# actually exercised. Values keep BEA's display formatting (thousands commas,
# "..." for a value not published).
GDP_GROWTH_BODY = """
{"BEAAPI":{"Request":{"RequestParam":[
  {"ParameterName":"TABLENAME","ParameterValue":"T10101"},
  {"ParameterName":"FREQUENCY","ParameterValue":"Q"}]},
 "Results":{
  "Statistic":"NIPA Table",
  "UTCProductionTime":"2026-08-24T11:02:17.113",
  "Dimensions":[
    {"Name":"TableName","DataType":"string","IsValue":"0"},
    {"Name":"SeriesCode","DataType":"string","IsValue":"0"},
    {"Name":"LineNumber","DataType":"string","IsValue":"0"},
    {"Name":"LineDescription","DataType":"string","IsValue":"0"},
    {"Name":"TimePeriod","DataType":"string","IsValue":"0"},
    {"Name":"METRIC_NAME","DataType":"string","IsValue":"0"},
    {"Name":"CL_UNIT","DataType":"string","IsValue":"0"},
    {"Name":"UNIT_MULT","DataType":"numeric","IsValue":"0"},
    {"Name":"DataValue","DataType":"numeric","IsValue":"1"},
    {"Name":"NoteRef","DataType":"string","IsValue":"0"}],
  "Data":[
   {"TableName":"T10101","SeriesCode":"A191RL","LineNumber":"1","LineDescription":"Gross domestic product","TimePeriod":"2026Q1","METRIC_NAME":"Percent change, annual rate","CL_UNIT":"Percent change, annual rate","UNIT_MULT":"0","DataValue":"1.4","NoteRef":"T10101"},
   {"TableName":"T10101","SeriesCode":"DPCERL","LineNumber":"2","LineDescription":"Personal consumption expenditures","TimePeriod":"2026Q1","METRIC_NAME":"Percent change, annual rate","CL_UNIT":"Percent change, annual rate","UNIT_MULT":"0","DataValue":"1.9","NoteRef":"T10101"},
   {"TableName":"T10101","SeriesCode":"DGDSRL","LineNumber":"3","LineDescription":"Goods","TimePeriod":"2026Q1","METRIC_NAME":"Percent change, annual rate","CL_UNIT":"Percent change, annual rate","UNIT_MULT":"0","DataValue":"0.4","NoteRef":"T10101"},
   {"TableName":"T10101","SeriesCode":"A191RL","LineNumber":"1","LineDescription":"Gross domestic product","TimePeriod":"2026Q2","METRIC_NAME":"Percent change, annual rate","CL_UNIT":"Percent change, annual rate","UNIT_MULT":"0","DataValue":"-0.8","NoteRef":"T10101"},
   {"TableName":"T10101","SeriesCode":"DPCERL","LineNumber":"2","LineDescription":"Personal consumption expenditures","TimePeriod":"2026Q2","METRIC_NAME":"Percent change, annual rate","CL_UNIT":"Percent change, annual rate","UNIT_MULT":"0","DataValue":"2.1","NoteRef":"T10101"},
   {"TableName":"T10101","SeriesCode":"DGDSRL","LineNumber":"3","LineDescription":"Goods","TimePeriod":"2026Q2","METRIC_NAME":"Percent change, annual rate","CL_UNIT":"Percent change, annual rate","UNIT_MULT":"0","DataValue":"...","NoteRef":"T10101"},
   {"TableName":"T10101","SeriesCode":"A191RX","LineNumber":"29","LineDescription":"Gross domestic product, current dollars","TimePeriod":"2026Q2","METRIC_NAME":"Current Dollars","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"31,204,118","NoteRef":"T10101"}]}}}
"""

# Monthly account — same envelope, "2026M06"-style periods.
PERSONAL_INCOME_BODY = """
{"BEAAPI":{"Request":{"RequestParam":[
  {"ParameterName":"TABLENAME","ParameterValue":"T20600"},
  {"ParameterName":"FREQUENCY","ParameterValue":"M"}]},
 "Results":{
  "Statistic":"NIPA Table",
  "Data":[
   {"TableName":"T20600","SeriesCode":"A065RC","LineNumber":"1","LineDescription":"Personal income","TimePeriod":"2026M06","METRIC_NAME":"Current Dollars","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"26,410,502","NoteRef":"T20600"},
   {"TableName":"T20600","SeriesCode":"A067RC","LineNumber":"27","LineDescription":"Disposable personal income","TimePeriod":"2026M06","METRIC_NAME":"Current Dollars","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"23,015,880","NoteRef":"T20600"},
   {"TableName":"T20600","SeriesCode":"A068RC","LineNumber":"28","LineDescription":"Personal outlays","TimePeriod":"2026M06","METRIC_NAME":"Current Dollars","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"21,904,301","NoteRef":"T20600"},
   {"TableName":"T20600","SeriesCode":"A072RC","LineNumber":"34","LineDescription":"Personal saving as a percentage of disposable personal income","TimePeriod":"2026M06","METRIC_NAME":"Percent","CL_UNIT":"Percent","UNIT_MULT":"0","DataValue":"4.8","NoteRef":"T20600"},
   {"TableName":"T20600","SeriesCode":"A065RC","LineNumber":"1","LineDescription":"Personal income","TimePeriod":"2026M05","METRIC_NAME":"Current Dollars","CL_UNIT":"Level","UNIT_MULT":"6","DataValue":"26,301,177","NoteRef":"T20600"}]}}}
"""


def body(text: str) -> dict:
    return json.loads(text)


def gdp_rows() -> list[dict]:
    return bea.parse_data(body(GDP_GROWTH_BODY), table="T10101")


# --------------------------------------------------------------------------- #
# The HTTP-200 auth failure — the thing this adapter exists to survive
# --------------------------------------------------------------------------- #

def test_auth_error_body_raises_instead_of_yielding_no_items():
    """HTTP 200 + an error body must not read as 'BEA had nothing today'."""
    with pytest.raises(UpstreamAuthError) as exc:
        bea.results_blocks(body(AUTH_ERROR_BODY))
    assert "Invalid API UserId" in str(exc.value)


def test_auth_error_reaches_the_data_parser_as_a_raise():
    """parse_data() is the call site pull() actually uses; it must raise too."""
    with pytest.raises(UpstreamAuthError):
        bea.parse_data(body(AUTH_ERROR_BODY), table="T10101")


def test_auth_error_also_detected_above_results():
    with pytest.raises(UpstreamAuthError):
        bea.results_blocks(body(TOP_LEVEL_AUTH_BODY))


def test_auth_error_is_reported_with_the_provider_error_code():
    with pytest.raises(UpstreamAuthError) as exc:
        bea.results_blocks(body(AUTH_ERROR_BODY))
    assert "APIErrorCode 1" in str(exc.value)


def test_non_credential_api_error_is_not_misreported_as_auth():
    """A bad TableName is a broken request, not a rejected key — but it is
    still a failure, never an empty day."""
    with pytest.raises(UpstreamHTTPError) as exc:
        bea.results_blocks(body(BAD_TABLE_BODY))
    assert not isinstance(exc.value, UpstreamAuthError)
    assert "201" in str(exc.value)


def test_catalog_parser_raises_on_the_auth_body():
    """The credential is spent on GetParameterValues first; that call must fail
    loudly rather than returning an empty catalog."""
    with pytest.raises(UpstreamAuthError):
        bea.parse_table_catalog(body(AUTH_ERROR_BODY))


# --------------------------------------------------------------------------- #
# Envelope shapes
# --------------------------------------------------------------------------- #

def test_results_as_a_list_is_accepted():
    """BEA returns Results as an array when it splits a multi-table answer."""
    payload = body(GDP_GROWTH_BODY)
    payload["BEAAPI"]["Results"] = [payload["BEAAPI"]["Results"]]
    assert len(bea.parse_data(payload, table="T10101")) == 7


def test_error_inside_a_list_shaped_results_still_raises():
    payload = {"BEAAPI": {"Results": [
        {"Data": []},
        {"Error": {"APIErrorCode": "1",
                   "APIErrorDescription": "Invalid Request - Invalid API UserId."}},
    ]}}
    with pytest.raises(UpstreamAuthError):
        bea.results_blocks(payload)


@pytest.mark.parametrize("payload", [
    [],                                   # JSON array where an object is due
    {"nope": 1},                          # no BEAAPI envelope
    {"BEAAPI": {"Request": {}}},          # envelope with no Results
    {"BEAAPI": {"Results": ["a string"]}},
])
def test_unrecognized_envelope_raises_schema_error(payload):
    with pytest.raises(UpstreamSchemaError):
        bea.results_blocks(payload)


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

def test_catalog_maps_table_names_to_descriptions():
    cat = bea.parse_table_catalog(body(CATALOG_BODY))
    assert cat["T10101"].startswith("Table 1.1.1.")
    assert "T20600" in cat


def test_empty_catalog_is_a_failure_not_a_quiet_day():
    with pytest.raises(UpstreamSchemaError):
        bea.parse_table_catalog({"BEAAPI": {"Results": {"ParamValue": []}}})


def test_catalog_accepts_the_key_desc_alias():
    cat = bea.parse_table_catalog(
        {"BEAAPI": {"Results": {"ParamValue": [{"Key": "T10101", "Desc": "GDP"}]}}}
    )
    assert cat == {"T10101": "GDP"}


# --------------------------------------------------------------------------- #
# Data parsing
# --------------------------------------------------------------------------- #

def test_parse_data_extracts_the_contract_fields():
    rows = gdp_rows()
    assert len(rows) == 7
    first = rows[0]
    assert first["line_description"] == "Gross domestic product"
    assert first["period"] == "2026Q1"
    assert first["value"] == 1.4
    assert first["units"] == "Percent change, annual rate"
    assert first["series_code"] == "A191RL"
    assert first["line"] == "1"


def test_display_commas_are_stripped_from_values():
    row = next(r for r in gdp_rows() if r["line"] == "29")
    assert row["value"] == 31204118.0
    assert row["value_text"] == "31,204,118"     # BEA's formatting is preserved
    assert row["unit_mult"] == "6"


def test_unpublished_value_is_none_but_keeps_its_marker():
    row = next(r for r in gdp_rows()
               if r["period"] == "2026Q2" and r["line"] == "3")
    assert row["value"] is None
    assert row["value_text"] == "..."


@pytest.mark.parametrize("text,expected", [
    ("1.4", 1.4),
    ("-0.8", -0.8),
    ("31,204,118", 31204118.0),
    ("(1.2)", -1.2),      # parenthesized negative
    ("...", None),
    ("(NA)", None),
    ("", None),
    (None, None),
    ("banana", None),
])
def test_parse_value(text, expected):
    assert bea.parse_value(text) == expected


def test_missing_data_block_raises():
    """BEA answered, but with no observations block at all."""
    with pytest.raises(UpstreamSchemaError):
        bea.parse_data({"BEAAPI": {"Results": {"Statistic": "NIPA Table"}}},
                       table="T10101")


def test_empty_data_block_raises_rather_than_reporting_a_quiet_quarter():
    """A two-year window of a published national account is never empty."""
    with pytest.raises(UpstreamSchemaError) as exc:
        bea.parse_data({"BEAAPI": {"Results": {"Data": []}}}, table="T10101")
    assert "T10101" in str(exc.value)


def test_units_disappearing_from_the_schema_raises():
    """Units are part of what this section promises; losing them silently
    would publish bare numbers with no scale."""
    payload = body(GDP_GROWTH_BODY)
    for row in payload["BEAAPI"]["Results"]["Data"]:
        row.pop("METRIC_NAME")
        row.pop("CL_UNIT")
    with pytest.raises(UpstreamSchemaError):
        bea.parse_data(payload, table="T10101")


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("period,expected", [
    ("2026Q1", "2026-03-31"),
    ("2026Q2", "2026-06-30"),
    ("2026Q4", "2026-12-31"),
    ("2026M06", "2026-06-30"),
    ("2024M02", "2024-02-29"),   # leap year, via calendar not a lookup table
    ("2026", "2026-12-31"),
    ("garbage", ""),
    ("", ""),
])
def test_period_to_date(period, expected):
    assert bea.period_to_date(period) == expected


def test_period_ordering_is_numeric_not_lexical():
    periods = ["2025Q4", "2026Q1", "2026Q2"]
    assert max(periods, key=bea.period_key) == "2026Q2"
    months = ["2026M9", "2026M10"]   # unpadded month would sort wrong as text
    assert max(months, key=bea.period_key) == "2026M10"


def test_unparseable_period_never_wins_the_latest_slot():
    assert max(["2026Q1", "ZZZZ"], key=bea.period_key) == "2026Q1"


# --------------------------------------------------------------------------- #
# Item construction
# --------------------------------------------------------------------------- #

def gdp_items() -> list[dict]:
    spec = next(s for s in bea.WATCHLIST if s.table == "T10101")
    return bea.build_items(spec, gdp_rows(), "Table 1.1.1. Percent Change ...")


def test_only_the_latest_period_is_emitted():
    items = gdp_items()
    assert {it["period"] for it in items} == {"2026Q2"}


def test_item_carries_description_period_value_and_units():
    it = next(i for i in gdp_items() if i["line"] == "1")
    assert it["line_description"] == "Gross domestic product"
    assert it["period"] == "2026Q2"
    assert it["value"] == -0.8
    assert it["units"] == "Percent change, annual rate"
    assert it["date"] == "2026-06-30"
    assert "-0.8" in it["title"] and "-0.8" in it["summary"]


def test_item_id_is_stable_and_period_scoped():
    a = next(i for i in gdp_items() if i["line"] == "1")
    b = next(i for i in gdp_items() if i["line"] == "1")
    assert a["id"] == b["id"] == "bea:NIPA:T10101:L1:2026Q2"


def test_unit_multiplier_is_reported_not_applied():
    """Rescaling by UNIT_MULT would invent a magnitude we cannot verify."""
    it = next(i for i in gdp_items() if i["line"] == "29")
    assert it["value"] == 31204118.0
    assert it["unit_mult"] == "6"
    assert "10^6" in it["summary"]


def test_aggregates_sort_ahead_of_detail_lines():
    items = gdp_items()
    order = [i["line_description"] for i in items]
    assert order.index("Gross domestic product") < order.index("Goods")
    assert order.index("Personal consumption expenditures") < order.index("Goods")


def test_truncation_is_visible_in_the_data(monkeypatch):
    monkeypatch.setattr(bea, "MAX_LINES_PER_TABLE", 2)
    items = gdp_items()
    assert len(items) == 2
    # 4 lines exist at 2026Q2; the cap must not hide that.
    assert {i["table_lines_available"] for i in items} == {4}


def test_items_survive_the_display_deduper(tmp_path, monkeypatch):
    """Section._dedup_display_items drops items that share a URL (query string
    stripped). Every line of a table points at the same BEA release page, so
    without the per-line fragment the whole section would render as one row.
    """
    monkeypatch.setenv("BEA_API_KEY", "TESTKEY")
    install(monkeypatch, _happy_router)
    items = make_section(tmp_path).pull()

    kept = bea.BeaSection._dedup_display_items(items)
    assert len(kept) == len(items), (
        f"{len(items) - len(kept)} BEA items were collapsed as duplicates"
    )


def test_item_url_records_its_table_and_line():
    it = next(i for i in gdp_items() if i["line"] == "1")
    assert it["url"].startswith("https://www.bea.gov/data/gdp/")
    assert it["url"].endswith("#T10101-L1")


def test_monthly_table_builds_items():
    spec = next(s for s in bea.WATCHLIST if s.table == "T20600")
    rows = bea.parse_data(body(PERSONAL_INCOME_BODY), table="T20600")
    items = bea.build_items(spec, rows, "Table 2.6.")
    assert {i["period"] for i in items} == {"2026M06"}
    assert items[0]["line_description"] == "Personal income"
    saving = next(i for i in items if i["line"] == "34")
    assert saving["value"] == 4.8 and saving["units"] == "Percent"


def test_build_items_raises_when_no_period_parses():
    spec = bea.WATCHLIST[0]
    rows = [{"table": "T10101", "series_code": "", "line": "1",
             "line_description": "Gross domestic product", "period": "??",
             "value": 1.0, "value_text": "1.0", "units": "Percent",
             "unit_mult": "0"}]
    with pytest.raises(UpstreamSchemaError):
        bea.build_items(spec, rows)


# --------------------------------------------------------------------------- #
# Transport — fake session, no network
# --------------------------------------------------------------------------- #

class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _FakeSession:
    """Stands in for requests.Session; records the params it was called with."""

    def __init__(self, router):
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []
        self._router = router

    def get(self, url, params=None, timeout=None):
        self.calls.append(params or {})
        return self._router(params or {})


def _happy_router(params: dict):
    if params.get("method") == "GetParameterValues":
        return _FakeResponse(body(CATALOG_BODY))
    table = params.get("TableName")
    if table == "T20600":
        return _FakeResponse(body(PERSONAL_INCOME_BODY))
    # Every NIPA table shares one envelope shape, so the quarterly capture is
    # reused for the other product-account tables with its id swapped.
    payload = body(GDP_GROWTH_BODY)
    for row in payload["BEAAPI"]["Results"]["Data"]:
        row["TableName"] = table
    return _FakeResponse(payload)


def make_section(tmp_path) -> bea.BeaSection:
    return bea.BeaSection(store=SnapshotStore(path=tmp_path / "bea.sqlite"))


def install(monkeypatch, router) -> _FakeSession:
    session = _FakeSession(router)
    monkeypatch.setattr(bea.requests, "Session", lambda: session)
    return session


def test_pull_walks_the_catalog_then_every_watched_table(tmp_path, monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", "TESTKEY")
    session = install(monkeypatch, _happy_router)

    items = make_section(tmp_path).pull()

    methods = [c["method"] for c in session.calls]
    assert methods[0] == "GetParameterValues"
    assert methods.count("GetData") == len(bea.WATCHLIST)
    assert {c["UserID"] for c in session.calls} == {"TESTKEY"}
    assert {i["table"] for i in items} == {s.table for s in bea.WATCHLIST}
    assert all(i["id"] and i["units"] and i["period"] for i in items)


def test_pull_raises_on_the_auth_body_at_the_catalog_step(tmp_path, monkeypatch):
    """The end-to-end version of the HTTP-200 auth failure."""
    monkeypatch.setenv("BEA_API_KEY", "BADKEY")
    install(monkeypatch, lambda p: _FakeResponse(body(AUTH_ERROR_BODY)))
    with pytest.raises(UpstreamAuthError):
        make_section(tmp_path).pull()


def test_pull_raises_on_the_auth_body_at_the_data_step(tmp_path, monkeypatch):
    """A key that dies mid-run must not truncate the section to silence."""
    monkeypatch.setenv("BEA_API_KEY", "BADKEY")

    def router(params):
        if params.get("method") == "GetParameterValues":
            return _FakeResponse(body(CATALOG_BODY))
        return _FakeResponse(body(AUTH_ERROR_BODY))

    install(monkeypatch, router)
    with pytest.raises(UpstreamAuthError):
        make_section(tmp_path).pull()


def test_watched_table_missing_from_the_catalog_fails_loudly(tmp_path, monkeypatch):
    """A retired/renumbered id must not quietly halve the section."""
    monkeypatch.setenv("BEA_API_KEY", "TESTKEY")

    def router(params):
        if params.get("method") == "GetParameterValues":
            payload = body(CATALOG_BODY)
            payload["BEAAPI"]["Results"]["ParamValue"] = [
                v for v in payload["BEAAPI"]["Results"]["ParamValue"]
                if v["TableName"] != "T10105"
            ]
            return _FakeResponse(payload)
        return _happy_router(params)

    install(monkeypatch, router)
    with pytest.raises(UpstreamSchemaError) as exc:
        make_section(tmp_path).pull()
    assert "T10105" in str(exc.value)


def test_http_error_status_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", "TESTKEY")
    install(monkeypatch, lambda p: _FakeResponse(None, status_code=503))
    with pytest.raises(UpstreamHTTPError):
        make_section(tmp_path).pull()


def test_non_json_body_raises_parse_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", "TESTKEY")
    install(monkeypatch, lambda p: _FakeResponse(None, status_code=200))
    with pytest.raises(UpstreamParseError):
        make_section(tmp_path).pull()


def test_transport_failure_never_leaks_the_key(tmp_path, monkeypatch):
    """requests puts the effective URL — key and all — into its exception text,
    and section errors are stored and rendered into the brief's HTML."""
    monkeypatch.setenv("BEA_API_KEY", "SUPERSECRET")

    def router(params):
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='apps.bea.gov', port=443): Max retries "
            "exceeded with url: /api/data?UserID=SUPERSECRET&method=GetData"
        )

    install(monkeypatch, router)
    with pytest.raises(UpstreamHTTPError) as exc:
        make_section(tmp_path).pull()
    assert "SUPERSECRET" not in str(exc.value)
    assert "***" in str(exc.value)


def test_redact_is_a_no_op_without_a_key():
    assert bea._redact("nothing to hide", None) == "nothing to hide"


# --------------------------------------------------------------------------- #
# Section wiring
# --------------------------------------------------------------------------- #

def test_key_is_required_not_optional():
    """BEA has no keyless mode, so this is requires_env — the base class must
    refuse to call pull() without it."""
    assert bea.BeaSection.requires_env == ("BEA_API_KEY",)
    assert "BEA_API_KEY" not in bea.BeaSection.optional_env


def test_section_id_and_tier_are_stable():
    assert bea.BeaSection.id == "bea"
    assert bea.BeaSection.source_tier == "primary_document"
    assert bea.BeaSection.source_id == "bea-nipa"


def test_missing_key_records_a_broken_sensor_not_a_quiet_day(tmp_path, monkeypatch):
    monkeypatch.delenv("BEA_API_KEY", raising=False)
    state = make_section(tmp_path).resolve()
    assert state.state == STATE_NO_DATA
    assert state.error_type == "MissingCredential"
    assert "BEA_API_KEY" in (state.error or "")


def test_pull_called_directly_without_a_key_still_raises(tmp_path, monkeypatch):
    """run_section can drive pull() without going through resolve()'s gate."""
    monkeypatch.delenv("BEA_API_KEY", raising=False)
    with pytest.raises(MissingCredential):
        make_section(tmp_path).pull()


def test_year_window_covers_the_year_that_just_closed():
    assert bea.BeaSection._years(date(2026, 1, 4)) == "2025,2026"
    assert bea.BeaSection._years(date(2026, 8, 24)) == "2025,2026"


def test_contraction_emits_an_anomaly(tmp_path):
    from worldscope.sections import SectionState

    section = make_section(tmp_path)
    items = [dict(i, _id=i["id"]) for i in gdp_items()]
    state = SectionState(section_id="bea", title="", emoji="", state="fresh",
                         items=items, new=[], comparison_date=None,
                         source_date="2026-06-30")
    out = section.emit_structured(state)
    assert len(out["anomalies"]) == 1
    anomaly = out["anomalies"][0]
    assert anomaly["category"] == "macro-contraction"
    assert "-0.8" in anomaly["description"]
    assert anomaly["evidence"] == ["bea:NIPA:T10101:L1:2026Q2"]


def test_growth_emits_no_anomaly(tmp_path):
    from worldscope.sections import SectionState

    section = make_section(tmp_path)
    items = [dict(i, _id=i["id"], value=2.4) for i in gdp_items()]
    state = SectionState(section_id="bea", title="", emoji="", state="fresh",
                         items=items, new=[], comparison_date=None,
                         source_date="2026-06-30")
    assert section.emit_structured(state)["anomalies"] == []


# --------------------------------------------------------------------------- #
# Optional live smoke — skips cleanly with no key, which is the CI default
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not __import__("os").environ.get("BEA_API_KEY"),
    reason="BEA_API_KEY not set; live smoke test skipped",
)
def test_live_smoke(tmp_path):
    items = make_section(tmp_path).pull()
    assert items, "live BEA pull returned nothing"
    assert {i["table"] for i in items} == {s.table for s in bea.WATCHLIST}
    for it in items:
        assert it["id"].startswith("bea:NIPA:")
        assert it["period"] and it["units"] and it["line_description"]
        assert it["date"] == bea.period_to_date(it["period"])
