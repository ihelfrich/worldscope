"""forecasts (a required readiness source) and form4 fail loud, not empty."""
from types import SimpleNamespace

import pytest
import requests

from worldscope.sections import UpstreamHTTPError, UpstreamParseError, UpstreamSchemaError
from worldscope.sections import forecasts, form4


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _bare(cls):
    return cls.__new__(cls)


def test_forecasts_http_error_raises(monkeypatch):
    monkeypatch.setattr(forecasts.requests, "get", lambda *a, **k: _Resp(503))
    with pytest.raises(UpstreamHTTPError, match="503"):
        _bare(forecasts.ForecastsSection).pull()


def test_forecasts_network_error_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(forecasts.requests, "get", boom)
    with pytest.raises(UpstreamHTTPError, match="unreachable"):
        _bare(forecasts.ForecastsSection).pull()


def test_forecasts_wrong_shape_raises(monkeypatch):
    monkeypatch.setattr(forecasts.requests, "get", lambda *a, **k: _Resp(200, {"error": "x"}))
    with pytest.raises(UpstreamSchemaError):
        _bare(forecasts.ForecastsSection).pull()


def test_forecasts_non_json_raises(monkeypatch):
    monkeypatch.setattr(forecasts.requests, "get", lambda *a, **k: _Resp(200, None))
    with pytest.raises(UpstreamParseError):
        _bare(forecasts.ForecastsSection).pull()


def test_forecasts_empty_list_is_a_genuine_empty(monkeypatch):
    monkeypatch.setattr(forecasts.requests, "get", lambda *a, **k: _Resp(200, []))
    assert _bare(forecasts.ForecastsSection).pull() == []


def test_form4_http_status_raises(monkeypatch):
    monkeypatch.setattr(form4.feedparser, "parse",
                        lambda *a, **k: SimpleNamespace(status=403, entries=[], bozo=False))
    with pytest.raises(UpstreamHTTPError, match="403"):
        _bare(form4.Form4Section).pull()


def test_form4_bozo_empty_feed_raises(monkeypatch):
    monkeypatch.setattr(form4.feedparser, "parse",
                        lambda *a, **k: SimpleNamespace(status=200, entries=[], bozo=True,
                                                        bozo_exception="not well-formed"))
    with pytest.raises(UpstreamParseError, match="not well-formed"):
        _bare(form4.Form4Section).pull()


def test_form4_valid_empty_feed_is_a_genuine_empty(monkeypatch):
    monkeypatch.setattr(form4.feedparser, "parse",
                        lambda *a, **k: SimpleNamespace(status=200, entries=[], bozo=False))
    assert _bare(form4.Form4Section).pull() == []
