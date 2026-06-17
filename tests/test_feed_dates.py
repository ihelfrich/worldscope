"""Tests for feed date resolution + the recency gate (worldscope.sections.state_news).

These guard the staleness fix: a feed entry with a real but unusually-formatted
date must resolve (so an old article is correctly dated and dropped from the
fresh window), and an entry with no date must not silently masquerade as fresh
with a blank record_date.
"""
from datetime import date

from worldscope.sections.state_news import (
    _normalize_feed_date, _parse_rss, keep_recent,
)


# ---- date normalization ----------------------------------------------------

def test_rfc822_pubdate_resolves():
    assert _normalize_feed_date("Mon, 09 Jun 2025 12:00:00 GMT") == "2025-06-09"


def test_iso8601_with_z_resolves():
    assert _normalize_feed_date("2026-06-10T14:30:00Z") == "2026-06-10"


def test_bare_iso_date_resolves():
    assert _normalize_feed_date("2026-06-10") == "2026-06-10"


def test_garbage_yields_empty_not_junk():
    # Must NOT return a truncated junk string like "Mon, 09 Ju".
    assert _normalize_feed_date("Mon, 09 Ju") == ""
    assert _normalize_feed_date("") == ""
    assert _normalize_feed_date("not a date") == ""


# ---- dc:date capture in the parser -----------------------------------------

def test_parser_captures_dublin_core_date():
    xml = b"""<?xml version="1.0"?>
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
             xmlns="http://purl.org/rss/1.0/"
             xmlns:dc="http://purl.org/dc/elements/1.1/">
      <item>
        <title>Old analysis of the budget bill</title>
        <link>https://example.org/a</link>
        <dc:date>2025-06-15T08:00:00Z</dc:date>
      </item>
    </rdf:RDF>"""
    items = _parse_rss(xml)
    assert len(items) == 1
    assert items[0]["date"] == "2025-06-15"  # previously came back blank


# ---- recency gate ----------------------------------------------------------

def test_keep_recent_drops_old_dated_item():
    today = date(2026, 6, 17)
    cutoff = date(2026, 6, 15)
    it = {"date": "2025-06-15", "title": "year-old story"}
    assert keep_recent(it, cutoff, today=today) is False  # the staleness bug


def test_keep_recent_keeps_fresh_dated_item():
    today = date(2026, 6, 17)
    cutoff = date(2026, 6, 15)
    it = {"date": "2026-06-16", "title": "fresh"}
    assert keep_recent(it, cutoff, today=today) is True
    assert "date_estimated" not in it


def test_keep_recent_flags_and_stamps_undated_item():
    today = date(2026, 6, 17)
    cutoff = date(2026, 6, 15)
    it = {"date": "", "title": "no date in feed"}
    assert keep_recent(it, cutoff, today=today) is True   # kept (coverage)
    assert it["date"] == "2026-06-17"                     # never blank
    assert it["date_estimated"] is True                   # but marked estimated
