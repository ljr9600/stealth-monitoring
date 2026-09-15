---
id:         {{ID}}
title:      {{TITLE}}
type:       STORY
status:     OPEN
priority:   P3
area:       {{AREA}}
tags:
opened:     {{DATE}}
creator:    {{ACTOR}}
opener:     {{ACTOR}}
starter:
closer:
estimate:
milestone:
epic:
---

## Summary

Two or three sentences of PLAIN ENGLISH for a reader who is not an engineer:
what is wrong or wanted, and why it matters. No jargon, no file paths.

## The story

The full human narrative. Name the ACTORS as people and machines doing things;
walk the FLOW end to end; say WHERE IT BREAKS; name who USES the path today; state
the REPERCUSSIONS if this stays unfixed. Every factual claim verified against the
code at writing time — grep it, never copy an older document's summary.

## Objective

One sentence: the engineering goal.

## Current behaviour

## Required behaviour

## Scope / Out of scope

## Acceptance criteria

- **AC-1** — observable, able to fail. Never "works correctly", "robust", "handles errors".
- **AC-2** —

## Test cases

- **TC-{{ID}}-001** — covers AC-1; run by: `<the gate command that executes it>`
- **TC-{{ID}}-002** — covers AC-2; run by:

## Documentation impact

None. — OR — name every current-state document this change makes stale; each must
exist (or be marked `(new)`), unconditionally. The close EXECUTES this list in the
same commit as the close; declaring it is not doing it.

## Decisions

<!-- machine-filled: decisions recorded while this item was worked, copied verbatim from the decision files (KIT-017) -->
To cite a decision that DROVE this item, write `{{Dn}}` anywhere above; it expands here at commit.

## Background

## Reopen history

<!-- machine-appended by `python3 scripts/wi.py reopen <ID> "<reason>"` (KIT-054): one entry
     per reopen — when, who reopened it, the close it reverses and that close's actor, and
     why it did not hold. Append-only; `reopened:` in the front matter counts these. -->

## Update Log

<!-- machine-appended by scripts/stamp-ticket-times.py (KIT-050) on every commit that
     touches this file: timestamp, actor (creator/closer identity or whoever is
     current), and what changed. Append-only -- an existing line is never rewritten. -->
