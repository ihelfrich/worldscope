"""ACLED adapter: OAuth password/refresh grants, retry/backoff, typed failures.

Fixtures mirror the live endpoint behaviour observed 2026-10-09:
  POST /oauth/token  bad creds -> 400 {"error":"invalid_grant",
      "error_description":"The user credentials were incorrect."}
  GET  /api/acled/read  no token -> 403 {"message":"Access denied"}
                        bad token -> 401 {"message":"The resource owner or
                        authorization server denied the request."}
A successful token body (per the documented flow) is
  {"token_type":"Bearer","expires_in":86400,"access_token":..,"refresh_token":..}
and the read envelope is {"status":200,"success":true,"count":..,"data":[..]}.
Real credentials are not available in this environment, so the happy path is
exercised against these fixtures only.
"""
import json
import time

import pytest
import requests

from worldscope.sections import MissingCredential, UpstreamAuthError, UpstreamHTTPError
from worldscope.sections import acled
from worldscope.store import SnapshotStore

TOKEN_OK = {"token_type": "Bearer", "expires_in": 86400,
            "access_token": "tok-1", "refresh_token": "ref-1"}
TOKEN_REFRESHED = {"token_type": "Bearer", "expires_in": 86400,
                   "access_token": "tok-2", "refresh_token": "ref-2"}
INVALID_GRANT = {"error": "invalid_grant",
                 "error_description": "The user credentials were incorrect."}
EVENT = {
    "event_id_cnty": "SUD12345", "event_date": "2026-10-07", "year": 2026,
    "event_type": "Battles", "sub_event_type": "Armed clash",
    "actor1": "RSF: Rapid Support Forces", "actor2": "Military Forces of Sudan (2019-)",
    "country": "Sudan", "admin1": "North Darfur", "location": "El Fasher",
    "latitude": "13.6306", "longitude": "25.3497", "fatalities": 12,
    "notes": "Clashes reported on the outskirts of the city.", "source": "Local media",
}
READ_OK = {"status": 200, "success": True, "last_update": 3, "count": 1,
           "messages": [], "data": [EVENT]}


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ACLED_EMAIL", "someone@example.org")
    monkeypatch.setenv("ACLED_PASSWORD", "hunter2")
    monkeypatch.setattr(acled, "TOKEN_CACHE", tmp_path / "acled_token.json")
    monkeypatch.setattr(acled.time, "sleep", lambda *_: None)
    return tmp_path


def _section(tmp_path):
    return acled.AcledSection(store=SnapshotStore(tmp_path / "s.sqlite"))


def test_password_grant_then_read(env, monkeypatch):
    posts, gets = [], []

    def fake_post(url, data=None, headers=None, timeout=None):
        assert url == acled.TOKEN_URL and timeout
        posts.append(data)
        return FakeResp(200, TOKEN_OK)

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == acled.READ_URL and timeout
        gets.append((params, headers))
        return FakeResp(200, READ_OK)

    monkeypatch.setattr(acled.requests, "post", fake_post)
    monkeypatch.setattr(acled.requests, "get", fake_get)
    items = _section(env).pull()

    assert posts == [{"username": "someone@example.org", "password": "hunter2",
                      "grant_type": "password", "client_id": "acled",
                      "scope": "authenticated"}]
    assert gets[0][1]["Authorization"] == "Bearer tok-1"
    assert gets[0][0]["_format"] == "json"
    assert gets[0][0]["event_date_where"] == "BETWEEN"
    assert "Sudan" in gets[0][0]["country"]
    # Two queries (watchlist + global sweep); the event is deduped by id.
    assert len(gets) == 2 and len(items) == 1
    it = items[0]
    assert it["id"] == "acled-SUD12345" and it["date"] == "2026-10-07"
    assert it["country"] == "Sudan" and it["fatalities"] == 12
    assert it["actors"] == "RSF: Rapid Support Forces vs Military Forces of Sudan (2019-)"
    assert set(it) >= {"id", "date", "title", "url", "summary", "country", "event_type",
                       "sub_event_type", "actors", "fatalities", "location",
                       "latitude", "longitude"}
    cache = json.loads((env / "acled_token.json").read_text())
    assert cache["access_token"] == "tok-1" and cache["refresh_token"] == "ref-1"
    assert cache["expires_at"] > time.time() + 80000


def test_missing_credentials_is_typed(monkeypatch, tmp_path):
    monkeypatch.delenv("ACLED_EMAIL", raising=False)
    monkeypatch.delenv("ACLED_PASSWORD", raising=False)
    monkeypatch.setattr(acled, "TOKEN_CACHE", tmp_path / "acled_token.json")
    with pytest.raises(MissingCredential):
        _section(tmp_path).pull()


def test_invalid_grant_raises_auth_error_with_upstream_detail(env, monkeypatch):
    monkeypatch.setattr(acled.requests, "post", lambda *a, **k: FakeResp(400, INVALID_GRANT))
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: pytest.fail("must not read"))
    with pytest.raises(UpstreamAuthError) as ei:
        _section(env).pull()
    assert "credentials were incorrect" in str(ei.value)
    assert not (env / "acled_token.json").exists()


def test_token_endpoint_retries_on_429_then_succeeds(env, monkeypatch):
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return FakeResp(429, {"message": "slow down"}, {"Retry-After": "1"})
        return FakeResp(200, TOKEN_OK)

    monkeypatch.setattr(acled.requests, "post", fake_post)
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: FakeResp(200, READ_OK))
    assert _section(env).pull()
    assert len(calls) == 2


def test_token_endpoint_persistent_5xx_is_http_error(env, monkeypatch):
    monkeypatch.setattr(acled.requests, "post", lambda *a, **k: FakeResp(503, {"message": "down"}))
    with pytest.raises(UpstreamHTTPError):
        _section(env).pull()


def test_refresh_grant_preferred_when_cached_token_expired(env, monkeypatch):
    (env / "acled_token.json").write_text(json.dumps({
        "access_token": "old", "refresh_token": "ref-1", "expires_at": time.time() - 10}))
    posts = []

    def fake_post(url, data=None, **k):
        posts.append(data)
        return FakeResp(200, TOKEN_REFRESHED)

    monkeypatch.setattr(acled.requests, "post", fake_post)
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: FakeResp(200, READ_OK))
    _section(env).pull()
    assert posts == [{"grant_type": "refresh_token", "refresh_token": "ref-1",
                      "client_id": "acled"}]
    assert json.loads((env / "acled_token.json").read_text())["access_token"] == "tok-2"


def test_expired_refresh_falls_back_to_password_grant(env, monkeypatch):
    (env / "acled_token.json").write_text(json.dumps({
        "access_token": "old", "refresh_token": "stale", "expires_at": 0}))
    posts = []

    def fake_post(url, data=None, **k):
        posts.append(data["grant_type"])
        if data["grant_type"] == "refresh_token":
            return FakeResp(400, {"error": "invalid_grant",
                                  "error_description": "The refresh token is invalid."})
        return FakeResp(200, TOKEN_OK)

    monkeypatch.setattr(acled.requests, "post", fake_post)
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: FakeResp(200, READ_OK))
    assert _section(env).pull()
    assert posts == ["refresh_token", "password"]


def test_read_401_clears_cache_and_raises_auth(env, monkeypatch):
    (env / "acled_token.json").write_text(json.dumps({
        "access_token": "cached", "refresh_token": "r", "expires_at": time.time() + 9999}))
    monkeypatch.setattr(acled.requests, "post", lambda *a, **k: pytest.fail("token cached"))
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: FakeResp(
        401, {"message": "The resource owner or authorization server denied the request."}))
    with pytest.raises(UpstreamAuthError) as ei:
        _section(env).pull()
    assert "401" in str(ei.value)
    assert not (env / "acled_token.json").exists()


def test_read_retries_5xx_then_succeeds(env, monkeypatch):
    monkeypatch.setattr(acled.requests, "post", lambda *a, **k: FakeResp(200, TOKEN_OK))
    seq = iter([FakeResp(502, {"message": "bad gateway"}), FakeResp(200, READ_OK),
                FakeResp(200, {"status": 200, "success": True, "data": []})])
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: next(seq))
    assert len(_section(env).pull()) == 1


def test_read_envelope_failure_is_http_error(env, monkeypatch):
    monkeypatch.setattr(acled.requests, "post", lambda *a, **k: FakeResp(200, TOKEN_OK))
    monkeypatch.setattr(acled.requests, "get", lambda *a, **k: FakeResp(
        200, {"status": 200, "success": False, "messages": ["terms not accepted"], "data": []}))
    with pytest.raises(UpstreamHTTPError):
        _section(env).pull()


def test_network_errors_are_typed(env, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("proxy down")
    monkeypatch.setattr(acled.requests, "post", boom)
    with pytest.raises(UpstreamHTTPError):
        _section(env).pull()
