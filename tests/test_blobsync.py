"""blobsync: durable storage for the lake outside git.

The lake, dist/ and data/store.sqlite were committed to git on every run via
`git add -f`, which overrode the .gitignore that was supposed to stop it. That
put 948 MB of generated data into a 1.36 GB public repository growing ~35 MB a
day, and forced four concurrent workflows to contend on one branch with an
8-attempt `git rebase -X theirs` retry loop.

blobsync replaces that with two tiers:
  * actions/cache for the hot path (fast, evicts after 7 idle days)
  * a GitHub Release per day for durability (`data-YYYY-MM-DD`)

Every subprocess call goes through an injectable runner so the whole restore /
persist decision tree is testable without network, gh, or a repository.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from worldscope import blobsync


class FakeRunner:
    """Records commands; returns scripted results."""

    def __init__(self, responses: dict | None = None):
        self.calls: list[list[str]] = []
        self.responses = responses or {}

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        key = " ".join(argv[:3])
        for pattern, result in self.responses.items():
            if pattern in " ".join(argv):
                if isinstance(result, Exception):
                    raise result
                return result
        return blobsync.RunResult(returncode=0, stdout="", stderr="")

    def saw(self, needle: str) -> bool:
        return any(needle in " ".join(c) for c in self.calls)


@pytest.fixture
def workdir(tmp_path):
    lake = tmp_path / "lake" / "sections" / "macro" / "2026-08-24"
    lake.mkdir(parents=True)
    (lake / "raw.jsonl").write_text('{"id":"a"}\n')
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "store.sqlite").write_bytes(b"sqlite-ish")
    return tmp_path


# --------------------------------------------------------------------------- #
# Archive round-trip
# --------------------------------------------------------------------------- #

def test_archive_and_extract_round_trip(workdir, tmp_path):
    out = tmp_path / "lake.tar.gz"
    blobsync.make_archive(workdir / "lake", out)
    assert out.exists() and out.stat().st_size > 0

    dest = tmp_path / "restored"
    dest.mkdir()
    blobsync.extract_archive(out, dest)
    assert (dest / "lake" / "sections" / "macro" / "2026-08-24" / "raw.jsonl").read_text() \
        == '{"id":"a"}\n'


def test_archive_of_missing_path_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        blobsync.make_archive(tmp_path / "nope", tmp_path / "x.tar.gz")


def test_extract_rejects_paths_escaping_the_destination(tmp_path):
    """A tarball is untrusted input; ../ members must not write outside dest."""
    evil = tmp_path / "evil.tar.gz"
    victim = tmp_path / "payload.txt"
    victim.write_text("pwned")
    with tarfile.open(evil, "w:gz") as tf:
        tf.add(victim, arcname="../escaped.txt")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(blobsync.UnsafeArchive):
        blobsync.extract_archive(evil, dest)
    assert not (tmp_path / "escaped.txt").exists()


# --------------------------------------------------------------------------- #
# Restore decision tree
# --------------------------------------------------------------------------- #

def test_restore_prefers_existing_local_data(workdir):
    runner = FakeRunner()
    sync = blobsync.BlobSync(root=workdir, runner=runner, repo="o/r")
    res = sync.restore(["lake"])
    assert res.source == "local"
    assert not runner.saw("gh release")


def test_restore_falls_back_to_release_when_absent(tmp_path):
    runner = FakeRunner({
        "release list": blobsync.RunResult(
            0, json.dumps([{"tagName": "data-2026-08-23"}]), ""),
    })
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    res = sync.restore(["lake"])
    assert res.source == "release"
    assert res.tag == "data-2026-08-23"
    assert runner.saw("release download data-2026-08-23")


def test_restore_cold_starts_when_no_release_exists(tmp_path):
    runner = FakeRunner({"release list": blobsync.RunResult(0, "[]", "")})
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    res = sync.restore(["lake"])
    assert res.source == "cold"
    assert res.tag is None


def test_restore_picks_the_newest_release(tmp_path):
    runner = FakeRunner({
        "release list": blobsync.RunResult(0, json.dumps([
            {"tagName": "data-2026-08-19"},
            {"tagName": "data-2026-08-23"},
            {"tagName": "data-2026-08-21"},
        ]), ""),
    })
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    assert sync.restore(["lake"]).tag == "data-2026-08-23"


def test_restore_ignores_non_data_releases(tmp_path):
    runner = FakeRunner({
        "release list": blobsync.RunResult(0, json.dumps([
            {"tagName": "v1.2.0"},
            {"tagName": "data-2026-08-20"},
        ]), ""),
    })
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    assert sync.restore(["lake"]).tag == "data-2026-08-20"


def test_cold_start_is_reported_not_hidden(tmp_path, capsys):
    """A cold start means the day rebuilds from nothing. It must be loud."""
    runner = FakeRunner({"release list": blobsync.RunResult(0, "[]", "")})
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    sync.restore(["lake"])
    assert "cold start" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# Persist
# --------------------------------------------------------------------------- #

def test_persist_creates_a_dated_release_and_uploads(workdir):
    runner = FakeRunner()
    sync = blobsync.BlobSync(root=workdir, runner=runner, repo="o/r")
    res = sync.persist(["lake", "data"], today="2026-08-24")
    assert res.tag == "data-2026-08-24"
    assert runner.saw("release create data-2026-08-24") or runner.saw("release upload data-2026-08-24")
    assert sorted(res.assets) == ["data.tar.gz", "lake.tar.gz"]


def test_persist_skips_paths_that_do_not_exist(workdir):
    runner = FakeRunner()
    sync = blobsync.BlobSync(root=workdir, runner=runner, repo="o/r")
    res = sync.persist(["lake", "does_not_exist"], today="2026-08-24")
    assert res.assets == ["lake.tar.gz"]


def test_persist_clobbers_an_existing_same_day_asset(workdir):
    """Re-running a day must replace the asset, not fail or duplicate it."""
    runner = FakeRunner()
    sync = blobsync.BlobSync(root=workdir, runner=runner, repo="o/r")
    sync.persist(["lake"], today="2026-08-24")
    assert runner.saw("--clobber")


def test_persist_failure_raises_rather_than_reporting_success(workdir):
    runner = FakeRunner({
        "release upload": blobsync.RunResult(1, "", "upload failed"),
    })
    sync = blobsync.BlobSync(root=workdir, runner=runner, repo="o/r")
    with pytest.raises(blobsync.BlobSyncError):
        sync.persist(["lake"], today="2026-08-24")


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #

def test_prune_keeps_the_configured_window(tmp_path):
    tags = [f"data-2026-06-{d:02d}" for d in range(1, 21)]
    runner = FakeRunner({
        "release list": blobsync.RunResult(
            0, json.dumps([{"tagName": t} for t in tags]), ""),
    })
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    deleted = sync.prune(keep=5)
    assert len(deleted) == 15
    assert "data-2026-06-20" not in deleted     # newest kept
    assert "data-2026-06-01" in deleted         # oldest dropped


def test_prune_is_a_noop_below_the_threshold(tmp_path):
    runner = FakeRunner({
        "release list": blobsync.RunResult(
            0, json.dumps([{"tagName": "data-2026-06-01"}]), ""),
    })
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    assert sync.prune(keep=60) == []
    assert not runner.saw("release delete")


def test_prune_never_touches_non_data_tags(tmp_path):
    runner = FakeRunner({
        "release list": blobsync.RunResult(0, json.dumps([
            {"tagName": "v1.0.0"}, {"tagName": "v0.9.0"},
            {"tagName": "data-2026-06-01"}, {"tagName": "data-2026-06-02"},
        ]), ""),
    })
    sync = blobsync.BlobSync(root=tmp_path, runner=runner, repo="o/r")
    deleted = sync.prune(keep=1)
    assert deleted == ["data-2026-06-01"]
    assert not runner.saw("release delete v1.0.0")


# --------------------------------------------------------------------------- #
# Migration safety net: git history seeding
# --------------------------------------------------------------------------- #

def test_restore_seeds_from_git_history_when_no_release_exists(tmp_path):
    """The changeover run must not cold-start. lake/ was committed for months;
    the last commit that held it is still the authoritative copy."""
    calls: list[list[str]] = []

    payload = tmp_path / "seedsrc" / "lake" / "sections"
    payload.mkdir(parents=True)
    (payload / "kept.jsonl").write_text("from-history\n")

    def runner(argv, **kw):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "release list" in joined:
            return blobsync.RunResult(0, "[]", "")
        if "git log" in joined:
            return blobsync.RunResult(0, "deadbeef\n", "")
        if "git archive" in joined:
            out = Path(argv[argv.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(out, "w") as tf:
                tf.add(tmp_path / "seedsrc" / "lake", arcname="lake")
            return blobsync.RunResult(0, "", "")
        return blobsync.RunResult(0, "", "")

    sync = blobsync.BlobSync(root=tmp_path / "repo", repo="o/r", runner=runner)
    (tmp_path / "repo").mkdir()
    res = sync.restore(["lake"])

    assert res.source == "git-history"
    assert res.paths == ["lake"]
    assert (tmp_path / "repo" / "lake" / "sections" / "kept.jsonl").read_text() \
        == "from-history\n"
    assert any("--diff-filter=D" in " ".join(c) for c in calls)


def test_cold_start_only_when_git_history_is_also_empty(tmp_path):
    def runner(argv, **kw):
        joined = " ".join(argv)
        if "release list" in joined:
            return blobsync.RunResult(0, "[]", "")
        if "git log" in joined:
            return blobsync.RunResult(0, "", "")      # never committed
        return blobsync.RunResult(0, "", "")

    sync = blobsync.BlobSync(root=tmp_path, repo="o/r", runner=runner)
    assert sync.restore(["lake"]).source == "cold"


# --------------------------------------------------------------------------- #
# Nested paths (ukraine-hourly persists one subtree, not the whole lake)
# --------------------------------------------------------------------------- #

def test_asset_name_encodes_nested_paths_flatly():
    assert blobsync.asset_name("lake") == "lake.tar.gz"
    assert blobsync.asset_name("lake/sections/ukraine_theater") == \
        "lake__sections__ukraine_theater.tar.gz"


def test_nested_archive_round_trips_to_the_original_location(workdir, tmp_path):
    rel = "lake/sections/macro"
    out = tmp_path / blobsync.asset_name(rel)
    blobsync.make_archive(workdir / rel, out, arcname=rel)

    dest = tmp_path / "restored"
    dest.mkdir()
    blobsync.extract_archive(out, dest)
    assert (dest / rel / "2026-08-24" / "raw.jsonl").exists(), \
        "a nested subtree must restore to its original path, not the top level"


def test_persist_of_a_subtree_uses_the_encoded_asset_name(workdir):
    runner = FakeRunner()
    sync = blobsync.BlobSync(root=workdir, runner=runner, repo="o/r")
    res = sync.persist(["lake/sections/macro"], today="2026-08-24")
    assert res.assets == ["lake__sections__macro.tar.gz"]
