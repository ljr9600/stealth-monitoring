#!/usr/bin/env python3
"""Shared internals of the portable teaching-doc kit (DOCS-033, D418).

The kit lets a design document TEACH: a reader new to the codebase gets the
reasoning behind a choice inlined right where they need it, instead of a bare
"D34" they must go chase. A `{{D34}}` marker in a doc is expanded, at render
time, into the FULL text of that decision — never a hover, never a link to
follow (owner ruling 2026-09-04). The decisions stay the single source of
truth; the doc copies nothing by hand, so nothing can drift.

Both the renderer (expands the markers into the HTML) and the checker
(verifies every marker resolves and the template is followed) import this, so
the two can never disagree about what a citation means.

Everything project-specific lives in docs/doc-kit.config; this module holds no
open-teleporter paths. That is what makes the kit portable: copy these files
and set four values.
"""
from __future__ import annotations
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)

import re
import os
import subprocess
from pathlib import Path

ACTORS = {"CODEX": "Codex", "CLAUDE": "Claude", "HUMAN": "Human"}


def current_actor(root: Path) -> str:
    """Ticket writes need a process-local actor; shared Git config is not identity (D42)."""
    raw = os.environ.get("TICKETING_ACTOR", "").strip()
    if raw.upper() not in ACTORS:
        raise SystemExit("Set TICKETING_ACTOR before writing tickets: export TICKETING_ACTOR=Codex "
                         "(or Claude; Human only for an actual human).")
    return ACTORS[raw.upper()]


# --- reading ANOTHER repository from inside a hook (KIT-069) ---------------------------
#
# git exports its repository variables to a hook. Inherited by a `git -C <other repo>` they
# win over `-C`: in a linked worktree GIT_DIR is absolute and names the CALLER, so a read
# meant for the epics clone quietly reads the member instead. That switched the scope word
# lists off (the member has no vocabulary file, and "no file" means "no list") and made
# every `epic:` look missing. The kit's own tests have unset these since TEST-017; the
# production reads never did. Local reads keep them on purpose — a staged read in a
# worktree must see that worktree's index, which is exactly what GIT_INDEX_FILE names.
GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX",
                 "GIT_NAMESPACE", "GIT_QUARANTINE_PATH")


def foreign_git_env() -> dict:
    """os.environ minus git's repository variables — for `git -C` against a repo that is
    not the one this process was started in (the epics clone, a board member)."""
    return {k: v for k, v in os.environ.items() if k not in GIT_REPO_VARS}


def load_config(root: Path) -> dict:
    """Parse docs/doc-kit.config (key = value, # comments) into a dict.

    Every key has a default equal to open-teleporter's behaviour, so a repo
    with no config file behaves exactly like the repo the kit was extracted from.
    """
    cfg = {
        "teaching_docs": "docs/subsystems/*.md",
        "decisions_dir": "docs/decisions",
        "decisions_frozen": "",
        "decisions_index": "",
        "tickets_dir": "docs/tickets",
        "citation_prefix": "D",
        "work_item_types": "EPIC STORY TASK BUG SPIKE",
        "epic_field": "epic",
        "prefixes": "",
        "retired_prefixes": "",   # KIT-059: still resolve, refused for new tickets
        # Never a ticket prefix: id-shaped words that appear in prose with dash-digits (KIT-020).
        "prefix_denylist": "API ID IP SHA AES RSA RFC ISO ADR TLS SSL HTTP HTTPS UTC URL URI DNS TCP UDP SQL CVE PR MR ETA FYI",
        # Epics model (ticketing-template D4): blank = epics live in this repo's own
        # tickets_dir; a scope name = they live in the sibling repo <scope>-epics.
        "epics_scope": "",
        "epics_remote": "",
        "epics_marker": ".epics-root",
        "default_branch": "master",
        "maintenance_paths": "TODO.md docs/DECISIONS.md docs/decisions docs/DECISIONS_INDEX.md CLAUDE.md",
        "commit_exempt_regex": "",
        "kit_exempt_regex": r"^chore\(ticketing-template\): (install|upgrade) v[0-9]+\.[0-9]+\.[0-9]+",
        "citation_sources": "docs/**/*.md README.md CLAUDE.md AGENTS.md ARCHITECTURE.md TODO.md",
        "citation_exempt": "docs/archive docs/reports docs/DECISIONS.md docs/DECISIONS_INDEX.md",
        "templates_dir": "docs/templates",
    }
    f = root / "docs" / "doc-kit.config"
    if f.exists():
        cfg.update(parse_config_text(f.read_text(encoding="utf-8")))
    return cfg


def parse_config_text(text: str) -> dict:
    """`key = value` lines (# comments) -> dict. Shared by load_config (a working tree)
    and the board (a config read from origin/<branch>, KIT-032)."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        # An uninstalled template still carries @PLACEHOLDER@ values; a
        # placeholder is not a setting (it once read as a scope named "@…@").
        if re.fullmatch(r"@[A-Z_]+@", v):
            v = ""
        out[k.strip()] = v
    return out


# --- derived state (D6, D20) ------------------------------------------------------------
#
# Status is OPEN | BLOCKED | CLOSED. "In progress" is never declared: an OPEN item is in
# progress when work on it has begun — its `started:` stamp is set (the first commit on its
# branch writes it) OR a `<type>/<ID>` branch exists. Both are needed: `started:` reaches the
# default branch only when the branch merges, and a merged branch is usually deleted.
# wi.py and the board both use this, so they can never disagree (KIT-032 AC-5).

# A ticket-id prefix (KIT-057): capital letters, whole words joined by single underscores,
# 2 to 24 characters: `KIT`, `HOST`, `TWITTER_SCRAPER`. Parsers match PREFIX_RE; declaring a
# prefix also checks the 24-character cap (valid_prefix). The underscore is a word character,
# so `\b` before a prefix still means "not inside a longer word" -- an old parser simply does
# not see `TWITTER_SCRAPER-001`, and no existing id changes meaning. The same grammar in
# ERE form lives in git-hooks/commit-msg (PFX) and install.sh.
PREFIX_RE = r"[A-Z](?:_?[A-Z]){1,23}"
PREFIX_MAX = 24
ID_RE = PREFIX_RE + r"-\d{3}"
PREFIX_RULE = ("2 to 24 characters: capital letters, whole words joined by single underscores "
               "(KIT, MARKETDATA_API)")


def valid_prefix(p: str) -> bool:
    """True when `p` may be declared as a prefix: the grammar AND the length cap."""
    return len(p) <= PREFIX_MAX and re.fullmatch(PREFIX_RE, p) is not None


ITEM_BRANCH_RE = re.compile(r"(?:^|/)(?:story|bug|task|spike)/(" + ID_RE + r")$")


def work_branches(repo: Path, remote_only: bool = False) -> set[str]:
    """Ids that have a `<type>/<ID>` branch — at origin (remote_only) or anywhere."""
    import subprocess
    refs = ["refs/remotes/origin"] if remote_only else ["refs/heads", "refs/remotes/origin"]
    r = subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)", *refs],
                       capture_output=True, text=True)
    return {m.group(1) for line in r.stdout.splitlines() if (m := ITEM_BRANCH_RE.search(line))}


def item_state(status: str, started: str, has_branch: bool) -> str:
    """'closed' | 'blocked' | 'in-progress' | 'queued' — the one definition (D20)."""
    s = (status or "").upper()
    if s == "CLOSED":
        return "closed"
    if s == "BLOCKED":
        return "blocked"
    return "in-progress" if (started or has_branch) else "queued"


# --- epics repo resolution (ticketing-template D4/D5) --------------------------------
#
# A multi-repo change is an EPIC with one STORY per repo. Stories live in the
# repo they change; epics live in ONE sibling repo per scope, `<scope>-epics`,
# found by walking up from this repo and looking for that sibling at each level.
# Epics are read from that clone's origin/<default_branch> ONLY — never its
# working tree — so a clone another session left on a branch or dirty cannot
# mislead anyone, and an epic that was never pushed resolves for nobody, its
# author included.

def epics_root(root: Path, cfg: dict) -> tuple[Path | None, str]:
    """(path, reason). path is None when epics are local (reason 'local') or
    when the scope's repo cannot be found (reason says how to fix it)."""
    import os
    scope = cfg.get("epics_scope", "").strip()
    if not scope:
        return None, "local"
    marker = cfg.get("epics_marker", ".epics-root")
    name = f"{scope}-epics"

    def accept(p: Path) -> bool:
        m = p / marker
        if not m.is_file():
            return False
        declared = ""
        for line in m.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("scope"):
                declared = line.split("=", 1)[-1].strip()
        return declared == scope

    env = os.environ.get("EPICS_ROOT", "").strip()
    if env:
        p = Path(env)
        if accept(p):
            return p, "EPICS_ROOT"
        return None, (f"EPICS_ROOT={env} is not the '{scope}' epics repo "
                      f"(no {marker}, or its scope= is not '{scope}')")
    d = root.resolve()
    while True:
        cand = d.parent / name
        if accept(cand):
            return cand, f"sibling of {d}"
        if d.parent == d:
            break
        d = d.parent
    remote = cfg.get("epics_remote", "").strip() or "<epics_remote not configured>"
    return None, (f"no '{name}' repo beside this repo or any ancestor — "
                  f"run: bash scripts/epics.sh ensure   "
                  f"(clones {remote} to {root.resolve().parent / name}; or set EPICS_ROOT)")


def epics_at_master(eroot: Path, cfg: dict) -> dict[str, dict]:
    """id -> {type, status, path} for every ticket in the epics repo, read from
    origin/<default_branch>. Empty (not an error) when that ref does not exist:
    an epics repo that was never pushed has published nothing (D5)."""
    import subprocess
    tdir = cfg.get("tickets_dir", "docs/tickets")
    ref = f"origin/{cfg.get('default_branch', 'master')}"

    def git(*a):
        r = subprocess.run(["git", "-C", str(eroot), *a], capture_output=True, text=True,
                           env=foreign_git_env())
        return r.stdout if r.returncode == 0 else ""

    out: dict[str, dict] = {}
    for p in git("ls-tree", "-r", "--name-only", ref, "--", tdir).splitlines():
        if not p.endswith(".md"):
            continue
        text = git("show", f"{ref}:{p}")
        m = re.match(r"^---\n(.*?)\n---", text, re.S)
        if not m:
            continue
        fm = {}
        for line in m.group(1).splitlines():
            km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if km:
                fm[km.group(1)] = km.group(2).strip()
        if fm.get("id"):
            out[fm["id"]] = {"type": fm.get("type", "").upper(),
                             "status": fm.get("status", "").upper(), "path": p}
    return out


# --- word lists: areas and tags (KIT-060) ------------------------------------------------
# A project MAY keep `vocabulary/areas.tsv` and `vocabulary/tags.tsv`. In a scoped work repo
# they are the SCOPE's, read from the epics repo at origin/<default> exactly like epics -- a
# word exists for other sessions only once it is pushed. Elsewhere (an epics repo itself, a
# single-repo product) they are read from the repo's own tree. A missing file means that
# list is not enforced: areas and tags stay free text, which is the behaviour before KIT-060.
VOCAB_KINDS = ("areas", "tags")
WORD_RE = r"[a-z0-9]+(?:-[a-z0-9]+)*"
WORD_MAX = 20
NOT_WORDS = {"misc", "other", "general", "various"}   # a pile, never a word or a synonym


def vocab_rows(text: str) -> list[dict]:
    """Every row of a list, in order (duplicates kept -- the checker reports them). The header
    row names the columns; `word` and `meaning` are required, the rest optional:
    synonyms (comma-separated), added, first_ticket, reason, merged_into."""
    rows, cols = [], None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if cols is None:
            cols = [c.lower() for c in cells]
            continue
        d = dict(zip(cols, cells + [""] * (len(cols) - len(cells))))
        d["synonyms"] = [s for s in re.split(r"[,\s]+", d.get("synonyms", "")) if s]
        d["_cols"] = cols
        rows.append(d)
    return rows


def vocab_map(text: str) -> dict[str, dict]:
    """word -> row (the first of any duplicate)."""
    out: dict[str, dict] = {}
    for r in vocab_rows(text):
        if r.get("word"):
            out.setdefault(r["word"], r)
    return out


def vocab_source(root: Path, cfg: dict, kind: str, staged: bool = False) -> tuple[str | None, str]:
    """(the list's text, or None when there is NO such list; where it was read).

    "No list" (None, the file is absent) switches the check off, as before KIT-060. "Cannot
    read the lists" is different and never silently passes: `where` starts with UNAVAILABLE
    when a scoped repo has no epics clone, or the clone has no origin/<default> to read.
    `staged` reads this repo's own list from the INDEX, so a commit is judged by what it
    commits, never by an unstaged edit (the pre-commit checker uses it)."""
    import subprocess
    vdir = (cfg.get("vocabulary_dir") or "vocabulary").strip().strip("/") or "vocabulary"
    rel = f"{vdir}/{kind}.tsv"
    eroot, why = epics_root(root, cfg)
    if eroot is None and why != "local":
        return None, f"UNAVAILABLE: {why}"
    if eroot is not None:
        ref = f"origin/{cfg.get('default_branch', 'master')}"
        if subprocess.run(["git", "-C", str(eroot), "rev-parse", "--verify", "--quiet", ref],
                          capture_output=True, env=foreign_git_env()).returncode != 0:
            return None, (f"UNAVAILABLE: {eroot} has no {ref} to read the word lists from — "
                          f"run: bash scripts/epics.sh fetch")
        r = subprocess.run(["git", "-C", str(eroot), "show", f"{ref}:{rel}"], capture_output=True, text=True,
                           env=foreign_git_env())
        return (r.stdout if r.returncode == 0 else None), f"{eroot.name} {ref}:{rel}"
    if staged:
        r = subprocess.run(["git", "-C", str(root), "show", f":{rel}"], capture_output=True, text=True)
        return (r.stdout if r.returncode == 0 else None), f"{rel} (staged)"
    f = root / rel
    return (f.read_text(encoding="utf-8") if f.is_file() else None), rel


def load_vocab(root: Path, cfg: dict, staged: bool = False) -> tuple[dict[str, tuple[dict, str]], str]:
    """({kind: (word -> row, where)} for each list that exists, the UNAVAILABLE reason or '')."""
    out, unavailable = {}, ""
    for kind in VOCAB_KINDS:
        text, where = vocab_source(root, cfg, kind, staged)
        if where.startswith("UNAVAILABLE"):
            unavailable = where
        elif text is not None:
            out[kind] = (vocab_map(text), where)
    return out, unavailable


def check_word(rows: dict[str, dict], kind: str, w: str) -> str:
    """'' when `w` may be used as a(n) area/tag; otherwise why not, and what to use."""
    one = kind[:-1]
    if w in rows and not rows[w].get("merged_into"):
        return ""
    if w in rows:
        return f"{one} '{w}' was merged into '{rows[w]['merged_into']}' — use {rows[w]['merged_into']}"
    for word, r in rows.items():
        if w in r["synonyms"]:
            return f"'{w}' is listed as a synonym of the {one} '{word}' — use {r.get('merged_into') or word}"
    live = sorted(x for x, r in rows.items() if not r.get("merged_into"))
    return (f"'{w or '(none)'}' is not on the {kind} list. Use one of: {', '.join(live) or '(the list is empty)'}. "
            f"If none fits, add a row to the list (docs/TICKETING.md, 'Areas and tags'), push it, "
            f"then file the ticket.")


def _cli() -> int:
    """python3 scripts/doc_kit.py epics-root | epics-ls — the ONE resolver,
    callable from bash hooks so bash and python can never disagree."""
    import subprocess
    import sys
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    root = Path(top or ".")
    cfg = load_config(root)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    p, why = epics_root(root, cfg)
    if cmd == "epics-root":
        if p:
            print(p)
            return 0
        print(why, file=sys.stderr)
        return 0 if why == "local" else 1
    if cmd == "epics-ls":
        if not p:
            print(why, file=sys.stderr)
            return 0 if why == "local" else 1
        for i, e in sorted(epics_at_master(p, cfg).items()):
            print(f"{i}\t{e['type']}\t{e['status']}")
        return 0
    print("usage: doc_kit.py epics-root | epics-ls", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())


def marker_re(cfg: dict) -> re.Pattern:
    """The {{<prefix><number>}} citation marker."""
    pre = re.escape(cfg["citation_prefix"])
    return re.compile(r"\{\{(" + pre + r"\d+)\}\}")


def teaching_docs(root: Path, cfg: dict) -> list[Path]:
    """Every document that must follow the teaching template."""
    out: list[Path] = []
    for pat in cfg["teaching_docs"].split():
        out.extend(sorted(root.glob(pat)))
    # A doc listed twice (overlapping globs) is de-duplicated, order kept.
    seen, uniq = set(), []
    for p in out:
        # An index/readme/underscore file is a landing page, not a subsystem
        # doc — it does not follow the teaching template.
        if p.name.upper().startswith(("README", "INDEX", "_")):
            continue
        if p not in seen and p.exists():
            seen.add(p)
            uniq.append(p)
    return uniq


class Decision:
    """One decision, resolved from either the per-file dir or the frozen log."""

    def __init__(self, id: str, title: str, body: str, link: str):
        self.id = id
        self.title = title      # the heading text after "<id> — "
        self.body = body        # markdown, the argument itself
        self.link = link        # href to the decision's own page (repo-relative)


def find_decision(root: Path, cfg: dict, id: str) -> Decision | None:
    """Resolve a decision id to its title, body and a link — or None.

    Per-file first (docs/<decisions_dir>/<id>-*.md), then the frozen log. The
    file's/section's first line is "## <id> — <title> (<date>)"; the title is
    what follows the em dash, the body is everything after that line.
    """
    ddir = root / cfg["decisions_dir"]
    hits = sorted(ddir.glob(f"{id}-*.md")) if ddir.exists() else []
    if hits:
        text = hits[0].read_text(encoding="utf-8")
        title, body = _split_heading(text, id)
        link = hits[0].with_suffix(".html").relative_to(root).as_posix()
        return Decision(id, title, body, link)

    frozen = cfg.get("decisions_frozen", "").strip()
    if frozen:
        fpath = root / frozen
        if fpath.exists():
            block = _extract_frozen(fpath.read_text(encoding="utf-8"), id)
            if block is not None:
                title, body = _split_heading(block, id)
                link = Path(frozen).with_suffix(".html").as_posix()
                return Decision(id, title, body, link)
    return None


def _split_heading(text: str, id: str) -> tuple[str, str]:
    """Return (title, body) from a decision whose first heading names <id>."""
    m = re.search(rf"^#{{1,3}}\s+{re.escape(id)}\s*[—-]\s*(.+?)\s*$", text, re.M)
    if not m:
        # No recognisable heading: use the id as the title, whole text as body.
        return id, text.strip()
    title = m.group(1)
    body = text[m.end():].lstrip("\n")
    return title, body


def _extract_frozen(log: str, id: str) -> str | None:
    """The section for <id> in a frozen log: its heading until the next heading."""
    lines = log.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^#{{1,3}}\s+{re.escape(id)}\b", line):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,3}\s+\S", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def find_ticket(root: Path, cfg: dict, id: str) -> str | None:
    """A ticket id -> a repo-relative link to its rendered page, or None.

    Tickets live in <tickets_dir> and, once closed, <tickets_dir>/closed. The
    id keeps its number forever, so a citation resolves on either side.
    """
    tdir = cfg.get("tickets_dir", "").strip()
    if not tdir:
        return None
    base = root / tdir
    for cand in (base, base / "closed"):
        hits = sorted(cand.glob(f"{id}-*.md")) if cand.exists() else []
        if hits:
            return hits[0].with_suffix(".html").relative_to(root).as_posix()
    return None


def linkify_refs(html: str, root: Path, cfg: dict, rel) -> str:
    """Turn bare decision and ticket ids in an inlined decision into links.

    Depth is ONE: a decision box shows its own text, and the ids it cites
    become clickable links (never more boxes) — a reader can follow one to its
    page, but the learner reading the narrative need not (D419). Only ids in
    TEXT are linked, never those already inside a tag or an existing <a>.
    """
    pre = re.escape(cfg["citation_prefix"])
    ref = re.compile(r"\b(" + pre + r"\d+)\b|\b(" + ID_RE + r")\b")

    def sub(m):
        if m.group(1):  # a decision id
            d = find_decision(root, cfg, m.group(1))
            href = rel(d.link) if d else None
            token = m.group(1)
        else:           # a ticket id
            href = find_ticket(root, cfg, m.group(2))
            if href:
                href = rel(href)
            token = m.group(2)
        return f'<a href="{href}">{token}</a>' if href else token

    out, in_anchor = [], False
    for seg in re.split(r"(<[^>]+>)", html):
        if seg.startswith("<"):
            low = seg.lower()
            if low.startswith("<a "):
                in_anchor = True
            elif low.startswith("</a"):
                in_anchor = False
            out.append(seg)
        elif in_anchor:
            out.append(seg)          # never nest a link inside a link
        else:
            out.append(ref.sub(sub, seg))
    return "".join(out)
