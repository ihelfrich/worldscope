"""Momentum calls: uncertain, self-grading, calibrated forecasts."""
import json
import sqlite3
from datetime import date

from worldscope import momentum_calls as mc

STORIES = [
    {"key": "nvidia", "label": "Nvidia", "klass": "surging", "records": [1, 2, 9], "days_present": 3},
    {"key": "ss united states", "label": "SS United States", "klass": "new", "records": [0, 0, 4],
     "days_present": 1},
    {"key": "senate", "label": "Senate", "klass": "fading", "records": [9, 5, 1]},
]


def test_calls_only_for_surging_and_new_with_a_fixed_horizon():
    calls = mc.make_calls(STORIES, today=date(2026, 11, 12), record={})
    assert [c["_key"] for c in calls] == ["nvidia", "ss united states"]
    c = calls[0]
    assert c["target_date"] == "2026-11-15" and c["method"] == mc.METHOD
    assert c["confidence"] == 0.5 and c["indicators_used"] == ["surging:perennial"]
    assert calls[1]["indicators_used"] == ["new:episodic"]
    assert ">= 3 distinct WORLDSCOPE sections on 2026-11-15" in c["resolution_criteria"]
    assert "key 'nvidia'" in c["resolution_criteria"]


def test_probability_follows_the_record_within_caps():
    assert mc.probability("surging", {"surging": (12, 119)}) == round(17 / 129, 4)
    assert mc.probability("new", {"new": (0, 1000)}) == mc.CAP[0]
    assert mc.probability("new", {"new": (1000, 1000)}) == mc.CAP[1]


def test_grading_counts_sections_on_the_target_day():
    by_key = {"nvidia": {"tech_news", "foreign_news", "paper_bets"}, "putin": {"foreign_news"}}
    assert mc.grade_key_on_day("nvidia", by_key) == "YES"
    assert mc.grade_key_on_day("putin", by_key) == "NO"
    assert mc.grade_key_on_day("absent", by_key) == "NO"


def test_track_record_merges_backtest_and_live(tmp_path, monkeypatch):
    bt = tmp_path / "bt.json"
    bt.write_text(json.dumps({"by_class": {"surging": {"yes": 12, "n": 119}}}))
    monkeypatch.setattr(mc, "BACKTEST_PATH", bt)
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE predictions (method TEXT, indicators_used_json TEXT, actual_outcome TEXT)")
    c.executemany("INSERT INTO predictions VALUES (?,?,?)", [
        (mc.METHOD, '["surging"]', "YES"), (mc.METHOD, '["new:episodic"]', "NO"),
        ("signal-fusion-v1", '["a","b"]', "YES"), (mc.METHOD, '["surging"]', None)])
    assert mc.track_record(c) == {"surging": (13, 120), "new:episodic": (0, 1)}
    assert mc.track_record(c, include_backtest=False) == {"surging": (1, 1), "new:episodic": (0, 1)}


def test_a_day_with_no_collection_is_not_graded_no(monkeypatch):
    class _Lake:
        def __init__(self):
            self.resolved = []

        def resolve_prediction(self, **kw):
            self.resolved.append(kw)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE predictions (id TEXT, method TEXT, target_date TEXT, "
              "resolution_criteria TEXT, actual_outcome TEXT)")
    c.execute("INSERT INTO predictions VALUES ('p1', ?, '2026-11-15', "
              "'Resolves YES if ''Nvidia'' (key ''nvidia'') ...', NULL)", (mc.METHOD,))
    lake = _Lake()
    monkeypatch.setattr(mc, "sections_on_day", lambda day, lake_path=None: {})
    assert mc.grade_due(lake, c, today=date(2026, 11, 16)) == 0 and lake.resolved == []
    monkeypatch.setattr(mc, "sections_on_day",
                        lambda day, lake_path=None: {"nvidia": {"a", "b", "c"}})
    assert mc.grade_due(lake, c, today=date(2026, 11, 16)) == 1
    assert lake.resolved[0]["actual_outcome"] == "YES"


def test_newest_collected_day_never_runs_past_the_data(tmp_path):
    from worldscope.momentum import newest_collected_day
    db = tmp_path / "w.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE records (ingested_at TEXT)")
    c.execute("INSERT INTO records VALUES ('2026-11-12T23:52:19Z')")
    c.commit()
    c.close()
    assert newest_collected_day(date(2026, 11, 13), db) == date(2026, 11, 12)
    assert newest_collected_day(date(2026, 11, 10), db) == date(2026, 11, 10)
    assert newest_collected_day(date(2026, 11, 13), tmp_path / "missing.sqlite") == date(2026, 11, 13)
