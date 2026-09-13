#!/usr/bin/env python3
"""A work item's Documentation impact must be a definite, checkable claim (DOCS-040).

THE RULE (owner, 2026-09-04): closing an item updates the documents its change
made stale, in the same change. DOCS-038 measured how well that held -- 44% of
closes executed their stated impact in full -- and found the rule leaking in ways
no reviewer would catch by reading:

  1. A named document need not EXIST. Five items named documents that are not in
     this repository at all -- and four of those names (the deploy guide, the git
     workflow, the Mongo schema, the doc standards) belong to a DIFFERENT project
     under ~/dev, whose rules this repository explicitly does not follow. The
     statements were pattern-matched from another codebase's documentation map.
     Nothing noticed, because nothing looked.

  2. The statement may be CONDITIONAL. "the deploy guide, if it describes building
     as a manual-only step" defers the question instead of answering it. An impact
     that says *maybe* cannot be executed and cannot be checked; someone still has
     to go and look, and nobody does.

  3. A named document need not be TOUCHED. That is measured by
     scripts/audit-doc-impact.py, which reports the compliance rate.

"None" is a first-class answer. A checker-only or process change may legitimately
have no documentation impact, and this must never push an author into inventing
one -- an invented impact is worse than an honest none.
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import pathlib
import re
import sys

from doc_kit import load_config
from repo_files import tracked

ROOT = pathlib.Path(__file__).resolve().parent.parent
TICKETS = ROOT / load_config(ROOT).get("tickets_dir", "docs/tickets")

SECTION = re.compile(r"##\s*Documentation impact\s*\n(.*?)(?=\n##\s|\Z)", re.S | re.I)
# A path in backticks, or the target of a markdown link.
PATHS = (re.compile(r"`([^`\s]+\.(?:md|sql|go|rs|sh|py|toml))`"),
         re.compile(r"\]\(([^)\s]+\.md)\)"),
         # A bare filename in prose. Included because an item that writes
         # "CLAUDE.md, ENGINEERING_STANDARD.md" has named its documents perfectly
         # clearly, and a checker that only sees backticks would call that empty.
         # The lookbehind excludes a preceding HYPHEN as well as word characters: without
         # it, "stamp-ticket-times.py" matched from "ticket-times.py" and the checker
         # reported a phantom file that was really the tail of a real one.
         re.compile(r"(?<![`(/\w-])([A-Za-z][\w./-]*\.(?:md|sql|sh|py))\b"))
# Words that make a claim conditional rather than decided.
HEDGES = re.compile(r"\b(if|maybe|perhaps|possibly|might need|may need|should it)\b", re.I)
# An explicit "this document does not exist yet, the item creates it".
NEW = re.compile(r"\(new\)", re.I)
NONE = re.compile(r"\bnone\b", re.I)


def classify(named: str) -> str:
    """Is the named document "tracked", "untracked" (present on disk but not in
    the repository — DOCS-041), or "missing"? Accepts a repo-relative or
    docs-relative form."""
    named = named.strip().lstrip("./")
    while named.startswith("../"):
        named = named[3:]
    cands = [ROOT / named]
    # Written relative to docs/ or to the item's own directory.
    for base in (ROOT / "docs", ROOT / "docs" / "subsystems", ROOT / "docs" / "tickets"):
        cands += [base / named, base / pathlib.PurePath(named).name]
    if any(tracked(c, ROOT) for c in cands):
        return "tracked"
    # A bare filename tracked anywhere in the tree (excluding history).
    name = pathlib.PurePath(named).name
    for hit in ROOT.rglob(name):
        rel = hit.relative_to(ROOT).as_posix()
        if rel.startswith((".git/", "docs/archive/")):
            continue
        if tracked(hit, ROOT):
            return "tracked"
    return "untracked" if any(c.exists() for c in cands) else "missing"


def sentences(text: str):
    for part in re.split(r"(?<=[.;])\s+", text):
        if part.strip():
            yield " ".join(part.split())


def main() -> int:
    problems: list[str] = []
    checked = 0

    for f in sorted(list(TICKETS.glob("*.md")) + list((TICKETS / "closed").glob("*.md"))):
        m = SECTION.search(f.read_text(errors="replace"))
        if not m:
            continue
        body = m.group(1)
        checked += 1
        rel = f.relative_to(ROOT).as_posix()

        named = []
        for pat in PATHS:
            named += pat.findall(body)

        # 1. every named document exists IN THE REPOSITORY, unless marked (new).
        for path in named:
            state = classify(path)
            if state == "tracked":
                continue
            line = next((s for s in sentences(body) if path in s), body)
            if NEW.search(line):
                continue
            if state == "untracked":
                problems.append(
                    f"{rel}: names `{path}`, which exists locally but is UNTRACKED — "
                    f"the impact cannot be executed for anyone who clones. Commit or "
                    f"stage it (in the same change), or write `{path}` (new).")
            else:
                problems.append(
                    f"{rel}: names `{path}`, which does not exist in this repository. "
                    f"If the item creates it, write `{path}` (new); if it belongs to "
                    f"another project, do not name it here.")

        # 2. a named document's inclusion must not be conditional.
        for s in sentences(body):
            if not any(p in s for pat in PATHS for p in pat.findall(s)):
                continue
            hedge = HEDGES.search(s)
            if hedge:
                problems.append(
                    f"{rel}: documentation impact is conditional "
                    f"(\"{hedge.group(0)}\") — resolve it now, not at close: "
                    f"{s[:90]}...")

        # 3. saying nothing at all is not the same as saying none.
        if not named and not NONE.search(body) and len(body.split()) < 4:
            problems.append(
                f"{rel}: the Documentation impact section is empty. Name the "
                f"documents, or say None and why.")

    if problems:
        print("Documentation-impact statements that cannot be executed or checked:",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(file=sys.stderr)
        print("  An impact statement is a promise the close has to keep. A phantom "
              "path or a\n  'maybe' cannot be kept, and cannot be checked.", file=sys.stderr)
        return 1

    print(f"doc impact: {checked} statements are definite and resolvable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
