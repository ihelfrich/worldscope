"""gdelt_regions: bounded, parallel, partial-tolerant country queries.

The 429 fixture is the live plain-text body GDELT returned on 2026-10-09:
"Please limit requests to one every 5 seconds ...". The artlist fixture is
the DOC 2.0 article shape (url, title, seendate, domain, language,
sourcecountry) the adapter has always consumed.
"""
import threading
import time

import pytest
import requests

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


def _store(tmp_path):
    return SnapshotStore(tmp_path / "s.sqlite")


def _section(tmp_path, **attrs):
    sec = gr.GdeltRegionsSection(store=_store(tmp_path))
    for k, v in attrs.items():
        setattr(sec, k, v)
    return sec


def test_partial_results_are_kept_and_normalized(monkeypatch, tmp_path):
    def fetch(self, code, params):
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


def test_budget_returns_partial_without_waiting_for_slow_queries(monkeypatch, tmp_path):
    release = threading.Event()

    def fetch(self, code, params):
        if code == "CH":
            return {"articles": [ART]}
        release.wait(5)          # slow / hung request
        return {"articles": [ART]}

    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", fetch)
    sec = _section(tmp_path, BUDGET_S=0.5, MAX_WORKERS=2)
    t0 = time.monotonic()
    try:
        items = sec.pull()
    finally:
        release.set()
    assert time.monotonic() - t0 < 2.0
    assert [it["country"] for it in items] == ["China"]


def test_queries_run_concurrently(monkeypatch, tmp_path):
    active, peak = [0], [0]
    lock = threading.Lock()

    def fetch(self, code, params):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return {"articles": []}

    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", fetch)
    items = _section(tmp_path, MAX_WORKERS=2).pull()
    assert items == []           # every country answered (empty) -> quiet, not failure
    assert peak[0] == 2


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
    monkeypatch.setattr(gr.requests, "get", lambda url, **k: (seen.append(k), next(seq))[1])
    sleeps = []
    monkeypatch.setattr(gr.time, "sleep", lambda s: sleeps.append(s))
    sec = _section(tmp_path)
    sec._deadline = time.monotonic() + 60
    assert sec._fetch_one("JA", {"query": "q"}) == {"articles": [ART]}
    assert sleeps == [sec.RETRY_SLEEP_S]
    assert all(k["timeout"] == sec.REQUEST_TIMEOUT_S for k in seen)


def test_fetch_one_gives_up_on_429_when_budget_is_short(monkeypatch, tmp_path):
    monkeypatch.setattr(gr.requests, "get", lambda url, **k: _Resp(429, text=RATE_LIMIT_TEXT))
    monkeypatch.setattr(gr.time, "sleep", lambda s: pytest.fail("must not sleep past budget"))
    sec = _section(tmp_path)
    sec._deadline = time.monotonic() + 1.0
    assert sec._fetch_one("JA", {"query": "q"}) is None


def test_fetch_one_never_raises(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise requests.ReadTimeout("slow")
    monkeypatch.setattr(gr.requests, "get", boom)
    assert _section(tmp_path)._fetch_one("JA", {}) is None
    monkeypatch.setattr(gr.requests, "get", lambda *a, **k: _Resp(500))
    assert _section(tmp_path)._fetch_one("JA", {}) is None


def test_deadline_is_inside_section_timeout():
    assert gr.GdeltRegionsSection.BUDGET_S < 60
    assert gr.GdeltRegionsSection.BUDGET_S + gr.GdeltRegionsSection.REQUEST_TIMEOUT_S \
        <= gr.GdeltRegionsSection.PULL_TIMEOUT_S
