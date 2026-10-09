"""Unit tests for worldscope.godseye against a tiny synthetic lake."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from worldscope import godseye

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
D0 = NOW.date().isoformat()
D1 = (NOW - timedelta(days=1)).date().isoformat()
D9 = (NOW - timedelta(days=9)).date().isoformat()

WATCH_YAML = """
watch_areas:
  - name: "Taiwan Strait"
    priority: high
    keywords: [taiwan strait, pla navy]
    bbox: [105.0, 5.0, 130.0, 28.0]
  - name: "Sahel"
    priority: normal
    keywords: [jnim]
    bbox: [-17.0, 10.0, 16.0, 18.0]
  - name: "No bbox area"
    priority: low
    keywords: [fomc]
"""


def _rec(section, rid, day, text, url, extra, source=None):
    return {"id": rid, "section_id": section, "source_id": source or section,
            "record_date": day, "ingested_at_utc": f"{day}T10:00:00Z",
            "original_text": text, "original_url": url, "extra": extra, "entities": []}


def _write(lake: Path, section: str, day: str, records: list[dict]) -> None:
    d = lake / "sections" / section / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "raw.jsonl").write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""),
                                 encoding="utf-8")
    (d / "structured.json").write_text(json.dumps({"section": section, "date": day,
                                                   "record_count": len(records)}), encoding="utf-8")


@pytest.fixture
def lake(tmp_path: Path) -> Path:
    lake = tmp_path / "lake"
    _write(lake, "usgs_quakes", D0, [
        _rec("usgs_quakes", "us1", D0, "M 6.3 - Vanuatu — M6.3 · depth 10 km",
             "https://earthquake.usgs.gov/earthquakes/eventpage/us1",
             {"lat": -15.5, "lon": 168.2, "mag": 6.3, "depth_km": 10, "tsunami": 1, "alert": "green"}),
        _rec("usgs_quakes", "us2", D0, "M 4.6 - Fiji — M4.6",
             "https://earthquake.usgs.gov/earthquakes/eventpage/us2",
             {"lat": -18.0, "lon": -178.4, "mag": 4.6}),
        _rec("usgs_quakes", "bad", D0, "no coordinates", "https://x", {"mag": 5.0}),
    ])
    _write(lake, "weather", D0, [
        _rec("weather", "earthquake-us1", D0, "M6.3 — Vanuatu — dup of usgs us1",
             "https://earthquake.usgs.gov/earthquakes/eventpage/us1",
             {"subsection": "earthquake", "coordinates": [168.2, -15.5, 10.0], "magnitude": 6.3}, "noaa-aggregate"),
        _rec("weather", "alert-1", D0, "[Severe] Flood Warning: Montgomery — no geometry",
             "https://api.weather.gov/alerts/1", {"subsection": "active_alert", "severity": "Severe"}, "noaa-aggregate"),
    ])
    _write(lake, "firms", D1, [
        _rec("firms", f"f{i}", D1, f"[taiwan] VIIRS fire detection ({i})",
             "https://firms.modaps.eosdis.nasa.gov/", {"latitude": 23.5, "longitude": 120.0 + i * 0.01,
                                                      "frp": 10.0 * i, "confidence": "n",
                                                      "acq_datetime": f"{D1}T0120Z"})
        for i in range(1, 8)
    ] + [
        _rec("firms", "g1", D1, "[global] VIIRS fire detection outside watch areas",
             "https://firms.modaps.eosdis.nasa.gov/", {"latitude": -30.0, "longitude": 140.0, "frp": 400.0,
                                                      "confidence": "h"}),
    ])
    _write(lake, "gdacs", D1, [
        _rec("gdacs", "gdacs-FL-1", D1, "[Orange] Flood in China — Flood · Orange alert",
             "https://www.gdacs.org/report.aspx?eventid=1",
             {"alert_level": "Orange", "country": "China", "event_type": "Flood", "lat": "28.15", "lon": "120.68"}),
    ])
    _write(lake, "acled", D1, [
        _rec("acled", "a1", D1, "[Mali] Battles: Armed clash at Gao (12 fatalities) — notes",
             "https://acleddata.com/dashboard/", {"latitude": 16.27, "longitude": -0.04, "fatalities": 12,
                                                   "event_type": "Battles", "country": "Mali"}),
    ])
    _write(lake, "vip_flights", D0, [
        _rec("vip_flights", "abc:RCH1", D0, "RCH1 (United States) — icao24: abc",
             "https://opensky-network.org/aircraft-profile?icao24=abc",
             {"icao24": "abc", "callsign": "RCH1", "lat": 24.0, "lon": 121.0, "altitude_m": 11000.0,
              "watch_areas": ["Taiwan Strait"]}),
        _rec("vip_flights", "abc:RCH1-old", D1, "RCH1 older position", "javascript:alert(1)",
             {"icao24": "abc", "callsign": "RCH1", "lat": 20.0, "lon": 118.0, "altitude_m": 9000.0}),
    ])
    ring = [[37.80, 48.70, 0], [37.78, 48.69, 0], [37.76, 48.67, 0], [37.72, 48.67, 0], [37.80, 48.70, 0]]
    feat = json.dumps({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {}})
    truncated = "[" + feat + ", " + feat + ", " + feat[:40]
    _write(lake, "ukraine_theater", D0, [
        _rec("ukraine_theater", "firms-theater-1", D1,
             f"[FIRMS] thermal anomaly 52.386, 33.490 (FRP 1.57 MW, conf nominal) — acquired {D1} 0020Z",
             "https://firms.modaps.eosdis.nasa.gov/", {"source_kind": "thermal", "latitude": 52.386,
                                                      "longitude": 33.49, "frp": "1.57", "confidence": "nominal"},
             "ukraine-theater:nasa-firms"),
        _rec("ukraine_theater", "deepstatemap-1", D0, "[DeepStateMap] frontline snapshot — polygons in extra",
             "https://deepstatemap.live/", {"source_kind": "frontline", "features_json": truncated},
             "ukraine-theater:deepstatemap"),
        _rec("ukraine_theater", "alert-1", D0, "[Air alert] Kharkiv (air_raid) — active",
             "https://alerts.in.ua/", {"source_kind": "air-alert", "oblast": "Kharkiv oblast"},
             "ukraine-theater:ua-air-force-alerts"),
        _rec("ukraine_theater", "alerts-fetch-error", D0, "[Air alerts error] HTTPError",
             "https://api.alerts.in.ua/", {"source_kind": "air-alert", "_error": True},
             "ukraine-theater:ua-air-force-alerts"),
        _rec("ukraine_theater", "liveuamap-1", D0, "[Liveuamap] text only, no geo", "https://liveuamap.com/",
             {"source_kind": "osint-feed"}, "ukraine-theater:liveuamap"),
    ])
    _write(lake, "usgs_quakes", D9, [
        _rec("usgs_quakes", "old1", D9, "M 7.0 - ancient", "https://earthquake.usgs.gov/old",
             {"lat": 10.0, "lon": 10.0, "mag": 7.0}),
    ])
    _write(lake, "gdelt_gkg", D0, [
        _rec("gdelt_gkg", "gkg1", D0, "tagged article", "https://example.com/a",
             {"watch_areas": ["Taiwan Strait", "Sahel"]}),
        _rec("gdelt_gkg", "gkg2", D0, "tagged article 2", "https://example.com/b",
             {"watch_areas": ["Taiwan Strait"]}),
    ])
    meta = lake / "sections" / "_meta" / D0
    meta.mkdir(parents=True)
    (meta / "cross_section.json").write_text(json.dumps({
        "day": D0,
        "by_confidence": {
            "high": [{"canonical_name": "Taiwan", "entity_type": "place", "n_sections": 3,
                      "total_mentions": 9, "sections": ["gdelt_gkg", "firms", "vip_flights"],
                      "section_counts": {"gdelt_gkg": 5, "firms": 3, "vip_flights": 1}}],
            "medium": [{"canonical_name": "JNIM", "entity_type": "org", "n_sections": 2,
                        "total_mentions": 2, "sections": ["acled", "gdelt_gkg"],
                        "section_counts": {"acled": 1, "gdelt_gkg": 1}}],
        }}), encoding="utf-8")
    (meta / "top_stories.json").write_text(json.dumps({
        "date": D0, "story_count": 1,
        "stories": [{"headline": "<b>PLA</b> drills near Taiwan", "n_records": 12, "n_outlets": 4,
                     "n_sections": 3, "sections": ["gdelt_gkg", "conflict", "foreign_news"], "score": 9.5,
                     "representative_url": "https://example.com/story", "representative_source": "gdelt_gkg"}],
    }), encoding="utf-8")
    (tmp_path / "watchareas.yaml").write_text(WATCH_YAML, encoding="utf-8")
    return lake


def _build(lake: Path, tmp_path: Path, *, days: int = 7, network: bool = False) -> tuple[dict, Path]:
    out = tmp_path / "dist" / "live"
    manifest = godseye.build(out, lake=lake, days=days, network=network,
                             watch_path=tmp_path / "watchareas.yaml", now=NOW)
    return manifest, out


def _layer(out: Path, name: str) -> list[dict]:
    fc = json.loads((out / "layers" / f"{name}.geojson").read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    return fc["features"]


def test_writes_every_layer_and_page(lake, tmp_path):
    manifest, out = _build(lake, tmp_path)
    for name in godseye.LAYERS:
        assert (out / "layers" / f"{name}.geojson").is_file(), name
    assert (out / "manifest.json").is_file()
    assert (out / "signals.json").is_file()
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "unpkg.com/leaflet@1.9.4" in page
    assert "Analytic context only. Not safety or navigation guidance." in page
    assert "Generated 2026-10-09T12:00:00Z" in page
    assert "__DAYS__" not in page
    assert "api_key" not in page.lower() and "apikey" not in page.lower()


def test_feature_schema_and_severity_bounds(lake, tmp_path):
    manifest, out = _build(lake, tmp_path)
    total = 0
    for name in godseye.LAYERS:
        for f in _layer(out, name):
            total += 1
            p = f["properties"]
            assert f["type"] == "Feature"
            assert f["geometry"]["type"] in ("Point", "Polygon")
            for key in ("title", "summary", "date", "ts", "severity", "source", "url", "layer"):
                assert key in p, (name, key)
            assert p["layer"] == name
            assert 1 <= p["severity"] <= 10
            assert isinstance(p["ts"], int) and p["ts"] > 0
            assert len(p["summary"]) <= 240
            assert p["url"] == "" or p["url"].startswith(("http://", "https://"))
            datetime.strptime(p["date"], "%Y-%m-%dT%H:%M:%SZ")
    assert total > 0


def test_manifest_counts_match_files(lake, tmp_path):
    manifest, out = _build(lake, tmp_path)
    assert manifest["generated_at"] == "2026-10-09T12:00:00Z"
    assert manifest["days"] == 7 and manifest["network"] is False
    layers = {m["layer"]: m for m in manifest["layers"]}
    assert set(layers) == set(godseye.LAYERS)
    for name, m in layers.items():
        feats = _layer(out, name)
        assert m["count"] == len(feats) == manifest["counts"][name]
        if feats:
            assert m["freshest_ts"] == max(f["properties"]["ts"] for f in feats)
            assert m["freshest_at"].endswith("Z")
        assert m["severity"]
    names = {i["name"] for i in manifest["inputs"]}
    assert "lake:usgs_quakes+weather" in names and "watchareas.yaml" in names
    assert not any(n.startswith("live:") for n in names)
    assert all(i["ok"] for i in manifest["inputs"])
    on_disk = json.loads((out / "manifest.json").read_text())
    assert on_disk["counts"] == manifest["counts"]


def test_quakes_dedupe_and_window(lake, tmp_path):
    _, out = _build(lake, tmp_path)
    feats = _layer(out, "quakes")
    ids = sorted(f["properties"]["event_id"] for f in feats)
    assert ids == ["us1", "us2"]
    big = next(f for f in feats if f["properties"]["event_id"] == "us1")
    assert big["properties"]["severity"] == 8
    assert big["geometry"]["coordinates"] == [168.2, -15.5]


def test_firms_cap_prefers_watch_areas(lake, tmp_path, monkeypatch):
    monkeypatch.setattr(godseye, "FIRMS_CAP_WATCH", 3)
    monkeypatch.setattr(godseye, "FIRMS_CAP_GLOBAL", 1)
    _, out = _build(lake, tmp_path)
    feats = _layer(out, "firms")
    assert len(feats) == 4
    frps = [f["properties"]["frp"] for f in feats]
    assert frps[:3] == [70.0, 60.0, 50.0]
    assert frps[3] == 400.0
    assert feats[3]["geometry"]["coordinates"] == [140.0, -30.0]


def test_watch_area_bbox_polygons(lake, tmp_path):
    _, out = _build(lake, tmp_path)
    feats = _layer(out, "watch_areas")
    assert [f["properties"]["name"] for f in feats] == ["Taiwan Strait", "Sahel"]
    tw = feats[0]
    assert tw["geometry"]["type"] == "Polygon"
    ring = tw["geometry"]["coordinates"][0]
    assert ring == [[105.0, 5.0], [130.0, 5.0], [130.0, 28.0], [105.0, 28.0], [105.0, 5.0]]
    assert tw["properties"]["priority"] == "high" and tw["properties"]["severity"] == 7
    assert feats[1]["properties"]["severity"] == 5
    assert tw["properties"]["bbox"] == [105.0, 5.0, 130.0, 28.0]


def test_ukraine_layer_salvages_truncated_frontline_and_alerts(lake, tmp_path):
    _, out = _build(lake, tmp_path)
    feats = _layer(out, "ukraine")
    kinds = sorted(f["properties"]["kind"] for f in feats)
    assert kinds == ["air-alert", "frontline", "thermal"]
    front = next(f for f in feats if f["properties"]["kind"] == "frontline")
    assert front["geometry"]["type"] == "Polygon"
    assert front["geometry"]["coordinates"][0][0] == front["geometry"]["coordinates"][0][-1]
    thermal = next(f for f in feats if f["properties"]["kind"] == "thermal")
    assert thermal["properties"]["date"] == f"{D1}T00:20:00Z"
    alert = next(f for f in feats if f["properties"]["kind"] == "air-alert")
    assert alert["properties"]["oblast"] == "Kharkiv"
    assert alert["geometry"]["coordinates"] == [36.23, 49.99]


def test_flights_keep_latest_and_sanitize_url(lake, tmp_path):
    _, out = _build(lake, tmp_path)
    feats = _layer(out, "flights")
    assert len(feats) == 1
    p = feats[0]["properties"]
    assert p["callsign"] == "RCH1" and p["severity"] == 6
    assert p["url"].startswith("https://")


def test_conflict_and_gdacs_from_lake(lake, tmp_path):
    _, out = _build(lake, tmp_path)
    conflict = _layer(out, "conflict")
    assert len(conflict) == 1 and conflict[0]["properties"]["severity"] == 7
    gd = _layer(out, "gdacs")
    assert len(gd) == 1 and gd[0]["properties"]["severity"] == 6
    assert gd[0]["geometry"]["coordinates"] == [120.68, 28.15]
    assert _layer(out, "weather_alerts") == []


def test_signals_panel_data(lake, tmp_path):
    _, out = _build(lake, tmp_path)
    sig = json.loads((out / "signals.json").read_text(encoding="utf-8"))
    assert sig["day"] == D0
    assert [e["name"] for e in sig["entities"]] == ["Taiwan", "JNIM"]
    assert sig["entities"][0]["section_counts"]["gdelt_gkg"] == 5
    assert sig["stories"][0]["headline"] == "PLA drills near Taiwan"
    assert sig["stories"][0]["url"] == "https://example.com/story"
    assert sig["watch_area_counts"]["counts"] == {"Taiwan Strait": 3, "Sahel": 1, "No bbox area": 0}


def test_no_network_makes_no_http_calls(lake, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    manifest, out = _build(lake, tmp_path, network=False)
    assert manifest["counts"]["quakes"] == 2
    assert (out / "index.html").is_file()


def test_live_failures_only_log(lake, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(godseye.Build, "live_frontline", lambda self: [])
    manifest, out = _build(lake, tmp_path, network=True)
    live = [i for i in manifest["inputs"] if i["name"].startswith("live:")]
    assert live, "live inputs should be recorded"
    failed = [i for i in live if not i["ok"]]
    assert all("ConnectionError" in i["error"] for i in failed)
    assert manifest["counts"]["quakes"] == 2
    assert manifest["network"] is True


def test_cli_entry(lake, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cli"
    rc = godseye._main(["--out", str(out), "--lake", str(lake), "--days", "3", "--no-network",
                        "--watchareas", str(tmp_path / "watchareas.yaml")])
    assert rc == 0
    assert (out / "manifest.json").is_file()
    assert "[godseye] layers:" in capsys.readouterr().out


def test_severity_heuristics():
    assert godseye._quake_severity(4.0) == 2
    assert godseye._quake_severity(7.9, tsunami=1) == 10
    assert godseye._quake_severity(None) == 3
    assert godseye._frp_severity(0) == 2 and godseye._frp_severity(500) == 10
    assert godseye._gdacs_severity("Red") == 9 and godseye._gdacs_severity("") == 4
    assert godseye._fatality_severity(0) == 3 and godseye._fatality_severity(100) == 9
    assert 2 <= godseye._count_severity(1) <= godseye._count_severity(1000) <= 8
    assert godseye._nws_severity("Extreme") == 9


def test_summary_truncation_and_escaping():
    assert len(godseye._summary("x" * 1000)) == 240
    assert godseye._summary("<a href='x'>hi</a>&amp; there") == "hi & there"
    assert godseye._http_url("javascript:alert(1)") == ""
    assert godseye._lonlat({"lat": "91", "lon": 0}) is None
    assert godseye._lonlat({"coordinates": [120.5, 23.5, 10]}) == (120.5, 23.5)
