"""lake_archive.py — durable, git-friendly cold storage for evicted lake rows.

lake/db/worldscope.sqlite is committed to git and capped at ~85 MB, so
lake_maintenance evicts the oldest days of `records` every run. Before this
module existed those rows were simply gone from the queryable lake (the only
trace was the per-day raw.jsonl under lake/sections/). Now every evicted row
is written to a monthly gzip-compressed JSONL partition first:

    lake/archive/records/YYYY-MM.jsonl.gz            one JSON object per line,
    lake/archive/record_entities/YYYY-MM.jsonl.gz    all table columns
    lake/archive/index.json                          rows / min / max / sha256

Layout rules (all enforced here, relied on by worldscope.history):
  * partition = month of records.ingested_at (first-seen time);
  * rows inside a partition are sorted by (ingested_at, id) — for
    record_entities by (ingested_at, record_id, entity_id) — and de-duplicated
    on that id (first write wins, so first-seen ingested_at is preserved);
  * a partition file never exceeds MAX_PART_BYTES (~48 MB, under GitHub's
    50 MB warning); overflow goes to YYYY-MM-part2.jsonl.gz, -part3, ...;
  * gzip output is deterministic (mtime=0, no filename, fixed level), so a
    partition whose content did not change is byte-identical across runs;
  * appends of strictly-newer rows are written as an extra gzip *member*
    (valid concatenated gzip), which leaves the existing bytes untouched so
    git's delta compression keeps the daily commit small. Anything else
    (out-of-order rows, duplicates to drop) triggers a full sorted rewrite.
  * quarantine rows are never archived — only records + record_entities.

Pure stdlib. Reading helpers live here too so the archive is self-describing.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator, Optional

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = REPO / "lake" / "archive"

MAX_PART_BYTES = 48 * 1024 * 1024       # hard cap per partition file (compressed)
GZIP_LEVEL = 6                          # fixed: determinism depends on it
_FLUSH_EVERY = 2000                     # rows between size checks on rewrite
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_PART_RE = re.compile(r"^(\d{4}-\d{2})(?:-part(\d+))?\.jsonl\.gz$")

TABLES = ("records", "record_entities")
KEY_COLUMNS = {
    "records": ("ingested_at", "id"),
    "record_entities": ("ingested_at", "record_id", "entity_id"),
}
ID_COLUMNS = {
    "records": ("id",),
    "record_entities": ("record_id", "entity_id"),
}


# --------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------- #

def month_of(ingested_at: Optional[str]) -> str:
    """Partition key for a row: 'YYYY-MM' of ingested_at, or 'unknown'."""
    s = (ingested_at or "")[:7]
    return s if _MONTH_RE.match(s) else "unknown"


def _dumps(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sort_key(table: str, row: dict) -> tuple:
    return tuple(str(row.get(c) or "") for c in KEY_COLUMNS[table])


def _id_key(table: str, row: dict) -> tuple:
    return tuple(str(row.get(c) or "") for c in ID_COLUMNS[table])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gz_member(lines: Iterable[str]) -> bytes:
    """One deterministic gzip member holding the given JSON lines."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf,
                       compresslevel=GZIP_LEVEL, mtime=0) as gz:
        for line in lines:
            gz.write(line.encode("utf-8"))
            gz.write(b"\n")
    return buf.getvalue()


def _part_path(root: Path, table: str, month: str, n: int) -> Path:
    name = f"{month}.jsonl.gz" if n == 1 else f"{month}-part{n}.jsonl.gz"
    return root / table / name


def partition_files(root: Path, table: str, month: Optional[str] = None) -> list[Path]:
    """All partition files of a table (optionally one month), in order:
    chronological by month, then base file, part2, part3, ..."""
    d = Path(root) / table
    if not d.is_dir():
        return []
    found: list[tuple[str, int, Path]] = []
    for p in d.iterdir():
        m = _PART_RE.match(p.name)
        if not m:
            continue
        if month is not None and m.group(1) != month:
            continue
        found.append((m.group(1), int(m.group(2) or 1), p))
    found.sort()
    return [p for _, _, p in found]


def read_partition(path: Path) -> Iterator[dict]:
    """Yield rows of one partition file (handles multi-member gzip)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_month(root: Path, table: str, month: str) -> Iterator[dict]:
    for p in partition_files(root, table, month):
        yield from read_partition(p)


# --------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------- #

def _index_path(root: Path) -> Path:
    return Path(root) / "index.json"


def load_index(root: Path) -> dict:
    p = _index_path(root)
    if not p.exists():
        return {"version": 1, "partitions": {t: [] for t in TABLES}}
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        idx = {}
    idx.setdefault("version", 1)
    parts = idx.setdefault("partitions", {})
    for t in TABLES:
        parts.setdefault(t, [])
    return idx


def _stats_of(path: Path, table: str) -> dict:
    """Row count + min/max ingested_at of one partition (full read)."""
    n = 0
    lo = hi = None
    for row in read_partition(path):
        n += 1
        ts = row.get("ingested_at")
        if ts is None:
            continue
        if lo is None or ts < lo:
            lo = ts
        if hi is None or ts > hi:
            hi = ts
    return {"rows": n, "min_ingested_at": lo, "max_ingested_at": hi}


def refresh_index(root: Path, known: Optional[dict[tuple[str, str], dict]] = None) -> dict:
    """Rebuild lake/archive/index.json from the files on disk.

    `known` maps (table, filename) -> fresh stats for files just written, so
    they need not be re-read. Any other file whose sha256 still matches the
    previous index keeps its entry; everything else is re-scanned.
    """
    root = Path(root)
    old = load_index(root)
    old_by_file = {(t, e["file"]): e for t in TABLES for e in old["partitions"].get(t, [])}
    known = known or {}
    out = {"version": 1, "partitions": {}}
    for t in TABLES:
        entries = []
        for p in partition_files(root, t):
            sha = _sha256(p)
            size = p.stat().st_size
            if (t, p.name) in known:
                st = known[(t, p.name)]
            else:
                prev = old_by_file.get((t, p.name))
                if prev and prev.get("sha256") == sha:
                    st = {k: prev.get(k) for k in ("rows", "min_ingested_at", "max_ingested_at")}
                else:
                    st = _stats_of(p, t)
            m = _PART_RE.match(p.name)
            entries.append({
                "file": p.name,
                "month": m.group(1) if m else month_of(st.get("min_ingested_at")),
                "rows": st["rows"],
                "min_ingested_at": st["min_ingested_at"],
                "max_ingested_at": st["max_ingested_at"],
                "bytes": size,
                "sha256": sha,
            })
        out["partitions"][t] = entries
    out["total_bytes"] = sum(e["bytes"] for t in TABLES for e in out["partitions"][t])
    root.mkdir(parents=True, exist_ok=True)
    _index_path(root).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
    return out


def archive_total_bytes(root: Path) -> int:
    root = Path(root)
    return sum(p.stat().st_size for t in TABLES for p in partition_files(root, t))


# --------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------- #

class _PartWriter:
    """Streams sorted lines into YYYY-MM.jsonl.gz, -part2, ... under the cap."""

    def __init__(self, root: Path, table: str, month: str) -> None:
        self.root, self.table, self.month = Path(root), table, month
        self.n = 0
        self.files: list[Path] = []
        self._f = None
        self._gz = None
        self._rows = 0
        self._rows_in_part = 0
        self.stats: dict[str, dict] = {}
        self._lo: dict[str, Optional[str]] = {}
        self._hi: dict[str, Optional[str]] = {}

    def _open(self) -> None:
        self.n += 1
        path = _part_path(self.root, self.table, self.month, self.n)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "wb")
        self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._f,
                                 compresslevel=GZIP_LEVEL, mtime=0)
        self.files.append(path)
        self._rows_in_part = 0
        self._lo[path.name] = self._hi[path.name] = None

    def _close_part(self) -> None:
        if self._gz is None:
            return
        self._gz.close()
        self._f.close()
        name = self.files[-1].name
        self.stats[name] = {"rows": self._rows_in_part,
                            "min_ingested_at": self._lo[name],
                            "max_ingested_at": self._hi[name]}
        self._gz = self._f = None

    def write(self, line: str, ingested_at: Optional[str]) -> None:
        if self._gz is None:
            self._open()
        self._gz.write(line.encode("utf-8"))
        self._gz.write(b"\n")
        self._rows += 1
        self._rows_in_part += 1
        name = self.files[-1].name
        if ingested_at is not None:
            if self._lo[name] is None or ingested_at < self._lo[name]:
                self._lo[name] = ingested_at
            if self._hi[name] is None or ingested_at > self._hi[name]:
                self._hi[name] = ingested_at
        if self._rows_in_part % _FLUSH_EVERY == 0:
            self._gz.flush()
            if self._f.tell() >= MAX_PART_BYTES:
                self._close_part()

    def close(self) -> None:
        if self._gz is None and not self.files:
            self._open()            # an empty month still gets an (empty) file
        self._close_part()


def _write_month(root: Path, table: str, month: str, rows: Iterable[dict]) -> dict:
    """Full deterministic rewrite of a month: sort, de-dup on id, split.
    Returns {filename: stats} for the files written; stale parts are removed."""
    root = Path(root)
    best: dict[tuple, dict] = {}
    for r in rows:
        k = _id_key(table, r)
        cur = best.get(k)
        if cur is None or _sort_key(table, r) < _sort_key(table, cur):
            best[k] = r               # earliest ingested_at wins (first-seen)
    ordered = sorted(best.values(), key=lambda r: _sort_key(table, r))
    old_files = partition_files(root, table, month)
    w = _PartWriter(root, table, month)
    for r in ordered:
        w.write(_dumps(r), r.get("ingested_at"))
    w.close()
    for p in old_files:
        if p not in w.files:
            p.unlink()
    return w.stats


def _append_month(root: Path, table: str, month: str, new_rows: list[dict],
                  existing_files: list[Path], last_key: tuple, last_stats: dict) -> dict:
    """Append strictly-newer rows as one extra gzip member. Returns stats."""
    new_rows.sort(key=lambda r: _sort_key(table, r))
    lines = [_dumps(r) for r in new_rows]
    blob = _gz_member(lines)
    last = existing_files[-1]
    lo = min(str(r.get("ingested_at") or "") for r in new_rows)
    hi = max(str(r.get("ingested_at") or "") for r in new_rows)
    if last.stat().st_size + len(blob) <= MAX_PART_BYTES:
        with open(last, "ab") as f:
            f.write(blob)
        st = dict(last_stats)
        prev_lo, prev_hi = st.get("min_ingested_at"), st.get("max_ingested_at")
        st["rows"] = (st.get("rows") or 0) + len(new_rows)
        st["min_ingested_at"] = lo if not prev_lo else min(prev_lo, lo)
        st["max_ingested_at"] = hi if not prev_hi else max(prev_hi, hi)
        return {last.name: st}
    if len(blob) > MAX_PART_BYTES:
        return {}                     # caller falls back to a full rewrite
    m = _PART_RE.match(last.name)
    n = int(m.group(2) or 1) + 1
    path = _part_path(root, table, month, n)
    path.write_bytes(blob)
    return {path.name: {"rows": len(new_rows), "min_ingested_at": lo, "max_ingested_at": hi}}


def write_rows(root: Path, table: str, rows: Iterable[dict], *,
               rewrite: bool = False) -> dict:
    """Merge `rows` into the archive for `table` (grouped by ingested_at
    month). Duplicates (same id) already present are dropped — the archived
    first-seen row wins. Returns {"written": n, "months": [...], "files": {...}}.

    With rewrite=True every touched month is rebuilt from scratch from
    `rows` only (used by the backfill, which supplies whole months).
    """
    root = Path(root)
    if table not in TABLES:
        raise ValueError(f"unknown archive table {table!r}")
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        if not all(r.get(c) for c in ID_COLUMNS[table]):
            continue
        by_month.setdefault(month_of(r.get("ingested_at")), []).append(r)

    written = 0
    file_stats: dict[tuple[str, str], dict] = {}
    for month in sorted(by_month):
        batch = by_month[month]
        existing = partition_files(root, table, month)
        if rewrite or not existing:
            n_before = len({_id_key(table, r) for r in batch})
            stats = _write_month(root, table, month, batch)
            written += n_before
            for name, st in stats.items():
                file_stats[(table, name)] = st
            continue

        # Incremental path: read what is there (ids + last sort key) once.
        seen: set[tuple] = set()
        last_key: tuple = ()
        last_stats = {"rows": 0, "min_ingested_at": None, "max_ingested_at": None}
        idx = load_index(root)
        idx_by_file = {e["file"]: e for e in idx["partitions"].get(table, [])}
        for p in existing:
            for r in read_partition(p):
                seen.add(_id_key(table, r))
                k = _sort_key(table, r)
                if k > last_key:
                    last_key = k
        e = idx_by_file.get(existing[-1].name)
        if e and e.get("sha256") == _sha256(existing[-1]):
            last_stats = {k: e.get(k) for k in ("rows", "min_ingested_at", "max_ingested_at")}
        else:
            last_stats = _stats_of(existing[-1], table)

        fresh: dict[tuple, dict] = {}
        for r in batch:
            k = _id_key(table, r)
            if k in seen or k in fresh:
                continue
            fresh[k] = r
        if not fresh:
            continue
        new_rows = list(fresh.values())
        appendable = all(_sort_key(table, r) > last_key for r in new_rows)
        stats = _append_month(root, table, month, new_rows, existing,
                              last_key, last_stats) if appendable else {}
        if not stats:
            # out-of-order rows (or an oversized batch): rebuild the month
            all_rows = list(read_month(root, table, month)) + new_rows
            stats = _write_month(root, table, month, all_rows)
        written += len(new_rows)
        for name, st in stats.items():
            file_stats[(table, name)] = st

    if file_stats:
        refresh_index(root, known=file_stats)
    return {"written": written, "months": sorted(by_month),
            "files": {name: st for (_, name), st in file_stats.items()}}


# --------------------------------------------------------------------- #
# Archive straight from a lake connection (used before eviction)
# --------------------------------------------------------------------- #

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone()
    return row is not None


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def archive_records_where(con: sqlite3.Connection, where_sql: str, params=(),
                          root: Path = DEFAULT_ARCHIVE) -> dict:
    """Archive every `records` row matching `where_sql` (plus its
    record_entities links, with the entity's canonical name when the
    entities table is available). Call this BEFORE deleting those rows.

    Works against any table that has at least (id, ingested_at); every column
    present is carried into the archive. Never touches quarantine.
    """
    root = Path(root)
    if not _table_exists(con, "records"):
        return {"records": 0, "record_entities": 0}
    cols = _columns(con, "records")
    cur = con.execute(f"SELECT * FROM records WHERE {where_sql}", params)
    rows = [dict(zip(cols, r)) for r in cur]
    if not rows:
        return {"records": 0, "record_entities": 0}
    res = write_rows(root, "records", rows)

    n_links = 0
    if _table_exists(con, "record_entities"):
        has_entities = _table_exists(con, "entities")
        name_sql = ("(SELECT canonical_name FROM entities e WHERE e.id = re.entity_id)"
                    if has_entities else "NULL")
        link_rows: list[dict] = []
        ids = [str(r["id"]) for r in rows]
        when = {str(r["id"]): r.get("ingested_at") for r in rows}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = (f"SELECT re.record_id, re.entity_id, {name_sql} FROM record_entities re "
                 f"WHERE re.record_id IN ({','.join('?' * len(chunk))})")
            for rid, eid, name in con.execute(q, chunk):
                link_rows.append({"record_id": rid, "entity_id": eid,
                                  "entity_name": name,
                                  "ingested_at": when.get(str(rid))})
        if link_rows:
            n_links = write_rows(root, "record_entities", link_rows)["written"]
    return {"records": res["written"], "record_entities": n_links,
            "months": res["months"]}
