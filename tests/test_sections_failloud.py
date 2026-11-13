"""Sections must fail loud, not 'succeed' with zero records.

Each of these adapters used to turn every failure into ``return []``, which the
run report records as fresh_empty: an innocuous-looking empty that hid real
outages for weeks (Forbes refusing limit=1500 with 503; a Wikidata roster key
rename that emptied the roster). A raised SourceUnavailable instead becomes
stale_after_failure / no_key, carries the previous snapshot forward and shows
up in source health.
"""
from __future__ import annotations

import pytest

from worldscope.sections import (
    MissingCredential,
    UpstreamAuthError,
    UpstreamHTTPError,
    UpstreamParseError,
)
from worldscope.sections import billionaires, conflict, firms, mediacloud, wikidata_changes

@pytest.fixture(autouse=True)
def _doc_path_only(monkeypatch):
    """These tests exercise the DOC API fallback; no bulk-file download."""
    from worldscope.lib import gdelt_files

    def unavailable(*a, **k):
        raise gdelt_files.GdeltFilesUnavailable("disabled in tests")
    monkeypatch.setattr(gdelt_files, "recent_articles", unavailable)



class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Store:
    def most_recent(self, _id):
        return None


def _bare(cls):
    obj = cls.__new__(cls)
    obj.store = _Store()
    return obj


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    from worldscope.lib import gdelt
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(gdelt, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(gdelt, "PENALTY_S", 0.0)
    gdelt.reset()


# --- billionaires -----------------------------------------------------------

def test_billionaires_requests_a_size_forbes_serves(monkeypatch):
    seen = {}

    def get(url, params=None, **k):
        seen.update(params)
        return _Resp(200, {"personList": {"personsLists": [
            {"rank": 1, "personName": "A", "finalWorth": 400000, "uri": "a"}]}})
    monkeypatch.setattr(billionaires.requests, "get", get)
    items = _bare(billionaires.BillionairesSection).pull()
    assert seen["limit"] <= 200 and items and items[0]["title"].startswith("#1 A")


def test_billionaires_http_failure_raises(monkeypatch):
    monkeypatch.setattr(billionaires.requests, "get", lambda *a, **k: _Resp(503, None))
    with pytest.raises(UpstreamHTTPError, match="503"):
        _bare(billionaires.BillionairesSection).pull()


def test_billionaires_empty_list_raises(monkeypatch):
    monkeypatch.setattr(billionaires.requests, "get", lambda *a, **k: _Resp(200, {"personList": {}}))
    with pytest.raises(UpstreamParseError):
        _bare(billionaires.BillionairesSection).pull()


# --- wikidata_changes -------------------------------------------------------

_ROSTER = [{"country": "France", "leader_name": "Leader X", "leader_qid": "Q1"}]


def test_wikidata_reads_leader_qid_roster(monkeypatch):
    monkeypatch.setattr(wikidata_changes, "current_heads_of_state", lambda: _ROSTER)
    monkeypatch.setattr(wikidata_changes, "current_heads_of_government", lambda: [])
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = {"title": "Q1", "revisions": [{"revid": 9, "timestamp": now, "comment": "edit"}]}
    monkeypatch.setattr(wikidata_changes.requests, "get",
                        lambda *a, **k: _Resp(200, {"query": {"pages": [page]}}))
    items = _bare(wikidata_changes.WikidataChangesSection).pull()
    assert len(items) == 1
    assert items[0]["person"] == "Leader X" and items[0]["country"] == "France"


def test_wikidata_empty_roster_raises(monkeypatch):
    monkeypatch.setattr(wikidata_changes, "current_heads_of_state", lambda: [{"country": "X"}])
    monkeypatch.setattr(wikidata_changes, "current_heads_of_government", lambda: [])
    with pytest.raises(UpstreamParseError):
        _bare(wikidata_changes.WikidataChangesSection).pull()


def test_wikidata_all_batches_failing_raises(monkeypatch):
    monkeypatch.setattr(wikidata_changes, "current_heads_of_state", lambda: _ROSTER)
    monkeypatch.setattr(wikidata_changes, "current_heads_of_government", lambda: [])
    monkeypatch.setattr(wikidata_changes.requests, "get", lambda *a, **k: _Resp(500))
    with pytest.raises(UpstreamHTTPError):
        _bare(wikidata_changes.WikidataChangesSection).pull()


def test_wikidata_api_error_body_is_a_failure_not_an_empty(monkeypatch):
    monkeypatch.setattr(wikidata_changes, "current_heads_of_state", lambda: _ROSTER)
    monkeypatch.setattr(wikidata_changes, "current_heads_of_government", lambda: [])
    body = {"error": {"code": "invalidparammix", "info": "rvlimit needs a single page"}}
    monkeypatch.setattr(wikidata_changes.requests, "get", lambda *a, **k: _Resp(200, body))
    with pytest.raises(UpstreamHTTPError, match="invalidparammix"):
        _bare(wikidata_changes.WikidataChangesSection).pull()


def test_wikidata_old_edit_is_outside_the_window(monkeypatch):
    monkeypatch.setattr(wikidata_changes, "current_heads_of_state", lambda: _ROSTER)
    monkeypatch.setattr(wikidata_changes, "current_heads_of_government", lambda: [])
    page = {"title": "Q1", "revisions": [{"revid": 1, "timestamp": "2020-01-01T00:00:00Z"}]}
    monkeypatch.setattr(wikidata_changes.requests, "get",
                        lambda *a, **k: _Resp(200, {"query": {"pages": [page]}}))
    assert _bare(wikidata_changes.WikidataChangesSection).pull() == []


def test_wikidata_quiet_window_is_a_genuine_empty(monkeypatch):
    monkeypatch.setattr(wikidata_changes, "current_heads_of_state", lambda: _ROSTER)
    monkeypatch.setattr(wikidata_changes, "current_heads_of_government", lambda: [])
    monkeypatch.setattr(wikidata_changes.requests, "get",
                        lambda *a, **k: _Resp(200, {"query": {"pages": [{"title": "Q1"}]}}))
    assert _bare(wikidata_changes.WikidataChangesSection).pull() == []


# --- conflict (GDELT DOC) ---------------------------------------------------

def test_conflict_rate_limited_twice_raises(monkeypatch):
    monkeypatch.setattr(conflict.gdelt.requests, "get", lambda *a, **k: _Resp(429))
    with pytest.raises(UpstreamHTTPError, match="429"):
        _bare(conflict.ConflictSection).pull()


def test_conflict_non_json_raises_parse_error(monkeypatch):
    monkeypatch.setattr(conflict.gdelt.requests, "get", lambda *a, **k: _Resp(200, None))
    with pytest.raises(UpstreamParseError):
        _bare(conflict.ConflictSection).pull()


def test_conflict_quiet_window_is_a_genuine_empty(monkeypatch):
    monkeypatch.setattr(conflict.gdelt.requests, "get", lambda *a, **k: _Resp(200, {"articles": []}))
    assert _bare(conflict.ConflictSection).pull() == []


# --- FIRMS ------------------------------------------------------------------

def test_firms_missing_key(monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    with pytest.raises(MissingCredential):
        _bare(firms.FirmsSection).pull()


def test_firms_invalid_key_is_auth_error(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", "bad")
    monkeypatch.setattr(firms.requests, "get",
                        lambda *a, **k: _Resp(200, text="Invalid MAP_KEY."))
    with pytest.raises(UpstreamAuthError):
        _bare(firms.FirmsSection).pull()


def test_firms_header_only_csv_is_a_genuine_empty(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", "ok")
    header = "latitude,longitude,bright_ti4,acq_date,acq_time,satellite,confidence,frp\n"
    monkeypatch.setattr(firms.requests, "get", lambda *a, **k: _Resp(200, text=header))
    assert _bare(firms.FirmsSection).pull() == []


# --- MediaCloud -------------------------------------------------------------

def test_mediacloud_missing_key(monkeypatch):
    monkeypatch.delenv("MEDIACLOUD_API_KEY", raising=False)
    with pytest.raises(MissingCredential):
        _bare(mediacloud.MediaCloudSection).pull()


def test_mediacloud_all_areas_rejected_is_auth_error(monkeypatch):
    class _Area:
        name, keywords, priority, topics = "A", ["kw"], "high", []

    class _Search:
        def __init__(self, key):
            pass

        def story_list(self, *a, **k):
            raise RuntimeError("401 Unauthorized")

    class _Api:
        SearchApi = _Search
    monkeypatch.setenv("MEDIACLOUD_API_KEY", "k")
    monkeypatch.setattr(mediacloud, "_mc_api", _Api)
    monkeypatch.setattr(mediacloud, "load_watch_areas", lambda: [_Area()])
    with pytest.raises(UpstreamAuthError):
        _bare(mediacloud.MediaCloudSection).pull()
