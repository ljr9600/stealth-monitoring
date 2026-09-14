#!/usr/bin/env bash
# ticketing-template: the tail of EVERY hook (KIT-031). Not a hook itself — git ignores
# names it does not know. Runs, in this order, for the hook named in $1:
#
#   1. scripts/git-hooks/<hook>.local  — the repo's own VERSIONED hook work (KIT-023)
#   2. <git common dir>/hooks/<hook>   — the clone's own, machine-installed hook: the one
#                                        git stopped reading the moment core.hooksPath
#                                        was pointed at scripts/git-hooks
#
# The kit's pre-commit and commit-msg call this after their checks. For every other
# client-side hook the kit ships a one-line passthrough that only calls this, so adopting
# the kit never switches a hook off. A non-zero exit from either refuses the operation
# and names the hook that refused. Hooks fed on stdin (pre-push, post-rewrite) have it
# captured once and replayed to each.
#
#   usage: _chain.sh <hook-name> [hook args...]
set -u
hook="$1"; shift
args=("$@")
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
common="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)"
case "$common" in /*) ;; *) common="$repo/$common" ;; esac
rp() { readlink -f "$1" 2>/dev/null || echo "$1"; }

stdin_file=""
case "$hook" in
  pre-push|post-rewrite|pre-receive|post-receive|reference-transaction) stdin_file="$(mktemp)"; cat > "$stdin_file" ;;
esac
cleanup() { [ -n "$stdin_file" ] && rm -f "$stdin_file"; }

run() { # <path> <label shown on refusal>
  local p="$1" label="$2" rc=0
  if [ -n "$stdin_file" ]; then "$p" ${args[@]+"${args[@]}"} < "$stdin_file" || rc=$?
  else "$p" ${args[@]+"${args[@]}"} || rc=$?; fi
  if [ "$rc" != 0 ]; then echo "$hook: REJECTED by $label (exit $rc)" >&2; cleanup; exit "$rc"; fi
}

[ -x "$here/$hook.local" ] && run "$here/$hook.local" "scripts/git-hooks/$hook.local"
legacy="$common/hooks/$hook"
# The guard: if core.hooksPath were ever the clone's own hooks dir, "legacy" would be this
# very file's sibling — running it again would recurse forever.
if [ -x "$legacy" ] && [ "$(rp "$legacy")" != "$(rp "$here/$hook")" ]; then
  run "$legacy" "${legacy#"$repo"/}"
fi
cleanup
exit 0
