"""Tests for the cross-source signal-fusion engine (worldscope.signals)."""
from datetime import date

from worldscope import signals as sg


def _rec(section, title, day="2026-05-31", rid=None, url="", entities=None):
    return {
        "id": rid or f"{section}-{abs(hash((section, title))) % 10**8}",
        "section_id": section,
        "original_text": title,
        "title": title,
        "original_url": url,
        "record_date": day,
        "entities": entities or [],
    }


# ---- key extraction --------------------------------------------------------

def test_clean_text_strips_html_and_leading_tags():
    assert sg._clean_text("[China] <a href='x'>Beijing</a> tightens rules") \
        == "Beijing tightens rules"
    assert sg._clean_text("[TITLE: Foo | LEDE: bar]").startswith("Foo")


def test_keys_capture_entities_tickers_and_cves():
    rec = _rec("markets", "Nvidia $NVDA hit by CVE-2026-1234 disclosure",
               entities=["org:nvidia-corp"])
    keys = sg.record_keys(rec)
    assert "$nvda" in keys
    assert "cve-2026-1234" in keys
    assert "nvidia corp" in keys  # entity id decoded to words


def test_source_and_calendar_words_are_stopworded():
    keys = sg.record_keys(_rec("forecasts", "Kalshi market resolves in June"))
    assert "kalshi" not in keys
    assert "june" not in keys


def test_noise_records_are_dropped():
    assert sg.is_noise_record(_rec("state_news", "[feed error] CO Governor: HTTPError"))
    assert sg.is_noise_record({"_error": "boom", "section_id": "x"})
    assert not sg.is_noise_record(_rec("state_news", "Governor signs budget"))


# ---- fusion ----------------------------------------------------------------

def test_fuse_requires_cross_section_corroboration():
    # 'Solo' appears in only one section -> filtered; 'Iran' in three -> kept.
    recs = [
        _rec("a", "Solo Topic advances"),
        _rec("b", "Iran talks stall"),
        _rec("c", "Iran deadline passes"),
        _rec("d", "Iran sanctions debated"),
    ]
    sigs = sg.fuse(recs, today=date(2026, 5, 31), min_sections=2)
    labels = {s.key for s in sigs}
    assert "iran" in labels
    assert "solo topic" not in labels


def test_fuse_ranks_more_corroborated_higher_and_sets_fields():
    recs = [
        _rec("a", "Iran one"), _rec("b", "Iran two"), _rec("c", "Iran three"),
        _rec("d", "China one"), _rec("e", "China two"),
    ]
    sigs = sg.fuse(recs, today=date(2026, 5, 31), min_sections=2)
    assert sigs[0].key == "iran"  # 3 sections beats 2
    top = sigs[0]
    assert top.n_sections == 3
    assert set(top.sections) == {"a", "b", "c"}
    assert sg.CONF_FLOOR <= top.confidence <= sg.CONF_CEIL
    assert top.evidence and "id" in top.evidence[0]


def test_records_outside_window_are_ignored():
    recs = [
        _rec("a", "Iran now", day="2026-05-31"),
        _rec("b", "Iran now", day="2026-05-31"),
        _rec("c", "Iran old", day="2026-01-01"),
    ]
    sigs = sg.fuse(recs, today=date(2026, 5, 31), window_days=7, min_sections=2)
    iran = [s for s in sigs if s.key == "iran"][0]
    assert iran.n_records == 2  # the January record is out of window


# ---- predictions + grading -------------------------------------------------

def test_signals_to_predictions_shape():
    recs = [_rec("a", "Iran one"), _rec("b", "Iran two")]
    sigs = sg.fuse(recs, today=date(2026, 5, 31), min_sections=2)
    preds = sg.signals_to_predictions(sigs, today=date(2026, 5, 31), horizon_days=14)
    p = preds[0]
    assert p["predicted_outcome"] == "YES"
    assert p["target_date"] == "2026-06-14"
    assert p["method"] == sg.METHOD
    assert p["_key"] == "iran"
    assert sg.CONF_FLOOR <= p["confidence"] <= sg.CONF_CEIL


def test_grade_key_outcome_yes_and_no():
    future = [
        _rec("a", "Iran resurfaces", day="2026-06-05"),
        _rec("b", "Iran again", day="2026-06-06"),
        _rec("c", "Unrelated", day="2026-06-06"),
    ]
    yes = sg.grade_key_outcome("iran", future, made=date(2026, 5, 31),
                               target=date(2026, 6, 14))
    assert yes == "YES"
    no = sg.grade_key_outcome("nonexistent", future, made=date(2026, 5, 31),
                              target=date(2026, 6, 14))
    assert no == "NO"


def test_grade_respects_window_bounds():
    # mentions only before made_at or after target -> NO
    recs = [
        _rec("a", "Iran early", day="2026-05-20"),
        _rec("b", "Iran late", day="2026-07-01"),
    ]
    out = sg.grade_key_outcome("iran", recs, made=date(2026, 5, 31),
                               target=date(2026, 6, 14))
    assert out == "NO"


# ---- panel -----------------------------------------------------------------

def test_lake_resolve_prediction_sets_outcome_and_brier(tmp_path):
    from worldscope.lake import Lake
    lake = Lake.open(tmp_path / "t.sqlite")
    lake.add_prediction(
        prediction_id="p1", target_date="2026-06-14",
        resolution_criteria="key 'iran'", predicted_outcome="YES",
        confidence=0.7, training_window_days=7, indicators_used=["a", "b"],
        method=sg.METHOD, evidence=["r1"], section_id="signals",
    )
    lake.resolve_prediction(prediction_id="p1", resolved_at="2026-06-14",
                            actual_outcome="YES")
    conn = lake._ensure_open()
    row = conn.execute(
        "SELECT actual_outcome, brier_contribution FROM predictions WHERE id='p1'"
    ).fetchone()
    assert row[0] == "YES"
    assert abs(row[1] - (0.7 - 1.0) ** 2) < 1e-9   # correct -> (0.7-1)^2 = 0.09
    lake.close()


def test_persist_and_grade_round_trip(tmp_path):
    """End-to-end: persist signal predictions, then grade due ones from records."""
    from worldscope.lake import Lake
    lake = Lake.open(tmp_path / "t2.sqlite")
    recs = [_rec("a", "Iran one"), _rec("b", "Iran two")]
    sigs = sg.fuse(recs, today=date(2026, 5, 31), min_sections=2)
    preds = sg.signals_to_predictions(sigs, today=date(2026, 5, 31), horizon_days=14)
    assert sg.persist_predictions(lake, preds) == len(preds)
    conn = lake._ensure_open()
    # nothing due yet (target is 2026-06-14)
    assert sg.grade_due_predictions(lake, conn, today=date(2026, 5, 31)) == 0
    lake.close()


def test_render_panel_is_html_or_empty():
    assert sg.render_signals_panel([], []) == ""
    recs = [_rec("a", "Iran one"), _rec("b", "Iran two")]
    sigs = sg.fuse(recs, today=date(2026, 5, 31), min_sections=2)
    html = sg.render_signals_panel(sigs, sg.signals_to_predictions(sigs, today=date(2026, 5, 31)))
    assert "<section class='section'>" in html
    assert "Signals" in html
