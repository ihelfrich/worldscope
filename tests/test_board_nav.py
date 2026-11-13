"""The homepage board links out to the brief, archive and sections."""
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import render_board as rb  # noqa: E402


def test_nav_links_latest_brief_archive_and_sections(tmp_path, monkeypatch):
    (tmp_path / "briefings").mkdir()
    for d in ("2026-11-10", "2026-11-12", "2026-11-20"):
        (tmp_path / "briefings" / f"{d}.md").write_text("x")
    monkeypatch.setattr(rb, "REPO", tmp_path)
    nav = rb._nav(date(2026, 11, 12), "./")
    assert "href='./briefings/2026-11-12.html'>Today's brief" in nav   # not a future date
    assert "href='./briefings/'>Archive" in nav and "href='./sections/'>Sections" in nav
    assert "weekly_briefings" not in nav                                 # none exist here
    later = rb._nav(date(2026, 11, 13), "../../")
    assert "href='../../briefings/2026-11-12.html'>Latest brief · 2026-11-12" in later
