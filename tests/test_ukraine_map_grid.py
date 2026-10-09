"""Ukraine theater map compositing + run_section failure visibility.

Regression for the hourly job that reported success from 2026-09-13 on while
logging "operands could not be broadcast together with shapes (200,300)
(220,320)": the activity KDE was rasterised at 300x200 and multiplied by a
320x220 population grid.
"""
from __future__ import annotations

import numpy as np
import pytest


def test_match_grid_resamples_to_target_shape_and_is_noop_when_equal():
    from worldscope.cartography_ukraine import _match_grid
    pop = np.random.default_rng(0).random((220, 320))
    kde = np.random.default_rng(1).random((200, 300))
    with pytest.raises(ValueError):
        kde * pop  # the original failure
    aligned = _match_grid(kde, pop.shape)
    assert aligned.shape == (220, 320)
    assert (aligned * pop).shape == (220, 320)
    # corners stay anchored to the same bbox corners
    assert aligned[0, 0] == kde[0, 0]
    assert aligned[-1, -1] == kde[-1, -1]
    assert _match_grid(pop, pop.shape) is pop
    assert _match_grid(np.zeros((0, 0)), (4, 5)).shape == (4, 5)
    with pytest.raises(ValueError):
        _match_grid(np.zeros(5), (2, 2))


def test_population_at_risk_composites_heat_over_population(tmp_path):
    """Drive the real render path with stubbed lake records (no network, no
    fiona) and assert the heat layer is produced at the population grid's
    shape instead of raising."""
    pytest.importorskip("matplotlib")
    from worldscope import cartography_ukraine as cu

    recs = [
        {"longitude": 30.52, "latitude": 50.45, "fatalities": 2, "source_kind": "thermal"},
        {"longitude": 36.23, "latitude": 49.99, "fatalities": 0, "source_kind": "conflict-events"},
        {"longitude": 35.14, "latitude": 47.84, "fatalities": 5, "source_kind": "thermal"},
    ]
    maps = cu.UkraineMaps(lake_db_path=tmp_path / "missing.sqlite", output_root=tmp_path / "figs")
    maps._fetch_ukraine_records = lambda since, kinds=None: recs  # type: ignore[method-assign]

    shapes: list[tuple[int, ...]] = []
    real_imshow = cu.plt.Axes.imshow

    def spy(self, X, *a, **k):
        shapes.append(np.asarray(X).shape)
        return real_imshow(self, X, *a, **k)

    cu.plt.Axes.imshow = spy  # type: ignore[assignment]
    try:
        path = maps.render_population_at_risk("2026-10-09")
    finally:
        cu.plt.Axes.imshow = real_imshow  # type: ignore[assignment]
    assert path.exists() and path.stat().st_size > 0
    assert len(shapes) == 2, shapes  # population baseline + heat overlay
    assert shapes[0] == shapes[1]


def test_run_section_reports_map_failure_visibly(monkeypatch, capsys):
    from worldscope import run_section

    class Boom:
        def render_all(self, stem):
            raise ValueError("operands could not be broadcast together")

    import worldscope.cartography_ukraine as cu
    monkeypatch.setattr(cu, "UkraineMaps", Boom)
    rc = run_section.emit_ukraine_maps("2026-10-09")
    out = capsys.readouterr().out
    assert rc == 1
    assert "::error" in out
    assert "map emit failed (data still refreshed)" in out


def test_run_section_map_success_returns_zero(monkeypatch, tmp_path, capsys):
    from worldscope import run_section

    class Ok:
        def render_all(self, stem):
            return {"theater": tmp_path / "theater.png"}

    import worldscope.cartography_ukraine as cu
    monkeypatch.setattr(cu, "UkraineMaps", Ok)
    monkeypatch.setattr(run_section, "_mirror_maps", lambda stem: 0)
    assert run_section.emit_ukraine_maps("2026-10-09") == 0
    assert "::error" not in capsys.readouterr().out
