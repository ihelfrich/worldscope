"""Prediction confidence is calibrated against the system's own track record."""
import json
import sqlite3

from worldscope import signals as sg


def _db(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE predictions (method TEXT, indicators_used_json TEXT, actual_outcome TEXT)")
    c.executemany("INSERT INTO predictions VALUES (?,?,?)", rows)
    return c


def test_track_record_buckets_by_breadth():
    six = json.dumps(["a", "b", "c", "d", "e", "f", "g"])          # 7 sections -> bucket 6
    rows = [(sg.METHOD, six, "YES")] * 99 + [(sg.METHOD, six, "NO")] \
        + [(sg.METHOD, json.dumps(["a", "b"]), "NO"), ("other", six, "NO"),
           (sg.METHOD, six, None)]
    assert sg.track_record(_db(rows)) == {6: (99, 100), 2: (0, 1)}


def test_calibration_moves_toward_the_record_and_never_reaches_certainty():
    rec = {6: (1350, 1361), 4: (13, 15)}
    assert 0.98 < sg.calibrate(0.78, 7, rec) <= sg.CALIBRATION_CAP
    thin = sg.calibrate(0.68, 4, rec)
    assert 0.68 < thin < 0.80                        # 15 calls barely move the prior
    assert sg.calibrate(0.6, 3, rec) == 0.6          # no record for this breadth
    assert sg.calibrate(0.78, 6, {6: (1000, 1000)}) == sg.CALIBRATION_CAP
    assert sg.calibrate(0.78, 6, {6: (0, 200)}) < 0.1  # a bad record lowers it too


def test_predictions_carry_calibrated_confidence():
    s = sg.Signal(key="nvidia", label="Nvidia", score=1.0, n_sections=6,
                  sections=list("abcdef"), n_records=10, recency_days=0, confidence=0.78)
    from datetime import date
    p = sg.signals_to_predictions([s], today=date(2026, 11, 13), record={6: (99, 100)})[0]
    assert p["confidence"] > 0.9
