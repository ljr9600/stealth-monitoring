#!/usr/bin/env python3
"""Set a work item's priority or status, safely (D373).

WHY A SCRIPT. The owner asked "how can I prioritise these now?" and the answer
was: hand-edit YAML front matter in the right file. That is a wall -- it is the
same wall a new person hits on day one, and the reason D370 said the four
missing fields should be added before the item count grows.

It edits ONE field, validates the value, re-renders the HTML, and stops. It does
not commit: what changed should be visible in `git diff` before it is recorded.

    python3 scripts/wi.py                       # list, grouped, with priorities
    python3 scripts/wi.py HOST-007 p1           # set priority (P1 highest, P4 lowest)
    python3 scripts/wi.py DOCS-011 blocked      # set status (open|blocked|closed)
    python3 scripts/wi.py HOST-007 p1 blocked
    python3 scripts/wi.py CP-002 blocked-by CP-003   # record a dependency
    python3 scripts/wi.py CP-004 8h                  # estimate, in HOURS (or 2-6h)
    python3 scripts/wi.py reopen HOST-007 "why the close did not hold"
    python3 scripts/wi.py words                      # the area and tag lists (KIT-060)
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import datetime
import pathlib
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from doc_kit import (ID_RE, PREFIX_RE, PREFIX_RULE, check_word, current_actor, load_config,  # noqa: E402
                     load_vocab, item_state, valid_prefix, work_branches)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = load_config(ROOT)
TICKETS = ROOT / CFG.get("tickets_dir", "docs/tickets")
PRIOS = {"p1", "p2", "p3", "p4"}
EST = __import__("re").compile(r"^\d{1,3}(-\d{1,3})?h$")
# Status is OPEN | BLOCKED | CLOSED — nothing else (standard §5). "In progress"
# is DERIVED (an open item with commits naming it), never declared.
STATES = {"open", "blocked", "closed"}
ORDER = {"p1": 0, "p2": 1, "p3": 2, "p4": 3}
DEFAULT = "p3"   # absent means normal, never "unimportant"
REOPEN_HEADER = "## Reopen history"
# One entry per reopen, append-only. The count in `reopened:` must equal the number
# of these, and check-ticket-status.py refuses the pair when it does not (KIT-054).
REOPEN_ENTRY = re.compile(r"^- \*\*Reopen (\d+)\*\*", re.M)


def find(item_id: str) -> pathlib.Path | None:
    hits = sorted(TICKETS.rglob(f"{item_id.upper()}-*.md"))
    return hits[0] if hits else None


def field(text: str, key: str) -> str:
    # [ \t] not \s: under re.M, \s* crosses the newline and an EMPTY value reads
    # the NEXT line's key as the value (KIT-003).
    m = re.search(rf"^{key}:[ \t]*(.*?)[ \t]*$", text, re.M)
    return m.group(1) if m else ""


def set_field(path: pathlib.Path, key: str, value: str) -> str:
    text = path.read_text()
    head, sep, rest = text.partition("\n---\n")
    if not sep:
        return f"{path.name}: no front matter"
    old = field(head, key)
    if old:
        head = re.sub(rf"^{key}:.*$", f"{key}:".ljust(12) + value, head, count=1, flags=re.M)
    else:
        # Keep it inside the block, after `status:` so related fields sit together.
        head = re.sub(r"^(status:.*)$", lambda m: m.group(1) + "\n" + f"{key}:".ljust(12) + value,
                      head, count=1, flags=re.M)
    path.write_text(head + sep + rest)
    return f"  {path.name}: {key} {old or '(unset)'} -> {value}"


def listing() -> int:
    rows = []
    branches = work_branches(ROOT)
    for md in sorted(TICKETS.glob("*.md")):
        t = md.read_text()
        state = item_state(field(t, "status"), field(t, "started"), field(t, "id") in branches)  # D20
        n = field(t, "reopened").strip()
        title = field(t, "title")
        if n.isdigit() and int(n) > 0:   # a returning item is not a fresh one (KIT-054)
            title = f"[reopened x{n}] {title}"
        rows.append((field(t, "priority").lower() or DEFAULT, state,
                     field(t, "id"), field(t, "type"), title,
                     field(t, "estimate") or "—"))
    for group, label in (("in-progress", "IN PROGRESS"), ("blocked", "BLOCKED"), ("queued", "QUEUED")):
        sel = [r for r in rows if r[1] == group]
        if not sel:
            continue
        print(f"\n{label}")
        for pr, st, i, ty, title, est in sorted(sel, key=lambda r: ORDER.get(r[0], 1)):
            print(f"  {pr.upper():<3} {est:<6} {i:<10} {ty:<6} {title[:64]}")
    print("\nP1 drop everything · P2 next · P3 normal (default) · P4 someday")
    print("set one with:  python3 scripts/wi.py <ID> <p1|p2|p3|p4> [open|blocked|closed]")
    return 0


def _append_section(text: str, header: str, entry: str) -> str:
    """Add `entry` under `header`, creating the section at the end if absent.

    The section is append-only: a new entry goes AFTER every existing one, and no
    existing line is touched. `## Update Log` (KIT-050) is kept last when present,
    so the reopen history sits with the contract rather than inside the commit log.
    """
    lines = text.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
    except StopIteration:
        tail = next((i for i, ln in enumerate(lines) if ln.strip() == "## Update Log"), None)
        block = ["", header, "", f"- {entry}"]
        if tail is None:
            return text.rstrip("\n") + "\n" + "\n".join(block) + "\n"
        return "\n".join(lines[:tail] + block[1:] + [""] + lines[tail:])
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    last = max((i for i in range(start + 1, end) if lines[i].startswith("- ")), default=None)
    if last is None:   # no entries yet — sit after the template's explanatory comment
        last = max((i for i in range(start + 1, end) if lines[i].strip()), default=start)
    return "\n".join(lines[:last + 1] + [f"- {entry}"] + lines[last + 1:])


def reopen(args: list[str]) -> int:
    """wi.py reopen <ID> "<reason>" — reverse a close, on the record (KIT-054, D24).

    A close is the one lifecycle event with no reviewer, so reversing one must leave
    evidence: who closed it, who reversed that, when, why, and how many times it has
    happened. The superseded `closed:`/`closer:` move into `## Reopen history` and are
    cleared from the front matter, so the item stops claiming to be open and closed at
    once and the NEXT close can stamp itself truthfully (stamp() only writes an empty
    field). Nothing is destroyed — the old values live in the history entry.
    """
    if len(args) < 2 or not args[1].strip():
        print('usage: wi.py reopen <ID> "<why the close did not hold>"', file=sys.stderr)
        return 2
    item = args[0].upper()
    path = find(item)
    if not path:
        print(f"no work item {item}", file=sys.stderr)
        return 1
    text = path.read_text()
    status = field(text, "status").upper()
    if status != "CLOSED":
        print(f"{item} is {status or 'unset'}, not CLOSED — nothing to reopen.\n"
              f"  To change status without reversing a close: wi.py {item} open",
              file=sys.stderr)
        return 2

    reason = " ".join(args[1:]).strip()
    was_closed = field(text, "closed").strip() or "an unstamped close"
    was_closer = field(text, "closer").strip() or "an unrecorded actor"
    count = field(text, "reopened").strip()
    n = (int(count) if count.isdigit() else 0) + 1
    now = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    actor = current_actor(ROOT)

    head, sep, body = text.partition("\n---\n")
    entry = (f"**Reopen {n}** — {now} — reopened by {actor} — "
             f"reverses the close of {was_closed} by {was_closer} — {reason}")
    body = _append_section(body, REOPEN_HEADER, entry)
    path.write_text(head + sep + body)

    # Front matter, in place: the item is open again and carries no close.
    set_field(path, "status", "OPEN")
    set_field(path, "reopened", str(n))
    for f in ("closed", "closer"):
        text = path.read_text()
        path.write_text(re.sub(rf"^{f}:.*$", f"{f}:", text, count=1, flags=re.M))

    live = TICKETS / path.name
    if path != live:
        subprocess.run(["git", "-C", str(ROOT), "mv", str(path), str(live)], check=False)
        if path.exists():           # not tracked by git, or git mv declined — move it anyway
            path.rename(live)
        for ext in (".html",):      # the rendered sibling follows its source
            sib = path.with_suffix(ext)
            dest = live.with_suffix(ext)
            if not sib.exists():
                continue
            # A stale rendering may already sit at the destination — an earlier close that
            # moved the .md and left the .html behind (the drift HCEXP-002..005 cleaned up).
            # Plain `git mv` refuses that with `fatal: destination exists` and the run
            # continues anyway, so use -f and stage both sides (KIT-055).
            r = subprocess.run(["git", "-C", str(ROOT), "mv", "-f", str(sib), str(dest)],
                               capture_output=True, check=False)
            if sib.exists():        # untracked, or git declined — move it regardless
                sib.replace(dest)
            if r.returncode != 0:   # keep the index coherent when git did not do the move
                subprocess.run(["git", "-C", str(ROOT), "add", "--all", "--",
                                str(sib), str(dest)], check=False)
    print(f"  {live.name}: CLOSED -> OPEN, reopened {n}")
    print(f"  superseded close ({was_closed} by {was_closer}) recorded in {REOPEN_HEADER}")
    print(f"  commit it on a {field(live.read_text(), 'type').lower()} branch for {item}")
    return 0


def new(args: list[str]) -> int:
    """wi.py new <ID> <TYPE> "<title>" [--area X] [--epic ID] — a ticket from
    the template, contract-valid by construction, ready to commit on the default
    branch. Refuses an undeclared prefix, a duplicate id, a type the repo does not
    use, and an EPIC in a repo whose epics live in <scope>-epics (D4)."""
    import datetime
    if len(args) < 3:
        print('usage: wi.py new <ID> <TYPE> "<title>" [--area X] [--tags a,b] [--epic ID]', file=sys.stderr)
        return 2
    wid, typ, title = args[0].upper(), args[1].upper(), args[2]
    area, epic, tags, rest = "", "", [], args[3:]
    while rest:
        if rest[0] == "--area" and len(rest) > 1:
            area, rest = rest[1].strip().lower(), rest[2:]
        elif rest[0] == "--tags" and len(rest) > 1:
            tags, rest = [t for t in re.split(r"[,\s]+", rest[1].strip().lower()) if t], rest[2:]
        elif rest[0] == "--epic" and len(rest) > 1:
            epic, rest = rest[1].upper(), rest[2:]
        else:
            print(f"unknown argument {rest[0]}", file=sys.stderr)
            return 2
    types = set(CFG.get("work_item_types", "EPIC STORY TASK BUG SPIKE").split())
    if typ not in types:
        print(f"{typ} is not one of {sorted(types)} (docs/doc-kit.config work_item_types)", file=sys.stderr)
        return 2
    scope = CFG.get("epics_scope", "").strip()
    if typ == "EPIC" and scope:
        print(f"epics for this repo live in {scope}-epics — create it there (D4)", file=sys.stderr)
        return 2
    if not re.fullmatch(ID_RE, wid) or not valid_prefix(wid.split("-")[0]):
        print(f"{wid}: an id is PREFIX-NNN — the prefix is {PREFIX_RULE}; then a dash and "
              f"three digits (KIT-057)", file=sys.stderr)
        return 2
    if find(wid):
        print(f"{wid} already exists: {find(wid)}", file=sys.stderr)
        return 2
    # One creation per commit (KIT-004): the pre-commit status check reads the whole
    # tickets dir, so a second uncommitted ticket makes the first one's commit fail —
    # and a later commit then sweeps both files in under one subject.
    import subprocess
    pending = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", str(TICKETS.relative_to(ROOT))],
                             capture_output=True, text=True).stdout
    pending = [l[3:] for l in pending.splitlines() if l[:2] in ("??", "A ", "AM") and l.endswith(".md")]
    if pending:
        print(f"one ticket creation per commit (KIT-004): commit {pending[0]} first, then create {wid}", file=sys.stderr)
        return 2
    deny = set(CFG.get("prefix_denylist", "").split())
    if wid.split("-")[0] in deny:
        print(f"prefix {wid.split('-')[0]} is reserved (prefix_denylist): it appears in prose with dash-digits, "
              f"so declaring it would turn ordinary text into citations. Pick another.", file=sys.stderr)
        return 2
    declared = set(CFG.get("prefixes", "").split())
    used = {m.group(1) for p in TICKETS.rglob("*.md") if (m := re.match("(" + PREFIX_RE + r")-\d{3}-", p.name))}
    # Retired prefixes (KIT-059): their ids live on, but nothing new is filed under them,
    # and numbering continues past them so an old and a new id never share a number.
    retired = set(CFG.get("retired_prefixes", "").split())
    if retired & declared:
        print(f"prefix {' '.join(sorted(retired & declared))} is both declared and retired in "
              f"docs/doc-kit.config — keep it in exactly one of `prefixes` / `retired_prefixes`",
              file=sys.stderr)
        return 2
    if retired:
        pfx, num = wid.split("-")[0], int(wid.split("-")[1])
        numbers: dict[str, int] = {}
        for p in TICKETS.rglob("*.md"):
            if m := re.match("(" + PREFIX_RE + r")-(\d{3})-", p.name):
                numbers[m.group(1)] = max(numbers.get(m.group(1), 0), int(m.group(2)))
        floor = max((numbers.get(r, 0) for r in retired), default=0)
        active = [p for p in CFG.get("prefixes", "").split() if p not in retired]
        use = pfx if pfx in active else (active[0] if active else "")
        nxt = f"{use}-{max(floor, numbers.get(use, 0)) + 1:03d}" if use else ""
        if pfx in retired:
            print(f"prefix {pfx} is retired (retired_prefixes): its tickets stay valid, but new ones "
                  f"are filed under {' / '.join(active) or '(no active prefix declared)'}"
                  + (f" — next: {nxt}" if nxt else ""), file=sys.stderr)
            return 2
        if floor and num <= floor:
            top = max((r for r in retired if numbers.get(r)), key=lambda r: numbers[r])
            print(f"{wid}: numbering continues past the retired prefixes ({top}-{floor:03d} is the "
                  f"highest), so an old and a new id never share a number — next: {nxt}",
                  file=sys.stderr)
            return 2
    if wid.split("-")[0] not in declared | used:
        print(f"prefix {wid.split('-')[0]} is not declared — add it to `prefixes =` in "
              f"docs/doc-kit.config (declared: {sorted(declared | used) or 'none'})", file=sys.stderr)
        return 2
    # Areas and tags come from the word lists when the project keeps them (KIT-060).
    vocab, unavailable = load_vocab(ROOT, CFG)
    if unavailable:
        print(f"cannot read the word lists: {unavailable.removeprefix('UNAVAILABLE: ')}", file=sys.stderr)
        return 2
    if "areas" in vocab:
        why = check_word(vocab["areas"][0], "areas", area)
        if why:
            print(f"--area: {why}\n  (the list: {vocab['areas'][1]}; see: python3 scripts/wi.py words)", file=sys.stderr)
            return 2
    area = area or "misc"          # no list: free text, as before KIT-060
    if "tags" in vocab:
        for tag in tags:
            why = check_word(vocab["tags"][0], "tags", tag)
            if why:
                print(f"--tags: {why}\n  (the list: {vocab['tags'][1]}; see: python3 scripts/wi.py words)", file=sys.stderr)
                return 2
    tdir = ROOT / CFG.get("templates_dir", "docs/templates")
    tpl = tdir / f"ticket-{typ}.md"
    if not tpl.exists():
        tpl = tdir / "ticket-STORY.md"
    if not tpl.exists():
        print(f"no template in {tdir} (ticket-{typ}.md or ticket-STORY.md)", file=sys.stderr)
        return 2
    text = (tpl.read_text(encoding="utf-8")
            .replace("{{ID}}", wid).replace("{{TITLE}}", title)
            .replace("{{AREA}}", area).replace("{{DATE}}", datetime.date.today().isoformat())
            .replace("{{ACTOR}}", current_actor(ROOT)))
    text = re.sub(r"^type:.*$", "type:".ljust(12) + typ, text, count=1, flags=re.M)
    tagline = ("tags:".ljust(12) + ", ".join(tags)) if tags else "tags:"
    if re.search(r"^tags:", text, re.M):
        text = re.sub(r"^tags:.*$", tagline, text, count=1, flags=re.M)
    elif tags:                     # a template from before KIT-060
        text = re.sub(r"^(area:.*)$", lambda m: m.group(1) + "\n" + tagline, text, count=1, flags=re.M)
    if epic:
        if re.search(r"^epic:", text, re.M):
            text = re.sub(r"^epic:.*$", "epic:".ljust(12) + epic, text, count=1, flags=re.M)
        else:
            text = re.sub(r"^(status:.*)$", r"\1\n" + "epic:".ljust(12) + epic, text, count=1, flags=re.M)
    elif typ != "EPIC":
        text = re.sub(r"^epic:\s*\n", "", text, count=1, flags=re.M)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "item"
    TICKETS.mkdir(parents=True, exist_ok=True)
    path = TICKETS / f"{wid}-{slug}.md"
    path.write_text(text, encoding="utf-8")
    rel = path.relative_to(ROOT)
    print(f"{rel}")
    print(f"  fill the contract, then on {CFG.get('default_branch', 'master')}:")
    print(f"  git add {rel} && git commit -m \"{wid}: file — {title} (ticket creation)\" && git push")
    return 0


def decision(args: list[str]) -> int:
    """wi.py decision new "<title>" | list — one FILE per decision (KIT-016), numbered one
    above the highest anywhere: local files, origin/<default_branch>, and a frozen legacy
    docs/DECISIONS.md. Several agents in several worktrees never touch a shared file."""
    import datetime
    import subprocess
    ddir = ROOT / CFG.get("decisions_dir", "docs/decisions")
    head = re.compile(r"^#{1,3}\s+D(\d+)\s*[—-]", re.M)
    nums: set[int] = set()
    for f in ddir.glob("D*.md") if ddir.is_dir() else []:
        m = re.match(r"D(\d+)-", f.name)
        if m:
            nums.add(int(m.group(1)))
    legacy = ROOT / "docs" / "DECISIONS.md"          # a hand-written log is frozen; numbering continues after it
    if legacy.is_file():
        nums |= {int(n) for n in head.findall(legacy.read_text(encoding="utf-8", errors="replace"))}
    ref = f"origin/{CFG.get('default_branch', 'master')}"
    ls = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", ref, "--",
                         str(ddir.relative_to(ROOT))], capture_output=True, text=True).stdout
    for line in ls.splitlines():
        m = re.match(r".*/D(\d+)-", line)
        if m:
            nums.add(int(m.group(1)))
    if not args or args[0] not in ("new", "list"):
        print('usage: wi.py decision new "<title>" | list', file=sys.stderr)
        return 2
    if args[0] == "list":
        for n in sorted(nums):
            hit = next(iter(ddir.glob(f"D{n}-*.md")), None) if ddir.is_dir() else None
            title = ""
            if hit:
                m = re.search(r"^#{1,3}\s+D\d+\s*[—-]\s*(.+?)\s*$", hit.read_text(encoding="utf-8"), re.M)
                title = m.group(1) if m else ""
            print(f"D{n}  {title}{'' if hit else '  (frozen log / origin only)'}")
        return 0
    if len(args) < 2 or not args[1].strip():
        print('usage: wi.py decision new "<title>"', file=sys.stderr)
        return 2
    title = args[1].strip()
    n = (max(nums) if nums else 0) + 1
    tpl = ROOT / CFG.get("templates_dir", "docs/templates") / "decision.md"
    if not tpl.exists():
        print(f"no template at {tpl}", file=sys.stderr)
        return 2
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "decision"
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / f"D{n}-{slug}.md"
    path.write_text(tpl.read_text(encoding="utf-8").replace("{{ID}}", f"D{n}").replace("{{TITLE}}", title)
                    .replace("{{DATE}}", datetime.date.today().isoformat()), encoding="utf-8")
    print(f"{path.relative_to(ROOT)}")
    print("  fill the four parts; commit it WITH the change it explains (the index regenerates itself)")
    return 0


def words() -> int:
    """The area and tag lists, as an agent needs them before filing (KIT-060)."""
    vocab, unavailable = load_vocab(ROOT, CFG)
    if unavailable:
        print(f"cannot read the word lists: {unavailable.removeprefix('UNAVAILABLE: ')}", file=sys.stderr)
        return 2
    for kind in ("areas", "tags"):
        if kind not in vocab:
            print(f"{kind.upper()}: no list — free text")
            continue
        rows, where = vocab[kind]
        print(f"{kind.upper()}  ({where})")
        for w, r in sorted(rows.items()):
            if r.get("merged_into"):
                print(f"  {w:<20} → merged into {r['merged_into']}")
                continue
            syn = f"   (also: {', '.join(r['synonyms'])})" if r["synonyms"] else ""
            print(f"  {w:<20} {r.get('meaning', '')}{syn}")
        print()
    print("Pick an existing word or synonym. Add one only when nothing fits: docs/TICKETING.md, 'Areas and tags'.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return listing()
    if args[0] == "new":
        return new(args[1:])
    if args[0] == "reopen":
        return reopen(args[1:])
    if args[0] == "words":
        return words()
    if args[0] == "decision":
        return decision(args[1:])
    if args[0] == "decisions":
        if len(args) < 2 or not find(args[1]):
            print("usage: wi.py decisions <ID>   (an existing item)", file=sys.stderr)
            return 2
        text = find(args[1]).read_text(encoding="utf-8")
        m = re.search(r"^## Decisions\s*$", text, re.M)
        heads = re.findall(r"^###\s+(D\d+)\s*[—-]\s*(.+?)\s*$", text[m.end():] if m else "", re.M)
        if not heads:
            print(f"{args[1].upper()}: no decisions recorded on it yet")
            return 0
        for did, title in heads:
            print(f"{did}  {title}")
        return 0
    path = find(args[0])
    if not path:
        print(f"no work item {args[0].upper()}", file=sys.stderr)
        return 1
    changes = []
    rest = args[1:]
    if len(rest) >= 2 and rest[0].lower() == "blocked-by":
        changes.append(set_field(path, "blocked-by", rest[1].upper()))
        rest = rest[2:]
    for a in rest:
        v = a.lower()
        if EST.match(v):
            changes.append(set_field(path, "estimate", v))
        elif v in PRIOS:
            # Upper-case on write. An earlier run stored "p2" verbatim beside
            # "P2" elsewhere -- harmless to read, and wrong the moment anything
            # groups or sorts on the raw string.
            changes.append(set_field(path, "priority", v.upper()))
        elif v in STATES:
            changes.append(set_field(path, "status", v.upper()))
        else:
            print(f"'{a}' is not an estimate (4h, 2-6h), a priority {sorted(PRIOS)} "
                  f"or a status {sorted(STATES)}",
                  file=sys.stderr)
            return 2
    if not changes:
        print("nothing to change", file=sys.stderr)
        return 2
    print("\n".join(changes))
    renderer = ROOT / "scripts" / "render-work-items.py"
    if renderer.exists():
        result = subprocess.run(["python3", str(renderer)], capture_output=True)
        if result.returncode == 0:
            print("  (html re-rendered; not committed — check `git diff` first)")
        else:
            print(f"  (render-work-items.py failed: {result.stderr.decode().strip()})",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
