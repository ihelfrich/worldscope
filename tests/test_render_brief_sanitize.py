"""tools/render_brief.py must not pass active content from briefing Markdown
through to the public Pages site (stored XSS), while leaving the renderer's own
Leaflet embed intact."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rb():
    pytest.importorskip("markdown")
    sys.path.insert(0, str(REPO / "tools"))
    try:
        return importlib.import_module("render_brief")
    finally:
        sys.path.remove(str(REPO / "tools"))


def test_strips_script_and_iframe_blocks(rb):
    html = ('<p>before</p><script>alert(1)</script><p>mid</p>'
            '<SCRIPT src="https://evil.example/x.js"></SCRIPT>'
            '<iframe src="https://evil.example"></iframe><p>after</p>')
    out = rb.sanitize_html(html)
    assert "<script" not in out.lower()
    assert "<iframe" not in out.lower()
    assert "alert(1)" not in out
    assert out == "<p>before</p><p>mid</p><p>after</p>"


def test_strips_event_handler_attributes(rb):
    html = ('<img src="../2026-10-09-map.png" alt="m" onerror="alert(1)">'
            "<div onclick=alert(1) class=\"x\">t</div>"
            "<a href=\"https://ok.example\" onmouseover='alert(1)'>l</a>")
    out = rb.sanitize_html(html)
    assert "onerror" not in out and "onclick" not in out and "onmouseover" not in out
    assert '<img src="../2026-10-09-map.png" alt="m">' in out
    assert 'class="x"' in out
    assert '<a href="https://ok.example">l</a>' in out


def test_neutralises_javascript_hrefs_including_bypasses(rb):
    html = ('<a href="javascript:alert(1)">a</a>'
            '<a href="  JaVa\tscript:alert(1)">b</a>'
            '<a href="&#106;avascript:alert(1)">c</a>'
            "<a href='vbscript:x'>d</a>"
            '<a href="data:text/html;base64,PHNjcmlwdD4=">e</a>'
            '<a href="https://fine.example/p?q=1">f</a>')
    out = rb.sanitize_html(html)
    assert "javascript" not in out.lower()
    assert "vbscript" not in out.lower()
    assert "data:text/html" not in out
    assert out.count('href="#"') == 5
    assert '<a href="https://fine.example/p?q=1">f</a>' in out


def test_split_tag_cannot_reassemble(rb):
    out = rb.sanitize_html("<scr<script>ipt>alert(1)</script>")
    assert "<script" not in out.lower()


def test_ordinary_markup_passes_through(rb):
    html = ('<h2 id="x">Head</h2><table><tr><td>1</td></tr></table>'
            '<ul><li><a href="https://a.example">t</a>'
            '<span class="ws-hostpill">a.example</span></li></ul>'
            '<img src="../2026-10-09-ukraine_theater.png" alt="map">'
            '<pre><code>x &lt; y</code></pre>')
    assert rb.sanitize_html(html) == html


def test_render_one_sanitises_body_but_keeps_leaflet_embed(rb, tmp_path, monkeypatch):
    import json
    md = tmp_path / "briefings"
    md.mkdir()
    brief = md / "2026-10-09.md"
    brief.write_text(
        "# Title\n\n## Section\n\nprose <script>alert(1)</script> "
        "<a href=\"javascript:alert(2)\">x</a> <img src=x onerror=alert(3)>\n",
        encoding="utf-8")
    (md / "2026-10-09-events.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    monkeypatch.setattr(rb, "load_watch_dashboard", lambda: [])
    monkeypatch.setattr(rb, "_brief_network_seed", lambda: "{}")
    out_dir = tmp_path / "dist" / "briefings"
    page = rb.render_one(brief, out_dir, "briefings").read_text(encoding="utf-8")
    body = page.split("<h2 id=\"section\"", 1)[-1] if "<h2 id=\"section\"" in page else page
    # The injected payloads are gone from the rendered body ...
    assert "alert(1)" not in page and "alert(2)" not in page and "alert(3)" not in page
    assert "onerror" not in page
    assert "javascript:" not in page
    # ... but the renderer's own Leaflet embed is still there.
    assert 'id="gridmap"' in page
    assert "leaflet@1.9.4/dist/leaflet.js" in page
    assert '"type": "FeatureCollection"' in page
    assert "<h2 id=\"section\"" in page or "Section" in body
