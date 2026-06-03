"""run_section.py — run ONE section (pull -> snapshot store + lake) and,
optionally, emit its maps. Used by the ukraine-hourly workflow:

    python -m worldscope.run_section --section ukraine_theater --emit-maps

This existed only implicitly: the workflow invoked `worldscope.run_section`
but the module was missing, so the hourly job died at import and the theater
maps went stale. It mirrors the per-section path in brief.py (resolve -> to_lake)
plus the Ukraine map render + the figures->briefings mirror, defensively so a
failure in maps never loses the data refresh.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import date
from pathlib import Path

from .store import SnapshotStore

REPO = Path(__file__).resolve().parent.parent


def _section_class(section_id: str):
    # Imported lazily: brief.py pulls in every section + heavier deps.
    from .brief import SECTION_REGISTRY
    for cls in SECTION_REGISTRY:
        if getattr(cls, "id", "") == section_id:
            return cls
    return None


def _mirror_maps(stem: str) -> int:
    """Copy figures/daily/<date>/maps/*.png -> briefings/<date>-<name>.png, the
    same naming the renderer/discovery expects."""
    briefings = REPO / "briefings"
    briefings.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in (REPO / "figures" / "daily" / stem / "maps").glob("*.png"):
        try:
            shutil.copy(src, briefings / f"{stem}-{src.name}")
            n += 1
        except Exception as exc:
            print(f"[mirror] {src.name} failed: {type(exc).__name__}: {exc}")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a single section; optionally emit maps.")
    ap.add_argument("--section", required=True, help="section id (e.g. ukraine_theater)")
    ap.add_argument("--emit-maps", action="store_true",
                    help="render the section's maps (ukraine_theater only)")
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.date)

    cls = _section_class(args.section)
    if cls is None:
        print(f"[run_section] unknown section: {args.section!r}")
        return 2

    sec = cls(store=SnapshotStore())
    state = sec.resolve(today=today)
    print(f"[{sec.id}] state={state.state} · {len(state.items)} items"
          + (f" · error: {state.error}" if state.error else ""))
    try:
        sec.to_lake(state)
    except Exception as exc:
        print(f"[{sec.id}] to_lake failed: {type(exc).__name__}: {exc}")

    if args.emit_maps and args.section == "ukraine_theater":
        try:
            from .cartography_ukraine import UkraineMaps
            for name, path in UkraineMaps().render_all(today.isoformat()).items():
                print(f"[ukraine-map] {name}: {path}")
            print(f"[run_section] mirrored {_mirror_maps(today.isoformat())} maps into briefings/")
        except Exception as exc:
            print(f"[run_section] map emit failed (data still refreshed): "
                  f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
