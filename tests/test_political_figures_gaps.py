"""political_figures: failed signal feeds are reported, not scored as calm."""
from types import SimpleNamespace

import pytest

from worldscope.sections import political_figures as pf


def test_courtlistener_lookup_failure_is_none_not_empty(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(pf.requests, "get", boom)
    assert pf._fetch_courtlistener_recent("Jane Doe") is None


def test_doj_feed_http_error_raises(monkeypatch):
    import feedparser
    monkeypatch.setattr(feedparser, "parse",
                        lambda *a, **k: SimpleNamespace(status=403, entries=[], bozo=False))
    with pytest.raises(RuntimeError, match="403"):
        pf._fetch_doj_rss()


def test_pull_reports_signal_gaps_on_every_item(monkeypatch, capsys):
    reg = [{"id": "f1", "name": "Jane Doe", "role": "Senator"},
           {"id": "f2", "name": "John Roe", "role": "Governor"}]
    monkeypatch.setattr(pf, "load_registry", lambda: reg)
    for loader in ("_load_quiver_ptrs_from_lake", "_load_gdelt_gkg_from_lake",
                   "_load_form4_from_lake"):
        monkeypatch.setattr(pf, loader, lambda: [])

    def doj_down():
        raise RuntimeError("DOJ RSS answered HTTP 503")
    monkeypatch.setattr(pf, "_fetch_doj_rss", doj_down)
    monkeypatch.setattr(pf, "_fetch_courtlistener_recent", lambda name, **k: None)
    items = pf.PoliticalFiguresSection.__new__(pf.PoliticalFiguresSection)
    items._cached_signals = None
    out = items.pull()
    real = [it for it in out if not it.get("is_stub")]
    assert real and all(len(it["signal_gaps"]) == 2 for it in real)
    assert "DOJ press releases unavailable" in real[0]["summary"]
    assert "failed for all 2 top figures" in real[0]["summary"]
    assert "signal gaps" in capsys.readouterr().out
