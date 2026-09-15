# Ticketing — what an agent does here, step by step

> **STATE: OPERATING PROCEDURE** — the procedure every repository on ticketing-template follows; the git hooks enforce it. Installed by the kit, refreshed on upgrade: edit it in the kit, not here.

(Setting up a machine or a clone — cloning the kit and the epics repo, installing hooks —
is the kit's `START-HERE.md`, not repeated here.)

This repository tracks work as **files**: one ticket per file in `docs/tickets/`,
moved to `docs/tickets/closed/` when done, never deleted. Git hooks enforce the rules
offline and tell you the fixing command at the moment you break one. Read this once;
after that the hooks teach.

Two kinds of repository use this exact document:

- a **work repo** (code lives here; stories, tasks, bugs and spikes live here), and
- an **epics repo** named `<scope>-epics` (only EPICs live here — the parents of work
  that spans several repos).

`docs/doc-kit.config` says which one this is (`work_item_types = EPIC` means an
epics repo; `epics_scope = <name>` means a work repo whose epics live in `<name>-epics`).

## 1. Start of a session

```bash
git fetch -q                       # the hooks compare against origin/master
python3 scripts/wi.py              # this repo's board: in flight, blocked, queued, by priority
bash scripts/epics.sh fetch        # work repo with a scope: refresh the epics clone
```

### Working from a Git worktree

Every worktree is a separate checkout of the same repository. Do not use the same
branch in two worktrees. From the new worktree, install the versioned hooks before
creating or committing tickets:

```bash
git worktree add ../repo-KIT-043 -b story/KIT-043
cd ../repo-KIT-043
bash scripts/install-git-hooks.sh
export TICKETING_ACTOR=Codex       # or Claude; Human is the explicit human actor
```

Worktrees contain tracked files only. Copy required ignored configuration or secrets
from the main checkout using the repository's documented safe procedure; never commit
those files. Ticket files, lifecycle stamps, and generated documentation must be
written from the active worktree and checked with `git -C` or `git rev-parse` when a
script is invoked from elsewhere. The hook resolves the active worktree's top level,
so it does not write ticket state to the main checkout.

Across repositories, your project's board (built by `scripts/gen-board.py`, one per
project; ask whoever runs it for the URL) and the OpenProject mirror show the same
tickets as they stand at origin. Both are read-only, and nothing you have not pushed
appears on either.

Pick the highest-priority unblocked item. If nothing covers what you must do, **open a
ticket first** — nothing is too small for a TASK.

## 2. Create a ticket — one at a time, on master, then push

```bash
git switch master && git pull -q
python3 scripts/wi.py new MARKETDATA_API-012 STORY "Serve-time split adjustment" --area marketdata --epic STEALTH-012
#   → docs/tickets/MARKETDATA_API-012-serve-time-split-adjustment.md   (from docs/templates/ticket-STORY.md)
$EDITOR docs/tickets/MARKETDATA_API-012-*.md       # fill the contract (below)
git add docs/tickets/MARKETDATA_API-012-*.md
git commit -m "MARKETDATA_API-012: file — Serve-time split adjustment (ticket creation)"
git push                                  # BEFORE branching: the hooks check origin/master
```

Rules the hooks apply at this moment:

- **One creation per commit.** `wi.py new` refuses while another new ticket is
  uncommitted. Create → commit → push → next.
- **Actor identity is explicit.** Set `TICKETING_ACTOR=Codex`, `Claude`, or `Human`.
  New tickets record `creator:` and `opener:`; the first work and close hooks record
  `starter:` and `closer:`. Commits touching Codex/Claude-created tickets carry one
  `Agent: Codex` or `Agent: Claude` trailer.
- **Every touch is narrated, not just the three moments (KIT-050).** A ticket's
  `## Update Log` section gets one line per commit that changes it — timestamp, the
  same actor identity as above, and what changed (a field diff, which body section
  moved, or `created`/`started`/`closed` for those three). Append-only: an existing
  line is never rewritten, and re-running the hook before an actual commit does not
  duplicate the pending line.
- **What a creation commit may carry on master:** the ticket file, a decision recorded
  with it (`wi.py decision new`, and the index the hook regenerates), the paths in
  `maintenance_paths`, and the rendered `.html` sibling of any of those (a repo whose
  own hook renders documents, KIT-033). Anything else is work, and belongs on a branch.
- **The id is `PREFIX-NNN`**, and the prefix must be declared in `docs/doc-kit.config`
  (`prefixes =`) or already in use. Anything else id-shaped (`SHA-256`, `ADR-007`) is
  prose, not a ticket — and words like `API`, `SHA`, `RFC`, `TLS` can never be prefixes
  (`prefix_denylist`). Choosing one: 2–24 characters, capital letters with whole words
  joined by single underscores, unique across the scope (KIT-057). **Spell words out**:
  the people reading the board should never have to decode an abbreviation. In a
  multi-repo scope, use **one prefix per repo**, the repo's name in words
  (`marketdata-api → MARKETDATA_API`, `uw-flow-indexer → UW_FLOW_INDEXER`). A scope may
  pin the exact transformation in a decision in its epics repo. In a single-repo
  product, use one prefix per area (open-teleporter's `HOST`, `RFB`). Don't mix the two
  conventions in one scope. Older 2–10 letter prefixes stay valid.
- **Renaming a prefix.** An id can never change, because it is written into pushed
  commits. So a renamed prefix is **retired**, not removed (KIT-059). Put the new prefix
  in `prefixes =` and move the old one to `retired_prefixes =`. In a scope, update the
  repository's `repos.tsv` row in the epics repo in the same change (`story_prefix`, and a
  `retired_prefixes` column). Old tickets keep their ids and can still be worked, cited and
  closed. `wi.py new` refuses the old prefix, and new numbers continue past its highest one:

  ```
  prefixes         = TWITTER_SCRAPER
  retired_prefixes = TWSCR     # TWSCR-001…020 stay valid; the next new id is TWITTER_SCRAPER-021
  ```
- **Areas and tags** (KIT-060). `area:` is exactly one word naming the lasting part of the
  system a ticket concerns; the board groups by it. `tags:` holds none or several words,
  comma-separated, naming topics that cut across repos: a feed, production, an incident.
  When the project keeps word lists (`vocabulary/areas.tsv`, `vocabulary/tags.tsv`), both
  must come from them. In a scoped repo those lists are the scope's, kept in
  `<scope>-epics` and read at `origin` like epics. Run `python3 scripts/wi.py words`, pick
  a word (a listed synonym tells you which word to use), and pass `--area` / `--tags` to
  `wi.py new`. The hooks refuse anything else on a ticket whose area or tags you set or
  change. An old ticket keeping an old word is left alone.

  **Adding a word is yours to decide; nobody approves it.** The rules:
  1. If an existing word or one of its synonyms covers the idea, use it. Never add a
     plural, a variant or an abbreviation of a word that is already there.
  2. An area names a lasting part of the system that more than one ticket will concern. A
     topic that concerns just one ticket is a tag, or nothing.
  3. Lowercase whole words joined by `-`, at most 20 characters. Acronyms only if the
     codebase already uses them (`api`, `ui`). Never `misc`, `other`, `general` or
     `various`.
  4. Add a row with `word`, a one-line `meaning` a person understands, `synonyms`,
     `added`, `first_ticket` (the ticket you are about to file), `reason`. Commit it alone,
     as `words: add <word> (<area|tag>) — <why>`, on the default branch of the repo that
     holds the lists, and push. Then file the ticket.
  5. Never delete a row. To replace a word, set its `merged_into` to the word that
     replaces it. Old tickets then read as the new word, and no ticket file changes.

  ```
  word	meaning	synonyms	added	first_ticket	reason	merged_into
  security	who may do what, and secrets	sec, tls, auth	2026-09-15	-	starter list
  backtesting	replaying past market data to test a strategy		2026-09-16	QUANT_LAB-001	no area covered it
  ```

  On the board, a ticket shows under the live word: a merged word reads as its successor and
  a synonym as its word. Tags get their own pages, and new words appear under "Words added
  this week". A many-repository board can also group repositories by domain (a `domain`
  column in `repos.tsv`) and lead each row with the title (`row_style = title-first`), with
  the ID in small type at the end (KIT-061).
- **The type is one of** `EPIC STORY TASK BUG SPIKE`, chosen by what *closing* needs:

  | type | closes when |
  |---|---|
  | STORY | its acceptance tests pass; it owns its own test |
  | BUG | a committed reproduction failed first and now passes, gate-wired |
  | TASK | the thing is done; no current-state claim changed |
  | SPIKE | a finding or a decision — "we looked; the answer is no" closes honestly |
  | EPIC | every child story is closed — a tracking parent, never worked on directly |

- **The contract** (from the template) must be present before any history: a plain-
  English `## Summary` first; for STORY/BUG a `## The story`, `## Acceptance criteria`
  as a list of `- **AC-n**`, `## Test cases` mapping each to `TC-<ID>-nnn` and the
  command that runs it; and `## Documentation impact` — the documents this change
  will make stale, each existing (or marked `(new)`), unconditionally, or `None.`
- **A decision that drove this item:** write `{{Dn}}` anywhere in the ticket. At commit it
  becomes `Dn` in place and the full text lands under `## Decisions`; a `{{Dn}}` naming no
  decision refuses the commit. `python3 scripts/wi.py decisions <ID>` lists what an item carries.
- **`epic:`** (optional) must name a real EPIC. In a scoped work repo it lives in
  `<scope>-epics` and must already be **pushed** there. In an epics repo, only EPICs
  may be created.
- **Never in a feature branch.** A ticket born inside a branch is invisible to every
  other agent until merge (the board reads master), so the hook refuses a
  `<type>/<ID>` branch commit until `<ID>` is on `origin/master`.

### If another agent takes the ID first

Ticket numbers are allocated by convention, so two agents can choose the same next
number between fetches. Git is the authority; do not force the push and do not use
`--no-verify` to get past the collision.

```bash
git fetch -q origin
# inspect origin/<default branch> and choose the next unused ID
python3 scripts/wi.py new <next-free-ID> <TYPE> "<title>"
```

If a local unpushed ticket already has the claimed ID, keep the work but change its
`id:` and filename to the next unused ID, then run the ticket checks again. If the
collision appears during `git push`, fetch first, verify whether the remote accepted
the ticket or another agent used the ID, and reconcile against the remote tree before
retrying. A remote ticket always wins; never publish two tickets with one ID. If a
branch or commit already names the old ID, update the branch name and commit subject
before continuing. This is discovery-and-retry, not a locking system.

## 3. Work — on a branch named for the item

```bash
git switch -c story/MARKETDATA_API-012            # story/ bug/ task/ spike/ — exactly one item per branch
... edit, test ...
git commit -m "MARKETDATA_API-012: adjust at serve time, not at ingest

Follows the approach settled in STEALTH-012; supersedes MARKETDATA_API-004."
git push -u origin story/MARKETDATA_API-012
```

- **Every commit's subject names the branch's item.** Other items go in the body —
  they are citations and must exist, in any status.
- **Nothing lands on master directly** except merges, reverts, ticket-only commits
  (creation, and epic maintenance), and the machine commits listed in the config.
- **A decision made while working is written the same commit** — as its OWN file:
  `python3 scripts/wi.py decision new "<title>"` → `docs/decisions/Dn-<slug>.md`, four
  parts: what we picked, the alternatives and why each lost, the constraint that forced
  it, what would make us revisit. Never edit a decision; to reverse one, add a new one
  marking the old **SUPERSEDED by Dn**. **The hook copies the decision, in full, into the
  ticket you are working** (`## Decisions`, same commit) — nothing is typed twice. The index, `docs/DECISIONS_INDEX.md`, is
  generated — never hand-edit it; an older hand-written `docs/DECISIONS.md` stays frozen.
- **A new `- [ ]` bullet in `TODO.md`** must name an item or the commit must create one.
- **A bug found in passing** is fixed here or filed — never neither.

The first commit on the branch stamps `started:` into the ticket automatically.

## 4. Close — in the final branch commit, then merge

Before closing, ask: *what does a reader of the contract or the subsystem document now
believe that this change made false?* Fix each of those documents **in the closing
commit** — the Documentation impact section is executed, not declared.

```bash
sed -i 's/^status:.*$/status:     CLOSED/' docs/tickets/MARKETDATA_API-012-*.md
git mv docs/tickets/MARKETDATA_API-012-*.md docs/tickets/closed/
git add <the documents the impact section named>
git commit -m "MARKETDATA_API-012: serve-time adjustment, docs updated (closes)"
bash tests/…                             # the repo's gate, green
git switch master && git merge --no-ff story/MARKETDATA_API-012 && git push
git branch -d story/MARKETDATA_API-012
```

The hook stamps `closed:` (and repairs a missing `started:`) when the file is staged
under `closed/`. If the close turns out to be wrong, reverse it with section 4a's
`wi.py reopen` — never by hand. Status is `OPEN | BLOCKED | CLOSED` — "in progress" is derived from
commits, never written.

## 4a. Reopen — when a close did not hold

A close is the only lifecycle event with no reviewer, so reversing one leaves evidence
rather than quietly undoing itself:

```bash
python3 scripts/wi.py reopen MARKETDATA_API-012 "the healthcheck was never run in a container"
git switch -c bug/MARKETDATA_API-012            # a reopen is WORK, not a creation — it needs a branch
git add docs/tickets/MARKETDATA_API-012-*.md
git commit -m "MARKETDATA_API-012: reopen — the close did not hold"
git push -u origin bug/MARKETDATA_API-012
```

The branch step is not optional: the default branch takes merges, reverts, ticket *creations*
and the configured machine commits, and a reopen is none of those. (If the item's old branch
still exists from the close that did not hold, `git branch -d <type>/<ID>` it first — it is
merged.)

It moves the file back out of `closed/`, sets `status: OPEN`, increments `reopened:`, and
appends one entry to an append-only `## Reopen history`:

```
- **Reopen 1** — 2026-09-15 09:12 ET — reopened by Claude — reverses the close of
  2026-09-15 08:12 ET by Codex — the healthcheck was never run in a container
```

The superseded `closed:`/`closer:` MOVE into that entry and are cleared from the front
matter. That is deliberate (D24): stamps are never overwritten, so leaving them would make
the eventual re-close credit the actor whose close did not hold, and hide the one who
actually fixed it. Nothing is lost — the old close is in the history entry, permanently.

The gate enforces both halves: an OPEN or BLOCKED item carrying `closed:` or `closer:` is
refused, and `reopened: n` must equal the number of `## Reopen history` entries. Reopening
by hand trips one of those; use the command. `python3 scripts/wi.py` marks a returning item
`[reopened xN]` on the board, so an item that keeps coming back cannot look like a fresh one.

Then work it like any other open item (section 3) and close it again (section 4) — the
second close stamps the real closer, because the first one is no longer in the way.

## 5. Epics — work that spans repositories

A story must own one self-contained test. A change touching two repositories cannot,
so **it is an EPIC with one STORY per repository**. Stories live where the code
lives; the epic lives in the scope's epics repo.

```bash
cd ~/dev/stealth-epics                      # the epics repo for scope "stealth"
git pull -q
python3 scripts/wi.py new STEALTH-012 EPIC "Split adjustment across the market-data stack"
git add docs/tickets && git commit -m "STEALTH-012: file — Split adjustment across the market-data stack (ticket creation)"
git push                                    # an epic exists for others ONLY at origin/master
```

- **Epics are created, updated and closed on master** by ticket-only commits (the
  subject names the epic; the commit touches only ticket files and the paths listed
  in `maintenance_paths`, e.g. `repos.tsv`, `docs/DECISIONS.md`, plus their rendered
  `.html` siblings). Nobody branches an
  epic; nobody commits code against one.
- **Children are derived** from each story's `epic:` field — never listed by hand.
- **`repos.tsv`** in the epics repo lists every member repository with its ticket prefix;
  add a row when a repo adopts the kit. The epics repo's pre-commit checks it against the
  repos themselves (`scripts/check-scope.py`): a row's prefix must equal what the repo
  declares, no prefix twice, every repo claiming the scope has a row.
- The epic **closes when its last story closes**. Closing it with an open child is not
  blocked at commit time (the epics repo cannot see other repos offline); the board
  flags it.

## 6. A fresh machine or a clone somewhere else

```bash
git clone <work repo> && cd <work repo>
bash scripts/install-git-hooks.sh       # hooks are local config; a clone has none until this
bash scripts/epics.sh ensure            # scoped repo: clones <scope>-epics beside this repo
#   or: export EPICS_ROOT=/path/to/<scope>-epics
```

Everything else is offline. If the epics clone is missing, ordinary commits pass with a
NOTE; creating a story that names an epic fails loudly with the clone command.

Hooks the clone already had in `.git/hooks/` (a machine-installed doc check, say) are
not switched off: every kit hook runs them after its own checks, and the installer lists
each one it found as `kept:` (KIT-031).

## 7. When a hook refuses — what it means

| the message says | do this |
|---|---|
| no work item referenced | put the item id in the **subject** line; open one if none fits |
| work commits do not land on master | `git switch -c <type>/<ID>` and commit there |
| this branch belongs to X | the subject must name X; other items go in the body |
| has no ticket on origin/master yet | create the ticket on master, commit, **push**, come back |
| ticket ID already exists or push is non-fast-forward | fetch again, inspect the remote, choose the next free ID, reconcile any unpushed local ticket, then retry |
| is CLOSED | `wi.py reopen <ID> "<why>"` (section 4a) or open a new item — unless this commit is the close (stage the move) |
| is an EPIC, a tracking parent | commit against one of its stories; cite the epic in the body |
| cited in the body but does not exist | file it, or fix the id — a name that looks tracked and isn't is the worst case |
| names an epic that does not exist | create the epic in the epics repo, **push**, then retry |
| epics live in `<scope>-epics` | you created an EPIC in a work repo; create it there instead |
| no `<scope>-epics` repo beside this repo | `bash scripts/epics.sh ensure` (or set `EPICS_ROOT`) |
| a ticket fails check-… | read the checker's own lines above the refusal; fix the file |
| new TODO items, but no work item | make the bullet name an item, or create one in this commit |
| nothing is staged and the subject names no existing item | an amend/empty commit must still name a real item |

`--no-verify` exists for exactly one case: re-authoring a commit the hook already
accepted (e.g. fixing the author). Never for anything else; its use should be rare and
visible.

## 8. Do / don't

- Do create the ticket before the branch, push, then branch. Don't batch creations.
- Do keep the subject to one item. Don't cite a second item in the subject.
- Do write decisions as you make them, one file each, in the same commit. Don't "catch up" later, and don't append to a shared log.
- Do close with the documents updated. Don't move a file to `closed/` and call it done.
- Do file what you find. Don't leave a bug in a comment or a TODO with no item.
- Don't edit `~/dev/<scope>-epics` on a branch or leave it dirty — every session on the
  machine reads it (at `origin/master`, so what you did not push does not exist).

## 9. Maintaining the kit itself (only in the `ticketing-template` repo)

A repo that needs MORE than the kit's hooks (its own generators, renderers, targeted
tests, extra message rules) puts them in `scripts/git-hooks/pre-commit.local` and
`scripts/git-hooks/commit-msg.local` — the kit's hooks call them after their own checks,
and upgrades never touch them. After `.local` comes the clone's own `.git/hooks/<hook>`,
the per-clone machine config git stopped reading when `core.hooksPath` was set; the kit
ships a passthrough for every client hook it has no rule for (`post-commit`, `pre-push`,
…) so those events reach `.local` and `.git/hooks/` too (KIT-031). A repo with a better decisions index sets
`decisions_index = none`. The installer refuses to overwrite any `scripts/` file it did
not ship (`KIT_FILES` is its manifest).

The payload is `kit/`; the repo's own `scripts/` and `docs/` are an *installed copy* of
it. After changing anything under `kit/`, re-run `bash install.sh .` so the copy the
hooks actually execute is the new one, and bump `VERSION` in the release ticket. Every
change rides a `KIT-nnn` ticket like anywhere else; `bash tests/run-all.sh` is the gate.

Four lessons from the first day of dogfooding, each of which cost a cycle:

- **Never silence `git add`.** `git add a b missing 2>/dev/null` stages NOTHING when one
  pathspec is missing — the close of KIT-016 shipped without its implementation that way,
  and the gate stayed green because it tests the working tree. Let it fail loudly.
- **`git add` after `git mv`.** Editing a file and then `git mv`-ing it stages the rename
  with the OLD content; the closing commit carried `status: OPEN`. Move, then add.
- **Patch files with a script, not nested heredocs.** Three attempts at one fix died on
  escaping (`\\s` vs `\s`, an em dash outside a string). Write the patch to a file or use
  `str.replace`/function-based substitution, and assert the patch landed before going on.
- **Assert a clean tree after every close**, and check the patch is on disk before the
  gate: `[ -z "$(git status --short)" ] || exit 1`. A `&&`-chain under `set -e` swallows
  a refused commit; check exit codes explicitly.
