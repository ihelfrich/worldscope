"""Sprint 1 / Priority 1: false-empty sections must now fail loudly.

A section that can't reach its source (missing credential, HTTP error, auth
rejection, unparseable body) must *raise* a typed SourceUnavailable, so the base
state machine records stale_after_failure / no_data — never a misleading
fresh_empty — and source_health logs the failure. Empty lists are reserved for
genuine, source-confirmed quiet days.
"""
import pytest
import requests

from worldscope.sections import (
    MissingCredential, UpstreamHTTPError, UpstreamParseError, UpstreamAuthError,
    SourceUnavailable, STATE_FRESH_EMPTY, STATE_NO_DATA, STATE_STALE,
)
from worldscope.sections.macro import MacroSection
from worldscope.sections.markets import MarketsSection
from worldscope.sections.acled import AcledSection
from worldscope.sections import reliefweb, promed
from worldscope.store import SnapshotStore


def _store(tmp_path):
    return SnapshotStore(tmp_path / "store.sqlite")


# ---- missing credentials -> MissingCredential ------------------------------

def test_macro_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(MissingCredential):
        MacroSection(store=_store(tmp_path)).pull()


def test_markets_uses_keyless_yahoo_without_finnhub(monkeypatch, tmp_path):
    # No Finnhub key is NOT a failure: markets falls back to keyless Yahoo.
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    from worldscope.sections import markets as mk
    monkeypatch.setattr(mk, "_fetch_quote_yahoo",
                        lambda s, sym: {"c": 100.0, "d": 1.0, "dp": 1.0,
                                        "h": 101.0, "l": 99.0, "t": None})
    monkeypatch.setattr(mk.time, "sleep", lambda *_: None)
    items = mk.MarketsSection(store=_store(tmp_path)).pull()
    assert items and all("value" in it for it in items)


def test_markets_all_fetches_fail_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    from worldscope.sections import markets as mk
    monkeypatch.setattr(mk, "_fetch_quote_yahoo", lambda s, sym: None)
    monkeypatch.setattr(mk.time, "sleep", lambda *_: None)
    with pytest.raises(UpstreamHTTPError):
        mk.MarketsSection(store=_store(tmp_path)).pull()


def test_acled_missing_creds_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("ACLED_EMAIL", raising=False)
    monkeypatch.delenv("ACLED_PASSWORD", raising=False)
    with pytest.raises(MissingCredential):
        AcledSection(store=_store(tmp_path)).pull()


# ---- upstream failures -> typed SourceUnavailable --------------------------

def test_reliefweb_http_error_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(reliefweb.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("down")))
    with pytest.raises(UpstreamHTTPError):
        reliefweb.ReliefWebSection(store=_store(tmp_path)).pull()


def test_promed_http_error_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(promed.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("down")))
    with pytest.raises(UpstreamHTTPError):
        promed.PromedSection(store=_store(tmp_path)).pull()


def test_promed_parse_error_raises(monkeypatch, tmp_path):
    class FakeResp:
        content = b"<<< this is not xml >>>"
        def raise_for_status(self):
            return None
    monkeypatch.setattr(promed.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(UpstreamParseError):
        promed.PromedSection(store=_store(tmp_path)).pull()


# ---- the whole point: failure != fresh_empty -------------------------------

def test_credential_failure_resolves_to_no_data_not_fresh_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    state = MacroSection(store=_store(tmp_path)).resolve()
    assert state.state != STATE_FRESH_EMPTY
    assert state.state in (STATE_NO_DATA, STATE_STALE)
    assert state.error and "MissingCredential" in state.error


def test_typed_exceptions_share_a_base():
    for exc in (MissingCredential, UpstreamHTTPError, UpstreamParseError, UpstreamAuthError):
        assert issubclass(exc, SourceUnavailable)


def test_gdelt_regions_total_failure_raises(monkeypatch, tmp_path):
    # Every GDELT fetch failing (rate-limited) must raise, not look like a quiet
    # day. Budget logic still returns partial when at least one fetch succeeds.
    from worldscope.sections import gdelt_regions as gr
    monkeypatch.setattr(gr.GdeltRegionsSection, "_fetch_one", lambda self, c, p: None)
    monkeypatch.setattr(gr.time, "sleep", lambda *_: None)
    with pytest.raises(UpstreamHTTPError):
        gr.GdeltRegionsSection(store=_store(tmp_path)).pull()
