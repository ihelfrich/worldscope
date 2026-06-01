"""Tests for the display-time repetitive/broken-item filter on rendered
sections (Section._dedup_display_items and friends)."""
from worldscope.sections import Section


def test_collapses_title_casing_and_punctuation_variants():
    items = [
        {"title": "Iran deadline passes without a deal", "url": "https://a.com/x"},
        {"title": "IRAN DEADLINE PASSES WITHOUT A DEAL!", "url": "https://b.com/y"},
    ]
    out = Section._dedup_display_items(items)
    assert [o["title"] for o in out] == ["Iran deadline passes without a deal"]


def test_collapses_url_duplicates_ignoring_query_and_trailing_slash():
    items = [
        {"title": "Story one", "url": "https://a.com/article?utm=1"},
        {"title": "Story two", "url": "https://a.com/article/"},
    ]
    out = Section._dedup_display_items(items)
    assert len(out) == 1


def test_drops_broken_items():
    items = [
        {"title": "(no title)", "url": "https://c.com"},
        {"title": "", "url": "https://d.com"},
        {"title": "—", "url": "https://e.com"},
        {"title": "ok", "url": "https://f.com"},  # 2 alnum chars -> broken
        {"title": "A real headline here", "url": "https://g.com"},
    ]
    out = Section._dedup_display_items(items)
    assert [o["title"] for o in out] == ["A real headline here"]


def test_distinct_headlines_are_preserved_and_ordered():
    items = [
        {"title": "Alpha event in the east", "url": "https://a.com/1"},
        {"title": "Beta event in the west", "url": "https://a.com/2"},
        {"title": "Gamma event up north", "url": "https://a.com/3"},
    ]
    out = Section._dedup_display_items(items)
    assert [o["title"] for o in out] == [
        "Alpha event in the east",
        "Beta event in the west",
        "Gamma event up north",
    ]


def test_empty_input_returns_empty():
    assert Section._dedup_display_items([]) == []
