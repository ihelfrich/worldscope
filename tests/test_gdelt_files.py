"""GDELT bulk GKG files: parsing, story ranking, and the three sections' file path.

ROW is built in the real GKG 2.1 column layout (27 tab-separated fields),
modelled on a live row from 20261113004500.gkg.csv.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from worldscope.lib import gdelt_files as gf


def row(url="https://www.example.co.uk/a", domain="example.co.uk",
        title="Military helicopter crash in Turkey leaves four dead",
        stamp="20261113004500", themes="KILL,10;ARMEDCONFLICT,20;MILITARY,5",
        v1loc="4#Ankara, Ankara, Turkey#TU#TU68#39.93#32.86#-736", v2loc=None,
        tone="-6.0,1.6,7.6,9.3,26.3,0.6,545"):
    if v2loc is None:
        v2loc = ("4#Ankara, Ankara, Turkey#TU#TU68##39.93#32.86#-736#120;"
                 "1#Turkey#TU#TU##39#35#TU#300")
    f = [""] * 27
    f[0], f[1], f[2], f[3], f[4] = f"{stamp}-0", stamp, "1", domain, url
    f[7] = ";".join(t.split(",")[0] for t in themes.split(";"))
    f[8], f[9], f[10], f[15] = themes, v1loc, v2loc, tone
    f[26] = f"<PAGE_PRECISEPUBTIMESTAMP>{stamp}</PAGE_PRECISEPUBTIMESTAMP><PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(f)


def test_parse_row_fields():
    a = gf.parse_row(row())
    assert a.url == "https://www.example.co.uk/a" and a.domain == "example.co.uk"
    assert a.title.startswith("Military helicopter crash")
    assert a.seen == datetime(2026, 11, 13, 0, 45, tzinfo=timezone.utc)
    assert {"KILL", "ARMEDCONFLICT", "MILITARY"} == set(a.themes)
    assert a.countries[0] == "TU" and "TU" in a.subnational and a.country_name == "Turkey"
    assert (39.93, 32.86) in a.points and a.tone == -6.0 and not a.us_local


def test_parse_row_rejects_rows_without_title_or_url():
    assert gf.parse_row(row(title="")) is None
    assert gf.parse_row(row(url="not-a-url")) is None
    assert gf.parse_row("short\trow") is None


def test_canonical_country_name_from_v1_not_the_word_as_written():
    a = gf.parse_row(row(v1loc="1#China#CH#CH#35#105#CH",
                         v2loc="1#Chinese#CH#CH##35#105#CH#10"))
    assert a.country_name == "China"


def test_slots_skip_the_newest_unpublished_file():
    now = datetime(2026, 11, 13, 1, 7, tzinfo=timezone.utc)
    slots = gf._slots(now, 2)
    assert slots[0] == "20261113004500" and slots[1] == "20261113001500"
    assert len(slots) == 4


def _art(title, domain, minute=0):
    return gf.parse_row(row(url=f"https://{domain}/{minute}", domain=domain, title=title,
                            stamp=f"202611130{minute % 10}0000"))


def test_top_stories_prefers_widely_covered_and_collapses_duplicates():
    arts = [
        _art("Turkey helicopter crash kills four soldiers", "a.com", 1),
        _art("Four soldiers killed in Turkey helicopter crash", "b.com", 2),
        _art("Helicopter crash kills four soldiers near Ankara", "c.com", 3),
        _art("Recipe: leftover turkey casserole", "d.com", 4),
        _art("Erdogan meets Gulf leaders on trade corridor", "e.com", 5),
    ]
    top = gf.top_stories(arts, 3, ignore=frozenset({"turkey"}))
    titles = [a.title for a, _ in top]
    assert "crash" in titles[0].lower() and top[0][1] == 2
    assert sum("crash" in t.lower() for t in titles) == 1      # same story once
    assert len(titles) == 3


def test_top_stories_one_article_per_outlet():
    arts = [_art("Alpha beta gamma delta", "same.com", 1),
            _art("Epsilon zeta eta theta", "same.com", 2)]
    assert len(gf.top_stories(arts, 5)) == 1


def test_recent_articles_dedupes_caches_and_tolerates_missing_files(monkeypatch):
    gf.clear_cache()
    calls = []

    def download(stamp, timeout):
        calls.append(stamp)
        if len(calls) == 1:
            raise RuntimeError("404 not yet published")
        return [gf.parse_row(row())]
    monkeypatch.setattr(gf, "_download", download)
    now = datetime(2026, 11, 13, 1, 7, tzinfo=timezone.utc)
    b = gf.recent_articles(2, now=now, workers=1)
    assert len(b.articles) == 1 and b.files_ok == 3 and b.files_tried == 4
    assert gf.recent_articles(2, now=now) is b and len(calls) == 4
    gf.clear_cache()


def test_recent_articles_raises_when_nothing_downloads(monkeypatch):
    gf.clear_cache()
    monkeypatch.setattr(gf, "_download", lambda s, t: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(gf.GdeltFilesUnavailable, match="down"):
        gf.recent_articles(1, now=datetime(2026, 11, 13, 1, 7, tzinfo=timezone.utc))
    gf.clear_cache()


# --- the three sections on the file path ------------------------------------

def _batch(*rows):
    return gf.Batch([gf.parse_row(r) for r in rows], 1, 1)


def test_regions_assigns_by_headline_and_gates_ambiguous_names(monkeypatch):
    from worldscope.sections import gdelt_regions as gr
    batch = _batch(
        row(url="https://a/1", domain="a.com"),                       # Turkey + Ankara tag
        row(url="https://b/1", domain="b.com", title="Leftover turkey casserole recipe",
            v1loc="1#Turkey#TU#TU#39#35#TU", v2loc="1#Turkey#TU#TU##39#35#TU#5"),
        row(url="https://c/1", domain="c.com", title="Japanese yen slides past 160",
            v1loc="", v2loc=""),
    )
    monkeypatch.setattr(gf, "recent_articles", lambda *a, **k: batch)
    sec = gr.GdeltRegionsSection.__new__(gr.GdeltRegionsSection)
    items = sec.pull()
    by = {it["country"]: it["title"] for it in items}
    assert by == {"Turkey": "[Turkey] Military helicopter crash in Turkey leaves four dead",
                  "Japan": "[Japan] Japanese yen slides past 160"}
    assert sec.coverage["countries"] == 2 and sec.coverage["source"] == "gkg-files"


def test_conflict_uses_theme_tags(monkeypatch):
    from worldscope.sections import conflict
    batch = _batch(row(), row(url="https://x/2", domain="x.com", title="Bake sale raises funds",
                              themes="TAX_FNCACT,1"))
    monkeypatch.setattr(gf, "recent_articles", lambda *a, **k: batch)
    items = conflict.ConflictSection.__new__(conflict.ConflictSection).pull()
    assert [it["country"] for it in items] == ["Turkey"]
    assert items[0]["title"].startswith("[Turkey] Military helicopter")


def test_gkg_watch_areas_match_themes_in_box_and_whole_word_keywords(monkeypatch):
    from worldscope.lib.watchareas import WatchArea
    from worldscope.sections import gdelt_gkg
    area = WatchArea(name="Anatolia", themes=["KILL", "ARMEDCONFLICT"],
                     keywords=["itc"], bbox=[26.0, 36.0, 45.0, 42.0])
    batch = _batch(
        row(),                                                         # in box, 2 themes
        row(url="https://y/1", domain="y.com", title="Gunman kills two in Ohio",
            v1loc="3#Columbus, Ohio#US#USOH#39.9#-83#1", v2loc="3#Columbus, Ohio#US#USOH##39.9#-83#1#4"),
        row(url="https://z/1", domain="z.com", title="Kansas City council meets", themes="X,1",
            v1loc="", v2loc=""),
        row(url="https://w/1", domain="w.com", title="ITC rules on solar panel imports",
            themes="X,1", v1loc="", v2loc=""),
    )
    monkeypatch.setattr(gf, "recent_articles", lambda *a, **k: batch)
    monkeypatch.setattr(gdelt_gkg, "load_watch_areas", lambda: [area])
    items = gdelt_gkg.GdeltGkgSection.__new__(gdelt_gkg.GdeltGkgSection).pull()
    subs = {it["url"]: it["title"].split("]")[0] for it in items}
    assert subs == {"https://www.example.co.uk/a": "[Anatolia · themes",
                    "https://w/1": "[Anatolia · keywords"}


def test_sections_fall_back_to_doc_api_when_files_unavailable(monkeypatch):
    from worldscope.sections import conflict
    def unavailable(*a, **k):
        raise gf.GdeltFilesUnavailable("cdn down")
    monkeypatch.setattr(gf, "recent_articles", unavailable)
    sec = conflict.ConflictSection.__new__(conflict.ConflictSection)
    monkeypatch.setattr(conflict.ConflictSection, "_pull_doc", lambda self: ["doc-path"])
    assert sec.pull() == ["doc-path"]


def test_regions_raises_schema_error_when_nothing_matches(monkeypatch):
    from worldscope.sections import UpstreamSchemaError
    from worldscope.sections import gdelt_regions as gr
    batch = _batch(row(title="Local bake sale raises funds", v1loc="", v2loc=""))
    monkeypatch.setattr(gf, "recent_articles", lambda *a, **k: batch)
    with pytest.raises(UpstreamSchemaError, match="layout"):
        gr.GdeltRegionsSection.__new__(gr.GdeltRegionsSection).pull()
