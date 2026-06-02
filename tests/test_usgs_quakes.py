"""Tests for the USGS earthquake adapter."""
import pytest
import requests

from worldscope.sections import UpstreamHTTPError, UpstreamParseError
from worldscope.sections.usgs_quakes import UsgsQuakesSection
from worldscope.store import SnapshotStore

_FIXTURE = {"features": [
    {"id": "us1", "geometry": {"coordinates": [-178.4, -17.9, 617.0]},
     "properties": {"mag": 4.6, "place": "232 km E of Levuka, Fiji",
                    "time": 1780408667568, "url": "https://usgs/us1",
                    "title": "M 4.6 - Fiji", "tsunami": 0, "alert": None}},
    {"id": "us2", "geometry": {"coordinates": [120.0, 24.0, 10.0]},
     "properties": {"mag": 6.8, "place": "Taiwan", "time": 1780408667568,
                    "url": "https://usgs/us2", "title": "M 6.8 - Taiwan",
                    "tsunami": 1, "alert": "orange"}},
]}


def _store(tmp_path):
    return SnapshotStore(tmp_path / "s.sqlite")


def _resp(payload, *, raise_http=False, bad_json=False):
    class R:
        def raise_for_status(self):
            if raise_http:
                raise requests.HTTPError("500")
        def json(self):
            if bad_json:
                raise ValueError("no")
            return payload
    return R()


def test_pull_sorts_by_magnitude(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    items = UsgsQuakesSection(store=_store(tmp_path)).pull()
    assert items[0]["mag"] == 6.8 and items[0]["id"] == "us2"
    assert items[0]["date"] == "2026-06-02"
    assert items[0]["lat"] == 24.0 and items[0]["depth_km"] == 10.0


def test_big_quake_tsunami_emit_anomaly(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    sec = UsgsQuakesSection(store=_store(tmp_path))
    anoms = sec.emit_structured(sec.resolve())["anomalies"]
    assert len(anoms) == 1  # only the M6.8/tsunami/orange one, not the M4.6
    assert anoms[0]["category"] == "seismic-major"
    assert "tsunami" in anoms[0]["description"]


def test_empty_features_is_a_quiet_day_not_failure(monkeypatch, tmp_path):
    # Unlike GDACS, no M4.5+ in a day is legitimate — must NOT raise.
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp({"features": []}))
    assert UsgsQuakesSection(store=_store(tmp_path)).pull() == []


def test_http_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("x")))
    with pytest.raises(UpstreamHTTPError):
        UsgsQuakesSection(store=_store(tmp_path)).pull()


def test_missing_features_raises_parse(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp({"metadata": {}}))
    with pytest.raises(UpstreamParseError):
        UsgsQuakesSection(store=_store(tmp_path)).pull()
