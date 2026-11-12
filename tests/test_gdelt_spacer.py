"""The shared GDELT spacer: one request per MIN_INTERVAL_S across the process."""
import threading
import time

import pytest

from worldscope.lib import gdelt


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.fixture(autouse=True)
def _reset():
    gdelt.reset()
    yield
    gdelt.reset()


def test_requests_from_many_threads_are_spaced(monkeypatch):
    monkeypatch.setattr(gdelt, "MIN_INTERVAL_S", 0.05)
    starts = []
    lock = threading.Lock()

    def get(*a, **k):
        with lock:
            starts.append(time.monotonic())
        return _Resp(200, {"articles": []})
    monkeypatch.setattr(gdelt.requests, "get", get)
    threads = [threading.Thread(target=gdelt.doc_get, args=({"q": i},)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 5 and min(gaps) >= 0.045


def test_429_twice_raises_with_reason(monkeypatch):
    monkeypatch.setattr(gdelt, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(gdelt, "PENALTY_S", 0.0)
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: _Resp(429, text="slow down"))
    with pytest.raises(gdelt.GdeltUnavailable, match="429"):
        gdelt.doc_get({})


def test_non_json_body_is_reported(monkeypatch):
    monkeypatch.setattr(gdelt, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(gdelt.requests, "get",
                        lambda *a, **k: _Resp(200, None, text="Your search contained an error"))
    with pytest.raises(gdelt.GdeltUnavailable, match="non-JSON: Your search"):
        gdelt.doc_get({})


def test_no_slot_before_deadline_does_not_sleep(monkeypatch):
    monkeypatch.setattr(gdelt, "MIN_INTERVAL_S", 100.0)
    monkeypatch.setattr(gdelt.requests, "get", lambda *a, **k: _Resp(200, {"ok": 1}))
    assert gdelt.doc_get({}) == {"ok": 1}          # takes the free slot
    t0 = time.monotonic()
    with pytest.raises(gdelt.GdeltUnavailable, match="out of time"):
        gdelt.doc_get({}, deadline=time.monotonic() + 1.0)
    assert time.monotonic() - t0 < 0.5
