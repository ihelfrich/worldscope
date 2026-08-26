"""worldscope.preflight — verify declared capabilities before a run starts.

Worldscope's defining failure mode was invisible degradation. Between May and
August 2026 the pipeline reported success on ~180 consecutive workflow runs
while:

  * model inference was not provisioned, so paper_bet_placement placed zero bets
    on 66 consecutive days and every LLM synthesis path ran on a template;
  * FIRMS_MAP_KEY was never set, so the thermal-anomaly layer returned [];
  * MEDIACLOUD_API_KEY and the mediacloud SDK were both absent;
  * matplotlib, fiona, duckdb and sentence-transformers were never installed,
    so five of eleven post-section stages ImportError'd into a swallowed log
    line.

None of that was detectable from the outside, because every one of those
failures was spelled `return []` or `except Exception: pass`.

Preflight makes the whole capability surface observable in one command and
non-zero in CI:

    python -m worldscope.preflight              # table; exit 1 if blocked
    python -m worldscope.preflight --json       # machine-readable
    python -m worldscope.preflight --warn-only  # report, always exit 0

Sections declare their needs via `requires_env` / `optional_env` /
`requires_packages` on the class (see worldscope.sections.Section). Stages
declare theirs in STAGE_REQUIREMENTS below. There is no separate manifest file
to drift out of sync — the declaration lives next to the code that reads it,
and tests/test_capabilities.py enforces that statically.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence


# --------------------------------------------------------------------------- #
# Stage requirements
#
# Post-section stages import their heavy dependencies lazily inside
# worldscope.brief, which is why a missing package surfaced as a swallowed
# ImportError rather than a build failure. Declaring them here lets preflight
# catch the same condition before the run burns 40 upstream pulls.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class StageRequirement:
    name: str
    packages: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    required: bool = True
    note: str = ""


STAGE_REQUIREMENTS: tuple[StageRequirement, ...] = (
    StageRequirement(
        "graphics", packages=("matplotlib",), required=True,
        note="daily charts; without it the brief has no figures at all",
    ),
    StageRequirement(
        "maps", packages=("matplotlib", "fiona"), required=True,
        note="world/regional maps",
    ),
    StageRequirement(
        "ukraine-maps", packages=("matplotlib", "fiona"), required=False,
        note="theater maps; the daily brief degrades without them",
    ),
    StageRequirement(
        "embeddings", packages=("sentence_transformers",), required=False,
        note="multilingual semantic index for cross-language dedup",
    ),
    StageRequirement(
        "warehouse", packages=("duckdb",), required=False,
        note="DuckDB time-series warehouse behind the anomaly screen",
    ),
    StageRequirement(
        "synthesis", packages=("cmd:copilot",), required=False,
        note="Copilot CLI prose + decisions; Actions uses its ephemeral GITHUB_TOKEN",
    ),
)


# --------------------------------------------------------------------------- #
# Report model
# --------------------------------------------------------------------------- #

@dataclass
class Row:
    id: str
    kind: str                                   # "section" | "stage"
    required: bool = True
    skipped: bool = False
    present_env: list[str] = field(default_factory=list)
    missing_env: list[str] = field(default_factory=list)
    degraded_env: list[str] = field(default_factory=list)   # optional + absent
    present_packages: list[str] = field(default_factory=list)
    missing_packages: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def blocked(self) -> bool:
        if self.skipped or not self.required:
            return False
        return bool(self.missing_env or self.missing_packages)

    @property
    def degraded(self) -> bool:
        if self.skipped:
            return False
        if self.degraded_env:
            return True
        return bool(not self.required and (self.missing_env or self.missing_packages))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "required": self.required,
            "skipped": self.skipped, "blocked": self.blocked,
            "degraded": self.degraded,
            "present_env": self.present_env, "missing_env": self.missing_env,
            "degraded_env": self.degraded_env,
            "present_packages": self.present_packages,
            "missing_packages": self.missing_packages,
            "note": self.note,
        }


@dataclass
class Report:
    rows: list[Row]

    @property
    def by_id(self) -> dict[str, Row]:
        return {r.id: r for r in self.rows}

    @property
    def blockers(self) -> list[Row]:
        return [r for r in self.rows if r.blocked]

    @property
    def warnings(self) -> list[Row]:
        return [r for r in self.rows if r.degraded]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked_count": len(self.blockers),
            "degraded_count": len(self.warnings),
            "rows": [r.to_dict() for r in self.rows],
        }

    # -- rendering ---------------------------------------------------------- #

    def render(self) -> str:
        out: list[str] = []
        blocked, degraded = self.blockers, self.warnings

        out.append("worldscope preflight")
        out.append("=" * 68)

        if blocked:
            out.append("")
            out.append(f"BLOCKED ({len(blocked)}) — these cannot produce data at all:")
            for r in blocked:
                bits = []
                if r.missing_env:
                    bits.append("env " + ", ".join(r.missing_env))
                if r.missing_packages:
                    bits.append("package " + ", ".join(r.missing_packages))
                out.append(f"  {r.id:<26} missing {'; '.join(bits)}")
                if r.note:
                    out.append(f"  {'':<26} {r.note}")

        if degraded:
            out.append("")
            out.append(f"DEGRADED ({len(degraded)}) — running, but not at full capability:")
            for r in degraded:
                bits = []
                if r.degraded_env:
                    bits.append("env " + ", ".join(r.degraded_env))
                if not r.required and r.missing_env:
                    bits.append("env " + ", ".join(r.missing_env))
                if not r.required and r.missing_packages:
                    bits.append("package " + ", ".join(r.missing_packages))
                out.append(f"  {r.id:<26} absent {'; '.join(bits)}")
                if r.note:
                    out.append(f"  {'':<26} {r.note}")

        healthy = [r for r in self.rows if not r.blocked and not r.degraded and not r.skipped]
        skipped = [r for r in self.rows if r.skipped]
        out.append("")
        out.append(
            f"{len(healthy)} at full capability · {len(degraded)} degraded · "
            f"{len(blocked)} blocked · {len(skipped)} skipped"
        )

        # The remedy. A preflight failure is useless if the operator has to go
        # find out how to fix it.
        missing_env: list[str] = []
        for r in blocked + degraded:
            missing_env.extend(r.missing_env)
            missing_env.extend(r.degraded_env)
        missing_env = sorted(set(missing_env))
        if missing_env:
            out.append("")
            out.append("To provision the missing credentials:")
            for var in missing_env:
                out.append(f"  gh secret set {var} --repo ihelfrich/worldscope")

        missing_pkgs: list[str] = []
        for r in blocked + degraded:
            missing_pkgs.extend(r.missing_packages)
        if missing_pkgs:
            out.append("")
            out.append("To install the missing packages:")
            out.append('  pip install -e ".[all]"')

        return "\n".join(out)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def _installed(pkg: str) -> bool:
    if pkg.startswith("cmd:"):
        return shutil.which(pkg.removeprefix("cmd:")) is not None
    try:
        return importlib.util.find_spec(pkg) is not None
    except (ImportError, ValueError):
        return False


def _skip_set() -> set[str]:
    raw = os.environ.get("WORLDSCOPE_SKIP", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def _default_sections() -> list[type]:
    from .brief import SECTION_REGISTRY
    return list(SECTION_REGISTRY)


def check(
    sections: Optional[Sequence[type]] = None,
    stages: Optional[Iterable[StageRequirement]] = None,
) -> Report:
    """Inspect declared capabilities against the current environment."""
    sections = _default_sections() if sections is None else list(sections)
    stages = STAGE_REQUIREMENTS if stages is None else list(stages)
    skip = _skip_set()

    rows: list[Row] = []

    for cls in sections:
        sid = getattr(cls, "id", cls.__name__)
        req_env = tuple(getattr(cls, "requires_env", ()) or ())
        opt_env = tuple(getattr(cls, "optional_env", ()) or ())
        req_pkg = tuple(getattr(cls, "requires_packages", ()) or ())
        rows.append(Row(
            id=sid,
            kind="section",
            required=True,
            skipped=sid in skip,
            present_env=[v for v in req_env if os.environ.get(v)],
            missing_env=[v for v in req_env if not os.environ.get(v)],
            degraded_env=[v for v in opt_env if not os.environ.get(v)],
            present_packages=[p for p in req_pkg if _installed(p)],
            missing_packages=[p for p in req_pkg if not _installed(p)],
        ))

    for st in stages:
        rows.append(Row(
            id=st.name,
            kind="stage",
            required=st.required,
            skipped=False,
            present_env=[v for v in st.env if os.environ.get(v)],
            missing_env=[v for v in st.env if not os.environ.get(v)],
            present_packages=[p for p in st.packages if _installed(p)],
            missing_packages=[p for p in st.packages if not _installed(p)],
            note=st.note,
        ))

    return Report(rows=rows)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    _sections: Optional[Sequence[type]] = None,
    _stages: Optional[Iterable[StageRequirement]] = None,
) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m worldscope.preflight",
        description="Verify every declared credential and dependency.",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--warn-only", action="store_true",
                    help="report problems but always exit 0")
    args = ap.parse_args(list(argv) if argv is not None else None)

    rep = check(sections=_sections, stages=_stages)

    if args.json:
        print(json.dumps(rep.to_dict(), indent=2))
    else:
        print(rep.render())

    if args.warn_only:
        return 0
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
