#!/usr/bin/env bash
# Mirror a whole scope into OpenProject, run FROM the scope's epics repo (D15, KIT-007):
# this repo's epics, every adopted member in repos.tsv (rows with a story_prefix), and
# any extra repositories in mirror-extra.tsv (path<TAB>ident<TAB>name<TAB>flags).
# Runs from cron on the machine that holds the clones — one writer. Files stay truth;
# nothing an agent does depends on this running.
#
#   bash scripts/mirror-scope.sh          # appends to the log, prints its tail
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"     # OUR repo, from this script's path — never the caller's cwd (cron runs from $HOME; KIT-015)
scope="$(sed -nE 's/^scope=(.*)$/\1/p' "$here/.epics-root" 2>/dev/null)"
[ -n "$scope" ] || { echo "mirror-scope: run this from an epics repo (no .epics-root here)"; exit 1; }
DEV="${DEV_ROOT:-$(dirname "$here")}"      # repos.tsv paths are relative to the epics repo's parent
M="$here/scripts/mirror-openproject.py"
LOG="${TT_MIRROR_LOG:-$HOME/.cache/ticketing-template/mirror-$scope.log}"; mkdir -p "$(dirname "$LOG")"
run() { # <path> <ident> <name> [flags...]
  local p="$1"; shift
  [ -d "$p/.git" ] || { echo "skip $p (not a repo here)"; return; }
  git -C "$p" fetch -q origin 2>/dev/null || echo "  (fetch failed for $p — mirroring what is local)"
  python3 "$M" --repo "$p" --ref origin/master --project "$1" --name "$2" "${@:3}"
}
{
  echo "== $(date -Is)"
  run "$here" "${scope}-epics" "${scope} — epics" --include-closed
  grep -v '^#' "$here/repos.tsv" 2>/dev/null | tail -n +2 | while IFS=$'\t' read -r repo path remote prefix; do
    [ -n "${prefix:-}" ] || continue
    run "$DEV/$path" "$repo" "$repo" --include-closed --epics-project "${scope}-epics"
  done
  if [ -f "$here/mirror-extra.tsv" ]; then
    grep -v '^#' "$here/mirror-extra.tsv" | while IFS=$'\t' read -r path ident name flags; do
      [ -n "${path:-}" ] || continue
      # shellcheck disable=SC2086
      run "${path/#\~/$HOME}" "$ident" "$name" ${flags:-}
    done
  fi
} >> "$LOG" 2>&1
tail -n 12 "$LOG"
