#!/usr/bin/env bash
# The epics repo, from a work repo's point of view (ticketing-template D4/D5).
#
#   bash scripts/epics.sh resolve          # print the epics clone's path, or why not
#   bash scripts/epics.sh ensure           # resolve; if absent, CLONE it beside this repo
#   bash scripts/epics.sh fetch            # git fetch the clone so origin/master is fresh
#   bash scripts/epics.sh ls               # every epic at origin/master: ID  TYPE  STATUS
#
# Resolution lives in ONE place, scripts/doc_kit.py — this is a thin wrapper that
# adds the two network operations (clone, fetch) agents run DELIBERATELY at story
# creation. The commit-msg hook never calls these: it reads, offline, only.
set -euo pipefail
repo="$(git rev-parse --show-toplevel)"
cmd="${1:-resolve}"

cfg() { # <key> — one value from docs/doc-kit.config, or empty (missing file = empty, KIT-027)
  [ -f "$repo/docs/doc-kit.config" ] || return 0
  sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*(.*)$/\1/p" "$repo/docs/doc-kit.config" 2>/dev/null | head -1 || true
}

case "$cmd" in
  resolve)
    python3 "$repo/scripts/doc_kit.py" epics-root ;;
  ensure)
    if p="$(python3 "$repo/scripts/doc_kit.py" epics-root 2>/dev/null)" && [ -n "$p" ]; then
      echo "$p"; exit 0
    fi
    scope="$(cfg epics_scope)"; remote="$(cfg epics_remote)"
    [ -n "$scope" ] || { echo "epics: this repo keeps its epics locally (no epics_scope)"; exit 0; }
    [ -n "$remote" ] || { echo "epics: epics_scope=$scope but no epics_remote to clone from" >&2; exit 1; }
    dest="$(dirname "$repo")/${scope}-epics"
    echo "epics: cloning $remote -> $dest"
    git clone -q "$remote" "$dest"
    python3 "$repo/scripts/doc_kit.py" epics-root ;;
  fetch)
    p="$(python3 "$repo/scripts/doc_kit.py" epics-root)" || exit 1
    [ -n "$p" ] && git -C "$p" fetch -q origin && echo "epics: fetched $p" ;;
  ls)
    python3 "$repo/scripts/doc_kit.py" epics-ls ;;
  *)
    echo "usage: epics.sh resolve|ensure|fetch|ls" >&2; exit 2 ;;
esac
