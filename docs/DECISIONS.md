# DECISIONS — the append-only decision log for `monitoring`

> **If you made a choice, it goes here.** Not just the big ones: libraries and versions chosen (**and the
> ones rejected**), safety rails, thresholds, timeouts, default values, data-model and architectural
> boundaries, workarounds for external-API quirks, anything deliberately **not** done, and corrections to
> earlier decisions.
>
> Read this before proposing a change to the stack. Every entry below was argued, and the losing options
> lost for reasons that may still be true. To overturn one, overturn the *reason* — not the conclusion.

## The rules

1. **Four questions, in this order.** (1) What we picked. (2) The alternatives, and why each lost — *a
   decision with no rejected alternative isn't a decision.* (3) Why — the actual constraint that forced
   it, not a restatement of the choice. (4) What would make us revisit it.
2. **Append-only, numbered `D1`, `D2`, …** Never renumber, never delete. To reverse a decision, add a
   **new** entry that supersedes it and mark the old one **"SUPERSEDED by Dn"**, leaving its text intact —
   the outdated reasoning is the valuable part; deleting it means remaking the same mistake.
3. **The entry ships in the SAME commit as the code it explains.** A decision recorded later is a decision
   reconstructed from memory.
4. **Cite the number wherever the decision is load-bearing** (e.g. `// see D14`), so a reader can find the
   argument behind the code.
5. **Write the reason, not the conclusion.** "We use X, not Y" is worthless; "X because \<constraint\>" is
   the entry.

---

## D1 — Adopted the append-only decision log

### What we picked

Every deliberate choice in this repo gets an entry in this file: numbered `D1`, `D2`, …, appended and
never rewritten, committed alongside the code it explains, and cited by number from any code comment
where the decision is load-bearing.

### The alternatives, and why each lost

| Option | Why it was attractive | Why it lost |
|---|---|---|
| **Keep reconstructing decisions from git archaeology** (the status quo) | Zero process, zero new files. The history is already captured — `git log -S`, `git blame`, the commit message, the PR thread. | **A diff records what changed and never why.** `git blame` lands on the line that *implements* the choice, not the argument that produced it. Worse, the rejected alternatives leave no trace at all: they were never committed, so there is no diff, no blame, no branch to recover them from. The single most valuable part of a decision is the only part git structurally cannot store. |
| **ADRs in `docs/adr/` only** | An established convention already in use at `~/dev/docs/adr/`, with a familiar template. | ADRs are ceremony-heavy, so in practice they get written only for architecture-scale calls. The decisions that actually burn us are small — a timeout, a retry cap, a threshold, a default that turns out load-bearing — and nobody opens a new ADR file for a magic number. Complementary, not a substitute: ADRs stay for cross-service architecture; this log catches everything else. |
| **Explain the reasoning in an inline code comment at the call site** | The reason lives exactly where a reader needs it, with no lookup. | A comment dies with the line it annotates. Refactor or delete the code and the argument vanishes — including the record that an alternative was ever weighed. Comments now *cite* entries (`see D1`) instead of carrying them, so the reasoning outlives any particular implementation. |
| **Explain it in the commit message** | Free, and already tied to the exact change. | Commit messages are write-once and effectively unsearchable at the granularity that matters; nobody greps 4,000 commits to learn why a timeout is 8s. They also cannot be amended when a decision is later superseded, so there is no way to link the reversal back to the original reasoning. |

### Why

This repo is built across many disconnected sessions — largely by LLM agents that carry **no memory
between sessions**, and by a maintainer returning to the code months later. Both parties read the code;
neither can see the argument that produced it.

The forcing constraint is narrow and specific: **rejected alternatives have no other storage location.**
Source code holds only what was chosen. Every other artifact — diffs, blame, tests, comments — is derived
from what was chosen. The option that was seriously considered and correctly ruled out exists nowhere
except in someone's head at the moment of choosing, and it is exactly the thing a future session needs in
order to stop re-proposing it. If it isn't written down then, it is not recoverable later at any price.

The cost is a few minutes per decision. The cost of the alternative is re-litigating settled questions
indefinitely, and periodically "fixing" something back into a state that was already tried and rejected.

### What would make us revisit it

- **Entries degenerate into a changelog** — a filled-in "What we picked" with an empty, hand-waved, or
  circular "alternatives" section. That means the log is being fed by obligation rather than judgment; it
  is then costing more than it returns and the format needs rethinking, not more entries.
- **Tooling appears that captures rejected alternatives at commit time** as reliably as a human writing
  them down. Then this file becomes generated rather than hand-maintained.
- **The log stops being read.** If a decision recorded here gets re-litigated anyway because nobody looked,
  the problem is discoverability, not capture — the fix would be surfacing entries at the call site
  (rule 4) more aggressively, not abandoning the log.

> Note on where this rule lives: `~/dev/CLAUDE.md` carries it for any agent session running under `~/dev`
> (Claude Code auto-loads `CLAUDE.md` from every ancestor directory). This preamble carries it for
> standalone clones of this repo, where that parent file is not an ancestor.
