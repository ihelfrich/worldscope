"""Operational workflow boundaries that prevent duplicate or false success."""
from __future__ import annotations

import re
from pathlib import Path


WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _read(name: str) -> str:
    return (WF / name).read_text()


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _literal_run_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() != "run: |":
            continue
        indent = len(line) - len(line.lstrip())
        block: list[str] = []
        for candidate in lines[index + 1:]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                break
            block.append(candidate)
        blocks.append("\n".join(block))
    return blocks


def test_daily_brief_has_no_push_trigger_but_keeps_schedule():
    body = _strip_comments(_read("daily-brief.yml"))
    trigger_block = body.split("permissions:")[0]
    assert not re.search(r"^\s*push:", trigger_block, re.M)
    assert re.search(r"^\s*schedule:", trigger_block, re.M)


def test_daily_installs_required_stage_dependencies():
    body = _read("daily-brief.yml")
    assert 'pip install -e ".[ci]"' in body
    assert "ci = [" in (WF.parents[1] / "pyproject.toml").read_text()


def test_daily_publishes_readiness_before_committing_dist():
    body = _read("daily-brief.yml")
    publish = "worldscope.readiness publish-daily"
    assert publish in body
    assert body.index("worldscope.brief --out dist") < body.index(publish)
    assert body.index(publish) < body.index("Commit archive + snapshot store + lake")


def test_compose_brief_gates_on_readiness_before_claude_and_never_pushes_from_claude():
    body = _read("compose-brief.yml")
    gate = body.index("worldscope.readiness check-daily")
    claude = body.index("anthropics/claude-code-action")
    commit = body.index("Validate and commit the brief")
    assert gate < claude < commit
    assert "sha256sum -c" in body
    assert 'workflows: ["Daily briefing"]' in body
    assert "vars.COMPOSE_IN_ACTIONS != 'false'" in body
    tools = body.split("--allowedTools", 1)[1].split("\n", 1)[0]
    assert "git" not in tools
    assert "Bash(git:*)" in body.split("--disallowedTools", 1)[1].split("\n", 1)[0]


def test_pushover_workflows_use_validated_delivery_boundary():
    for name in ("pushover-brief.yml", "watchdog-deadman.yml", "watchdog-alert.yml",
                 "brief-deadman.yml"):
        body = _strip_comments(_read(name))
        assert "python -m worldscope.pushover_delivery" in body, name
        assert "api.pushover.net/1/messages.json" not in body, name


def test_brief_marker_is_owned_by_delivery_helper():
    body = _strip_comments(_read("pushover-brief.yml"))
    assert "--sent-file .pushover-sent.json" in body
    assert "sent.append" not in body
    assert "json.dump(sent" not in body


def test_pushover_send_shell_does_not_interpolate_brief_outputs():
    body = _strip_comments(_read("pushover-brief.yml"))
    send = body.split("- name: Send Pushover", 1)[1].split(
        "- name: Commit validated delivery receipt", 1
    )[0]
    run = send.split("run: |", 1)[1]
    assert "${{ steps.pick.outputs" not in run
    assert "BRIEF_FILE: ${{ steps.pick.outputs.file }}" in send
    assert "BRIEF_KIND: ${{ steps.pick.outputs.kind }}" in send
    assert "BRIEF_HEADLINE: ${{ steps.pick.outputs.headline }}" in send
    assert "BRIEF_URL_PATH: ${{ steps.pick.outputs.url_path }}" in send


def test_pushover_workflow_never_embeds_expressions_in_shell_source():
    body = _strip_comments(_read("pushover-brief.yml"))
    shell_source = "\n".join(_literal_run_blocks(body))
    assert "${{" not in shell_source
    assert 'python3 -c "' not in shell_source
    assert "MANUAL_BRIEF: ${{ github.event.inputs.brief }}" in body


def test_pushover_pick_step_does_not_sigpipe_or_use_mtime():
    body = _strip_comments(_read("pushover-brief.yml"))
    pick = body.split("- id: pick", 1)[1].split("- name: Send Pushover", 1)[0]
    # `... | head -c N > file` closes the pipe early and SIGPIPEs sed under
    # `set -o pipefail` whenever the brief is longer than N bytes.
    assert not re.search(r"\|\s*head\s+-c", pick)
    # mtime is checkout time in CI; the date in the filename must decide.
    assert "ls -t" not in pick
    assert "python -m worldscope.pushover_delivery pick" in pick
    assert "--body-file /tmp/body.txt" in pick
    assert '--github-output "$GITHUB_OUTPUT"' in pick


def test_pushover_receipt_push_retries_with_rebase():
    body = _strip_comments(_read("pushover-brief.yml"))
    commit = body.split("- name: Commit validated delivery receipt", 1)[1]
    assert "git pull --rebase --autostash origin main" in commit
    assert re.search(r"for attempt in 1 2 3 4 5", commit)
    # a bare, unguarded push must not remain
    assert not re.search(r"^\s*git push\s*$", commit, re.M)


def test_ukraine_hourly_installs_ci_extras_and_passes_firms_key_code_reads():
    body = _read("ukraine-hourly.yml")
    assert 'pip install -e ".[ci]"' in body
    assert "pip install -r requirements.txt" not in body
    firms = (WF.parents[1] / "worldscope" / "sections" / "firms.py").read_text()
    assert 'os.environ.get("FIRMS_MAP_KEY")' in firms
    assert "FIRMS_MAP_KEY: ${{ secrets.FIRMS_MAP_KEY }}" in body
    assert "NASA_FIRMS_KEY" not in _strip_comments(body)


def test_ukraine_hourly_commits_lake_even_when_map_step_fails():
    body = _strip_comments(_read("ukraine-hourly.yml"))
    commit = body.split("- name: Commit & push refreshed artifacts", 1)[1]
    assert re.search(r"^\s*if:\s*\$\{\{\s*!cancelled\(\)\s*\}\}", commit, re.M)
    run = body.split("- name: Run Ukraine theater section only", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error" not in run
    assert "--emit-maps" in run

