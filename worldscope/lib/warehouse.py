"""
warehouse.py — DuckDB time-series + observation warehouse.

Stops the system from re-hitting upstream APIs for the same historical
macro/markets data on every run, and gives us a SQL surface for anomaly
detection, regime tracking, and cross-series joins.

Schema is intentionally narrow: one long-format `observations` table
(source, series_id, date, value, units, frequency, last_updated). Add
new sources by INSERT — no schema migrations as the surface area grows.

Companion `series_meta` table holds friendly labels + the watchlist
membership for each series.

Default location: ~/.worldscope/warehouse.duckdb (override with
WORLDSCOPE_WAREHOUSE_PATH).

Usage:
    from worldscope.lib import warehouse
    w = warehouse.open()
    w.upsert_observations("fred", "DGS10", [(date(2026,5,21), 4.57)])
    df = w.query("DGS10")
    z  = w.zscore_latest("DGS10", lookback_days=30)   # anomaly score
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

try:
    import duckdb
except ImportError:  # graceful degrade if duckdb isn't installed
    duckdb = None  # type: ignore


DEFAULT_PATH = Path(
    os.environ.get("WORLDSCOPE_WAREHOUSE_PATH",
                   str(Path.home() / ".worldscope" / "warehouse.duckdb"))
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    source        VARCHAR NOT NULL,    -- 'fred', 'finnhub', 'imf', ...
    series_id     VARCHAR NOT NULL,    -- 'DGS10', 'SPY', etc.
    obs_date      DATE NOT NULL,
    value         DOUBLE,              -- nullable for missing
    units         VARCHAR,
    frequency     VARCHAR,             -- 'D','W','M','Q','A'
    last_updated  TIMESTAMP NOT NULL,
    PRIMARY KEY (source, series_id, obs_date)
);

CREATE TABLE IF NOT EXISTS series_meta (
    source        VARCHAR NOT NULL,
    series_id     VARCHAR NOT NULL,
    label         VARCHAR,
    group_label   VARCHAR,             -- 'Rates', 'Inflation', etc.
    units         VARCHAR,
    frequency     VARCHAR,
    notes         VARCHAR,
    PRIMARY KEY (source, series_id)
);

CREATE INDEX IF NOT EXISTS obs_series_date_idx ON observations(series_id, obs_date);
CREATE INDEX IF NOT EXISTS obs_source_idx ON observations(source);
"""


@dataclass
class Observation:
    obs_date: date
    value: Optional[float]


class Warehouse:
    """Thin wrapper over a DuckDB connection with the worldscope schema."""

    def __init__(self, path: Path | str | None = None):
        if duckdb is None:
            raise RuntimeError("duckdb not installed; pip install duckdb")
        self.path = Path(path) if path else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self._init_schema()

    def _init_schema(self):
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                self.conn.execute(stmt)

    # ---- writes ---------------------------------------------------------

    def upsert_observations(self, source: str, series_id: str,
                            observations: Iterable[tuple[date, Optional[float]]],
                            *, units: str = "", frequency: str = "") -> int:
        """Insert or replace observations for one (source, series). Returns
        the count of rows written."""
        now = datetime.now(timezone.utc)
        rows = [(source, series_id, d, v, units, frequency, now)
                for d, v in observations]
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        return len(rows)

    def upsert_meta(self, source: str, series_id: str, *,
                    label: str = "", group_label: str = "",
                    units: str = "", frequency: str = "", notes: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO series_meta VALUES (?,?,?,?,?,?,?)",
            (source, series_id, label, group_label, units, frequency, notes),
        )

    # ---- queries --------------------------------------------------------

    def query(self, series_id: str, *,
              source: Optional[str] = None,
              start: Optional[date] = None,
              end: Optional[date] = None):
        """Return a DuckDB Relation; call .fetchdf() for a pandas DataFrame
        or .fetchall() for tuples."""
        where = ["series_id = ?"]
        params: list = [series_id]
        if source:
            where.append("source = ?"); params.append(source)
        if start:
            where.append("obs_date >= ?"); params.append(start)
        if end:
            where.append("obs_date <= ?"); params.append(end)
        sql = ("SELECT obs_date, value FROM observations "
               "WHERE " + " AND ".join(where) + " ORDER BY obs_date")
        return self.conn.execute(sql, params)

    def latest(self, series_id: str, *, source: Optional[str] = None) -> Optional[tuple]:
        """Return the most recent (date, value) observation or None."""
        where = ["series_id = ?"]
        params: list = [series_id]
        if source:
            where.append("source = ?"); params.append(source)
        sql = ("SELECT obs_date, value FROM observations "
               "WHERE " + " AND ".join(where) + " ORDER BY obs_date DESC LIMIT 1")
        row = self.conn.execute(sql, params).fetchone()
        return row

    def date_range(self, series_id: str) -> Optional[tuple[date, date]]:
        row = self.conn.execute(
            "SELECT MIN(obs_date), MAX(obs_date) FROM observations WHERE series_id = ?",
            (series_id,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return (row[0], row[1])

    def series_list(self, source: Optional[str] = None) -> list[tuple]:
        sql = "SELECT DISTINCT source, series_id FROM observations"
        params: list = []
        if source:
            sql += " WHERE source = ?"; params.append(source)
        sql += " ORDER BY source, series_id"
        return self.conn.execute(sql, params).fetchall()

    def stats(self) -> dict:
        rows = self.conn.execute("""
            SELECT source, COUNT(DISTINCT series_id) AS n_series,
                   COUNT(*) AS n_observations, MIN(obs_date), MAX(obs_date)
            FROM observations GROUP BY source ORDER BY n_observations DESC
        """).fetchall()
        return {
            "by_source": [
                {"source": r[0], "n_series": r[1], "n_obs": r[2],
                 "min_date": r[3], "max_date": r[4]} for r in rows
            ]
        }

    # ---- analytics ------------------------------------------------------

    def zscore_latest(self, series_id: str, *, lookback_days: int = 30,
                      source: Optional[str] = None) -> Optional[float]:
        """Z-score of the latest observation versus the trailing `lookback_days`
        window. None if the series has insufficient history or zero variance.
        This is the cheap-and-fast anomaly detector."""
        end_row = self.latest(series_id, source=source)
        if not end_row or end_row[1] is None:
            return None
        end_date, latest_v = end_row
        start = end_date - timedelta(days=lookback_days)
        rows = self.query(series_id, source=source, start=start, end=end_date).fetchall()
        vals = [v for _, v in rows if v is not None]
        if len(vals) < 5:
            return None
        n = len(vals)
        mean = sum(vals) / n
        var = sum((x - mean) ** 2 for x in vals) / (n - 1) if n > 1 else 0.0
        if var <= 0:
            return None
        sd = var ** 0.5
        return (latest_v - mean) / sd

    def anomaly_screen(self, *, lookback_days: int = 30, z_threshold: float = 2.0
                       ) -> list[dict]:
        """Run zscore_latest across every series in the warehouse. Return
        only series whose latest value exceeds the threshold."""
        out = []
        for source, sid in self.series_list():
            z = self.zscore_latest(sid, source=source, lookback_days=lookback_days)
            if z is None or abs(z) < z_threshold:
                continue
            latest = self.latest(sid, source=source)
            out.append({
                "source": source, "series_id": sid,
                "latest_date": latest[0], "latest_value": latest[1],
                "z": z,
            })
        return sorted(out, key=lambda d: abs(d["z"]), reverse=True)

    def close(self):
        self.conn.close()


# Convenience constructor
def open(path: Path | str | None = None) -> Warehouse:
    return Warehouse(path)
