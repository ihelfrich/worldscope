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


# ---- brief picker + body builder (pushover-brief.yml "pick" step) ----------

from datetime import date


def test_pick_prefers_fresh_daily_over_weekly_by_filename_date():
    mod = _delivery_module()
    cands = ["weekly_briefings/2026-W41.md", "briefings/2026-10-08.md",
             "briefings/2026-10-09.md", "briefings/2026-10-01.md"]
    assert mod.pick_brief(cands, today=date(2026, 10, 9)) == "briefings/2026-10-09.md"
    # yesterday's daily still beats this week's weekly
    assert mod.pick_brief(cands[:2], today=date(2026, 10, 9)) == "briefings/2026-10-08.md"


def test_pick_ignores_mtime_order_and_uses_embedded_date():
    mod = _delivery_module()
    # candidates deliberately given newest-first as `ls -t` would in a fresh
    # checkout (all mtimes equal): the date in the name must decide.
    cands = ["briefings/2026-10-07.md", "briefings/2026-10-09.md", "briefings/2026-10-08.md"]
    assert mod.pick_brief(cands, today=date(2026, 10, 9)) == "briefings/2026-10-09.md"


def test_pick_falls_back_to_weekly_then_two_day_old_daily():
    mod = _delivery_module()
    today = date(2026, 10, 9)  # Friday, ISO week 41
    assert mod.pick_brief(["briefings/2026-10-07.md", "weekly_briefings/2026-W41.md"],
                          today=today) == "weekly_briefings/2026-W41.md"
    assert mod.pick_brief(["briefings/2026-10-07.md", "weekly_briefings/2026-W40.md"],
                          today=today) == "weekly_briefings/2026-W40.md"
    assert mod.pick_brief(["briefings/2026-10-07.md"], today=today) == "briefings/2026-10-07.md"


def test_pick_never_returns_a_stale_daily_or_old_weekly():
    mod = _delivery_module()
    today = date(2026, 10, 9)
    assert mod.pick_brief(["briefings/2026-10-06.md", "briefings/2026-09-05.md"],
                          today=today) is None
    assert mod.pick_brief(["weekly_briefings/2026-W36.md"], today=today) is None
    # future-dated and undated files are ignored too
    assert mod.pick_brief(["briefings/2026-10-10.md", "briefings/notes.md",
                           "briefings/2026-10-09-ukraine.md"], today=today) is None


def test_pick_skips_already_notified_briefs():
    mod = _delivery_module()
    today = date(2026, 10, 9)
    cands = ["briefings/2026-10-09.md", "briefings/2026-10-08.md"]
    assert mod.pick_brief(cands, today=today,
                          sent={"briefings/2026-10-09.md"}) == "briefings/2026-10-08.md"
    assert mod.pick_brief(cands, today=today, sent=set(cands)) is None


def test_brief_body_truncates_to_900_bytes_without_breaking_utf8():
    mod = _delivery_module()
    md = "# Headline\n" + ("Ünïcode **bold** [link](https://x) `code` > quote\n" * 100)
    body = mod.brief_body(md)
    assert len(body.encode("utf-8")) <= 900
    assert body.encode("utf-8")  # valid UTF-8 round-trip
    assert "Headline" not in body
    assert "**" not in body and "](" not in body and "`" not in body
    assert body.startswith("Ünïcode bold link code  quote")


def test_brief_headline_prefers_first_h1():
    mod = _delivery_module()
    assert mod.brief_headline("# Big day\nrest") == "Big day"
    assert mod.brief_headline("> **DATA NOTE:** x\n\n# Real title\n") == "Real title"
    assert mod.brief_headline("no heading at all\n") == "no heading at all"
    assert mod.brief_headline("") == ""


def test_pick_cli_writes_github_output_and_body(tmp_path):
    mod = _delivery_module()
    (tmp_path / "briefings").mkdir()
    (tmp_path / "weekly_briefings").mkdir()
    (tmp_path / "briefings" / "2026-10-09.md").write_text(
        "# Today\n" + "x" * 2000, encoding="utf-8")
    (tmp_path / "weekly_briefings" / "2026-W41.md").write_text("# Week\nbody", encoding="utf-8")
    sent = tmp_path / ".pushover-sent.json"
    sent.write_text("[]")
    out = tmp_path / "gh_out.txt"
    body = tmp_path / "body.txt"
    rc = mod.main(["pick", "--root", str(tmp_path), "--sent-file", str(sent),
                   "--manual", "", "--today", "2026-10-09",
                   "--body-file", str(body), "--github-output", str(out)])
    assert rc == 0
    lines = out.read_text().splitlines()
    assert "file=briefings/2026-10-09.md" in lines
    assert "kind=Daily" in lines
    assert "url_path=briefings/2026-10-09.html" in lines
    assert "headline=Today" in lines
    assert len(body.read_bytes()) == 900

    # stale: nothing fresh -> file= empty, no body written
    out2 = tmp_path / "gh_out2.txt"
    body2 = tmp_path / "body2.txt"
    rc = mod.main(["pick", "--root", str(tmp_path), "--sent-file", str(sent),
                   "--manual", "", "--today", "2026-11-01",
                   "--body-file", str(body2), "--github-output", str(out2)])
    assert rc == 0
    assert out2.read_text().splitlines() == ["file="]
    assert not body2.exists()

    # already-notified daily is skipped; this week's weekly is next in line
    sent.write_text(json.dumps(["briefings/2026-10-09.md"]))
    out3 = tmp_path / "gh_out3.txt"
    rc = mod.main(["pick", "--root", str(tmp_path), "--sent-file", str(sent),
                   "--manual", "", "--today", "2026-10-09",
                   "--body-file", str(tmp_path / "body3.txt"),
                   "--github-output", str(out3)])
    assert rc == 0
    assert "file=weekly_briefings/2026-W41.md" in out3.read_text().splitlines()
    # ... and once both are notified, nothing is re-sent
    sent.write_text(json.dumps(["briefings/2026-10-09.md", "weekly_briefings/2026-W41.md"]))
    out4 = tmp_path / "gh_out4.txt"
    rc = mod.main(["pick", "--root", str(tmp_path), "--sent-file", str(sent),
                   "--manual", "", "--today", "2026-10-09",
                   "--body-file", str(tmp_path / "body4.txt"),
                   "--github-output", str(out4)])
    assert rc == 0
    assert out4.read_text().splitlines() == ["file="]


def test_pick_cli_manual_brief_bypasses_freshness_not_marker(tmp_path):
    mod = _delivery_module()
    (tmp_path / "weekly_briefings").mkdir()
    old = tmp_path / "weekly_briefings" / "2026-W30.md"
    old.write_text("# Old week\nbody", encoding="utf-8")
    sent = tmp_path / ".pushover-sent.json"
    sent.write_text("[]")
    out = tmp_path / "gh_out.txt"
    rc = mod.main(["pick", "--root", str(tmp_path), "--sent-file", str(sent),
                   "--manual", "weekly_briefings/2026-W30.md", "--today", "2026-10-09",
                   "--body-file", str(tmp_path / "b.txt"), "--github-output", str(out)])
    assert rc == 0
    lines = out.read_text().splitlines()
    assert "file=weekly_briefings/2026-W30.md" in lines
    assert "kind=Weekly" in lines
    assert "url_path=weekly_briefings/2026-W30.html" in lines
    sent.write_text(json.dumps(["weekly_briefings/2026-W30.md"]))
    out2 = tmp_path / "gh_out2.txt"
    mod.main(["pick", "--root", str(tmp_path), "--sent-file", str(sent),
              "--manual", "weekly_briefings/2026-W30.md", "--today", "2026-10-09",
              "--body-file", str(tmp_path / "b2.txt"), "--github-output", str(out2)])
    assert out2.read_text().splitlines() == ["file="]

