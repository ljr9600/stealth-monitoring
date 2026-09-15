---
id:         {{ID}}
title:      {{TITLE}}
type:       BUG
status:     OPEN
priority:   P2
area:       {{AREA}}
opened:     {{DATE}}
creator:    {{ACTOR}}
opener:     {{ACTOR}}
starter:
closer:
estimate:
found-in:
epic:
---

## Summary

Plain English: what goes wrong, for whom, and what it costs them. No paths, no jargon.

## The story

Who hits this, doing what; the flow up to the break; what they see; the repercussions
if it stays. Every claim verified against the code, not remembered.

## Reproduction

The committed script that FAILS today — the failure is the evidence the bug is real.
Path: `tests/<name>` (new). Command:

    <how the gate runs it>

## Objective

## Current behaviour

## Required behaviour

## Acceptance criteria

- **AC-1** — the reproduction passes.
- **AC-2** — the fix is gate-wired: the test that failed first now runs on every gate.

## Test cases

- **TC-{{ID}}-001** — covers AC-1/AC-2; run by: `<gate command>`

## Documentation impact

None. — OR — the documents whose current-state claims this bug proved wrong.

## Decisions

<!-- machine-filled: decisions recorded while this item was worked, copied verbatim from the decision files (KIT-017) -->
To cite a decision that DROVE this item, write `{{Dn}}` anywhere above; it expands here at commit.

## Background

## Update Log

<!-- machine-appended by scripts/stamp-ticket-times.py (KIT-050) on every commit that
     touches this file: timestamp, actor (creator/closer identity or whoever is
     current), and what changed. Append-only -- an existing line is never rewritten. -->
