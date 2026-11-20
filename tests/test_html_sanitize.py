from tools.html_sanitize import sanitize_html


def test_strips_script_and_handlers():
    out = sanitize_html('<p onclick="x()">hi</p><script>alert(1)</script><img src=x onerror=alert(1)>')
    assert "script" not in out and "onclick" not in out and "onerror" not in out
    assert "<p>hi</p>" in out


def test_blocks_dangerous_schemes():
    out = sanitize_html('<a href="javascript:alert(1)">a</a><a href=" JaVa\tscript:x">b</a>'
                        '<a href="data:text/html,x">c</a><a href="https://ok.example/x">d</a>')
    assert "javascript" not in out.lower() and "data:" not in out
    assert 'href="https://ok.example/x"' in out


def test_keeps_tables_and_ids():
    out = sanitize_html('<h2 id="a">T</h2><table><tr><td>1</td></tr></table>')
    assert 'id="a"' in out and "<td>1</td>" in out


def test_drops_iframe_content():
    assert "evil" not in sanitize_html("<iframe src='//x'>evil</iframe>ok")
