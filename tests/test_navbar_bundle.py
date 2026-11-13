"""Index pages link the newest bundle, never the bare zips/ folder (404 on Pages)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import render_brief as rb  # noqa: E402


def test_index_pages_link_newest_bundle(tmp_path, monkeypatch):
    z = tmp_path / "dist" / "zips"
    z.mkdir(parents=True)
    for d in ("2026-11-11", "2026-11-12"):
        (z / f"{d}.zip").write_bytes(b"x")
    monkeypatch.setattr(rb, "REPO", tmp_path)
    nav = rb._navbar(assets_prefix="./")
    assert 'href="./zips/2026-11-12.zip"' in nav
    assert 'href="./zips/"' not in nav


def test_no_bundle_link_when_none_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "REPO", tmp_path)
    assert "zips/" not in rb._navbar(assets_prefix="./")


def test_brief_page_links_its_own_bundle(tmp_path, monkeypatch):
    z = tmp_path / "dist" / "zips"
    z.mkdir(parents=True)
    (z / "2026-11-10.zip").write_bytes(b"x")
    (z / "2026-11-12.zip").write_bytes(b"x")
    monkeypatch.setattr(rb, "REPO", tmp_path)
    assert 'href="../zips/2026-11-10.zip"' in rb._navbar(brief_stem="2026-11-10")
