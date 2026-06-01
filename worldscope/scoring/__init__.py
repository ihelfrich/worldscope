"""worldscope.scoring: anomaly + self-evaluation scoring engines.

Per-figure and per-entity anomaly scoring that the political_figures section
(and, later, other watchlists) feed into, plus `track_record`: objective
calibration/skill metrics over the system's own resolved forecasts and paper
bets — how good its predictions actually were.

The scoring module is deliberately offline-friendly: every component degrades
to 0/None when its input data is missing, so a fresh install with an empty
lake still returns valid scores rather than crashing.
"""
from .figure_anomaly import (
    FigureAnomalyScorer,
    AnomalyComponents,
    score_figure,
    score_all,
)
from .track_record import (
    PredictionSkill,
    BetSkill,
    CalibrationBin,
    score_predictions,
    score_paper_bets,
    score_predictions_from_connection,
    score_paper_bets_from_connection,
)

__all__ = [
    "FigureAnomalyScorer",
    "AnomalyComponents",
    "score_figure",
    "score_all",
    "PredictionSkill",
    "BetSkill",
    "CalibrationBin",
    "score_predictions",
    "score_paper_bets",
    "score_predictions_from_connection",
    "score_paper_bets_from_connection",
]
