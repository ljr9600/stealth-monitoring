#!/usr/bin/env python3
"""Work-item SHAPE — epics and self-contained stories, enforced at creation (GOV-012, D468).

THE MODEL (portable). The AUTHOR declares a work item's `type`; the system never
infers it. An EPIC is a tracking/dependency parent — created on the default
branch, never branched. A STORY that belongs to an epic names its parent in an
`epic:` front-matter field. Stories are self-contained: independently workable
and testable, each owning its own test as an acceptance criterion — testing is
never its own ticket. This checker does NOT make the epic-vs-story judgement; it
VALIDATES the declaration. The forcing function for the judgement is the rule
that a story must have a self-contained test AC: if you cannot write one, that
is the signal the thing is an epic.

WHAT IT ENFORCES (all backward compatible — a pre-epic ticket with no EPIC and
no `epic:` passes unchanged, so adopting this never blocks existing work):
  * `type` is one of the configured set (default EPIC/STORY/TASK/BUG/SPIKE).
  * a STORY's `epic:`, if present, resolves to an existing EPIC (the dangling-
    parent bug — the same class as a doc-impact naming a file that isn't there).
  * a work item names at most ONE parent epic (no double-parenting).
  * an EPIC carries no `epic:` of its own (a parent has no parent here).

PORTABLE. Nothing here is project-specific. To adopt in another repository: copy
this script and set the values it reads from `docs/doc-kit.config` via
`doc_kit.load_config` — `tickets_dir` (already the doc-kit's seam), and
optionally `work_item_types` and `epic_field` to override the defaults below.
Wire it into the ticket-creation gate and `tests/<gate>` as a standalone check
(no args, exits non-zero naming the offending file).

    python3 scripts/check-ticket-shape.py         # exit 1 on the first bad shape
"""
from __future__ import annotations
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from doc_kit import load_config  # portable config seam (tickets_dir, ...)

# Defaults — overridable per project via docs/doc-kit.config.
DEFAULT_TYPES = "EPIC STORY TASK BUG SPIKE"
DEFAULT_EPIC_FIELD = "epic"


def front_matter(text: str) -> dict:
    """Parse the leading `--- ... ---` key: value block. No YAML dependency."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def parents(fm: dict, epic_field: str) -> list[str]:
    """The epic(s) a story names. A list so 'more than one' is detectable."""
    raw = fm.get(epic_field, "").strip()
    return [p for p in re.split(r"[,\s]+", raw) if p]


def main() -> int:
    cfg = load_config(ROOT)
    tickets = ROOT / cfg.get("tickets_dir", "docs/tickets")
    types = set(cfg.get("work_item_types", DEFAULT_TYPES).split())
    epic_field = cfg.get("epic_field", DEFAULT_EPIC_FIELD)

    # id -> type, over live AND closed items (a closed epic still parents).
    kind: dict[str, str] = {}
    items: list[tuple[Path, dict]] = []
    for md in sorted(tickets.rglob("*.md")):
        fm = front_matter(md.read_text(encoding="utf-8"))
        wid = fm.get("id", "").strip()
        if not wid:
            continue
        kind[wid] = fm.get("type", "").strip().upper()
        items.append((md, fm))

    errs: list[str] = []
    # Scoped epics (ticketing-template D4/D5): parents resolve in <scope>-epics, read at
    # origin/<default_branch>. A story's epic: that only exists unpushed, or on a
    # branch of that repo, does not exist yet — for anyone.
    from doc_kit import epics_at_master, epics_root
    eroot, why = epics_root(ROOT, cfg)
    scoped = bool(cfg.get("epics_scope", "").strip())
    if scoped:
        if eroot is None:
            print(f"ticket-shape check FAILED: {why}", file=sys.stderr)
            return 1
        remote = epics_at_master(eroot, cfg)
        for wid, e in remote.items():
            kind.setdefault(wid, e["type"])
        for md, fm in items:
            if fm.get("type", "").strip().upper() == "EPIC":
                errs.append(f"{md.relative_to(ROOT)}: is an EPIC, but this repo's epics live in "
                            f"{eroot} (epics_scope={cfg['epics_scope']}). Create it there.")

    epics = {wid for wid, t in kind.items() if t == "EPIC"}
    for md, fm in items:
        rel = md.relative_to(ROOT)
        wid = fm.get("id", "").strip()
        typ = fm.get("type", "").strip().upper()

        if typ and typ not in types:
            errs.append(f"{rel}: type '{typ}' is not one of {sorted(types)}.")

        ps = parents(fm, epic_field)
        if typ == "EPIC" and ps:
            errs.append(f"{rel}: an EPIC has no parent — remove its `{epic_field}:` "
                        f"({', '.join(ps)}). Epics are tracking parents.")
        if len(ps) > 1:
            errs.append(f"{rel}: names more than one parent epic ({', '.join(ps)}). "
                        f"A work item belongs to exactly one epic.")
        for p in ps:
            if typ == "EPIC":
                continue
            if p not in epics:
                if p in kind:
                    errs.append(f"{rel}: `{epic_field}: {p}` names {p}, which is a "
                                f"{kind[p] or 'non-EPIC'}, not an EPIC.")
                else:
                    errs.append(f"{rel}: `{epic_field}: {p}` names an epic that does not "
                                f"exist. Create the EPIC first, or fix the id.")

    if errs:
        print("ticket-shape check FAILED:", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        print("\n  An epic is a parent (type: EPIC, no `epic:`); a story names at most one\n"
              "  existing epic. Declare the type; the checker validates it.", file=sys.stderr)
        return 1

    n_epics = len(epics)
    n_children = sum(1 for _, fm in items if parents(fm, epic_field))
    print(f"ticket-shape: {len(items)} work items well-formed "
          f"({n_epics} epic(s), {n_children} with a parent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
