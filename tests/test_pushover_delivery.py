"""Delivery receipts must represent an accepted, routable Pushover message."""
from __future__ import annotations

import importlib
import json

import pytest


def _delivery_module():
    try:
        return importlib.import_module("worldscope.pushover_delivery")
    except ModuleNotFoundError:
        pytest.fail("worldscope.pushover_delivery is not implemented")


def test_missing_credentials_cannot_record_brief_as_notified(tmp_path):
    delivery = _delivery_module()
    sent = tmp_path / ".pushover-sent.json"
    sent.write_text('["briefings/earlier.md"]')

    def must_not_send(_payload):
        raise AssertionError("transport called without credentials")

    with pytest.raises(delivery.PushoverDeliveryError, match="credentials missing"):
        delivery.send_and_record(
            brief="briefings/2026-09-05.md", sent_file=sent,
            user_key="", app_token="", title="Daily brief", message="Body",
            url="https://example.test/brief.html", transport=must_not_send,
        )
    assert json.loads(sent.read_text()) == ["briefings/earlier.md"]


def test_no_active_devices_cannot_record_brief_as_notified(tmp_path):
    delivery = _delivery_module()
    sent = tmp_path / ".pushover-sent.json"
    sent.write_text("[]")

    def no_devices(_payload):
        return {"status": 1, "request": "request-id",
                "info": "no active devices to send to"}

    with pytest.raises(delivery.PushoverDeliveryError, match="no active devices"):
        delivery.send_and_record(
            brief="briefings/2026-09-05.md", sent_file=sent,
            user_key="user", app_token="app", title="Daily brief", message="Body",
            url="https://example.test/brief.html", transport=no_devices,
        )
    assert json.loads(sent.read_text()) == []


def test_accepted_response_records_brief_once(tmp_path):
    delivery = _delivery_module()
    sent = tmp_path / ".pushover-sent.json"
    sent.write_text("[]")

    def accepted(_payload):
        return {"status": 1, "request": "request-id"}

    request_id = delivery.send_and_record(
        brief="briefings/2026-09-05.md", sent_file=sent,
        user_key="user", app_token="app", title="Daily brief", message="Body",
        url="https://example.test/brief.html", transport=accepted,
    )
    assert request_id == "request-id"
    assert json.loads(sent.read_text()) == ["briefings/2026-09-05.md"]


def test_api_rejection_does_not_create_marker_file(tmp_path):
    delivery = _delivery_module()
    sent = tmp_path / ".pushover-sent.json"

    def rejected(_payload):
        return {"status": 0, "request": "request-id",
                "errors": ["user identifier is invalid"]}

    with pytest.raises(delivery.PushoverDeliveryError, match="user identifier is invalid"):
        delivery.send_and_record(
            brief="briefings/2026-09-05.md", sent_file=sent,
            user_key="user", app_token="app", title="Daily brief", message="Body",
            url="https://example.test/brief.html", transport=rejected,
        )
    assert not sent.exists()
