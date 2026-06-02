"""Tests for the WHO Disease Outbreak News adapter."""
import pytest
import requests

from worldscope.sections import UpstreamHTTPError, UpstreamParseError
from worldscope.sections.who_don import WhoDonSection
from worldscope.store import SnapshotStore

_FIXTURE = {"value": [
    {"DonId": "DON605", "Title": "Ebola (Bundibugyo), DRC & Uganda",
     "UseOverrideTitle": False, "Summary": "<p>The <b>BVD</b> outbreak continues.</p>",
     "PublicationDate": "2026-05-29T15:43:01Z", "UrlName": "2026-DON605"},
    {"DonId": "DON604", "Title": "x", "OverrideTitle": "Marburg, Tanzania",
     "UseOverrideTitle": True, "Summary": "Cases reported.",
     "PublicationDate": "2026-05-20T00:00:00Z", "UrlName": "2026-DON604"},
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
                raise ValueError("not json")
            return payload
    return R()


def test_pull_normalizes_title_url_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(_FIXTURE))
    items = WhoDonSection(store=_store(tmp_path)).pull()
    assert len(items) == 2
    a = items[0]
    assert a["id"] == "who-don-DON605"
    assert a["date"] == "2026-05-29"
    assert a["url"].endswith("/item/2026-DON605")
    assert "<b>" not in a["summary"] and "BVD" in a["summary"]  # html stripped
    # OverrideTitle honored when UseOverrideTitle is true
    assert items[1]["title"] == "Marburg, Tanzania"


def test_http_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("down")))
    with pytest.raises(UpstreamHTTPError):
        WhoDonSection(store=_store(tmp_path)).pull()


def test_empty_is_outage(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp({"value": []}))
    with pytest.raises(UpstreamHTTPError):
        WhoDonSection(store=_store(tmp_path)).pull()


def test_bad_json_raises_parse_error(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _resp(None, bad_json=True))
    with pytest.raises(UpstreamParseError):
        WhoDonSection(store=_store(tmp_path)).pull()
