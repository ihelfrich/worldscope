"""Atom <updated> moves when a brief is republished, and only then."""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import render_brief as rb  # noqa: E402


def _updated(out):
    return re.search(r"<updated>([^<]+)</updated>\s*<summary",
                     (out / "feed.xml").read_text()).group(1)


def test_updated_bumps_only_on_content_change(tmp_path, monkeypatch):
    src = tmp_path / "briefings"
    src.mkdir()
    md = src / "2026-11-12.md"
    md.write_text("# Morning Brief - Thursday 12 November 2026\n\nfirst\n")
    out = tmp_path / "dist"
    out.mkdir()
    page = out / "2026-11-12.html"
    page.write_text("x")
    monkeypatch.setattr(rb, "REPO", tmp_path)

    rb._render_feed(out, "briefings", [page])
    assert _updated(out) == "2026-11-12T11:00:00Z"            # first sighting
    assert "<published>2026-11-12T11:00:00Z</published>" in (out / "feed.xml").read_text()

    rb._render_feed(out, "briefings", [page])
    assert _updated(out) == "2026-11-12T11:00:00Z"            # unchanged: stable

    md.write_text("# Morning Brief - Thursday 12 November 2026\n\ncorrected\n")
    rb._render_feed(out, "briefings", [page])
    bumped = _updated(out)
    assert bumped > "2026-11-12T11:00:00Z"                    # republished: moves

    rb._render_feed(out, "briefings", [page])
    assert _updated(out) == bumped                            # and then stays
