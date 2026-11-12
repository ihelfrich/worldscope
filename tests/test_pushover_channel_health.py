"""The Pushover channel itself is tracked: a dead phone must surface as an
issue with a plain-English remedy, and close itself once delivery recovers.

Regression context: from 2026-11-11 every brief and watchdog page failed with
Pushover's HTTP-200 soft error "no active devices to send to". The runs went
red, but nothing told the owner *why* or that alarms were going nowhere.
"""
from __future__ import annotations

import pytest

from worldscope import alerting
from worldscope import pushover_delivery as pd


@pytest.fixture
def issue_log(monkeypatch):
    """Capture raise/resolve calls instead of hitting the GitHub API."""
    log: list[tuple] = []
    monkeypatch.setattr(alerting, "env_transport",
                        lambda: ("o/r", "owner", object()))

    def fake_raise(repo, key, title, message, **kw):
        log.append(("raise", key, title, message))
        return 99, True

    def fake_resolve(repo, key, note="", **kw):
        log.append(("resolve", key, note))
        return 99

    monkeypatch.setattr(alerting, "raise_alert", fake_raise)
    monkeypatch.setattr(alerting, "resolve_alert", fake_resolve)
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "t")
    return log


def _post_returning(response):
    return lambda payload: response


def test_diagnose_names_the_dead_device_remedy():
    advice = pd.diagnose("no active devices to send to")
    assert "no active device" in advice
    assert "Pushover app" in advice


def test_diagnose_distinguishes_bad_keys_from_dead_device():
    assert "PUSHOVER_USER_KEY" in pd.diagnose("user identifier is not a valid user")
    assert "PUSHOVER_APP_TOKEN" in pd.diagnose("application token is invalid")
    assert "not set" in pd.diagnose("Pushover credentials missing")
    assert "pushover.net" in pd.diagnose("something unforeseen")


def test_dead_device_opens_health_issue_and_step_succeeds(monkeypatch, issue_log):
    monkeypatch.setattr(pd, "_post", _post_returning(
        {"status": 1, "info": "no active devices to send to", "request": "x"}))
    rc = pd.main(["--title", "t", "--message", "m",
                  "--channel-health-issue", "pushover-delivery"])
    assert rc == 0  # on record + owner notified via GitHub, so not a red run
    assert len(issue_log) == 1
    kind, key, title, message = issue_log[0]
    assert (kind, key) == ("raise", "pushover-delivery")
    assert "not receiving" in title
    assert "Pushover app" in message and "no active devices" in message


def test_successful_delivery_closes_health_issue(monkeypatch, issue_log):
    monkeypatch.setattr(pd, "_post", _post_returning(
        {"status": 1, "request": "req-1"}))
    rc = pd.main(["--title", "t", "--message", "m",
                  "--channel-health-issue", "pushover-delivery"])
    assert rc == 0
    assert issue_log == [("resolve", "pushover-delivery",
                          "Recovered: Pushover accepted a message again.")]


def test_failure_without_any_record_still_fails_loudly(monkeypatch, issue_log):
    monkeypatch.setattr(pd, "_post", _post_returning(
        {"status": 1, "info": "no active devices to send to", "request": "x"}))

    def broken_env():
        raise alerting.AlertError("no token")
    monkeypatch.setattr(alerting, "env_transport", broken_env)
    rc = pd.main(["--title", "t", "--message", "m",
                  "--channel-health-issue", "pushover-delivery"])
    assert rc == 1  # neither channel recorded it: must stay red


def test_health_flag_absent_keeps_old_behaviour(monkeypatch, issue_log):
    monkeypatch.setattr(pd, "_post", _post_returning(
        {"status": 1, "info": "no active devices to send to", "request": "x"}))
    rc = pd.main(["--title", "t", "--message", "m"])
    assert rc == 1
    assert issue_log == []
