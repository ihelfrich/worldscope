"""worldscope.scoring: anomaly scoring engines.

Per-figure and per-entity scoring that the political_figures section (and,
later, other watchlists) feed into. Composite scores are normalized to [0, 1].

The scoring module is deliberately offline-friendly: every component degrades
to 0 when its input data is missing, so a fresh install with an empty lake
still returns valid (zero) scores rather than crashing.
"""
from .figure_anomaly import (
    FigureAnomalyScorer,
    AnomalyComponents,
    score_figure,
    score_all,
)

__all__ = [
    "FigureAnomalyScorer",
    "AnomalyComponents",
    "score_figure",
    "score_all",
]
