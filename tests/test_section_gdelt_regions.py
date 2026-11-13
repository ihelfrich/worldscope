"""gdelt_regions: bounded, sequential, partial-tolerant country queries.

The 429 fixture is the live plain-text body GDELT returned on 2026-10-09:
"Please limit requests to one every 5 seconds ...". The artlist fixture is
the DOC 2.0 article shape (url, title, seendate, domain, language,
sourcecountry) the adapter has always consumed.
"""
import threading
import time

import pytest
import requests

from worldscope.lib import gdelt
from worldscope.sections import UpstreamHTTPError
from worldscope.sections import gdelt_regions as gr
from worldscope.store import SnapshotStore

ART = {
    "url": "https://www.example.jp/news/2026/10/09/story",
    "url_mobile": "",
    "title": "Cabinet approves supplementary budget",
    "seendate": "20261009T061500Z",
    "socialimage": "",
    "domain": "example.jp",
    "language": "English",
    "sourcecountry": "Japan",
}
RATE_LIMIT_TEXT = ("Please limit requests to one every 5 seconds or contact "
                   "kalev.leetaru5@gmail.com for larger queries.")

@pytest.fixture(autouse=True)
def _doc_path_only(monkeypatch):
    """These tests exercise the DOC API fallback; no bulk-file download."""
    from worldscope.lib import gdelt_files

    def unavailable(*a, **k):
        raise gdelt_files.GdeltFilesUnavailable("disabled in tests")
    monkeypatch.setattr(gdelt_files, "recent_articles", unavailable)



@pytest.fixture(autouse=True)
def _fresh_spacer():
    gdelt.reset()
    yield
    gdelt.reset()


def _store(tmp_path):
    return SnapshotStore(tmp_path / "s.sqlite")


def _section(tmp_path, **attrs):
    sec = gr.GdeltRegionsSection(store=_store(tmp_path))
    for k, v in attrs.items():
        setattr(sec, k, v)
    return sec


def test_partial_results_are_kept_and_normalized(monkeypatch, tmp_path):
    def fetch(self, code, params):
        if code == "ALL":
            return None          # combined query rejected -> per-country fallback
        assert params["query"] == f"sourcecountry:{code} sourcelang:english"
        assert params["mode"] == "artlist" and params["maxrecords"] == self.PER_COUNTRY
        if code in ("JA", "UP"):
            return {"articles": [ART, {**ART, "url": "https://x/2", "seendate": "bad"}]}
        return None   # rate-limited / failed: acceptable, not fatal

    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", fetch)
    items = _section(tmp_path).pull()
    assert len(items) == 4
    assert {it["country"] for it in items} == {"Japan", "Ukraine"}
    good = next(it for it in items if it["url"] == ART["url"] and it["country"] == "Japan")
    assert good == {
        "id": ART["url"] + "|20261009T061500Z",
        "date": "2026-10-09",
        "title": "[Japan] Cabinet approves supplementary budget",
        "url": ART["url"],
        "summary": "example.jp · English",
        "country": "Japan",
        "domain": "example.jp",
        "tone": "",
        "language": "English",
    }
    assert items[0]["date"] >= items[-1]["date"]   # newest first; bad dates sort last
    assert "" in {it["date"] for it in items}


def test_all_countries_failing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", lambda self, c, p: None)
    with pytest.raises(UpstreamHTTPError):
        _section(tmp_path).pull()


def test_combined_query_covers_countries_and_tops_up_the_rest(monkeypatch, tmp_path):
    calls = []

    def fetch(self, code, params):
        calls.append(code)
        if code == "ALL":
            assert params["maxrecords"] == self.COMBINED_RECORDS
            assert "sourcecountry:JA OR" in params["query"]
            japan = [{**ART, "url": f"https://jp/{i}"} for i in range(10)]
            other = [{**ART, "url": "https://x/1", "sourcecountry": "Atlantis"},
                     {**ART, "url": "https://kr/1", "sourcecountry": "South Korea"}]
            return {"articles": japan + other}
        return {"articles": [{**ART, "url": f"https://{code}/1"}]} if code == "UP" else {"articles": []}

    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", fetch)
    sec = _section(tmp_path)
    items = sec.pull()
    counts = {}
    for it in items:
        counts[it["country"]] = counts.get(it["country"], 0) + 1
    assert counts == {"Japan": sec.PER_COUNTRY, "South Korea": 1, "Ukraine": 1}
    assert "JA" not in calls and "KS" not in calls      # covered: no top-up
    assert calls[0] == "ALL" and len(calls) == 1 + len(gr.WATCHLIST) - 2
    assert sec.coverage == {"combined": True, "countries": 3, "of": len(gr.WATCHLIST),
                            "topped_up": 1}


def test_budget_stops_issuing_queries(monkeypatch, tmp_path):
    calls = []
    clock = [0.0]
    monkeypatch.setattr(gr.time, "monotonic", lambda: clock[0])

    def fetch(self, code, params):
        calls.append(code)
        clock[0] += 10.0          # each query costs 10 s of wall clock
        return {"articles": [ART]}

    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", fetch)
    items = _section(tmp_path, BUDGET_S=25).pull()
    assert calls[0] == "ALL" and len(calls) == 3   # combined + 2 top-ups
    assert len(items) == 3


def test_queries_are_sequential_and_cover_every_country(monkeypatch, tmp_path):
    active, peak, seen = [0], [0], []
    lock = threading.Lock()

    def fetch(self, code, params):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        if code != "ALL":
            seen.append(code)
        with lock:
            active[0] -= 1
        return {"articles": []}

    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", fetch)
    items = _section(tmp_path).pull()
    assert items == []           # every country answered (empty) -> quiet, not failure
    assert peak[0] == 1
    assert sorted(seen) == sorted(c for c, _ in gr.WATCHLIST)


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._body


def test_fetch_one_retries_a_429_once_then_returns(monkeypatch, tmp_path):
    seq = iter([_Resp(429, text=RATE_LIMIT_TEXT), _Resp(200, {"articles": [ART]})])
    seen = []
    monkeypatch.setattr(gdelt.requests, "get", lambda url, **k: (seen.append(k), next(seq))[1])
    monkeypatch.setattr(gdelt.time, "sleep", lambda s: None)
    sec = _section(tmp_path)
    sec._deadline = time.monotonic() + 60
    assert sec._fetch_one("JA", {"query": "q"}) == {"articles": [ART]}
    assert len(seen) == 2
    assert all(k["timeout"] == sec.REQUEST_TIMEOUT_S for k in seen)


def test_fetch_one_gives_up_on_429_when_budget_is_short(monkeypatch, tmp_path):
    monkeypatch.setattr(gdelt.requests, "get", lambda url, **k: _Resp(429, text=RATE_LIMIT_TEXT))
    sleeps = []
    monkeypatch.setattr(gdelt.time, "sleep", lambda s: sleeps.append(s))
    sec = _section(tmp_path)
    sec._deadline = time.monotonic() + 1.0
    assert sec._fetch_one("JA", {"query": "q"}) is None
    assert sum(sleeps) < 1.0     # never waits past the budget
    assert "429" in sec.last_error


def test_fetch_one_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(gdelt.time, "sleep", lambda s: None)

    def boom(*a, **k):
        raise requests.ReadTimeout("slow")
    monkeypatch.setattr(gdelt.requests, "get", boom)
    assert _section(tmp_path)._fetch_one("JA", {}) is None
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: _Resp(500))
    assert _section(tmp_path)._fetch_one("JA", {}) is None


def test_deadline_is_inside_section_timeout():
    # all 19 countries fit at one request per MIN_INTERVAL_S
    assert len(gr.WATCHLIST) * gdelt.MIN_INTERVAL_S <= gr.GdeltRegionsSection.BUDGET_S
    assert gr.GdeltRegionsSection.BUDGET_S + gr.GdeltRegionsSection.REQUEST_TIMEOUT_S \
        <= gr.GdeltRegionsSection.PULL_TIMEOUT_S
