#!/usr/bin/env python3
"""Every work-item id cited in a live document must exist (D382).

WHY. `blocked-by:` is validated (D376), but a dependency is not how most items
get referenced — prose is. On 2026-09-02 `HOST-007` was closed with the sentence
"a separate problem, HOST-008" in it, and HOST-008 had never been filed. The
citation looked deliberate, the reader would have gone looking, and nothing
noticed. It was found only because the owner asked whether we should go back and
document what the day had turned up.

That is the failure this closes: **a bug named in passing and never filed**. The
naming makes it look tracked, which is worse than not mentioning it at all.

WHAT IS CHECKED, and what deliberately is not:

  checked   docs/tickets/**       what is being worked on
            docs/subsystems/**    what is true now
            CLAUDE.md, AGENTS.md  the rules
            bench/FINDINGS.md     the measurements

  NOT       docs/archive/**       frozen records; they cite ids that were real
                                  at the time, and rewriting them to satisfy a
                                  checker would corrupt the thing they are for
            docs/reports/**       generated, never edited
            docs/DECISIONS.md     append-only; a past entry cannot be corrected

The exclusions are the point: this checks documents that are meant to be TRUE
NOW. A frozen record is allowed to cite a world that has moved on.

    python3 scripts/check-item-references.py
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import pathlib
import re
import sys

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from doc_kit import epics_at_master, epics_root, load_config  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = load_config(ROOT)
TICKETS = ROOT / CFG.get("tickets_dir", "docs/tickets")
ID = re.compile(r"\b([A-Z]{2,10})-(\d{3})\b")


def _epics() -> dict:
    """Epics published by the scope's epics repo (D5), or {} when epics are local.
    A scoped repo whose epics clone is missing cannot check epic citations; say so
    once, on stderr, rather than pass them silently."""
    eroot, why = epics_root(ROOT, CFG)
    if eroot is None:
        if why != "local":
            print(f"item references: NOTE — {why}; epic citations not checked here", file=_sys.stderr)
        return {}
    return epics_at_master(eroot, CFG)


EPICS = _epics()

# Prefixes that name work items.
#
# DERIVED, NOT TYPED (DOCS-039). This used to be a hand-maintained set, and it
# had drifted in BOTH directions at once: it omitted GOV and ARCH, which name
# real items, and carried a prefix with no items at all. A dangling GOV citation
# was therefore invisible -- the checker read it as "not an id" and moved on.
#
# The set is now the union of two sources that cannot silently disagree with
# reality:
#   * every prefix that actually names a work item (the tickets themselves), so
#     a new prefix is covered the moment its first item is filed; and
#   * every prefix the project DECLARES in CLAUDE.md, so a prefix is covered
#     before its first item exists.
#
# Union is the safe direction: a prefix in either source means citations using it
# get checked. Retiring one is then a deliberate edit in both places, not an
# omission that quietly stops enforcement (AC-4).
def prefixes() -> set[str]:
    """The union of: every prefix a ticket here uses, every prefix
    docs/doc-kit.config DECLARES, and the scope's epic prefixes (D5)."""
    found = {m.group(1) for f in TICKETS.rglob("*.md")
             if (m := re.match(r"([A-Z]+)-\d{3}-", f.name))}
    found |= set(CFG.get("prefixes", "").split())
    found |= {i.split("-")[0] for i in EPICS}
    return found


def known() -> set[str]:
    ids = set()
    for md in TICKETS.rglob("*.md"):
        m = re.match(r"([A-Z]+-\d{3})-", md.name)
        if m:
            ids.add(m.group(1))
    return ids | set(EPICS)


def sources():
    """Every LIVE document (docs/doc-kit.config citation_sources, globs). History
    is exempt -- a frozen record cites what was true when it was written (D382)."""
    seen = set()
    for pat in CFG.get("citation_sources", "docs/**/*.md").split():
        for p in sorted(ROOT.glob(pat)):
            if p.is_file() and p not in seen:
                seen.add(p)
                yield p


# Frozen or generated documents (citation_exempt): a path prefix or a file.
EXEMPT = set(CFG.get("citation_exempt", "").split())


def exempt(rel: str) -> bool:
    return any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in EXEMPT)


def citable_text(path: pathlib.Path) -> str:
    """The document with FENCED BLOCKS REMOVED.

    A fenced block is an example or a transcript, not a citation -- the identity
    document draws a machine called `host-123` in one. Stripping fences is what
    lets the scan widen to every live document without inventing failures.
    """
    out, fenced = [], False
    for line in path.read_text(errors="replace").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def decisions_known() -> tuple[set[str], list[str]]:
    """Every decision id (files + a frozen legacy log), and the ids claimed twice (KIT-016)."""
    head = re.compile(r"^#{1,3}\s+(D\d+)\s*[—-]", re.M)
    ids: dict[str, list[str]] = {}
    ddir = ROOT / CFG.get("decisions_dir", "docs/decisions")
    for f in sorted(ddir.glob("D*.md")) if ddir.is_dir() else []:
        m = head.search(f.read_text(encoding="utf-8", errors="replace"))
        if m:
            ids.setdefault(m.group(1), []).append(f.relative_to(ROOT).as_posix())
    legacy = ROOT / "docs" / "DECISIONS.md"          # a hand-written log, frozen
    if legacy.is_file():
        for d in head.findall(legacy.read_text(encoding="utf-8", errors="replace")):
            ids.setdefault(d, []).append("docs/DECISIONS.md")
    dups = [f"{d} claimed by {', '.join(w)}" for d, w in ids.items() if len(w) > 1]
    return set(ids), dups


def main() -> int:
    have = known()
    dids, dups = decisions_known()
    if dups:
        print("decision ids claimed twice — a decision is ONE file, numbered once:", file=_sys.stderr)
        for d in dups:
            print(f"  {d}", file=_sys.stderr)
        return 1
    marker = re.compile(r"\{\{(D\d+)\}\}")
    dangling_d: dict[str, list[str]] = {}
    for src in sources():
        rel = src.relative_to(ROOT).as_posix()
        if exempt(rel):
            continue
        for d in marker.findall(citable_text(src)):
            if d not in dids:
                dangling_d.setdefault(d, []).append(rel)
    if dangling_d:
        print("decision citations that resolve to nothing ({{Dn}} must name a decision file):", file=_sys.stderr)
        for d, where in sorted(dangling_d.items()):
            print(f"  {d} — {', '.join(sorted(set(where))[:3])}", file=_sys.stderr)
        return 1
    known_prefixes = prefixes()
    dangling: dict[str, list[str]] = {}
    seen: set[pathlib.Path] = set()
    for src in sources():
        if src in seen:
            continue
        seen.add(src)
        if exempt(src.relative_to(ROOT).as_posix()):
            continue
        for pre, num in ID.findall(citable_text(src)):
            if pre not in known_prefixes:
                continue
            item = f"{pre}-{num}"
            if item not in have:
                dangling.setdefault(item, []).append(
                    str(src.relative_to(ROOT)))
    if not dangling:
        print(f"item references: every id cited in a live document exists "
              f"({len(have)} items, {len(known_prefixes)} prefixes)")
        return 0
    print("item-reference check FAILED:", file=sys.stderr)
    for item, where in sorted(dangling.items()):
        print(f"  {item} is cited but was never filed — {', '.join(sorted(set(where))[:3])}",
              file=sys.stderr)
    print(file=sys.stderr)
    print("  Naming an item makes it look tracked. File it, or stop citing it.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
