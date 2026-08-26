"""Auditable model inference without vendor API keys.

Scheduled runs use GitHub's supported Copilot CLI with the workflow's
short-lived GITHUB_TOKEN. Local runs can use an existing `copilot login`.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass

DEFAULT_MODEL = "auto"

class ModelGatewayError(RuntimeError): pass
class ModelUnavailable(ModelGatewayError): pass

@dataclass(frozen=True)
class ModelResult:
    text: str
    provider: str
    model: str
    prompt_hash: str = ""
    response_hash: str = ""
    inferred_at: str = ""

def available() -> bool:
    opted_in = bool(os.environ.get("GITHUB_TOKEN") or
                    os.environ.get("WORLDSCOPE_ENABLE_MODEL") == "1")
    return opted_in and shutil.which("copilot") is not None

def generate(system_prompt: str, user_prompt: str, *, json_mode: bool = False,
             max_tokens: int = 4000, temperature: float = 0.1) -> ModelResult:
    del max_tokens, temperature
    executable = shutil.which("copilot")
    if not executable:
        raise ModelUnavailable("GitHub Copilot CLI unavailable; install @github/copilot or run in the configured GitHub Actions workflow")
    suffix = "\nReturn valid JSON only, with no Markdown fence or commentary." if json_mode else ""
    prompt = f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{user_prompt}{suffix}"
    configured_model = os.environ.get("WORLDSCOPE_MODEL", DEFAULT_MODEL)
    command = [executable, "-p", prompt, "-s", "--no-ask-user",
               "--available-tools=", "--model", configured_model]
    allowed_env = {
        key: value for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SHELL",
                   "GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN",
                   "COPILOT_HOME", "XDG_CONFIG_HOME"}
    }
    try:
        with tempfile.TemporaryDirectory(prefix="worldscope-model-") as isolated_cwd:
            completed = subprocess.run(command, text=True, capture_output=True,
                                       timeout=120, check=False, env=allowed_env,
                                       cwd=isolated_cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelGatewayError(f"Copilot CLI transport failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise ModelGatewayError(f"Copilot CLI exited {completed.returncode}")
    text = completed.stdout.strip()
    if not text:
        raise ModelGatewayError("Copilot CLI response missing assistant text")
    return ModelResult(
        text, "github-copilot-cli", configured_model,
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        response_hash=hashlib.sha256(text.encode()).hexdigest(),
        inferred_at=datetime.now(timezone.utc).isoformat(),
    )

def translate(texts: list[str], source_language: str, *, guidance: str = "") -> list[str]:
    if not texts or not available(): return list(texts)
    numbered = "\n".join(f"{i}. {value}" for i, value in enumerate(texts, 1))
    system = "Translate news into concise English. Preserve names, organizations, numbers, uncertainty, and source framing. " + guidance
    prompt = f'Translate these {source_language} items. Return {{"translations": [one string per input, in order]}}.\n\n{numbered}'
    try:
        payload = json.loads(generate(system, prompt, json_mode=True, max_tokens=2500).text)
        values = payload.get("translations") if isinstance(payload, dict) else None
        if isinstance(values, list) and len(values) == len(texts):
            return [str(value) for value in values]
    except (ModelGatewayError, json.JSONDecodeError):
        pass
    return list(texts)
