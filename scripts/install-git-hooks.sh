#!/usr/bin/env bash
# Point this clone's hooks at the versioned ones in scripts/git-hooks/.
#
# core.hooksPath rather than copying into .git/hooks: a copy is a snapshot that
# silently goes stale when the hook is improved, and nothing tells you.
set -euo pipefail
repo="$(git rev-parse --show-toplevel)"
git -C "$repo" config core.hooksPath scripts/git-hooks
chmod +x "$repo"/scripts/git-hooks/* 2>/dev/null || true
echo "hooks installed: core.hooksPath -> scripts/git-hooks"
ls -1 "$repo/scripts/git-hooks"
