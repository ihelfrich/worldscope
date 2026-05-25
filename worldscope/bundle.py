"""
bundle.py — package the day's briefing as a downloadable zip.

Zip contents:
    index.html        the rendered briefing page
    overview.md       the analyst's morning brief (cross-section synthesis)
    raw/<section>.json     normalized items per section
    calendar.json     forthcoming events pulled today
    trends.json       trend stats per section
    manifest.json     provenance: pulled_at, sources, item counts
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def make_bundle(
    out_dir: Path,
    when: str,
    index_html: str,
    overview_md: str,
    section_deltas: dict[str, tuple[str, dict]],
    calendar: list[Any],
    trends: dict[str, dict],
    source_attribution: dict[str, dict],
) -> Path:
    out_dir = Path(out_dir)
    zips_dir = out_dir / "zips"
    zips_dir.mkdir(parents=True, exist_ok=True)
    zpath = zips_dir / f"{when}.zip"

    manifest = {
        "date": when,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {
            sid: {
                "title": title,
                "new_count": len(delta.get("new", [])),
                "total_count": len(delta.get("all", [])),
            }
            for sid, (title, delta) in section_deltas.items()
        },
        "calendar_count": len(calendar),
        "sources": source_attribution,
    }

    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", index_html)
        z.writestr("overview.md", overview_md)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("trends.json", json.dumps(trends, indent=2))
        z.writestr(
            "calendar.json",
            json.dumps(
                [{
                    "source": c.source,
                    "title": c.title,
                    "when": c.when.isoformat() if c.when else None,
                    "url": c.url,
                    "summary": c.summary,
                } for c in calendar],
                indent=2,
            ),
        )
        for sid, (_title, delta) in section_deltas.items():
            z.writestr(f"raw/{sid}.json", json.dumps(delta["all"], indent=2, default=str))
        readme = [
            f"WORLDSCOPE daily package — {when}",
            "",
            "Contents:",
            "  index.html       The rendered briefing page",
            "  overview.md      Analyst's morning brief (cross-section synthesis)",
            "  raw/<id>.json    Normalized items per section",
            "  calendar.json    Forthcoming events (next ~14 days, where dated)",
            "  trends.json      14-day section trends + carrying terms",
            "  manifest.json    Provenance + counts",
            "",
            "Provenance: all items link back to their source URL. Synthesis prose is",
            "grounded in the listed items only; numbers in 'trends' come from",
            "snapshots stored in ~/.worldscope/store.sqlite.",
        ]
        z.writestr("README.txt", "\n".join(readme))
    return zpath
