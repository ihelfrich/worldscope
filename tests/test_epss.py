"""Tests for the EPSS exploit-prediction adapter."""
import pytest
import requests

from worldscope.sections import UpstreamHTTPError, UpstreamParseError
from worldscope.sections.epss import EpssSection
from worldscope.store import SnapshotStore

_FIXTURE = {"status": "OK", "total": 2, "data": [
    {"cve": "CVE-2023-23752", "epss": "0.945200000", "percentile": "1.0", "date": "2026-06-02"},
    {"cve": "CVE-2024-0001", "epss": "0.120000000", "percentile": "0.7", "date": "2026-06-02"},
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
                raise ValueError("nope")
            return payload
    return R()


def test_pull_normalizes(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    items = EpssSection(store=_store(tmp_path)).pull()
    assert items[0]["id"] == "CVE-2023-23752"
    assert items[0]["epss"] == 0.9452
    assert items[0]["url"].endswith("CVE-2023-23752")


def test_extreme_scores_emit_anomaly(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    sec = EpssSection(store=_store(tmp_path))
    structured = sec.emit_structured(sec.resolve())
    anoms = structured["anomalies"]
    assert len(anoms) == 1  # only the 0.945 CVE, not the 0.12 one
    assert anoms[0]["category"] == "cyber-epss-extreme"


def test_http_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("down")))
    with pytest.raises(UpstreamHTTPError):
        EpssSection(store=_store(tmp_path)).pull()


def test_bad_status_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp({"status": "ERR", "data": []}))
    with pytest.raises(UpstreamHTTPError):
        EpssSection(store=_store(tmp_path)).pull()


def test_bad_json_raises_parse_error(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(None, bad_json=True))
    with pytest.raises(UpstreamParseError):
        EpssSection(store=_store(tmp_path)).pull()
