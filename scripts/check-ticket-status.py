#!/usr/bin/env python3
"""Work-item status invariants (GOV-001, D402; the DOCS-017 defect; GOV-002 branch field).

Three things, each of which has actually been wrong in this repository:

1. STATUS ENUM: `OPEN | BLOCKED | CLOSED` — nothing else. IN-PROGRESS is the
   field that put two closed stories on the board as in progress, undetected
   through 67 green gate runs (DOCS-017).
2. LOCATION matches STATUS, both directions: a file under closed/ says CLOSED;
   a live file does not.
3. CONTRACT: an open STORY or BUG opened on or after the standard (2026-09-03)
   has numbered acceptance criteria (AC-n) and a test-case section mapping them
   (TC-<ID>-nnn or a named gate-wired test). The grandfather clause that
   covered pre-standard items was deleted when DOCS-019 retrofitted them all.
4. ACTORS: newly actor-aware items carry creator/opener, and closed actor-aware
   items carry starter/closer. Historical items without creator are grandfathered.
5. SUMMARY: every open item begins with a plain-English '## Summary' as its
   FIRST section — readable by a product manager, above the technical
   contract (DOCS-023 template rule; DOCS-024 retrofitted the backlog).
6. THE STORY (--require-story): every open STORY/BUG carries a '## The story'
   section — the code-verified human narrative (actors, flow, break point,
   who uses the path, repercussions; standard section 6 item 2). Behind a flag
   until DOCS-026 retrofits the backlog (D403: never wire a knowingly-red
   check); DOCS-026's close adds the flag to the gate line.

7. REOPENS (KIT-054, D24): an OPEN or BLOCKED item carries no `closed:`/
   `closer:` -- a reopen that leaves them set makes the item claim both states
   at once, and because stamps are never overwritten the eventual re-close then
   credits the actor whose close did not hold. `reopened: n` must agree with the
   number of `## Reopen history` entries, so the count cannot drift from the
   evidence behind it.
8. TIME STAMPS (GOV-005): every item carries a machine-stamped `created:`;
   a closed item carries `started:` and `closed:` too; all in 'YYYY-MM-DD
   HH:MM ET' and in chronological order. The stamps are written by
   scripts/stamp-ticket-times.py at the moment of the event -- a missing or
   hand-mangled stamp is refused here so no time is ever typed from memory.

Exit 0 clean; exit 1 with the file AND the fix named, per failure.
Usage: check-ticket-status.py [--root DIR] [--require-story]
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import re
import sys
from datetime import datetime
from pathlib import Path

ENUM = {"OPEN", "BLOCKED", "CLOSED"}
REOPEN_ENTRY = re.compile(r"^- \*\*Reopen (\d+)\*\*", re.M)
REOPEN_CMD = 'python3 scripts/wi.py reopen <ID> "<why the close did not hold>"'
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} ET$")
BACKFILL = "python3 scripts/stamp-ticket-times.py --backfill"


def time_problems(name: str, fm: dict, is_closed: bool) -> list:
    """GOV-005: presence, format and order of the machine-written time stamps."""
    out = []
    need = ("created", "started", "closed") if is_closed else ("created",)
    for field in need:
        if not fm.get(field):
            out.append(
                f"{name}: missing machine-stamped '{field}:' (GOV-005).\n"
                f"    Times are stamped by the hook at the moment of the event, never typed.\n"
                f"    Fix: {BACKFILL}")
    parsed = {}
    for field in ("created", "started", "closed"):
        v = fm.get(field, "")
        if not v:
            continue
        if not STAMP.match(v):
            out.append(
                f"{name}: {field} '{v}' is not 'YYYY-MM-DD HH:MM ET' (GOV-005).\n"
                f"    Fix: delete the line and run {BACKFILL}")
        else:
            try:
                parsed[field] = datetime.strptime(v[:-3].rstrip(), "%Y-%m-%d %H:%M")
            except ValueError:  # right shape, impossible day or time: refuse, don't crash (KIT-070)
                out.append(
                    f"{name}: {field} '{v}' is not a real date and time (GOV-005).\n"
                    f"    Fix: delete the line and run {BACKFILL}")
    # Ordering: nothing may postdate the close. created<=started is NOT required —
    # three items (CP-002, SEC-001, DOCS-022) were honestly ticketed AFTER work
    # began, and backfill records history as it happened rather than as it should
    # have happened; the hooks make future items chronological on their own.
    for fa in ("created", "started"):
        if fa in parsed and "closed" in parsed and parsed[fa] > parsed["closed"]:
            out.append(
                f"{name}: times are out of order — {fa} '{fm[fa]}' is after closed '{fm['closed']}' (GOV-005).\n"
                f"    A stamp is never edited; delete the wrong one and run {BACKFILL}")
    return out


def reopen_problems(name: str, fm: dict, is_closed: bool) -> list:
    """KIT-054: a reopen leaves the item consistent, and its count matches its evidence."""
    out = []
    if not is_closed:
        for field in ("closed", "closer"):
            if fm.get(field, "").strip():
                out.append(
                    f"{name}: status is {fm.get('status', '?')} but '{field}:' is still set "
                    f"('{fm[field].strip()}').\n"
                    f"    An item cannot be open and closed at once, and a stamp is never\n"
                    f"    overwritten — leaving this here makes the NEXT close credit the actor\n"
                    f"    whose close did not hold (D24).\n"
                    f"    Fix: reopen it properly — {REOPEN_CMD}\n"
                    f"    (or, if it is closed, git mv it under closed/)")
    count = fm.get("reopened", "").strip()
    entries = REOPEN_ENTRY.findall(fm["_body"])
    if count and not count.isdigit():
        out.append(f"{name}: 'reopened:' must be a whole number (got '{count}').\n"
                   f"    It is written by {REOPEN_CMD}, never by hand.")
    elif (int(count) if count.isdigit() else 0) != len(entries):
        out.append(
            f"{name}: 'reopened: {count or 0}' but '## Reopen history' has {len(entries)} "
            f"entr{'y' if len(entries) == 1 else 'ies'}.\n"
            f"    The count is only useful while it matches the record behind it (KIT-054).\n"
            f"    Fix: reopen through {REOPEN_CMD} so both are written together.")
    return out


def actor_problems(name: str, fm: dict, is_closed: bool) -> list:
    """Actor-aware tickets retain who performed lifecycle events."""
    if not fm.get("creator"):
        return []  # tickets created before actor metadata was introduced
    out = []
    fields = ("creator", "opener") + (("starter", "closer") if is_closed else ())
    for field in fields:
        value = fm.get(field, "").strip()
        if not value:
            out.append(f"{name}: actor-aware item is missing '{field}:'.\n"
                       "    Set it to Codex, Claude, or Human; lifecycle hooks stamp future events.")
        elif value.lower() not in {"codex", "claude", "human"}:
            out.append(f"{name}: '{field}:' must be Codex, Claude, or Human (got '{value}').")
    return out


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
    fm["_body"] = text[m.end():] if m else text
    return fm


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    args = sys.argv[1:]
    require_story = "--require-story" in args
    if require_story:
        args.remove("--require-story")
    if len(args) == 2 and args[0] == "--root":
        root = Path(args[1])
    from doc_kit import load_config  # the portable config seam
    tickets = root / load_config(root).get("tickets_dir", "docs/tickets")
    problems = []

    for f in sorted(tickets.glob("*.md")):
        fm = front_matter(f)
        status = fm.get("status", "")
        problems.extend(time_problems(f.name, fm, is_closed=False))
        problems.extend(actor_problems(f.name, fm, is_closed=False))
        problems.extend(reopen_problems(f.name, fm, is_closed=False))
        if status not in ENUM:
            problems.append(
                f"{f.name}: status '{status}' is not one of OPEN|BLOCKED|CLOSED.\n"
                f"    In-progress is DERIVED (an OPEN item with commits naming it), never declared (D402).\n"
                f"    Fix: set 'status: OPEN' (or BLOCKED/CLOSED as true).")
        if status == "CLOSED":
            problems.append(
                f"{f.name}: says CLOSED but lives in docs/tickets/ — closing MOVES the file.\n"
                f"    Fix: git mv docs/tickets/{f.name} docs/tickets/closed/  (both .md and .html)")
        if "branch" in fm:
            problems.append(
                f"{f.name}: carries a 'branch:' field — deleted by GOV-002 (D401).\n"
                f"    The branch name is DERIVED from the item (story/{fm.get('id','ID')}); a declared\n"
                f"    copy of what git records eventually lies (it did, nine times).\n"
                f"    Fix: delete the line. (Closed items keep theirs as historical record.)")
        if status in ("OPEN", "BLOCKED"):
            first = re.search(r"^#{1,3}\s+(.+?)\s*$", fm["_body"], re.M)
            if not first or first.group(1) != "Summary":
                problems.append(
                    f"{f.name}: open item without a plain-English '## Summary' as its first section.\n"
                    f"    A product manager reads the summary; the contract below it is for engineers (DOCS-024).\n"
                    f"    Fix: add '## Summary' (2-3 sentences, no jargon/paths) directly under the front matter.")
        if (
            require_story
            and status in ("OPEN", "BLOCKED")
            and fm.get("type") in ("STORY", "BUG")
            and not re.search(r"^##\s+The story\s*$", fm["_body"], re.M)
        ):
            problems.append(
                f"{f.name}: open {fm.get('type')} without a '## The story' section.\n"
                f"    The Summary says WHAT; the story says WHO, HOW, and WHAT IT COSTS if unfixed —\n"
                f"    actors, the end-to-end flow, the break point, today's real users, repercussions,\n"
                f"    each claim verified in code (ENGINEERING_STANDARD.md section 6, DOCS-025).\n"
                f"    Fix: write it directly under '## Summary'.")
        if status in ("OPEN", "BLOCKED") and fm.get("type") in ("STORY", "BUG"):
            body = fm["_body"]
            if not re.search(r"^\s*(?:[-*]\s*)?(?:\*\*)?AC-\d+", body, re.M):
                problems.append(
                    f"{f.name}: open {fm.get('type')} with no numbered acceptance criteria (AC-1 ...).\n"
                    f"    Fix: add the contract sections — ENGINEERING_STANDARD.md section 6.")
            elif not re.search(r"^#+\s*Test cases", body, re.M | re.I):
                problems.append(
                    f"{f.name}: has acceptance criteria but no '## Test cases' section mapping them.\n"
                    f"    Fix: map each AC-n to a named test — ENGINEERING_STANDARD.md section 6.")

    closed = tickets / "closed"
    if closed.is_dir():
        for f in sorted(closed.glob("*.md")):
            fm = front_matter(f)
            status = fm.get("status", "")
            problems.extend(time_problems(f"closed/{f.name}", fm, is_closed=True))
            problems.extend(actor_problems(f"closed/{f.name}", fm, is_closed=True))
            problems.extend(reopen_problems(f"closed/{f.name}", fm, is_closed=True))
            if status != "CLOSED":
                problems.append(
                    f"closed/{f.name}: in closed/ but says 'status: {status}' — the board believes\n"
                    f"    the front matter and shows a finished item as live (the DOCS-017 defect).\n"
                    f"    Fix: set 'status: CLOSED' (a close is one commit: status, Closed section, move).")

    if problems:
        print(f"ticket-status check FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print("  " + p)
        return 1
    print("ticket-status: statuses valid and located, open contracts present, time stamps present and ordered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
