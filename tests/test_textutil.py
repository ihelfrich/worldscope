"""Tests for HTML/text hygiene helpers (worldscope.textutil)."""
from worldscope.textutil import clean_text, strip_html


def test_strips_figure_and_img_blobs():
    s = ('Group rallies to remember immigrants '
         '<figure><img width="1024" src="https://x/y.jpg?a=1&amp;b=2" '
         'class="rss-image"></figure>')
    assert strip_html(s) == "Group rallies to remember immigrants"


def test_decodes_entities_and_removes_entity_encoded_tags():
    assert strip_html("S&amp;P 500") == "S&P 500"
    assert strip_html("Title &lt;img src=x&gt; tail") == "Title tail"


def test_strips_anchor_wrappers_and_collapses_whitespace():
    assert strip_html('<a href="u">Link</a>   text\n\nmore') == "Link text more"


def test_strips_scraper_title_lede_wrapper():
    assert "TITLE" not in strip_html("[TITLE: Foo bar]")


def test_clean_text_caps_length_with_ellipsis():
    assert clean_text("abcdefghij", 5) == "abcde…"
    assert clean_text("short", 50) == "short"


def test_empty_and_none_safe():
    assert strip_html(None) == ""
    assert strip_html("") == ""
    assert clean_text(None, 10) == ""
