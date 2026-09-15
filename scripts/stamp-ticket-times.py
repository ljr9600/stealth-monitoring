#!/usr/bin/env python3
"""Stamp created/started/closed times into work items as each event happens (GOV-005, D415).

WHY A MACHINE WRITES THESE. The owner ruled (2026-09-04) that every ticket must
permanently record when it was created, when work started, and when it closed --
"not from memory". A time typed by hand at closing time is a recollection; the
moment the pre-commit hook runs IS the event, so the hook writes the stamp. The
gate's check-ticket-status.py refuses a ticket missing one, so the pair gives
the guarantee: the machine writes it, the machine checks it, nobody remembers
anything.

THE THREE MOMENTS, precisely:

  created:   this commit ADDS a new file under docs/tickets/ -- the creation
             commit is the creation moment.
  started:   this commit is on the item's <type>/<ID> branch and the item has
             no started: yet -- the first work commit is when work began, even
             though it usually does not touch the ticket file itself (the
             stamper stages the stamped ticket into the same commit).
  closed:    this commit stages the ticket under docs/tickets/closed/ -- the
             close commit is the close moment. A close also REPAIRS a missing
             started: (GOV-006): an item filed on another item's branch never
             saw a first commit on its own branch, and before this it hit the
             gate's refusal after a full run, fixable only by a manual
             --backfill -- three times in one day. The value is derived from
             git exactly as --backfill derives it, never invented.

Format: 'YYYY-MM-DD HH:MM ET' (the global rule: all times in ET). Stamps are
append-only facts: an existing stamp is NEVER overwritten, by any mode.

  python3 scripts/stamp-ticket-times.py                # from pre-commit
  python3 scripts/stamp-ticket-times.py --backfill     # fill gaps from git

--backfill derives missing stamps from git history, which recorded the real
times all along: created = the earliest commit that added the file, traced
through renames with --follow (the docs/work -> docs/tickets rename of
876c7af would otherwise read as sixteen tickets born at the same minute);
started = the earliest commit whose SUBJECT names the id other than the
creation commit (ticket-timing.py's convention; for a CLOSED item it falls
back to created when the creation commit was the only work — an OPEN item
with no work commit is left unstamped so the hook can record the real moment
later); closed = the latest commit that added the closed/ path (latest, not
earliest, so a reopened-and-reclosed item shows its final close). HEAD's
history only, never --all: abandoned branch lines carry rebased-away closes
at wrong times (origin/bug/VIEW-007 taught this). Backfill is evidence, not
reconstruction.
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_kit import ID_RE, current_actor, load_config  # noqa: E402  (the portable config seam)

ET = ZoneInfo("America/New_York")
FMT = "%Y-%m-%d %H:%M ET"
LIFECYCLE_FIELDS = ("opened", "created", "creator", "opener", "started", "starter",
                    "closed", "closer")
UPDATE_LOG_HEADER = "## Update Log"


def _cfg(root: Path) -> tuple[str, str]:
    """(tickets_dir, branch-type alternation) from docs/doc-kit.config."""
    cfg = load_config(root)
    tdir = cfg.get("tickets_dir", "docs/tickets").strip("/")
    types = [t.lower() for t in cfg.get("work_item_types", "EPIC STORY TASK BUG SPIKE").split()
             if t.upper() != "EPIC"]
    return tdir, "|".join(types) or "story|bug|task|spike"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True).stdout


def now_et() -> str:
    return datetime.now(ET).strftime(FMT)


def to_et(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ET).strftime(FMT)


def stamp(path: Path, field: str, value: str) -> bool:
    """Insert 'field: value' into the front matter. False if present already.

    Insertion keeps a stable order (opened, created, started, closed) so the
    fields read chronologically; alignment matches the existing style.
    """
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    if not m:
        return False
    fm = m.group(1)
    line = f"{field}:".ljust(12) + value + "\n"
    existing = re.search(rf"^{field}:[ \t]*([^\n]*)\n", fm, re.M)
    if existing:
        if existing.group(1).strip():
            return False
        new_fm = fm[:existing.start()] + line + fm[existing.end():]
        path.write_text("---\n" + new_fm + "---\n" + text[m.end():], encoding="utf-8")
        return True
    anchor = None  # insert after the LAST earlier lifecycle field already present
    order = ("opened", "created", "creator", "opener", "started", "starter", "closed", "closer")
    for prior in order:
        if prior == field:
            break
        am = None
        for am_ in re.finditer(rf"^{prior}:.*\n", fm, re.M):
            am = am_
        if am:
            anchor = am.end()
    new_fm = fm + line if anchor is None else fm[:anchor] + line + fm[anchor:]
    path.write_text("---\n" + new_fm + "---\n" + text[m.end():], encoding="utf-8")
    return True


def restage_if_needed(root: Path, path: Path, field: str) -> bool:
    """Re-stage a lifecycle field left unstaged after a later hook rejects a commit."""
    rel = str(path.relative_to(root))
    working = path.read_text(encoding="utf-8") if path.exists() else ""
    wm = re.search(rf"^{field}:[ \t]*(\S.*?)\s*$", working, re.M)
    if not wm:
        return False
    indexed = git(root, "show", f":{rel}")
    im = re.search(rf"^{field}:[ \t]*(\S.*?)\s*$", indexed, re.M)
    if im:
        return False
    subprocess.run(["git", "-C", str(root), "add", rel], check=False)
    print(f"stamp-ticket-times: re-staged {rel} ({field})")
    return True


def first_work_commit(root: Path, item: str, created_sha: str | None) -> str | None:
    """The earliest commit whose SUBJECT names the id, other than the ticket's
    own creation commit -- that is when work began (GOV-006). Returns None for
    an item nothing has worked on yet. ONE rule, shared by --backfill and the
    close path below, so the two cannot drift into answering differently.
    """
    # SUBJECTS ONLY. git --grep narrows the walk but matches the whole message,
    # and a body citation is a CITATION, not work -- the branch rule says "cite
    # other items in the BODY freely" for exactly that reason. The first draft
    # of this refactor forgot the re-filter and promptly invented start times
    # for eight open items from commits that merely mentioned them.
    naming = sorted(
        (when, sha) for when, sha, subj in
        (l.split("\t", 2) for l in
         git(root, "log", f"--grep=\\b{item}\\b", "--format=%cI\t%H\t%s").splitlines()
         if l.count("\t") >= 2)
        if re.search(rf"\b{item}\b", subj))
    return next((w for w, s in naming if s != created_sha), None)


def split_front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def summarize_change(old_text: str | None, new_text: str) -> str:
    """One phrase for the Update Log: what this commit changed about the ticket.

    A brand-new file is 'created'. Otherwise the moment this commit newly sets
    started:/closed: is named the same way the front matter already names it
    (GOV-005's three moments read the same in prose as in the fields) rather
    than as a raw field diff. Anything else names the non-lifecycle field(s)
    that changed, or the body section(s) that differ from the last commit.
    """
    if old_text is None:
        return "created"
    old_fm, old_body = split_front_matter(old_text)
    new_fm, new_body = split_front_matter(new_text)

    closing = not old_fm.get("closed") and new_fm.get("closed")
    starting = not old_fm.get("started") and new_fm.get("started")
    parts = []
    if closing:
        parts.append("closed")
    elif starting:
        parts.append("started")

    for key in sorted(set(old_fm) | set(new_fm)):
        if key in LIFECYCLE_FIELDS:
            continue
        if key == "status" and closing:
            continue  # already reported as 'closed', not a raw status flip
        ov, nv = (old_fm.get(key) or "").strip(), (new_fm.get(key) or "").strip()
        if ov != nv:
            parts.append(f"{key}: {ov or '(none)'} -> {nv or '(none)'}")

    old_sections = split_sections(old_body)
    new_sections = split_sections(new_body)
    changed = [h for h, t in new_sections.items()
               if h.strip().lower() != "update log" and old_sections.get(h) != t]
    changed += [h + " (removed)" for h in old_sections
                if h.strip().lower() != "update log" and h not in new_sections]
    if changed:
        parts.append("body: " + ", ".join(changed))

    return "; ".join(parts) if parts else "edited"


def _canonical(text: str) -> str:
    """(front matter, body sections) with Update Log itself excluded -- the part
    of a ticket a fingerprint should actually vary on."""
    fm, body = split_front_matter(text)
    sections = {h: t for h, t in split_sections(body).items()
                if h.strip().lower() != "update log"}
    return repr(sorted(fm.items())) + "\x00" + repr(sorted(sections.items()))


def fingerprint(old_text: str | None, new_text: str) -> str:
    """Identifies the (before, after) pair an Update Log line describes -- NOT the
    rendered note, which two different edits can render identically (two separate
    body edits both touching Summary both say 'body: Summary'). A hook re-run
    before the actual commit sees the same (old_text, new_text) pair as the run
    before it and must not append a second, duplicate line for it."""
    h = hashlib.sha1()
    h.update((old_text or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(_canonical(new_text).encode("utf-8"))
    return h.hexdigest()[:8]


def already_logged(text: str, fp: str) -> bool:
    last = None
    for line in text.split("\n"):
        if line.startswith("- "):
            last = line
    return last is not None and last.endswith(f"[{fp}]")


def append_update_log(text: str, entry: str) -> str:
    """Insert a new bullet into ## Update Log, creating the section if absent.
    Never touches an existing line -- append-only, same guarantee as stamp()."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == UPDATE_LOG_HEADER:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            k = j
            while k > i + 1 and lines[k - 1].strip() == "":
                k -= 1
            return "\n".join(lines[:k] + [f"- {entry}"] + lines[k:])
    return text.rstrip("\n") + "\n\n" + UPDATE_LOG_HEADER + "\n\n" + f"- {entry}\n"


def staged_ticket_touches(root: Path, tdir: str) -> list[tuple[str | None, str]]:
    """[(old_path_or_None, new_path), ...] for every staged ticket .md, rename-aware
    (a close's `git mv` must diff against the pre-move blob, not report 'created')."""
    out = git(root, "diff", "--cached", "--name-status", "-M")
    pat = re.compile(rf"^{re.escape(tdir)}/(closed/)?{ID_RE}-[^/]+\.md$")
    changes = []
    for line in out.splitlines():
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R"):
            old_path, new_path = parts[1], parts[2]
        elif status == "A":
            old_path, new_path = None, parts[1]
        elif status == "M":
            old_path, new_path = parts[1], parts[1]
        else:  # D, C -- not an update to narrate
            continue
        if pat.match(new_path):
            changes.append((old_path, new_path))
    return changes


def update_log(root: Path, tdir: str, actor: str, now: str) -> list[str]:
    """Append one Update Log line to every ticket this commit touches (KIT-050)."""
    touched = []
    for old_path, new_path in staged_ticket_touches(root, tdir):
        new_file = root / new_path
        if not new_file.exists():
            continue
        new_text = new_file.read_text(encoding="utf-8")
        old_text = git(root, "show", f"HEAD:{old_path}") if old_path else None
        if old_path and not old_text:
            old_text = None  # HEAD had no such blob -- treat as a creation
        note = summarize_change(old_text, new_text)
        fp = fingerprint(old_text, new_text)
        if already_logged(new_text, fp):
            continue
        updated = append_update_log(new_text, f"{now} — {actor} — {note} [{fp}]")
        if updated != new_text:
            new_file.write_text(updated, encoding="utf-8")
            touched.append(new_path)
    return touched


def precommit(root: Path) -> int:
    tdir, btypes = _cfg(root)
    staged = git(root, "diff", "--cached", "--name-only").splitlines()
    added = git(root, "diff", "--cached", "--name-only", "--diff-filter=A").splitlines()
    now = now_et()
    actor = current_actor(root)
    stamped: list[str] = []

    for p in added:  # created: a new live ticket file in this commit
        if re.fullmatch(rf"{re.escape(tdir)}/{ID_RE}-[^/]+\.md", p) and (root / p).exists():
            if stamp(root / p, "created", now):
                stamped.append(p)
            if stamp(root / p, "creator", actor):
                stamped.append(p)
            if stamp(root / p, "opener", actor):
                stamped.append(p)
            restage_if_needed(root, root / p, "created")
            restage_if_needed(root, root / p, "creator")
            restage_if_needed(root, root / p, "opener")

    # started: first commit on the item's branch
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    bm = re.fullmatch(rf"(?:{btypes})/({ID_RE})", branch)
    if bm:
        hits = sorted((root / tdir).glob(f"{bm.group(1)}-*.md"))
        if len(hits) == 1 and stamp(hits[0], "started", now):
            stamped.append(str(hits[0].relative_to(root)))
        if len(hits) == 1 and stamp(hits[0], "starter", actor):
            stamped.append(str(hits[0].relative_to(root)))
        if len(hits) == 1:
            restage_if_needed(root, hits[0], "started")
            restage_if_needed(root, hits[0], "starter")

    for p in staged:  # closed: the ticket is staged under closed/ in this commit
        if re.fullmatch(rf"{re.escape(tdir)}/closed/{ID_RE}-[^/]+\.md", p) and (root / p).exists():
            if stamp(root / p, "closed", now):
                stamped.append(p)
            if stamp(root / p, "closer", actor):
                stamped.append(p)
            if stamp(root / p, "starter", actor):
                stamped.append(p)
            restage_if_needed(root, root / p, "closed")
            restage_if_needed(root, root / p, "closer")
            restage_if_needed(root, root / p, "starter")
            # GOV-006: an item FILED ON ANOTHER ITEM'S BRANCH never saw a first
            # commit on a branch of its own, so it reaches its close with no
            # started: -- and the gate then refuses the close after a full run,
            # with the fix being a manual --backfill. The machine can do that
            # derivation here, at the moment the gap becomes visible. stamp()
            # refuses to overwrite, so an existing started: is never touched.
            item = re.match(rf"{re.escape(tdir)}/closed/({ID_RE})", p).group(1)
            born = git(root, "log", "--diff-filter=A", "--format=%cI %H",
                       "--", f"{tdir}/{item}-*.md").splitlines()
            created_iso, created_sha = born[-1].split(" ", 1) if born else (None, None)
            iso = first_work_commit(root, item, created_sha)
            if iso is None:
                # Filed and closed with no work commit in between: the creation
                # IS the start (the same created-and-done rule --backfill uses).
                iso = created_iso
            if iso and stamp(root / p, "started", to_et(iso)):
                stamped.append(p)

    for p in stamped:
        git(root, "add", p)
        print(f"stamp-ticket-times: stamped {p}")

    # KIT-050: narrate what changed, for this commit and any other ticket edit --
    # run AFTER staging the lifecycle fields above, so the diff this reads (index
    # vs HEAD) already reflects them, and a started:/closed: moment narrates as
    # such rather than as a raw field diff.
    for p in update_log(root, tdir, actor, now):
        git(root, "add", p)
        print(f"stamp-ticket-times: update-log {p} ({actor})")
    return 0


def backfill(root: Path) -> int:
    tdir = root / _cfg(root)[0]
    files = sorted(tdir.glob("*.md")) + sorted((tdir / "closed").glob("*.md"))
    n = 0
    for f in files:
        idm = re.match(rf"({ID_RE})", f.name)
        if not idm:
            continue
        item = idm.group(1)
        rel = str(f.relative_to(root))
        is_closed = f.parent.name == "closed"

        # creation: adds of THIS file traced through renames; earliest is birth
        born = git(root, "log", "--follow", "--diff-filter=A", "--format=%cI %H",
                   "--", rel).splitlines()
        created_iso, created_sha = born[-1].split(" ", 1) if born else (None, None)

        started_iso = first_work_commit(root, item, created_sha)
        if started_iso is None and is_closed:
            started_iso = created_iso  # created-and-done in one commit

        closed_iso = None
        if is_closed:  # the newest add AT the closed path is the (final) close
            moved = git(root, "log", "--diff-filter=A", "--format=%cI", "--", rel).splitlines()
            closed_iso = moved[0] if moved else None
        if started_iso and closed_iso and started_iso > closed_iso:
            # Work that rode a machine commit: VIEW-007's mid-close `git mv` was
            # swept into the 05:00 report cron's commit, so the first commit
            # NAMING the id postdates the close. Work had necessarily begun by
            # the close; the close moment is the latest honest bound.
            started_iso = closed_iso

        for field, iso in (("created", created_iso), ("started", started_iso),
                           ("closed", closed_iso)):
            if iso and stamp(f, field, to_et(iso)):
                n += 1
                print(f"stamp-ticket-times: backfilled {field} on {f.name}")
    print(f"stamp-ticket-times: backfill wrote {n} stamp(s)")
    return 0


def main() -> int:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip() or ".")
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1])
    return backfill(root) if "--backfill" in sys.argv else precommit(root)


if __name__ == "__main__":
    sys.exit(main())
