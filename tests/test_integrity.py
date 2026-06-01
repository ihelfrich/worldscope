"""Tests for the data-integrity layer (worldscope.integrity)."""
from datetime import date

from worldscope import integrity as ig

TODAY = date(2026, 6, 1)


def test_fresh_when_records_today():
    r = ig.classify_section("federal_register", today=TODAY,
                            last_record_date="2026-06-01", today_count=12)
    assert r.status == "FRESH" and "12 records" in r.reason


def test_no_key_takes_precedence_over_empty():
    # markets is empty AND its key is missing → report the root cause.
    r = ig.classify_section("markets", today=TODAY, last_record_date=None,
                            today_count=0, missing_keys=["FINNHUB_API_KEY"])
    assert r.status == "NO_KEY" and "FINNHUB_API_KEY" in r.reason


def test_failed_carries_error_first_line():
    r = ig.classify_section("reliefweb", today=TODAY, last_record_date=None,
                            today_count=0, consecutive_failures=3,
                            last_failure_error="HTTP 410 Gone\ntraceback...")
    assert r.status == "FAILED" and "410" in r.reason and "3×" in r.reason


def test_stale_vs_empty_by_age():
    stale = ig.classify_section("conflict", today=TODAY,
                                last_record_date="2026-05-30", today_count=0)
    empty = ig.classify_section("acled", today=TODAY,
                                last_record_date="2026-04-01", today_count=0)
    assert stale.status == "STALE"
    assert empty.status == "EMPTY"


def test_never_produced_is_empty():
    r = ig.classify_section("promed", today=TODAY, last_record_date=None, today_count=0)
    assert r.status == "EMPTY" and "never" in r.reason


def test_skipped_wins():
    r = ig.classify_section("sanctions", today=TODAY, last_record_date=None,
                            today_count=0, missing_keys=["X"], skipped=True)
    assert r.status == "SKIPPED"


def test_missing_keys_reads_env():
    assert ig._missing_keys("macro", env={}) == ["FRED_API_KEY"]
    assert ig._missing_keys("macro", env={"FRED_API_KEY": "x"}) == []
    assert ig._missing_keys("acled", env={"ACLED_EMAIL": "a"}) == ["ACLED_PASSWORD"]
    assert ig._missing_keys("federal_register", env={}) == []  # keyless source


def test_skip_set_parsing():
    assert ig._skip_set({"WORLDSCOPE_SKIP": "sanctions, people"}) == {"sanctions", "people"}
    assert ig._skip_set({}) == set()


def test_summary_line_is_honest_and_quantified():
    reports = [
        ig.classify_section("a", today=TODAY, last_record_date="2026-06-01", today_count=3),
        ig.classify_section("markets", today=TODAY, last_record_date=None,
                            today_count=0, missing_keys=["FINNHUB_API_KEY"]),
        ig.classify_section("promed", today=TODAY, last_record_date=None, today_count=0),
    ]
    line = ig.summary_line(reports)
    assert "1/3 sections fresh" in line
    assert "awaiting credentials" in line and "markets" in line
    assert "empty" in line and "gaps are shown, not hidden" in line


def test_render_panel_hides_when_all_fresh():
    reports = [ig.classify_section("a", today=TODAY,
                                   last_record_date="2026-06-01", today_count=1)]
    assert ig.render_integrity_panel(reports) == ""


def test_render_panel_flags_problems():
    reports = [
        ig.classify_section("a", today=TODAY, last_record_date="2026-06-01", today_count=1),
        ig.classify_section("markets", today=TODAY, last_record_date=None,
                            today_count=0, missing_keys=["FINNHUB_API_KEY"]),
    ]
    html = ig.render_integrity_panel(reports)
    assert "Data integrity" in html and "markets" in html and "NO_KEY" in html
