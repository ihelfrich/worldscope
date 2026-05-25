"""
brief.py — orchestrate one daily briefing run.

Usage:
    python -m worldscope.brief                       # runs all registered sections
    python -m worldscope.brief --section federal_register

Output: dist/YYYY-MM-DD.html and dist/index.html
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .render import render_page
from .sections.federal_register import FederalRegisterSection
from .store import SnapshotStore
from .synth import synthesize

SECTION_REGISTRY = [
    FederalRegisterSection,
    # add more here as they land: WorldMacroSection, MarketsSection,
    # SanctionsSection, NewsByRegionSection, VipFlightsSection, ...
]


def run(section_ids: list[str] | None = None, *, out_dir: Path | str = "dist") -> Path:
    store = SnapshotStore()
    today = date.today()
    sections_html: list[str] = []
    for cls in SECTION_REGISTRY:
        if section_ids and cls.id not in section_ids:
            continue
        sec = cls(store=store)
        delta = sec.delta(today=today)
        new_ids = {it["_id"] for it in delta["new"]}
        synth = synthesize(sec.title, delta["all"], new_ids)
        sections_html.append(sec.render_html(delta, synth))
        print(f"[{sec.id}] {len(delta['new'])} new / {len(delta['all'])} total")
    path = render_page(today, sections_html, Path(out_dir))
    print(f"\n→ wrote {path}")
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Generate today's WORLDSCOPE briefing")
    p.add_argument("--section", action="append", help="restrict to specific section id(s)")
    p.add_argument("--out", default="dist", help="output directory (default: dist)")
    args = p.parse_args()
    run(args.section, out_dir=args.out)


if __name__ == "__main__":
    main()
