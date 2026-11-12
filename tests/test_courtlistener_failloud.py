"""CourtListener must fail loud, not 'succeed' with zero opinions.

Regression: CourtListener v4 search answers 401 to unauthenticated requests.
The adapter swallowed every per-court error, so with a missing or rejected
token all ~65 courts returned nothing and the section reported fresh_empty —
an innocuous-looking empty that hid the outage.
"""
from __future__ import annotations

import pytest

from worldscope.sections import MissingCredential, UpstreamAuthError, UpstreamHTTPError
from worldscope.sections import courtlistener as cl


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _section():
    return cl.CourtListenerSection.__new__(cl.CourtListenerSection)


def test_missing_token_is_a_missing_credential(monkeypatch):
    monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)
    with pytest.raises(MissingCredential):
        _section().pull()


def test_token_rejected_by_every_court_is_an_auth_error(monkeypatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "expired")
    monkeypatch.setattr(cl.requests, "get", lambda *a, **k: _Resp(401))
    with pytest.raises(UpstreamAuthError, match="renew COURTLISTENER_API_TOKEN"):
        _section().pull()


def test_every_court_erroring_is_an_http_error(monkeypatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "t")

    def boom(*a, **k):
        raise ConnectionError("network down")
    monkeypatch.setattr(cl.requests, "get", boom)
    with pytest.raises(UpstreamHTTPError):
        _section().pull()


def test_partial_outage_still_returns_what_answered(monkeypatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "t")
    good = {"results": [{"cluster_id": 7, "dateFiled": "2026-11-10",
                         "caseName": "Doe v. Roe", "absolute_url": "/o/7/"}]}

    def flaky(url, params=None, **k):
        return _Resp(200, good) if params["court"] == "scotus" else _Resp(503)
    monkeypatch.setattr(cl.requests, "get", flaky)
    items = _section().pull()
    assert len(items) == 1 and "Doe v. Roe" in items[0]["title"]
