"""worldscope.analysis: second-pass analytical layers over the lake.

Each module here reads the lake's structured.json / SQLite snapshots and
produces a meta-analytical artifact that the desk-officer routine prompt
consumes alongside the per-section pulls. The routine prompt already asks
for cross-section recurrence, anomaly attribution, and source-tier
weighting; this package precomputes that data deterministically rather
than asking the model to derive it from raw text.
"""
