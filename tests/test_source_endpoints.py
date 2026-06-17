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


# ---- Source coverage page --------------------------------------------------

def test_coverage_table_renders_and_orders_by_status():
    from worldscope.site_builder import _coverage_table_html
    report = {
        "date": "2026-06-10",
        "sections": [
            {"section_id": "foreign_news", "status": "FRESH", "reason": "120 records today",
             "last_record_date": "2026-06-10", "today_count": 120, "consecutive_failures": 0},
            {"section_id": "acled", "status": "FAILED", "reason": "auth failed",
             "last_record_date": "2026-06-02", "today_count": 0, "consecutive_failures": 20},
            {"section_id": "firms", "status": "NO_KEY", "reason": "FIRMS_MAP_KEY not set",
             "last_record_date": None, "today_count": 0, "consecutive_failures": 0},
        ],
    }
    html_out = _coverage_table_html(report)
    # FAILED must sort above FRESH.
    assert html_out.index("acled") < html_out.index("foreign_news")
    # Status chips present for each status.
    assert "FAILED" in html_out and "NO_KEY" in html_out and "FRESH" in html_out
    # Fresh section links to its drill-down; the no-record/no-key one does not.
    assert 'href="sections/foreign_news/"' in html_out
    assert 'href="sections/firms/"' not in html_out
