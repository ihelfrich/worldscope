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


# ---- render_html must never emit untrusted strings as live HTML ------------

from worldscope.sections import STATE_FRESH, SectionState


class _Sec(Section):
    id = "xss_probe"
    title = "Probe"
    emoji = "P"

    def pull(self):  # pragma: no cover - never called
        return []


def _render(items, synth=None):
    sec = object.__new__(_Sec)  # skip __init__: render_html needs no store
    state = SectionState(section_id="xss_probe", title="Probe", emoji="P",
                         state=STATE_FRESH, items=items, new=[],
                         comparison_date=None, source_date="2026-10-09")
    return sec.render_html(state, synth)


def test_synth_paragraph_is_escaped_not_rendered_as_html():
    payload = "<img src=x onerror=alert(1)><script>alert(2)</script> & \"quotes\""
    out = _render([], synth=payload)
    assert "<script" not in out and "<img" not in out and "onerror" not in out.replace("onerror=", "")
    assert "&lt;img src=x onerror=alert(1)&gt;&lt;script&gt;" in out
    assert "&amp; &quot;quotes&quot;" in out
    assert "<p class='synth'>" in out


def test_item_href_blocks_javascript_and_data_urls():
    items = [
        {"_id": "1", "title": "A real headline here", "url": "javascript:alert(1)"},
        {"_id": "2", "title": "Another real headline", "url": "data:text/html,<script>1</script>"},
        {"_id": "3", "title": "Third real headline", "url": "https://ok.example/a?x=1&y=2"},
    ]
    out = _render(items)
    assert "javascript:" not in out
    assert "data:text/html" not in out
    assert out.count("href='#'") == 2
    assert "href='https://ok.example/a?x=1&amp;y=2'" in out


def test_item_title_and_summary_remain_escaped():
    items = [{"_id": "1", "title": "Real <b>bold</b> headline", "url": "https://x.example",
              "summary": "<script>alert(1)</script> sum"}]
    out = _render(items)
    assert "<script" not in out
    assert "<b>" not in out
    assert "alert(1) sum" in out  # clean_text strips tags, leaving inert text
    assert "Real bold headline" in out

