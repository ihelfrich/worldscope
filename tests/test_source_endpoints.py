"""Tests for the stale-source fixes: ReliefWeb v2 endpoint, ProMED + ReliefWeb
env-configurable endpoints, and ACLED auth-failure diagnostics."""
import os
import importlib
from unittest import mock

import pytest

from worldscope.sections import UpstreamAuthError


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

    fake_resp = mock.Mock(status_code=403, text="Forbidden: account not approved")
    with mock.patch.dict(os.environ, {"ACLED_EMAIL": "a@b.com", "ACLED_PASSWORD": "x"}), \
         mock.patch.object(acled, "TOKEN_CACHE") as cache, \
         mock.patch.object(acled.requests, "post", return_value=fake_resp):
        cache.exists.return_value = False  # no cached token
        with pytest.raises(UpstreamAuthError) as ei:
            sec._get_token()
    msg = str(ei.value)
    assert "403" in msg
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
