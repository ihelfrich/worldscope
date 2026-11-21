"""Regressions for the 2026-11-21 health review.

- a failed pull emptied the last good day's raw.jsonl
- an upstream error message published the account address
- routine briefs were validated and then never merged (draft pull requests)
- "translations" were the untranslated original
- model calls read content[0].text and recorded no usage
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldscope import llm
from worldscope import lake as lake_module
from worldscope.lib import translate as tr
from worldscope.sections import Section, SectionState, STATE_STALE, redact_error

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Lake: a failed pull must not overwrite a good day
# --------------------------------------------------------------------------- #

class _Quiet(Section):
    id = "quiet_test_source"
    title = "Quiet test source"
    emoji = ""

    def pull(self):
        return []


class _FakeLake:
    """Just enough of Lake for to_lake(); write_artifacts is the real one."""

    def __getattr__(self, name):
        if name == "write_artifacts":
            return lambda artifacts: lake_module.Lake.write_artifacts(self, artifacts)
        return lambda *args, **kwargs: None


def _section():
    section = _Quiet.__new__(_Quiet)
    section.store = None
    return section


def _stale_state(day: str) -> SectionState:
    return SectionState(
        section_id=_Quiet.id, title=_Quiet.title, emoji="", state=STATE_STALE,
        items=[{"_id": "a", "title": "kept from the last good day"}], new=[],
        comparison_date=None, source_date=day,
        error="UpstreamAuthError: rejected", error_type="UpstreamAuthError")


def test_failed_pull_leaves_the_last_good_days_files_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(lake_module, "LAKE_SECTIONS", tmp_path)
    folder = tmp_path / _Quiet.id / "2026-06-02"
    folder.mkdir(parents=True)
    good = '{"id": "a", "original_text": "a real record"}\n'
    (folder / "raw.jsonl").write_text(good, encoding="utf-8")
    (folder / "summary.md").write_text("good summary", encoding="utf-8")

    _section().to_lake(_stale_state("2026-06-02"), lake=_FakeLake())

    assert (folder / "raw.jsonl").read_text(encoding="utf-8") == good
    assert (folder / "summary.md").read_text(encoding="utf-8") == "good summary"


def test_failed_pull_with_no_earlier_files_still_records_the_day(tmp_path, monkeypatch):
    monkeypatch.setattr(lake_module, "LAKE_SECTIONS", tmp_path)
    _section().to_lake(_stale_state("2026-06-03"), lake=_FakeLake())
    assert (tmp_path / _Quiet.id / "2026-06-03" / "raw.jsonl").exists()


# --------------------------------------------------------------------------- #
# Error text is published: no addresses in it
# --------------------------------------------------------------------------- #

def test_error_text_loses_addresses_and_keeps_the_rest():
    text = redact_error("UpstreamAuthError: rejected credentials for someone@example.com: 400")
    assert "@" not in text
    assert text.startswith("UpstreamAuthError: rejected credentials for ")
    assert text.endswith(": 400")


def test_resolve_redacts_whatever_a_pull_raises():
    class Boom(_Quiet):
        def pull(self):
            raise RuntimeError("login someone@example.com refused")

    section = Boom.__new__(Boom)
    section.store = SimpleNamespace(most_recent=lambda _id: None)
    state = section.resolve(today=date(2026, 11, 21))
    assert "someone@example.com" not in state.error
    assert "refused" in state.error


def test_acled_error_does_not_name_the_account():
    source = (ROOT / "worldscope" / "sections" / "acled.py").read_text(encoding="utf-8")
    assert "rejected credentials for {email}" not in source


def test_scrub_rewrites_text_files_and_sqlite_and_is_idempotent(tmp_path):
    scrub = _load("scrub_published_errors", "tools/scrub_published_errors.py")
    leaked = "ACLED rejected credentials for Someone@example.com: 400 incorrect"
    page = tmp_path / "dist" / "page.html"
    page.parent.mkdir()
    page.write_text(f"<p>{leaked}</p><p>mail press@example.org</p>", encoding="utf-8")
    db = tmp_path / "lake.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE source_health (source_id TEXT, last_error TEXT)")
    conn.execute("INSERT INTO source_health VALUES ('acled', ?)", (leaked,))
    conn.commit()
    conn.close()

    assert scrub.scrub([tmp_path]) == (2, 2)
    text = page.read_text(encoding="utf-8")
    assert "Someone@example.com" not in text
    assert "rejected the configured credentials: 400 incorrect" in text
    assert "press@example.org" in text  # unrelated addresses are content
    conn = sqlite3.connect(db)
    assert "@" not in conn.execute("SELECT last_error FROM source_health").fetchone()[0]
    conn.close()
    assert scrub.scrub([tmp_path]) == (0, 0)


# --------------------------------------------------------------------------- #
# Landing routine briefs
# --------------------------------------------------------------------------- #

def test_lander_marks_a_draft_ready_before_merging():
    body = (ROOT / ".github" / "workflows" / "auto-merge-briefs.yml").read_text(encoding="utf-8")
    assert body.index("gh pr ready") < body.index("gh pr merge")


def test_a_daily_brief_without_source_links_waits_for_a_person():
    vbp = _load("validate_brief_pr", "tools/validate_brief_pr.py")
    files = [{"filename": "briefings/2026-11-20.md", "status": "added"}]
    unsourced = "# Morning brief - Friday 20 November 2026\n\n" + "claim " * 3000
    verdict, reason = vbp.classify(files, unsourced, date(2026, 11, 20))
    assert verdict == "ignore" and "source links" in reason
    sourced = unsourced + " [s](https://example.org/a)" * vbp.DAILY_MIN_LINKS
    assert vbp.classify(files, sourced, date(2026, 11, 20))[0] == "merge"


def test_hourly_jobs_do_not_cancel_a_running_refresh():
    for name in ("live-map.yml", "ukraine-hourly.yml"):
        body = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        head = body[:body.index("jobs:")]
        assert "cancel-in-progress: false" in head, name


# --------------------------------------------------------------------------- #
# Model calls
# --------------------------------------------------------------------------- #

def _response(text="answer", stop_reason="end_turn", thinking_first=True):
    content = [SimpleNamespace(type="thinking", thinking="")] if thinking_first else []
    content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0))


@pytest.fixture
def fake_api(monkeypatch):
    calls: list[dict] = []
    box = {"response": _response()}

    class _Messages:
        def create(self, **request):
            calls.append(request)
            result = box["response"]
            if isinstance(result, Exception):
                raise result
            return result(request) if callable(result) else result

    fake = SimpleNamespace(Anthropic=lambda **kwargs: SimpleNamespace(messages=_Messages()))
    monkeypatch.setattr(llm, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm.reset_usage()
    yield SimpleNamespace(calls=calls, box=box)
    llm.reset_usage()


def test_complete_reads_past_a_thinking_block_and_leaves_room_for_thinking(fake_api):
    assert llm.complete("q", purpose="t", max_tokens=400) == "answer"
    request = fake_api.calls[0]
    assert request["max_tokens"] > 400
    assert request["model"] == llm.SYNTH_MODEL
    assert "temperature" not in request and "thinking" not in request


def test_complete_raises_one_error_type_for_every_failure(fake_api, monkeypatch):
    fake_api.box["response"] = _response(stop_reason="refusal")
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("q", purpose="t")
    fake_api.box["response"] = _response(text="")
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("q", purpose="t")
    fake_api.box["response"] = RuntimeError("network down")
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("q", purpose="t")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("q", purpose="t")


def test_usage_is_tallied_by_purpose_with_a_cost_estimate(fake_api):
    llm.complete("q", purpose="overview", model="claude-sonnet-5-5")
    llm.complete("q", purpose="overview", model="claude-sonnet-5-5")
    summary = llm.usage_summary()
    row = summary["calls"][0]
    assert (row["calls"], row["tokens_in"], row["tokens_out"]) == (2, 2000, 1000)
    assert summary["est_cost_usd"] == pytest.approx((2000 * 2 + 1000 * 10) / 1e6)


def test_no_call_site_uses_a_retired_model_id_or_indexes_the_first_block():
    for path in list((ROOT / "worldscope").rglob("*.py")) + list((ROOT / "tools").rglob("*.py")):
        if path.name == "llm.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "claude-sonnet-4" not in source and "claude-haiku-4" not in source, path
        assert "content[0].text" not in source, path


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #

@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "translations.json"
    monkeypatch.setattr(tr, "_CACHE_PATH", path)
    return path


def _items(n=2):
    return [{"title": f"заголовок {i}", "summary": "лид"} for i in range(n)]


def test_without_a_credential_nothing_is_called_a_translation(cache, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    items = _items()
    assert tr.translate_item_titles(items, "ru", purpose="t") == 0
    assert all("title_en" not in it and it["untranslated"] for it in items)
    assert items[0]["title"] == "заголовок 0"


def test_translations_are_applied_and_cached(cache, fake_api):
    fake_api.box["response"] = _response(text='```json\n["Headline 0", "Headline 1"]\n```')
    items = _items()
    assert tr.translate_item_titles(items, "ru", purpose="t") == 2
    assert items[1]["title_en"] == "Headline 1"
    assert items[1]["title"].startswith("[Headline 1] (ru: заголовок 1")
    assert fake_api.calls[0]["model"] == llm.BULK_MODEL
    assert len(json.loads(cache.read_text(encoding="utf-8"))) == 2

    again = _items()
    assert tr.translate_item_titles(again, "ru", purpose="t") == 2
    assert len(fake_api.calls) == 1  # served from the cache


def test_a_malformed_answer_marks_the_batch_untranslated(cache, fake_api):
    fake_api.box["response"] = _response(text='["only one"]')
    items = _items()
    assert tr.translate_item_titles(items, "ru", purpose="t") == 0
    assert all(it.get("untranslated") for it in items)


def test_long_lists_are_sent_in_batches(cache, fake_api):
    def answer(request):
        count = request["messages"][0]["content"].count("TITLE:")
        return _response(text=json.dumps([f"en {i}" for i in range(count)]))

    fake_api.box["response"] = answer
    items = _items(tr.BATCH_SIZE + 5)
    assert tr.translate_item_titles(items, "ru", purpose="t") == tr.BATCH_SIZE + 5
    assert len(fake_api.calls) == 2
