"""ReliefWeb adapter on API v2.

Error fixtures are verbatim from live responses on 2026-10-09:
  GET /v1/reports      -> 410 "The API version 'v1' has been decommissioned..."
  GET /v2/reports?appname=worldscope -> 403 AccessDeniedHttpException
      "You are not using an approved appname. ..."
  GET /v2/reports (no appname)       -> 400 "Missing appname parameter"
The success fixture follows the documented v2 result structure
(time/href/links/totalCount/count/data; items {id, score, href, fields}) — a
live 200 needs a ReliefWeb-approved appname, which this environment lacks.
"""
import json

import pytest
import requests

from worldscope.sections import UpstreamAuthError, UpstreamHTTPError, UpstreamParseError
from worldscope.sections import reliefweb
from worldscope.store import SnapshotStore

SUCCESS = {
    "time": 14,
    "href": "https://api.reliefweb.int/v2/reports?appname=worldscope&limit=2",
    "links": {"self": {"href": "https://api.reliefweb.int/v2/reports?appname=worldscope&limit=2"},
              "next": {"href": "https://api.reliefweb.int/v2/reports?appname=worldscope&limit=2&offset=2"}},
    "took": 9,
    "totalCount": 1214557,
    "count": 2,
    "data": [
        {"id": "4172231", "score": 1,
         "href": "https://api.reliefweb.int/v2/reports/4172231",
         "fields": {
             "title": "Sudan: Humanitarian Update (8 October 2026)",
             "date": {"created": "2026-10-08T14:21:09+00:00"},
             "url_alias": "https://reliefweb.int/report/sudan/sudan-humanitarian-update-8-october-2026",
             "country": [{"name": "Sudan"}, {"name": "Chad"}],
             "primary_country": {"name": "Sudan"},
             "format": [{"name": "Situation Report"}],
             "source": [{"name": "UN Office for the Coordination of Humanitarian Affairs"}],
             "body-html": "<p>Fighting around El Fasher continued to displace civilians.</p>",
         }},
        {"id": "4172230", "score": 1,
         "href": "https://api.reliefweb.int/v2/reports/4172230",
         "fields": {
             "title": "Global Food Security Outlook",
             "date": {"created": "2026-10-08T13:02:00+00:00"},
             "format": [{"name": "Analysis"}],
             "source": [{"name": "WFP"}, {"name": "FAO"}],
         }},
    ],
}
UNAPPROVED = {"status": 403, "time": 35, "error": {
    "type": "AccessDeniedHttpException",
    "message": "You are not using an approved appname. Kindly request an appname from "
               "ReliefWeb here: https://apidoc.reliefweb.int/parameters#appname"}}
V1_GONE = {"status": 410, "time": 1, "error": {
    "type": "Exception",
    "message": "The API version 'v1' has been decommissioned. Please use version 'v2' instead."}}


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else "<html>"

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _section(tmp_path):
    return reliefweb.ReliefWebSection(store=SnapshotStore(tmp_path / "s.sqlite"))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(reliefweb.time, "sleep", lambda *_: None)


def test_uses_v2_with_appname_and_keeps_item_schema(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, timeout=timeout)
        return FakeResp(200, SUCCESS)

    monkeypatch.setattr(reliefweb.requests, "get", fake_get)
    monkeypatch.delenv("RELIEFWEB_APPNAME", raising=False)
    items = _section(tmp_path).pull()

    assert seen["url"] == "https://api.reliefweb.int/v2/reports"
    assert seen["timeout"]
    assert seen["params"]["appname"] == reliefweb.DEFAULT_APPNAME
    assert reliefweb.DEFAULT_APPNAME == "helfrich-worldscope-7k3q"
    assert seen["params"]["sort[]"] == "date.created:desc"
    assert "body-html" in seen["params"]["fields[include][]"]

    assert len(items) == 2
    first = items[0]
    assert first == {
        "id": "rw-4172231",
        "date": "2026-10-08",
        "title": "[Sudan] Sudan: Humanitarian Update (8 October 2026)",
        "url": "https://reliefweb.int/report/sudan/sudan-humanitarian-update-8-october-2026",
        "summary": "Situation Report · UN Office for the Coordination of Humanitarian Affairs",
        "country": "Sudan",
        "all_countries": ["Sudan", "Chad"],
        "topics": ["humanitarian"],
        "_source": "reliefweb",
        "_body": "<p>Fighting around El Fasher continued to displace civilians.</p>",
    }
    # Sparse item: no country / url_alias falls back to node URL and "Global".
    second = items[1]
    assert second["title"] == "[Global] Global Food Security Outlook"
    assert second["url"] == "https://reliefweb.int/node/4172230"
    assert second["country"] == "" and second["all_countries"] == []
    assert second["summary"] == "Analysis · WFP, FAO"


def test_appname_comes_from_env(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("RELIEFWEB_APPNAME", "acme-osint-x1")
    monkeypatch.setattr(reliefweb.requests, "get",
                        lambda url, params=None, **k: (seen.update(params), FakeResp(200, SUCCESS))[1])
    _section(tmp_path).pull()
    assert seen["appname"] == "acme-osint-x1"


def test_unapproved_appname_is_an_auth_error(monkeypatch, tmp_path):
    monkeypatch.setattr(reliefweb.requests, "get", lambda *a, **k: FakeResp(403, UNAPPROVED))
    with pytest.raises(UpstreamAuthError) as ei:
        _section(tmp_path).pull()
    msg = str(ei.value)
    assert "approved appname" in msg and "RELIEFWEB_APPNAME" in msg


def test_other_4xx_is_http_error_with_upstream_message(monkeypatch, tmp_path):
    monkeypatch.setattr(reliefweb.requests, "get", lambda *a, **k: FakeResp(410, V1_GONE))
    with pytest.raises(UpstreamHTTPError) as ei:
        _section(tmp_path).pull()
    assert "decommissioned" in str(ei.value)


def test_retries_429_and_5xx_then_succeeds(monkeypatch, tmp_path):
    seq = iter([FakeResp(429, {"status": 429}), FakeResp(503, {"status": 503}),
                FakeResp(200, SUCCESS)])
    monkeypatch.setattr(reliefweb.requests, "get", lambda *a, **k: next(seq))
    assert len(_section(tmp_path).pull()) == 2


def test_persistent_5xx_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(reliefweb.requests, "get", lambda *a, **k: FakeResp(502, {"status": 502}))
    with pytest.raises(UpstreamHTTPError):
        _section(tmp_path).pull()


def test_network_error_raises(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(reliefweb.requests, "get", boom)
    with pytest.raises(UpstreamHTTPError):
        _section(tmp_path).pull()


def test_non_json_and_bad_envelope_are_parse_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(reliefweb.requests, "get", lambda *a, **k: FakeResp(200, None))
    with pytest.raises(UpstreamParseError):
        _section(tmp_path).pull()
    monkeypatch.setattr(reliefweb.requests, "get", lambda *a, **k: FakeResp(200, {"data": "nope"}))
    with pytest.raises(UpstreamParseError):
        _section(tmp_path).pull()
