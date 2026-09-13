---
id:         {{ID}}
title:      {{TITLE}}
type:       EPIC
status:     OPEN
priority:   P3
area:       {{AREA}}
opened:     {{DATE}}
milestone:
---

## Summary

Plain English: the body of work this epic groups, and why it matters. An EPIC is
a tracking parent — it gives a set of self-contained stories a progress bar and
makes their dependencies explicit. It is created on the default branch and never
branched; the work happens in its stories.

## Why this is an epic and not a story

A story owns one self-contained test. This cannot: name the reason (it spans
repositories / it has no single acceptance test / it is a dependency chain).

## Stories

Children are DERIVED from each story's own `epic: {{ID}}` field — never listed here
by hand (a hand-kept list eventually lies). Multi-repo: one story per repository it
changes, each in that repository's own tickets directory.

## Closes when

Every child story is closed. (A closed epic with an open child is flagged by the
scope board; nothing blocks it at commit time — that is the honest limit of
multi-repo.)

## Documentation impact

None. — OR — the cross-cutting documents this epic as a whole makes stale.

## Decisions

<!-- machine-filled: decisions recorded while this item was worked, copied verbatim from the decision files (KIT-017) -->
To cite a decision that DROVE this item, write `{{Dn}}` anywhere above; it expands here at commit.

## Background

