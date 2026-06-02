"""Tests for source_runs per-run history (audit Sprint 1 #2)."""
import pytest

from worldscope.lake import Lake
from worldscope.sections import Section
from worldscope.store import SnapshotStore


def test_records_need_registered_source_else_fk_fails(tmp_path):
    # The bug that quarantined 18k records: news sections attribute each item to
    # a per-outlet source_id that to_lake must register, or the records->sources
    # FK rejects it. This documents the contract the fix relies on.
    lk = Lake(db_path=tmp_path / "lake.sqlite")
    lk._ensure_open()
    with pytest.raises(Exception):
        lk.upsert_record(record_id="r1", source_id="foreign-news:bbc-world",
                         section_id="foreign_news", original_url=None,
                         original_text="hi")
    lk.register_source(source_id="foreign-news:bbc-world", name="bbc world",
                       url=None, license="x", tier="aggregator")
    lk.upsert_record(record_id="r1", source_id="foreign-news:bbc-world",
                     section_id="foreign_news", original_url=None,
                     original_text="hi")
    assert lk._ensure_open().execute(
        "SELECT COUNT(*) FROM records").fetchone()[0] == 1


def _lake(tmp_path):
    lk = Lake(db_path=tmp_path / "lake.sqlite")
    lk._ensure_open()
    return lk


def test_record_source_run_appends_history(tmp_path):
    lk = _lake(tmp_path)
    rid = lk.record_source_run(
        source_id="acled", section_id="acled", success=False,
        error_type="UpstreamAuthError", error_message="auth failed", latency_ms=1200)
    row = lk._ensure_open().execute(
        "SELECT source_id, success, error_type, latency_ms FROM source_runs WHERE id=?",
        (rid,)).fetchone()
    assert row["source_id"] == "acled" and row["success"] == 0
    assert row["error_type"] == "UpstreamAuthError" and row["latency_ms"] == 1200
    # A second run appends rather than overwriting — that's the whole point.
    lk.record_source_run(source_id="acled", success=True, record_count=5)
    n = lk._ensure_open().execute(
        "SELECT COUNT(*) FROM source_runs WHERE source_id='acled'").fetchone()[0]
    assert n == 2


def test_resolve_sets_latency_and_error_type(tmp_path):
    store = SnapshotStore(tmp_path / "store.sqlite")

    class _Fail(Section):
        id, title, emoji = "t_fail", "t", "x"
        def pull(self):
            raise RuntimeError("boom")

    st = _Fail(store=store).resolve()
    assert st.error_type == "RuntimeError"
    assert st.latency_ms is not None and st.latency_ms >= 0

    class _OK(Section):
        id, title, emoji = "t_ok", "t", "x"
        def pull(self):
            return [{"id": "1", "title": "hi", "url": "", "date": "2026-06-01"}]

    st2 = _OK(store=store).resolve()
    assert st2.error_type is None
    assert st2.latency_ms is not None
