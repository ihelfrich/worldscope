from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [ROOT / ".github/workflows/daily-brief.yml",
             ROOT / ".github/workflows/render-briefings.yml"]

def test_workflows_grant_copilot_not_retired_models_api():
    for path in WORKFLOWS:
        body = path.read_text()
        assert re.search(r"^  copilot-requests: write$", body, re.MULTILINE)
        assert "models: read" not in body

def test_workflows_install_copilot_cli():
    for path in WORKFLOWS:
        assert "npm install -g @github/copilot@1.0.80" in path.read_text()
        assert "Copilot inference canary" in path.read_text()

def test_inference_steps_receive_ephemeral_token():
    daily = WORKFLOWS[0].read_text().split("- name: Generate briefing", 1)[1]
    render = WORKFLOWS[1].read_text().split("- name: Render every brief", 1)[1]
    assert "GITHUB_TOKEN: ${{ github.token }}" in daily
    assert "GITHUB_TOKEN: ${{ github.token }}" in render

def test_no_anthropic_or_retired_models_wiring():
    for path in WORKFLOWS:
        body = path.read_text()
        assert "ANTHROPIC_API_KEY" not in body
        assert "WORLDSCOPE_GITHUB_TOKEN" not in body
