"""Suite-wide guards."""
import pytest


@pytest.fixture(autouse=True)
def _no_gdelt_bulk_downloads(monkeypatch):
    """Sections read GDELT bulk files first; never let a test hit the network.

    A test that wants the file path patches ``gdelt_files._download`` or
    ``gdelt_files.recent_articles`` itself (applied after this fixture).
    """
    from worldscope.lib import gdelt_files

    def offline(stamp, timeout):
        raise RuntimeError("network disabled in tests")
    monkeypatch.setattr(gdelt_files, "_download", offline)
    gdelt_files.clear_cache()
    yield
    gdelt_files.clear_cache()
