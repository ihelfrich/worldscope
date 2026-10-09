"""Tests for the GDACS disaster adapter (frozen fixture + contract).

The fixture mirrors the live geteventlist/EVENTS4APP GeoJSON observed on
2026-10-09 (integer eventid, url as {geometry, report, details}, severitydata
object, iscurrent as the string "true"/"false"). The MAP endpoint now answers
400 {"message":"Eventtype is required."} unless exactly one ?eventtype= is
given; the adapter uses EVENTS4APP first and per-type MAP calls as fallback.
"""
import pytest
import requests

from worldscope.sections import UpstreamHTTPError, UpstreamParseError
from worldscope.sections import gdacs
from worldscope.sections.gdacs import GdacsSection
from worldscope.store import SnapshotStore

_RED_TC = {
    "type": "Feature",
    "bbox": [-104.0, 19.0, -104.0, 19.0],
    "geometry": {"type": "Point", "coordinates": [120.5, 14.6]},
    "properties": {
        "eventtype": "TC", "eventid": 1000, "episodeid": 9, "eventname": "MAWAR",
        "glide": "", "name": "Tropical Cyclone MAWAR",
        "description": "Tropical Cyclone MAWAR",
        "htmldescription": "Red Tropical Cyclone MAWAR ...",
        "icon": "https://www.gdacs.org/images/gdacs_icons/maps/Red/TC.png",
        "url": {
            "geometry": "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry?eventtype=TC&eventid=1000&episodeid=9",
            "report": "https://gdacs.org/report/TC/1000",
            "details": "https://www.gdacs.org/gdacsapi/api/events/geteventdata?eventtype=TC&eventid=1000",
        },
        "alertlevel": "Red", "alertscore": 3, "episodealertlevel": "Red",
        "episodealertscore": 3.0, "istemporary": "false", "iscurrent": "true",
        "country": "Philippines", "fromdate": "2026-05-30T00:00:00",
        "todate": "2026-06-01T15:00:00", "datemodified": "2026-06-01T15:14:50",
        "iso3": "PHL", "source": "NOAA", "sourceid": "",
        "affectedcountries": [{"iso2": "PH", "iso3": "PHL", "countryname": "Philippines"}],
        "severitydata": {"severity": 231.48, "severitytext": "Category 4", "severityunit": "km/h"},
    },
}
_GREEN_FL = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-72.0, 18.5]},
    "properties": {
        "eventtype": "FL", "eventid": 1001, "episodeid": 1, "alertlevel": "Green",
        "country": "Haiti", "iso3": "HTI", "name": "Flood in Haiti",
        "fromdate": "2026-05-29T00:00:00", "iscurrent": "true",
        "url": "https://gdacs.org/x",
    },
}
_FIXTURE = {"type": "FeatureCollection", "features": [_RED_TC, _GREEN_FL]}


def _store(tmp_path):
    return SnapshotStore(tmp_path / "s.sqlite")


def _resp(payload, *, raise_http=False, bad_json=False):
    class R:
        def raise_for_status(self):
            if raise_http:
                raise requests.HTTPError("500")
        def json(self):
            if bad_json:
                raise ValueError("not json")
            return payload
    return R()


def test_pull_normalizes_and_sorts_by_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    items = GdacsSection(store=_store(tmp_path)).pull()
    assert len(items) == 2
    # Red sorts before Green
    assert items[0]["alert_level"] == "Red"
    assert items[0]["event_type"] == "Tropical Cyclone"
    assert items[0]["id"] == "gdacs-TC-1000"
    assert items[0]["country"] == "Philippines"
    assert items[0]["iso3"] == "PHL"
    assert items[0]["date"] == "2026-05-30"
    assert items[0]["url"] == "https://gdacs.org/report/TC/1000"
    assert items[0]["lat"] == 14.6 and items[0]["lon"] == 120.5
    assert items[0]["summary"] == "Tropical Cyclone · Red alert · Philippines · Category 4"
    assert set(items[0]) == {"id", "date", "title", "url", "summary", "alert_level",
                             "event_type", "country", "iso3", "lon", "lat"}
    assert items[1]["id"] == "gdacs-FL-1001" and items[1]["url"] == "https://gdacs.org/x"


def test_primary_endpoint_is_events4app(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, timeout))
        return _resp(_FIXTURE)

    monkeypatch.setattr(requests, "get", fake_get)
    GdacsSection(store=_store(tmp_path)).pull()
    assert calls == [(gdacs.API, None, 20)]
    assert gdacs.API.endswith("/geteventlist/EVENTS4APP")


def test_falls_back_to_per_type_map_calls(monkeypatch, tmp_path):
    """EVENTS4APP down -> one MAP?eventtype=XX call per type, merged + deduped."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        if url == gdacs.API:
            raise requests.HTTPError("400 Client Error: Bad Request")
        et = params["eventtype"]
        if et == "TC":
            return _resp({"type": "FeatureCollection", "features": [_RED_TC, _RED_TC]})
        if et == "FL":
            return _resp({"type": "FeatureCollection", "features": [_GREEN_FL]})
        if et == "DR":
            raise requests.HTTPError("503")
        return _resp({"type": "FeatureCollection", "features": []})

    monkeypatch.setattr(requests, "get", fake_get)
    items = GdacsSection(store=_store(tmp_path)).pull()
    assert [c[1] for c in calls[1:]] == [{"eventtype": t} for t in gdacs.FALLBACK_TYPES]
    assert all(c[0] == gdacs.API_BY_TYPE for c in calls[1:])
    assert [it["id"] for it in items] == ["gdacs-TC-1000", "gdacs-FL-1001"]


def test_non_current_events_are_dropped(monkeypatch, tmp_path):
    stale = {**_GREEN_FL, "properties": {**_GREEN_FL["properties"], "iscurrent": "false"}}
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _resp({"type": "FeatureCollection", "features": [_RED_TC, stale]}))
    items = GdacsSection(store=_store(tmp_path)).pull()
    assert [it["id"] for it in items] == ["gdacs-TC-1000"]


def test_orange_red_emit_anomalies(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    sec = GdacsSection(store=_store(tmp_path))
    state = sec.resolve()
    structured = sec.emit_structured(state)
    anoms = structured["anomalies"]
    assert len(anoms) == 1  # only the Red TC, not the Green flood
    assert anoms[0]["category"] == "disaster-tropical-cyclone"
    assert "Red alert" in anoms[0]["description"]


def test_http_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("down")))
    with pytest.raises(UpstreamHTTPError):
        GdacsSection(store=_store(tmp_path)).pull()


def test_empty_response_is_an_outage(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _resp({"type": "FeatureCollection", "features": []}))
    with pytest.raises(UpstreamHTTPError):
        GdacsSection(store=_store(tmp_path)).pull()


def test_bad_json_raises_parse_error(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(None, bad_json=True))
    with pytest.raises(UpstreamParseError):
        GdacsSection(store=_store(tmp_path)).pull()
