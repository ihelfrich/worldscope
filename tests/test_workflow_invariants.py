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


def test_pushover_workflows_use_validated_delivery_boundary():
    for name in ("pushover-brief.yml", "watchdog-deadman.yml", "watchdog-alert.yml"):
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
