#!/usr/bin/env python3
"""A decision travels into the ticket it drove, in full (KIT-017). Runs from pre-commit.

Decisions are immutable (a reversal is a NEW file marking the old one superseded), so
copying one verbatim into a ticket is safe — the copy can never drift. Two moments:

  1. A decision recorded WHILE WORKING an item: a new file under <decisions_dir> is
     staged in a commit on the item's <type>/<ID> branch (or beside a staged epic on
     the default branch). Its full text is appended under the ticket's `## Decisions`
     section and the ticket is staged into the same commit.
  2. A decision that DROVE the item: the author writes `{{Dn}}` in the ticket. At commit
     the marker becomes the bare id in place, and the full text is appended under
     `## Decisions` (once). A `{{Dn}}` naming no decision refuses the commit.

A merge commit copies nothing by arrival — a decision that reaches a branch by merge was
recorded, and copied, on the branch that made it (KIT-030).

Nothing is typed twice, nothing is remembered: the machine writes it at the moment it
applies, the way it stamps times. `python3 scripts/wi.py decisions <ID>` lists what a
ticket carries.
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_kit import find_decision, load_config  # noqa: E402

FILLED = "<!-- machine-filled: decisions recorded while this item was worked, copied verbatim from the decision files (KIT-017) -->"
MARKER = re.compile(r"\{\{(D\d+)\}\}")
ID_RE = r"[A-Z]{2,10}-\d{3}"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout


def demote(did: str, title: str, body: str) -> str:
    """The decision as a sub-section of the ticket: `### Dn — title` then the body verbatim."""
    return f"### {did} — {title}\n\n{body.strip()}\n"


def carries(ticket_text: str, did: str) -> bool:
    return re.search(rf"^###\s+{re.escape(did)}\s*[—-]", ticket_text, re.M) is not None


def append_decision(ticket: Path, block: str) -> None:
    s = ticket.read_text(encoding="utf-8")
    if not re.search(r"^## Decisions\s*$", s, re.M):
        s = s.rstrip("\n") + "\n\n## Decisions\n\n" + FILLED + "\n"
    m = re.search(r"^## Decisions\s*$", s, re.M)
    rest = s[m.end():]
    nxt = re.search(r"^## ", rest, re.M)          # the next top-level section, or EOF
    end = m.end() + (nxt.start() if nxt else len(rest))
    head, tail = s[:end].rstrip("\n"), s[end:]
    s = head + "\n\n" + block.rstrip("\n") + "\n" + ("\n" + tail if tail.strip() else "\n")
    ticket.write_text(s, encoding="utf-8")


def main() -> int:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip() or ".")
    cfg = load_config(root)
    if not cfg.get("decisions_frozen", "").strip() and (root / "docs" / "DECISIONS.md").is_file():
        cfg["decisions_frozen"] = "docs/DECISIONS.md"   # a hand-written legacy log resolves too
    tdir = cfg.get("tickets_dir", "docs/tickets").strip("/")
    ddir = cfg.get("decisions_dir", "docs/decisions").strip("/")
    types = "|".join(t.lower() for t in cfg.get("work_item_types", "EPIC STORY TASK BUG SPIKE").split()
                     if t.upper() != "EPIC") or "story|bug|task|spike"

    staged = git(root, "diff", "--cached", "--name-only", "--diff-filter=AMR").splitlines()
    added = git(root, "diff", "--cached", "--name-only", "--diff-filter=A").splitlines()
    # A merge commit brings decisions recorded on OTHER branches, each already copied into
    # its own ticket where it was made. They were not recorded while working THIS item, so
    # moment 1 does not apply to a merge (KIT-030); moment 2 (markers) still does.
    mh = git(root, "rev-parse", "--git-path", "MERGE_HEAD").strip()
    merging = bool(mh) and (Path(mh) if Path(mh).is_absolute() else root / mh).exists()
    new_decisions = [] if merging else [
        p for p in added if p.startswith(ddir + "/") and re.match(r"D\d+-.*\.md$", Path(p).name)]

    # the tickets this commit is ABOUT: the branch's item, plus every staged ticket
    targets: dict[Path, bool] = {}     # path -> is it staged (marker expansion applies)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    bm = re.fullmatch(rf"(?:{types})/({ID_RE})", branch)
    if bm:
        for hit in sorted((root / tdir).glob(f"{bm.group(1)}-*.md")):
            targets[hit] = False
    for p in staged:
        if re.fullmatch(rf"{re.escape(tdir)}/(closed/)?{ID_RE}-[^/]+\.md", p) and (root / p).exists():
            targets[root / p] = True

    changed: set[Path] = set()
    for t in targets:
        # 1. decisions recorded in this commit -> the ticket, verbatim
        for p in new_decisions:
            did = re.match(r"(D\d+)-", Path(p).name).group(1)
            if carries(t.read_text(encoding="utf-8"), did):
                continue
            text = (root / p).read_text(encoding="utf-8")
            hm = re.search(rf"^#{{1,3}}\s+{re.escape(did)}\s*[—-]\s*(.+?)\s*$", text, re.M)
            title = hm.group(1) if hm else did
            body = text[hm.end():] if hm else text
            append_decision(t, demote(did, title, body))
            changed.add(t)
        # 2. {{Dn}} markers in a staged ticket -> id in place + full text under Decisions
        if targets[t]:
            s = t.read_text(encoding="utf-8")
            ids = list(dict.fromkeys(MARKER.findall(s)))
            for did in ids:
                d = find_decision(root, cfg, did)
                if d is None:
                    print(f"decisions-into-tickets: {t.relative_to(root)} cites {{{{{did}}}}} but no decision "
                          f"{did} exists in {ddir}/ (or the frozen log). Record it first: "
                          f"python3 scripts/wi.py decision new \"<title>\"", file=sys.stderr)
                    return 1
                s = s.replace("{{" + did + "}}", did)
                t.write_text(s, encoding="utf-8")
                if not carries(s, did):
                    append_decision(t, demote(did, d.title, d.body))
                changed.add(t)
    for t in sorted(changed):
        git(root, "add", str(t))
        print(f"decisions-into-tickets: {t.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
