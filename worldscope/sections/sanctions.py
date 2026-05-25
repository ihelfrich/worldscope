"""
sanctions.py — OpenSanctions section, sourced from the local bulk FtM corpus.

Data: ~/Projects/econscope/data/opensanctions/entities.ftm.json
      (2.6 GB JSON Lines, ~4.28M entities, FollowTheMoney schema)

Reads the file with a streaming line scan, keeps entities in the SANCTIONS
datasets only (drops PEP-only, Wikidata-only, occupancy records), filters
to those modified in the last N days, sorts by modifiedAt desc, returns
the top hits.

When run in CI (or anywhere the local file is missing) the section
ships empty — graceful degrade. The site doesn't break.

Refresh the corpus: download the latest entities.ftm.json from
https://data.opensanctions.org/datasets/latest/default/ on a weekly cadence.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import Section

# Override via env if you put the corpus somewhere else.
DEFAULT_DATA_PATH = Path(
    os.environ.get(
        "OPENSANCTIONS_DATA",
        str(Path.home() / "Projects" / "econscope" / "data" / "opensanctions" / "entities.ftm.json"),
    )
)

# Datasets we care about for a sanctions briefing — drop PEP and pure-Wikidata.
# Widened from the initial pass after inspecting the corpus (top 15 sanctions
# datasets by entity count).
INTERESTING_DATASETS = {
    # United States
    "us_ofac_sdn", "us_ofac_cons", "us_trade_csl", "us_sam_exclusions",
    # UK
    "gb_hmt_sanctions", "gb_fcdo_sanctions",
    # EU + member states
    "eu_fsf", "eu_journal_sanctions",
    "fr_tresor_gels_avoir", "be_fod_sanctions", "mc_fund_freezes",
    # Other major regimes
    "un_sc_sanctions", "ch_seco_sanctions",
    "ca_dfatd_sema_sanctions", "au_dfat_sanctions", "jp_mof_sanctions",
    # Ukraine (active war-related lists)
    "ua_nsdc_sanctions", "ua_war_sanctions",
    # Multilateral / debarment
    "worldbank_debarred", "icij_offshoreleaks",
}

# Schemas worth surfacing — drop Occupancy, Position, Documentation, etc.
INTERESTING_SCHEMAS = {
    "Person", "Company", "Organization", "LegalEntity",
    "Vessel", "Airplane", "PublicBody",
}


class SanctionsSection(Section):
    id = "sanctions"
    title = "Sanctions & Designations (recent)"
    emoji = "⚖️"

    # Window matches the corpus refresh cadence. If the local file is from
    # May 18 and today is May 25, a 14-day window leaves zero hits because
    # the file's modifiedAt values don't extend into the post-download
    # period. We default wide (90d) so the section is useful between bulk
    # refreshes; tighten this once you set up an auto-refresh job.
    WINDOW_DAYS = 90
    LIMIT = 30

    def __init__(self, *args, data_path: Optional[Path] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.data_path = Path(data_path) if data_path else DEFAULT_DATA_PATH

    def pull(self) -> list[dict]:
        if not self.data_path.exists():
            return []  # graceful no-op when corpus isn't mounted (e.g. in CI)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.WINDOW_DAYS)).date()
        cutoff_iso = cutoff.isoformat()

        items: list[dict] = []
        with self.data_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                # Cheap pre-filter: if no modifiedAt at all, skip
                if '"modifiedAt"' not in line:
                    continue
                try:
                    ent = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if ent.get("schema") not in INTERESTING_SCHEMAS:
                    continue
                datasets = set(ent.get("datasets") or [])
                if not (datasets & INTERESTING_DATASETS):
                    continue

                props = ent.get("properties") or {}
                modified = props.get("modifiedAt") or []
                if not modified:
                    continue
                # modifiedAt is a list; take the latest (sort lexicographically; ISO dates sort correctly)
                latest = max(modified)
                if latest < cutoff_iso:
                    continue

                ent_id = ent.get("id", "")
                caption = ent.get("caption") or (props.get("name") or ["(unnamed)"])[0]
                countries = props.get("country") or []
                topics = props.get("topics") or []
                sanction_specs = props.get("sanctionSpec") or props.get("program") or []
                ds_names = sorted(datasets & INTERESTING_DATASETS)

                summary_bits = []
                if ds_names:
                    summary_bits.append("source: " + ", ".join(ds_names))
                if countries:
                    summary_bits.append("country: " + ", ".join(countries[:3]))
                if topics:
                    summary_bits.append("topics: " + ", ".join(topics[:3]))
                if sanction_specs:
                    summary_bits.append("program: " + str(sanction_specs[0])[:80])

                items.append({
                    "id": ent_id,
                    "date": latest,
                    "title": f"{caption} ({ent.get('schema','')})",
                    "url": f"https://www.opensanctions.org/entities/{ent_id}/" if ent_id else "",
                    "summary": " · ".join(summary_bits),
                    "schema": ent.get("schema"),
                    "datasets": ds_names,
                    "countries": countries,
                    "topics": topics,
                    "modified_at": latest,
                })

        items.sort(key=lambda it: it["date"], reverse=True)
        return items[: self.LIMIT]
