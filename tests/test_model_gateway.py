import subprocess
import pytest
from worldscope import model_gateway as mg

def test_copilot_cli_is_noninteractive_and_tool_free(monkeypatch):
    seen = {}
    monkeypatch.setattr(mg.shutil, "which", lambda name: "/opt/bin/copilot")
    def fake_run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, '{"decisions":[]}', "")
    monkeypatch.setattr(mg.subprocess, "run", fake_run)
    result = mg.generate("system", "user", json_mode=True)
    assert result.provider == "github-copilot-cli"
    assert seen["command"][:2] == ["/opt/bin/copilot", "-p"]
    assert "-s" in seen["command"] and "--no-ask-user" in seen["command"]
    assert "--allow-all" not in seen["command"]
    assert "--available-tools=" in seen["command"]
    assert seen["command"][-2:] == ["--model", "auto"]
    assert "worldscope-model-" in seen["cwd"]

def test_no_cli_is_explicit(monkeypatch):
    monkeypatch.setattr(mg.shutil, "which", lambda name: None)
    with pytest.raises(mg.ModelUnavailable, match="Copilot CLI unavailable"):
        mg.generate("system", "user")

def test_cli_failure_does_not_leak_stderr(monkeypatch):
    monkeypatch.setattr(mg.shutil, "which", lambda name: "/bin/copilot")
    monkeypatch.setattr(mg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "secret-token"))
    with pytest.raises(mg.ModelGatewayError) as exc: mg.generate("system", "user")
    assert "secret-token" not in str(exc.value)

def test_configured_model_is_forwarded(monkeypatch):
    monkeypatch.setenv("WORLDSCOPE_MODEL", "gpt-5")
    monkeypatch.setattr(mg.shutil, "which", lambda name: "/bin/copilot")
    monkeypatch.setattr(mg.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "ok", ""))
    assert mg.generate("s", "u").model == "gpt-5"
