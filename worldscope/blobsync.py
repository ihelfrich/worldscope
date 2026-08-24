"""worldscope.blobsync — durable storage for generated data, outside git.

Why
===
`lake/`, `dist/` and `data/store.sqlite` were committed to the repository on
every run by:

    git add -f dist/ data/store.sqlite lake/

The `-f` overrode the .gitignore entries that were supposed to keep them out,
so 948 MB of regenerated data (96% of the tracked tree) lived in a 1.36 GB
*public* repository growing roughly 35 MB a day. Four workflows — daily-brief,
ukraine-hourly, render-briefings, pushover-brief — then contended on a single
branch, mediated by an 8-attempt `git rebase -X theirs` retry loop, and
ukraine-hourly cloned the full history 24 times a day to do it.

Storage model
=============
Two tiers, chosen so nothing depends on a third-party service:

  hot       actions/cache, keyed by date. Fast, but GitHub evicts caches after
            7 days without a hit, so it cannot be the only copy.
  durable   one GitHub Release per day, tagged ``data-YYYY-MM-DD``, carrying
            one tarball per top-level path. Release assets do not count against
            the repository's git object store and are capped at 2 GB each.

Restore order is local → release → git history (a one-time migration seed,
since these paths were committed for months) → cold start. A cold start is
announced loudly: it means the day is being rebuilt from nothing, which is a
real event and exactly the kind of thing this codebase used to swallow.

`dist/` is stored too, which is not obvious. It is fully regenerated each run,
so it looks disposable — but a Pages deployment replaces the *whole* site, and
render-briefings.yml deploys independently of daily-brief.yml. Starting it from
an empty dist/ would publish the briefs and delete zips/, sections/ and
assets/, including the zips/<date>.zip the desk-officer routine fetches each
morning.

Paths may be nested: ukraine-hourly persists only
lake/sections/ukraine_theater rather than re-uploading the whole 563 MB lake
24 times a day.

Testability
===========
Every subprocess call goes through an injectable runner, so the whole decision
tree is exercised without network, `gh`, or a repository.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

TAG_PREFIX = "data-"
TAG_RE = re.compile(r"^data-\d{4}-\d{2}-\d{2}$")

# gzip rather than zstd: present in every runner image and in Python's stdlib,
# so restore never depends on a binary that may not be installed. The lake is
# mostly JSON and compresses well either way.
ARCHIVE_SUFFIX = ".tar.gz"

DEFAULT_KEEP = 60


class BlobSyncError(RuntimeError):
    """A storage operation failed. Never swallowed: losing the lake silently
    is strictly worse than failing the run."""


class UnsafeArchive(BlobSyncError):
    """A tarball member resolved outside the extraction root."""


@dataclass
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(argv: Sequence[str], **kw) -> RunResult:
    proc = subprocess.run(
        list(argv), capture_output=True, text=True, check=False, **kw
    )
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


@dataclass
class RestoreResult:
    source: str                      # "local" | "release" | "cold"
    tag: Optional[str] = None
    paths: list[str] = field(default_factory=list)


@dataclass
class PersistResult:
    tag: str
    assets: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Archive helpers
# --------------------------------------------------------------------------- #

def make_archive(source: Path, dest: Path, *, arcname: Optional[str] = None) -> Path:
    """Tar+gzip `source`, storing members under `arcname`.

    `arcname` defaults to the basename, but callers that archive a nested path
    (ukraine-hourly persists only lake/sections/ukraine_theater, not the whole
    563 MB lake) must pass the path relative to the repository root, so
    extraction reproduces the original layout instead of dropping the subtree
    at the top level.
    """
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"blobsync: nothing to archive at {source}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(source, arcname=arcname or source.name)
    return dest


def asset_name(path: str) -> str:
    """Release assets live in one flat namespace, so a nested path is encoded.

    lake/sections/ukraine_theater -> lake__sections__ukraine_theater.tar.gz
    """
    return path.strip("/").replace("/", "__") + ARCHIVE_SUFFIX


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def extract_archive(archive: Path, dest: Path) -> None:
    """Extract `archive` into `dest`, refusing members that escape it.

    A Release asset is untrusted input in the same sense any downloaded file
    is; a `../` member would otherwise write anywhere the job can reach.
    """
    archive, dest = Path(archive), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            target = dest / member.name
            if not _is_within(dest, target):
                raise UnsafeArchive(
                    f"blobsync: archive member {member.name!r} escapes {dest}"
                )
            if member.issym() or member.islnk():
                link_target = dest / member.name
                if not _is_within(dest, link_target):
                    raise UnsafeArchive(
                        f"blobsync: link member {member.name!r} escapes {dest}"
                    )
        # Python 3.12+ understands filter=; older versions ignore the kwarg.
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # pragma: no cover - Python < 3.12
            tf.extractall(dest)


# --------------------------------------------------------------------------- #
# BlobSync
# --------------------------------------------------------------------------- #

@dataclass
class BlobSync:
    root: Path
    repo: str
    runner: Callable[..., RunResult] = _default_runner
    staging: Optional[Path] = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.staging = Path(self.staging) if self.staging else self.root / ".blobsync"

    # -- gh plumbing -------------------------------------------------------- #

    def _gh(self, *args: str, check: bool = True) -> RunResult:
        res = self.runner(["gh", *args])
        if check and res.returncode != 0:
            raise BlobSyncError(
                f"blobsync: `gh {' '.join(args)}` failed ({res.returncode}): "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )
        return res

    def _data_tags(self) -> list[str]:
        """Every ``data-YYYY-MM-DD`` release tag, newest first.

        Lexicographic sort is chronological for ISO dates, which is why the
        tag format is fixed rather than free-form.
        """
        res = self._gh("release", "list", "--repo", self.repo,
                       "--limit", "200", "--json", "tagName", check=False)
        if res.returncode != 0:
            return []
        try:
            payload = json.loads(res.stdout or "[]")
        except json.JSONDecodeError:
            return []
        tags = [r.get("tagName", "") for r in payload]
        return sorted((t for t in tags if TAG_RE.match(t)), reverse=True)

    # -- restore ------------------------------------------------------------ #

    def restore(self, paths: Iterable[str]) -> RestoreResult:
        paths = list(paths)

        present = [p for p in paths if (self.root / p).exists()]
        if len(present) == len(paths) and paths:
            print(f"[blobsync] restore: local copies present for {', '.join(paths)}")
            return RestoreResult(source="local", paths=present)

        tags = self._data_tags()
        if not tags:
            # Migration safety net. These paths were committed to git for
            # months, so on the first run after untracking them the history
            # still holds the last good copy. Without this, the changeover
            # would cold-start and silently discard the accumulated lake —
            # every carry-forward and day-over-day delta empty, with no
            # release yet written to recover from.
            seeded = self._seed_from_git_history(paths)
            if seeded:
                print(f"[blobsync] seeded {', '.join(seeded)} from git history "
                      f"(no data-* release exists yet; this run will create one)")
                return RestoreResult(source="git-history", paths=seeded)

            # Loud on purpose. A cold start rebuilds the day from nothing, and
            # every downstream carry-forward and day-over-day delta is empty.
            print("[blobsync] COLD START: no data-* release and nothing in git "
                  "history; the lake will be rebuilt from scratch and all "
                  "day-over-day comparisons will be empty today.")
            return RestoreResult(source="cold", paths=[])

        tag = tags[0]
        staging = self.staging / "restore"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        restored: list[str] = []
        for p in paths:
            asset = asset_name(p)
            res = self._gh("release", "download", tag, "--repo", self.repo,
                           "--pattern", asset, "--dir", str(staging),
                           "--clobber", check=False)
            if res.returncode != 0:
                print(f"[blobsync] {asset} not present in {tag}; "
                      f"{p} starts empty this run")
                continue
            local = staging / asset
            if local.exists():
                extract_archive(local, self.root)
            restored.append(p)

        print(f"[blobsync] restore: {tag} -> {', '.join(restored) or 'nothing'}")
        return RestoreResult(source="release", tag=tag, paths=restored)

    def _seed_from_git_history(self, paths: Iterable[str]) -> list[str]:
        """Recover paths from the last commit that still contained them.

        Used exactly once, on the first run after these paths are removed from
        the index. `git log --diff-filter=D` finds the commit that deleted the
        path; its parent still has the full tree, and `git archive` extracts it
        without checking anything out.
        """
        seeded: list[str] = []
        deepened = False
        for p in paths:
            def find_deletion() -> str:
                res = self.runner([
                    "git", "log", "-1", "--format=%H", "--diff-filter=D",
                    "--", f"{p}/",
                ])
                return (res.stdout or "").strip()

            sha = find_deletion()
            if not sha and not deepened:
                # CI checks out shallow (fetch-depth: 1) precisely so the daily
                # run stops cloning 1.36 GB of history. Deepen once, on demand,
                # only when the seed is actually needed — which is a single run,
                # ever.
                print("[blobsync] deepening shallow clone to locate the "
                      "pre-migration tree")
                self.runner(["git", "fetch", "--deepen=500", "--quiet"])
                deepened = True
                sha = find_deletion()
            if not sha:
                continue
            tar_path = self.staging / f"seed-{p}.tar"
            tar_path.parent.mkdir(parents=True, exist_ok=True)
            arch = self.runner([
                "git", "archive", "--format=tar", "-o", str(tar_path),
                f"{sha}^", "--", p,
            ])
            if arch.returncode != 0 or not tar_path.exists():
                continue
            try:
                extract_archive(tar_path, self.root)
            except (UnsafeArchive, tarfile.TarError):
                continue
            finally:
                tar_path.unlink(missing_ok=True)
            if (self.root / p).exists():
                seeded.append(p)
        return seeded

    # -- persist ------------------------------------------------------------ #

    def persist(self, paths: Iterable[str], *, today: str) -> PersistResult:
        tag = f"{TAG_PREFIX}{today}"
        staging = self.staging / "persist"
        staging.mkdir(parents=True, exist_ok=True)

        assets: list[str] = []
        for p in paths:
            source = self.root / p
            if not source.exists():
                print(f"[blobsync] persist: {p} absent, skipping")
                continue
            archive = staging / asset_name(p)
            make_archive(source, archive, arcname=p)
            assets.append(archive.name)

        if not assets:
            raise BlobSyncError(
                "blobsync: nothing to persist; refusing to publish an empty "
                "release, which would look like a successful backup"
            )

        # Create is idempotent-ish: it fails when the tag exists, which is the
        # normal case on a same-day re-run, so the failure is expected and the
        # upload below carries --clobber.
        self._gh("release", "create", tag, "--repo", self.repo,
                 "--title", f"Worldscope data {today}",
                 "--notes", "Generated lake + snapshot store. Not source.",
                 check=False)

        for name in assets:
            self._gh("release", "upload", tag, str(staging / name),
                     "--repo", self.repo, "--clobber")

        print(f"[blobsync] persist: {tag} <- {', '.join(assets)}")
        return PersistResult(tag=tag, assets=sorted(assets))

    # -- retention ---------------------------------------------------------- #

    def prune(self, *, keep: int = DEFAULT_KEEP) -> list[str]:
        """Delete data releases beyond the newest `keep`. Returns deleted tags."""
        tags = self._data_tags()
        doomed = tags[keep:]
        for tag in doomed:
            self._gh("release", "delete", tag, "--repo", self.repo,
                     "--yes", "--cleanup-tag", check=False)
        if doomed:
            print(f"[blobsync] prune: deleted {len(doomed)} release(s) "
                  f"older than the newest {keep}")
        return doomed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    from datetime import date, timezone, datetime

    ap = argparse.ArgumentParser(prog="python -m worldscope.blobsync")
    ap.add_argument("action", choices=["restore", "persist", "prune"])
    ap.add_argument("--paths", default="lake,data",
                    help="comma-separated top-level paths")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY",
                                                     "ihelfrich/worldscope"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = ap.parse_args(list(argv) if argv is not None else None)

    sync = BlobSync(root=Path(args.root), repo=args.repo)
    paths = [p.strip() for p in args.paths.split(",") if p.strip()]

    try:
        if args.action == "restore":
            sync.restore(paths)
        elif args.action == "persist":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            sync.persist(paths, today=today)
            sync.prune(keep=args.keep)
        else:
            sync.prune(keep=args.keep)
    except BlobSyncError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
