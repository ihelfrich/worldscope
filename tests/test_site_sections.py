"""Sections pages: real names, status chips from the run report, no empty-day padding."""
from worldscope import site_builder as sb

REPORT = {"sections": {
    "markets": {"state": "fresh"},
    "conflict": {"state": "stale_after_failure", "error_type": "UpstreamHTTPError"},
    "firms": {"state": "stale_after_failure", "error_type": "MissingCredential"},
    "people": {"state": "carry_forward", "source_date": "2026-05-25", "error_type": None},
    "promed": {"state": "no_data", "error_type": "FeedRetired"},
}}


def test_status_labels_match_the_brief():
    assert sb.section_status(REPORT, "markets") == ("ws-status-ok", "fresh")
    assert sb.section_status(REPORT, "conflict") == ("ws-status-bad", "upstream down")
    assert sb.section_status(REPORT, "firms") == ("ws-status-bad", "no key")
    assert sb.section_status(REPORT, "people") == ("ws-status-held", "carried from 2026-05-25")
    assert sb.section_status(REPORT, "promed") == ("ws-status-bad", "feed retired")
    assert sb.section_status(REPORT, "unknown") == ("", "")


def test_every_lake_section_has_a_real_name():
    ids = [d.name for d in sb.LAKE.iterdir() if d.is_dir() and not d.name.startswith("_")]
    crude = [sid for sid in ids if sid not in sb.PRETTY_NAMES]
    assert not crude, crude
