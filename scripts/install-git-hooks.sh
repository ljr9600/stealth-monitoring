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
# Hooks this clone already had (a machine-installed doc check, a header updater) are NOT
# switched off by the line above: every kit hook chains them after its own checks
# (KIT-031). Say so, per hook, so nobody has to discover it from a missing side effect.
common="$(git -C "$repo" rev-parse --git-common-dir)"; case "$common" in /*) ;; *) common="$repo/$common" ;; esac
for h in "$common"/hooks/*; do
  [ -x "$h" ] || continue; case "$h" in *.sample) continue ;; esac
  echo "kept: ${h#"$repo"/} — this clone's own hook keeps running, after the kit's checks and $(basename "$h").local (KIT-031)"
done
