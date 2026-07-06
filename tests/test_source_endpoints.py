"""Regression tests for the 2026 stale-source fixes.

These pin the endpoint/auth changes made after ACLED, ProMED, ReliefWeb and
vip_flights went stale in CI, so a future edit that reverts them fails loudly.
All network is mocked; nothing here hits the wire.

    ReliefWeb  — v1 retired (410); use v2 + a pre-approved, env-set appname.
    ProMED     — public RSS discontinued; feed URL is env-overridable.
    ACLED      — auth failures raise a descriptive UpstreamAuthError.
    vip_flights— anonymous /states/all is retried; OAuth2 creds used when set.
"""
import importlib
import os
from unittest import mock

import pytest
import requests

from worldscope.sections import UpstreamAuthError, UpstreamHTTPError


# ---- ReliefWeb: v2 endpoint + configurable appname -------------------------

def test_reliefweb_uses_v2_endpoint():
    from worldscope.sections import reliefweb
    assert reliefweb.API.endswith("/v2/reports")
    assert "/v1/" not in reliefweb.API


def test_reliefweb_appname_is_env_configurable():
    with mock.patch.dict(os.environ, {"RELIEFWEB_APPNAME": "my-registered-app"}):
        import worldscope.sections.reliefweb as rw
        importlib.reload(rw)
        assert rw.APPNAME == "my-registered-app"
    import worldscope.sections.reliefweb as rw
    importlib.reload(rw)  # restore default for other tests


def test_reliefweb_403_points_at_appname_registration():
    from worldscope.sections import reliefweb
    resp = mock.Mock(status_code=403)
    with mock.patch.object(reliefweb.requests, "get", return_value=resp):
        with pytest.raises(UpstreamHTTPError) as ei:
            reliefweb.ReliefWebSection().pull()
    msg = str(ei.value)
    assert "403" in msg and "RELIEFWEB_APPNAME" in msg


# ---- ProMED: configurable feed URL -----------------------------------------

def test_promed_feed_url_is_env_configurable():
    with mock.patch.dict(os.environ, {"PROMED_FEED_URL": "https://example.org/feed.xml"}):
        import worldscope.sections.promed as pm
        importlib.reload(pm)
        assert pm.FEED == "https://example.org/feed.xml"
    import worldscope.sections.promed as pm
    importlib.reload(pm)


# ---- ACLED: auth-failure diagnostics ---------------------------------------

def test_acled_token_rejection_raises_descriptive_error():
    from worldscope.sections import acled
    sec = acled.AcledSection()
    fake_resp = mock.Mock(status_code=400, text='{"error":"invalid_grant"}')
    with mock.patch.dict(os.environ, {"ACLED_EMAIL": "a@b.com", "ACLED_PASSWORD": "x"}), \
         mock.patch.object(acled, "TOKEN_CACHE") as cache, \
         mock.patch.object(acled.requests, "post", return_value=fake_resp):
        cache.exists.return_value = False  # no cached token
        with pytest.raises(UpstreamAuthError) as ei:
            sec._get_token()
    msg = str(ei.value)
    assert "400" in msg
    assert "myACLED" in msg  # points the operator at the credential fix


def test_acled_token_missing_access_token_raises():
    from worldscope.sections import acled
    sec = acled.AcledSection()
    fake_resp = mock.Mock(status_code=200)
    fake_resp.json.return_value = {"token_type": "bearer"}  # no access_token
    with mock.patch.dict(os.environ, {"ACLED_EMAIL": "a@b.com", "ACLED_PASSWORD": "x"}), \
         mock.patch.object(acled, "TOKEN_CACHE") as cache, \
         mock.patch.object(acled.requests, "post", return_value=fake_resp):
        cache.exists.return_value = False
        with pytest.raises(UpstreamAuthError) as ei:
            sec._get_token()
    assert "no access_token" in str(ei.value)


# ---- vip_flights: retry + optional OAuth2 ----------------------------------

def _states_resp(states=None):
    resp = mock.Mock(status_code=200)
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"time": 1000, "states": states or []}
    return resp


def test_vip_flights_retries_transient_timeout_then_succeeds():
    from worldscope.sections import vip_flights
    calls = [requests.Timeout("t1"), requests.ConnectionError("c2"), _states_resp()]
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENSKY_CLIENT_ID", None)
        os.environ.pop("OPENSKY_CLIENT_SECRET", None)
        with mock.patch.object(vip_flights.requests, "get", side_effect=calls) as g, \
             mock.patch.object(vip_flights.time, "sleep"):
            items = vip_flights.VipFlightsSection().pull()
    assert items == []           # empty states parse cleanly
    assert g.call_count == 3     # two retries then success


def test_vip_flights_raises_after_retries_exhausted():
    from worldscope.sections import vip_flights
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENSKY_CLIENT_ID", None)
        os.environ.pop("OPENSKY_CLIENT_SECRET", None)
        with mock.patch.object(vip_flights.requests, "get",
                               side_effect=requests.Timeout("always")), \
             mock.patch.object(vip_flights.time, "sleep"):
            with pytest.raises(UpstreamHTTPError):
                vip_flights.VipFlightsSection().pull()


def test_vip_flights_uses_oauth_when_credentials_present():
    from worldscope.sections import vip_flights
    vip_flights._TOKEN_CACHE.clear()
    token_resp = mock.Mock(status_code=200)
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {"access_token": "tok123", "expires_in": 1800}
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers
        return _states_resp()

    with mock.patch.dict(os.environ, {"OPENSKY_CLIENT_ID": "cid",
                                      "OPENSKY_CLIENT_SECRET": "sec"}), \
         mock.patch.object(vip_flights.requests, "post", return_value=token_resp), \
         mock.patch.object(vip_flights.requests, "get", side_effect=fake_get):
        vip_flights.VipFlightsSection().pull()
    assert captured["headers"].get("Authorization") == "Bearer tok123"
    vip_flights._TOKEN_CACHE.clear()
