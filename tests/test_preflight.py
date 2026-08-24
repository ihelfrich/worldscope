"""Preflight: verify every declared capability before a run starts.

The pipeline's defining failure mode was invisible degradation — a credential
that was never set, a package that was never installed, and 180 consecutive
green workflow runs reporting success while producing nothing. Preflight makes
that state observable in one command and non-zero in CI.
"""
from __future__ import annotations

import json

import pytest

from worldscope import preflight


class _Req:
    id = "req"
    requires_env = ("WS_PF_REQUIRED",)
    optional_env = ()
    requires_packages = ()


class _Opt:
    id = "opt"
    requires_env = ()
    optional_env = ("WS_PF_OPTIONAL",)
    requires_packages = ()


class _Pkg:
    id = "pkg"
    requires_env = ()
    optional_env = ()
    requires_packages = ("a_package_that_does_not_exist_ws",)


class _Clean:
    id = "clean"
    requires_env = ()
    optional_env = ()
    requires_packages = ()


def test_missing_required_env_is_a_blocker(monkeypatch):
    monkeypatch.delenv("WS_PF_REQUIRED", raising=False)
    rep = preflight.check(sections=[_Req], stages=[])
    assert rep.ok is False
    row = rep.by_id["req"]
    assert row.missing_env == ["WS_PF_REQUIRED"]
    assert rep.blockers


def test_satisfied_required_env_passes(monkeypatch):
    monkeypatch.setenv("WS_PF_REQUIRED", "x")
    rep = preflight.check(sections=[_Req], stages=[])
    assert rep.ok is True
    assert not rep.blockers


def test_missing_optional_env_is_a_warning_not_a_blocker(monkeypatch):
    monkeypatch.delenv("WS_PF_OPTIONAL", raising=False)
    rep = preflight.check(sections=[_Opt], stages=[])
    assert rep.ok is True
    assert rep.by_id["opt"].degraded_env == ["WS_PF_OPTIONAL"]
    assert rep.warnings


def test_missing_required_package_is_a_blocker():
    rep = preflight.check(sections=[_Pkg], stages=[])
    assert rep.ok is False
    assert rep.by_id["pkg"].missing_packages == ["a_package_that_does_not_exist_ws"]


def test_skipped_section_is_not_a_blocker(monkeypatch):
    """WORLDSCOPE_SKIP sections carry forward and never pull, so their
    credentials are irrelevant to this run."""
    monkeypatch.delenv("WS_PF_REQUIRED", raising=False)
    monkeypatch.setenv("WORLDSCOPE_SKIP", "req")
    rep = preflight.check(sections=[_Req], stages=[])
    assert rep.ok is True
    assert rep.by_id["req"].skipped is True


def test_stage_dependency_is_checked():
    stage = preflight.StageRequirement(
        name="graphics", packages=("a_package_that_does_not_exist_ws",), required=True
    )
    rep = preflight.check(sections=[], stages=[stage])
    assert rep.ok is False
    assert "graphics" in rep.by_id


def test_optional_stage_missing_package_is_a_warning():
    stage = preflight.StageRequirement(
        name="embeddings", packages=("a_package_that_does_not_exist_ws",), required=False
    )
    rep = preflight.check(sections=[], stages=[stage])
    assert rep.ok is True
    assert rep.warnings


def test_report_is_json_serialisable(monkeypatch):
    monkeypatch.delenv("WS_PF_REQUIRED", raising=False)
    rep = preflight.check(sections=[_Req, _Clean], stages=[])
    blob = json.loads(json.dumps(rep.to_dict()))
    assert blob["ok"] is False
    assert {r["id"] for r in blob["rows"]} == {"req", "clean"}


def test_human_table_names_the_remedy(monkeypatch):
    """A preflight failure has to tell the operator what to actually run."""
    monkeypatch.delenv("WS_PF_REQUIRED", raising=False)
    rep = preflight.check(sections=[_Req], stages=[])
    text = rep.render()
    assert "WS_PF_REQUIRED" in text
    assert "gh secret set" in text


def test_real_registry_is_introspectable():
    """The live registry must expose capabilities without importing heavy deps."""
    rep = preflight.check()
    assert len(rep.rows) > 30
    ids = {r.id for r in rep.rows}
    for expected in ("firms", "macro", "paper_bet_placement", "graphics"):
        assert expected in ids


def test_every_declared_credential_appears_in_the_report():
    """Whatever a section declares must be visible to the operator."""
    rep = preflight.check()
    firms = rep.by_id["firms"]
    assert "FIRMS_MAP_KEY" in (firms.missing_env + firms.present_env)


@pytest.mark.parametrize("exit_expected,env", [(1, {}), (0, {"WS_PF_REQUIRED": "x"})])
def test_main_exit_code(monkeypatch, exit_expected, env, capsys):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if not env:
        monkeypatch.delenv("WS_PF_REQUIRED", raising=False)
    code = preflight.main(argv=[], _sections=[_Req], _stages=[])
    assert code == exit_expected


def test_warn_only_never_fails(monkeypatch):
    monkeypatch.delenv("WS_PF_REQUIRED", raising=False)
    code = preflight.main(argv=["--warn-only"], _sections=[_Req], _stages=[])
    assert code == 0
