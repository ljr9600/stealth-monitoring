#!/usr/bin/env python3
"""Does a path belong to the REPOSITORY, or is it only on one machine's disk?

DOCS-041. The path-resolving checkers (links, the handoff link, evidence path
citations, documentation-impact paths) used to answer "does this file exist?" by
asking the working tree — so a file that exists locally but was never `git add`ed
satisfied every one of them. The gate went green, and the link was dead for
everyone who cloned. A check that answers "does this file exist" must answer it
about the repository, not about one developer's disk.

A path counts as existing iff git has it **committed OR staged**. Staged must
count (AC-3): the pre-commit hook runs before the commit exists, and refusing a
staged file would make it impossible to add a document and its link in one commit
— which is exactly how the project is required to work.

Outside a git work tree (an ad-hoc run, or the enforcement audit's throwaway
sandbox before it inits one) there is no repository to answer about, so this
falls back to plain on-disk existence — the old behaviour, which is the most a
check can promise there anyway.
"""
from __future__ import annotations
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def _index(root: str) -> frozenset[str] | None:
    """The set of repo-relative posix paths git has committed or staged at `root`,
    or None when `root` is not a git work tree. `git ls-files` lists the index,
    which is precisely committed ∪ staged: an untracked file is absent, a staged
    new file is present. Cached — one git call per root per process."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return frozenset(p for p in out.stdout.split("\0") if p)


@lru_cache(maxsize=None)
def _dirs(root: str) -> frozenset[str]:
    """Every directory prefix that the index has a file under — git tracks files,
    not directories, so a link to a directory (`../tickets/`) has to be judged by
    whether the repository has anything inside it. Empty when `root` is not a git
    work tree (the `tracked()` fallback handles that case on disk instead)."""
    idx = _index(root)
    if idx is None:
        return frozenset()
    dirs: set[str] = set()
    for f in idx:
        parts = f.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return frozenset(dirs)


def tracked(path, root) -> bool:
    """True if `path` is committed or staged in the git repo at `root` — a file in
    the index, or a directory the index has files under. Falls back to on-disk
    existence when `root` is not a git work tree."""
    idx = _index(str(root))
    p = Path(path)
    if idx is None:
        return p.exists()
    try:
        rel = p.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return p.exists()  # outside the repo: not ours to judge, keep old behaviour
    if not rel or rel == ".":
        return True  # the repo root itself
    return rel in idx or rel in _dirs(str(root))


def untracked_but_present(path, root) -> bool:
    """The AC-4 case: the file IS on disk but is NOT in the repository — so a
    message can say "untracked" rather than "no such file", which would send a
    reader hunting for a typo in a path that is spelled correctly."""
    return Path(path).exists() and not tracked(path, root)
