"""ProMED: the public feed is retired (live 2026-10-09: every RSS/REST
candidate under promedmail.org answers 404 from the relaunched Next.js site;
alerts sit behind an account-gated internal API). The section must report
no_data with an explicit message — never a fabricated feed, never a quiet
fresh_empty — and must still parse a real RSS body when PROMED_FEED_URL is
configured.
"""
from datetime import date

import pytest

from worldscope.sections import (
    SourceUnavailable, UpstreamHTTPError, STATE_NO_DATA, STATE_FRESH, STATE_STALE,
)
from worldscope.sections import promed
from worldscope.store import SnapshotStore

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>ProMED-mail</title>
<item>
  <title>Avian influenza - North America (12): USA (Iowa) poultry, HPAI H5N1</title>
  <link>https://promedmail.org/promed-post/?id=8712345</link>
  <description><![CDATA[<p>A commercial layer flock in Iowa tested positive...</p>]]></description>
  <pubDate>Wed, 08 Oct 2026 02:11:00 +0000</pubDate>
</item>
<item>
  <title>Undiagnosed illness - Sudan: (North Darfur) RFI</title>
  <link>https://promedmail.org/promed-post/?id=8712346</link>
  <description>Clusters of fever reported near El Fasher.</description>
  <pubDate>Tue, 07 Oct 2026 22:40:13 GMT</pubDate>
</item>
</channel></rss>"""


def _store(tmp_path):
    return SnapshotStore(tmp_path / "s.sqlite")


def test_without_feed_url_pull_raises_feed_retired(monkeypatch, tmp_path):
    monkeypatch.delenv("PROMED_FEED_URL", raising=False)
    monkeypatch.setattr(promed.requests, "get", lambda *a, **k: pytest.fail("no network"))
    with pytest.raises(promed.FeedRetired) as ei:
        promed.PromedSection(store=_store(tmp_path)).pull()
    assert issubclass(promed.FeedRetired, SourceUnavailable)
    assert "no longer publishes a public" in str(ei.value)
    assert "PROMED_FEED_URL" in str(ei.value)


def test_resolve_reports_no_data_with_message(monkeypatch, tmp_path):
    monkeypatch.delenv("PROMED_FEED_URL", raising=False)
    state = promed.PromedSection(store=_store(tmp_path)).resolve()
    assert state.state == STATE_NO_DATA
    assert state.error_type == "FeedRetired"
    assert "no longer publishes a public" in state.error
    assert state.items == []


def test_resolve_does_not_carry_forward_an_old_snapshot(monkeypatch, tmp_path):
    monkeypatch.delenv("PROMED_FEED_URL", raising=False)
    store = _store(tmp_path)
    store.put("promed", [{"_id": "x", "title": "old", "url": "u", "date": "2026-06-02"}],
              status="ok", when=date(2026, 6, 2))
    state = promed.PromedSection(store=store).resolve(today=date(2026, 10, 9))
    assert state.state == STATE_NO_DATA and state.state != STATE_STALE
    assert state.items == [] and state.source_date is None
    assert "FeedRetired" in state.error


def test_configured_feed_is_fetched_and_parsed(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMED_FEED_URL", "https://example.org/licensed/promed.rss")
    seen = {}

    class R:
        content = RSS
        def raise_for_status(self):
            return None

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, timeout=timeout)
        return R()

    monkeypatch.setattr(promed.requests, "get", fake_get)
    sec = promed.PromedSection(store=_store(tmp_path))
    items = sec.pull()
    assert seen == {"url": "https://example.org/licensed/promed.rss", "timeout": 25}
    assert len(items) == 2
    flu = items[0]
    assert flu["disease"] == "Avian influenza"
    assert flu["country"] == "USA (Iowa) poultry, HPAI H5N1"
    assert flu["date"] == "2026-10-08"
    assert flu["summary"] == "A commercial layer flock in Iowa tested positive..."
    assert flu["topics"] == ["health", "biosecurity"] and flu["_source"] == "promed"
    assert set(flu) == {"id", "date", "title", "url", "summary", "country", "disease",
                        "topics", "_source"}
    assert items[1]["date"] == "2026-10-07" and items[1]["disease"] == "Undiagnosed illness"
    assert sec.resolve().state == STATE_FRESH


def test_configured_feed_http_failure_is_typed(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMED_FEED_URL", "https://example.org/promed.rss")
    def boom(*a, **k):
        raise promed.requests.ConnectionError("down")
    monkeypatch.setattr(promed.requests, "get", boom)
    with pytest.raises(UpstreamHTTPError):
        promed.PromedSection(store=_store(tmp_path)).pull()
