"""Capability declaration + enforcement of the section trust rule.

The trust rule is already stated in worldscope/sections/__init__.py:

    a section must NOT swallow an upstream failure and return []. An empty
    list means "the source confirmed there was nothing today" — a quiet day.
    A failure (missing credential, HTTP error, auth rejection, unparseable
    body) must be *raised*.

It was documented but never enforced, which is how FIRMS_MAP_KEY,
MEDIACLOUD_API_KEY and ANTHROPIC_API_KEY went missing for three months while
every workflow run reported success. These tests enforce it two ways:

  1. Declaratively — a Section names the env vars it requires, and the base
     class refuses to call pull() when one is absent.
  2. Statically — no section module may contain the `if not <env>: return []`
     antipattern, so the rule cannot be re-broken by hand.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from worldscope.sections import (
    MissingCredential,
    STATE_NO_DATA,
    Section,
)

SECTIONS_DIR = Path(__file__).resolve().parent.parent / "worldscope" / "sections"


# --------------------------------------------------------------------------- #
# 1. Declarative requirement, enforced by the base class
# --------------------------------------------------------------------------- #

class _Probe(Section):
    """Records whether pull() was reached."""
    id = "probe"
    title = "Probe"
    requires_env = ("WS_TEST_REQUIRED_KEY",)

    def __init__(self, store=None):
        super().__init__(store=store)
        self.pull_called = False

    def pull(self):
        self.pull_called = True
        return [{"id": "x", "title": "t", "url": "u", "summary": "s"}]


def test_section_declares_capability_attributes():
    """Every Section carries the three capability tuples, defaulting to empty."""
    assert Section.requires_env == ()
    assert Section.optional_env == ()
    assert Section.requires_packages == ()


def test_missing_required_env_blocks_pull(tmp_path, monkeypatch):
    """A declared-but-absent credential must raise before pull() is reached.

    This is the whole point: firms.py returned [] and the pipeline recorded a
    quiet day. It must record a broken sensor instead.
    """
    monkeypatch.delenv("WS_TEST_REQUIRED_KEY", raising=False)
    from worldscope.store import SnapshotStore

    probe = _Probe(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    state = probe.resolve()

    assert probe.pull_called is False, "pull() ran despite a missing credential"
    assert state.state == STATE_NO_DATA
    assert state.error_type == "MissingCredential"
    assert "WS_TEST_REQUIRED_KEY" in (state.error or "")


def test_present_required_env_allows_pull(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_TEST_REQUIRED_KEY", "set")
    from worldscope.store import SnapshotStore

    probe = _Probe(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    state = probe.resolve()

    assert probe.pull_called is True
    assert state.error_type is None


def test_missing_credential_names_every_absent_var(tmp_path, monkeypatch):
    """The error must list all missing vars, not just the first."""
    class _Multi(_Probe):
        id = "multi"
        requires_env = ("WS_TEST_A", "WS_TEST_B", "WS_TEST_C")

    for v in ("WS_TEST_A", "WS_TEST_B", "WS_TEST_C"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("WS_TEST_B", "set")

    from worldscope.store import SnapshotStore
    state = _Multi(store=SnapshotStore(path=tmp_path / "s.sqlite")).resolve()

    assert "WS_TEST_A" in state.error and "WS_TEST_C" in state.error
    assert "WS_TEST_B" not in state.error


def test_optional_env_does_not_block(tmp_path, monkeypatch):
    class _Opt(_Probe):
        id = "opt"
        requires_env = ()
        optional_env = ("WS_TEST_OPTIONAL",)

    monkeypatch.delenv("WS_TEST_OPTIONAL", raising=False)
    from worldscope.store import SnapshotStore
    probe = _Opt(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    state = probe.resolve()

    assert probe.pull_called is True
    assert state.error_type is None


def test_missing_required_package_blocks_pull(tmp_path):
    class _NeedsPkg(_Probe):
        id = "needspkg"
        requires_env = ()
        requires_packages = ("a_package_that_does_not_exist_ws",)

    from worldscope.store import SnapshotStore
    probe = _NeedsPkg(store=SnapshotStore(path=tmp_path / "s.sqlite"))
    state = probe.resolve()

    assert probe.pull_called is False
    assert state.error_type == "MissingDependency"
    assert "a_package_that_does_not_exist_ws" in (state.error or "")


# --------------------------------------------------------------------------- #
# 2. Static enforcement — the antipattern cannot come back
# --------------------------------------------------------------------------- #

def _env_guarded_empty_returns(tree: ast.AST) -> list[int]:
    """Line numbers of `if not <env-derived>: return []` (and `is None` form).

    Tracks names bound from os.environ.get / os.environ[...] / os.getenv within
    each function, then flags a truthiness guard on such a name whose body just
    returns an empty list.
    """
    hits: list[int] = []

    def is_env_call(node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                if f.attr == "getenv":
                    return True
                if f.attr == "get" and isinstance(f.value, ast.Attribute) \
                        and f.value.attr == "environ":
                    return True
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ":
            return True
        return False

    def returns_empty_list(body: list[ast.stmt]) -> bool:
        return (
            len(body) == 1
            and isinstance(body[0], ast.Return)
            and isinstance(body[0].value, ast.List)
            and not body[0].value.elts
        )

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        env_names: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and is_env_call(node.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        env_names.add(t.id)
        for node in ast.walk(fn):
            if not isinstance(node, ast.If) or not returns_empty_list(node.body):
                continue
            # Flatten `a or b or c` so a compound guard such as
            # `if not key or _sdk is None: return []` is caught too — that
            # form is how mediacloud.py evaded an earlier version of this
            # check while still swallowing a missing credential.
            operands = (
                list(node.test.values)
                if isinstance(node.test, ast.BoolOp)
                else [node.test]
            )
            for test in operands:
                if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                    if is_env_call(test.operand):
                        hits.append(node.lineno)
                        break
                    if any(
                        isinstance(n, ast.Name) and n.id in env_names
                        for n in ast.walk(test.operand)
                    ):
                        hits.append(node.lineno)
                        break
                if isinstance(test, ast.Compare) and any(
                    isinstance(o, (ast.Is, ast.Eq)) for o in test.ops
                ):
                    if any(
                        isinstance(n, ast.Name) and n.id in env_names
                        for n in ast.walk(test.left)
                    ):
                        hits.append(node.lineno)
                        break
    return sorted(set(hits))


@pytest.mark.parametrize(
    "path", sorted(SECTIONS_DIR.glob("*.py")), ids=lambda p: p.name
)
def test_no_section_swallows_a_missing_credential(path: Path):
    """`if not os.environ.get(K): return []` reports a quiet day for a broken
    sensor. Declare the var in requires_env instead."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = _env_guarded_empty_returns(tree)
    assert not hits, (
        f"{path.name} returns [] on a missing credential at line(s) "
        f"{hits}. Declare it in requires_env so the base class raises "
        f"MissingCredential and the run is recorded as failed, not quiet."
    )


# --------------------------------------------------------------------------- #
# 3. Every credential the code reads is declared by the section that reads it
# --------------------------------------------------------------------------- #

def _env_vars_read(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            is_get = f.attr == "get" and isinstance(f.value, ast.Attribute) \
                and f.value.attr == "environ"
            if (f.attr == "getenv" or is_get) and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.add(a.value)
    return out


# Vars that configure behaviour rather than authenticate a source.
_NON_CREDENTIAL = {
    "WORLDSCOPE_SKIP", "WORLDSCOPE_STORE_PATH", "WORLDSCOPE_WAREHOUSE_PATH",
    "WORLDSCOPE_SYNTH_MODEL", "FORCE_FULL_RECRAWL", "GITHUB_WORKSPACE",
}


@pytest.mark.parametrize(
    "path", sorted(SECTIONS_DIR.glob("*.py")), ids=lambda p: p.name
)
def test_every_credential_read_is_declared(path: Path):
    """A section that reads an API key must declare it in requires_env or
    optional_env, so preflight can see it and CI can verify it."""
    if path.name in {"__init__.py", "_util.py"}:
        pytest.skip("not a section adapter")

    import importlib
    tree = ast.parse(path.read_text(), filename=str(path))
    read = {v for v in _env_vars_read(tree) if v not in _NON_CREDENTIAL}
    if not read:
        return

    mod = importlib.import_module(f"worldscope.sections.{path.stem}")
    declared: set[str] = set()
    for obj in vars(mod).values():
        if isinstance(obj, type) and issubclass(obj, Section) and obj is not Section:
            declared |= set(getattr(obj, "requires_env", ()))
            declared |= set(getattr(obj, "optional_env", ()))

    undeclared = read - declared
    assert not undeclared, (
        f"{path.name} reads {sorted(undeclared)} but declares {sorted(declared)}. "
        f"Add them to requires_env (hard dependency) or optional_env (degrades)."
    )
