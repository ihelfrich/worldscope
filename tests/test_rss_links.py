"""RSS/Atom parsing keeps the article link, never an enclosure image."""
from worldscope.sections.state_news import _parse_rss

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>Taiwan stocks rebound</title>
<link rel="alternate" type="text/html" href="https://www.intellinews.com/taiwan-stocks-484626/"/>
<link rel="enclosure" type="image/jpeg" href="https://cdn.example.net/img.jpg"/>
<updated>2026-11-12T10:00:00Z</updated></entry></feed>"""

ATOM_ENCLOSURE_FIRST = ATOM.replace(
    b'<link rel="alternate" type="text/html" href="https://www.intellinews.com/taiwan-stocks-484626/"/>\n'
    b'<link rel="enclosure" type="image/jpeg" href="https://cdn.example.net/img.jpg"/>',
    b'<link rel="enclosure" type="image/jpeg" href="https://cdn.example.net/img.jpg"/>\n'
    b'<link href="https://www.intellinews.com/taiwan-stocks-484626/"/>')

RSS = b"""<rss><channel><item><title>T</title><link>https://ex.com/a</link>
<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"""


def test_atom_alternate_wins_over_enclosure_in_either_order():
    for xml in (ATOM, ATOM_ENCLOSURE_FIRST):
        assert _parse_rss(xml)[0]["url"] == "https://www.intellinews.com/taiwan-stocks-484626/"


def test_plain_rss_link_text_still_works():
    assert _parse_rss(RSS)[0]["url"] == "https://ex.com/a"
