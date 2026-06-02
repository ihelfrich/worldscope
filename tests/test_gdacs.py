"""Tests for the GDACS disaster adapter (frozen fixture + contract)."""
import pytest
import requests

from worldscope.sections import UpstreamHTTPError, UpstreamParseError
from worldscope.sections.gdacs import GdacsSection
from worldscope.store import SnapshotStore

_FIXTURE = {
    "type": "FeatureCollection",
    "features": [
        {"geometry": {"type": "Point", "coordinates": [120.5, 14.6]},
         "properties": {"eventtype": "TC", "eventid": "1000", "alertlevel": "Red",
                        "country": "Philippines", "iso3": "PHL",
                        "name": "Tropical Cyclone MAWAR",
                        "fromdate": "2026-05-30T00:00:00",
                        "url": {"report": "https://gdacs.org/report/TC/1000"},
                        "severitydata": {"severitytext": "Category 4"}}},
        {"geometry": {"type": "Point", "coordinates": [-72.0, 18.5]},
         "properties": {"eventtype": "FL", "eventid": "1001", "alertlevel": "Green",
                        "country": "Haiti", "iso3": "HTI", "name": "Flood in Haiti",
                        "fromdate": "2026-05-29T00:00:00", "url": "https://gdacs.org/x"}},
    ],
}


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
    assert items[0]["url"] == "https://gdacs.org/report/TC/1000"
    assert items[0]["lat"] == 14.6 and items[0]["lon"] == 120.5


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
