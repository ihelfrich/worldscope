"""Watch-area keywords match whole words (plural-tolerant), not substrings."""
from worldscope.lib.watchareas import WatchArea, keyword_regex, load_watch_areas


def _hit(kws, text):
    r = keyword_regex(kws)
    return bool(r and r.search(text))


def test_no_substring_false_positives():
    assert not _hit(["cit"], "Kansas City council meets")
    assert not _hit(["ustr"], "Steel industry rebounds")
    assert not _hit(["coup"], "Coupon deals for Black Friday")
    assert not _hit(["dmz"], "Admz holdings files")


def test_whole_words_plurals_and_phrases():
    assert _hit(["cit"], "Ruling from the CIT on duties")
    assert _hit(["houthi"], "Houthis claim drone strikes")
    assert _hit(["houthi"], "the Houthi's missile")
    assert _hit(["court of international trade"], "U.S. Court of International Trade rules")
    assert _hit(["mt. dov"], "Fire near Mt. Dov reported")


def test_matcher_uses_whole_words():
    area = WatchArea(name="Trade", keywords=["cit", "ustr"])
    assert not area.matches({"title": "City industry news"})
    assert area.matches({"title": "USTR opens Section 301 review"})


def test_shipped_watch_areas_have_no_ambiguous_bare_keywords():
    banned = {"default", "moratorium", "powell", "wagner", "bowman", "waller",
              "petro", "coup", "junta", "tga", "cma", "dsa", "cit", "cxl", "nsr", "nlt",
              "rrp", "sln"}
    for area in load_watch_areas():
        assert not banned & {k.lower() for k in area.keywords}, area.name
