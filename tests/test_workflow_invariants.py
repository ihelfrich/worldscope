"""Invariants over .github/workflows that cost real money or real data when broken.

Each assertion here corresponds to a defect that ran undetected in production:

  * daily-brief triggered on push, so the desk-officer routine's commit fired a
    second full pipeline run every day — two crawls of ~41 sources, and a
    second write that overwrote the first run's forecast record.
  * ukraine-hourly exported NASA_FIRMS_KEY while the code reads FIRMS_MAP_KEY,
    so the thermal-anomaly layer was blind 24 times a day.
  * ukraine-hourly cloned full history (1.36 GB) 24 times a day.
  * daily-brief installed the bare package, so five stages ImportError'd into
    a swallowed log line.

These are cheap text assertions on purpose: they run without network, secrets,
or a YAML dependency, so they cannot themselves become the flaky thing nobody
trusts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _read(name: str) -> str:
    p = WF / name
    assert p.exists(), f"missing workflow {name}"
    return p.read_text()


def _strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


# --------------------------------------------------------------------------- #

def test_daily_brief_has_no_push_trigger():
    """A push trigger makes the routine's own commit re-run the pipeline."""
    body = _strip_comments(_read("daily-brief.yml"))
    trigger_block = body.split("permissions:")[0]
    assert not re.search(r"^\s*push:", trigger_block, re.M), (
        "daily-brief.yml triggers on push. The desk-officer routine commits "
        "with a PAT, which fires workflows, so the whole pipeline runs twice "
        "a day and the second run overwrites the first run's predictions."
    )


def test_daily_brief_still_has_a_schedule():
    """Removing the push trigger must not leave the brief with no trigger."""
    body = _strip_comments(_read("daily-brief.yml"))
    assert re.search(r"^\s*schedule:", body, re.M)
    assert "cron:" in body


def test_daily_brief_installs_the_stage_dependencies():
    """`pip install -e .` leaves matplotlib/fiona/duckdb/mediacloud absent."""
    body = _read("daily-brief.yml")
    assert re.search(r'pip install -e "\.\[[a-z,]*ci[a-z,]*\]"', body), (
        "daily-brief.yml must install the 'ci' extra; the bare package omits "
        "every dependency the post-section stages need."
    )
    assert not re.search(r"^\s*- run: pip install -e \.\s*$", body, re.M)


def test_daily_brief_runs_preflight_before_generating():
    body = _read("daily-brief.yml")
    assert "worldscope.preflight" in body, "daily-brief.yml must run preflight"
    assert body.index("worldscope.preflight") < body.index("worldscope.brief"), (
        "preflight must run before the brief, not after 41 upstream pulls"
    )


def test_daily_brief_publishes_dated_readiness_before_persisting_dist():
    """Without this step, the composer has no successful dated dependency gate."""
    body = _read("daily-brief.yml")
    publish = "worldscope.readiness publish-daily"
    persist = "worldscope.blobsync persist --paths lake,data,dist"
    assert publish in body
    assert body.index("worldscope.brief --out dist") < body.index(publish)
    assert body.index(publish) < body.index(persist)


def test_pushover_workflows_use_the_validated_delivery_boundary():
    """Raw curl accepted `status:1` plus `no active devices` as delivery."""
    for name in ("pushover-brief.yml", "watchdog-deadman.yml", "watchdog-alert.yml"):
        body = _strip_comments(_read(name))
        assert "python -m worldscope.pushover_delivery" in body, name
        assert "api.pushover.net/1/messages.json" not in body, name


def test_brief_marker_is_written_only_by_validated_delivery_helper():
    """A separate marker step can run after a skipped or soft-failed send."""
    body = _strip_comments(_read("pushover-brief.yml"))
    assert "--brief \"${{ steps.pick.outputs.file }}\"" in body
    assert "--sent-file .pushover-sent.json" in body
    assert "sent.append" not in body
    assert "json.dump(sent" not in body


@pytest.mark.parametrize("name", ["daily-brief.yml", "ukraine-hourly.yml"])
def test_firms_key_uses_the_name_the_code_reads(name: str):
    """worldscope/sections/firms.py reads FIRMS_MAP_KEY and nothing else."""
    body = _strip_comments(_read(name))
    assert "NASA_FIRMS_KEY" not in body, (
        f"{name} exports NASA_FIRMS_KEY, which no code reads. "
        f"firms.py reads FIRMS_MAP_KEY."
    )


def test_ukraine_hourly_does_not_clone_full_history():
    body = _strip_comments(_read("ukraine-hourly.yml"))
    assert "fetch-depth: 0" not in body, (
        "ukraine-hourly runs 24x/day; fetch-depth: 0 clones the entire "
        "repository history each time."
    )


def test_workflows_share_one_dependency_path():
    """Two install mechanisms drift. Everything installs the package."""
    for name in ("daily-brief.yml", "ukraine-hourly.yml"):
        body = _strip_comments(_read(name))
        assert "pip install -r requirements.txt" not in body, (
            f"{name} installs from requirements.txt while the other workflows "
            f"install the package; the two sets drifted apart."
        )


def test_no_workflow_force_adds_generated_data():
    """`git add -f` defeats .gitignore, which is why the lake kept landing in
    git after it was supposedly moved out."""
    for path in WF.glob("*.yml"):
        body = _strip_comments(path.read_text())
        assert "git add -f" not in body, (
            f"{path.name} uses `git add -f`, overriding .gitignore. The lake "
            f"and dist/ are excluded for a reason."
        )


def test_every_secret_referenced_is_read_by_something():
    """A workflow that exports a name nothing reads is a silent no-op.

    Guards against the NASA_FIRMS_KEY class of bug in the other direction.
    """
    repo = WF.parent.parent
    code = "\n".join(
        p.read_text()
        for p in (repo / "worldscope").rglob("*.py")
    )
    code += "\n".join(p.read_text() for p in (repo / "tools").rglob("*.py"))

    # Names consumed by shell steps rather than Python, or by design.
    shell_consumed = {
        "PUSHOVER_USER_KEY", "PUSHOVER_APP_TOKEN", "USER_KEY", "APP_TOKEN",
        "GITHUB_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "ALERT_MESSAGE",
        # Read by the `gh` CLI that worldscope.blobsync shells out to,
        # not by Python itself.
        "GH_TOKEN",
    }

    unread: set[str] = set()
    for path in WF.glob("*.yml"):
        for m in re.finditer(r"^\s+([A-Z_]{4,}): \$\{\{ secrets\.", path.read_text(), re.M):
            var = m.group(1)
            if var in shell_consumed:
                continue
            if f'"{var}"' not in code and f"'{var}'" not in code:
                unread.add(f"{path.name}:{var}")

    assert not unread, (
        f"workflows export environment variables no code reads: {sorted(unread)}. "
        f"Either the name is wrong or the feature was never wired up."
    )
