#!/usr/bin/env python3
"""gen-board.py — build ONE project's ticket board as a static site (KIT-032).

A project is a small config file naming its repositories (D19). The board is a set of
short pages, one question each (D18): Home, one page per repository (or per area, for a
one-repository project), one per ticket, Epics, Decisions, Metrics. Every repository is
read at origin/<its default branch> only — never a working tree — so it shows what was
pushed, wherever the work happens. The output contains data ONLY from the configured
repositories: a board never carries another project's tickets (the wall, D19). The
project switcher is a list of plain links from the config.

    python3 scripts/gen-board.py docs/board.conf            # fetch each repo, build
    python3 scripts/gen-board.py docs/board.conf --no-fetch # use what origin/* already holds
    python3 scripts/gen-board.py docs/board.conf --out DIR  # override the config's out
    python3 scripts/gen-board.py docs/board.conf --no-publish  # build only, even if `publish` is set

Config — `key = value` lines, `#` starts a comment line; `repo` and `board` repeat. Paths
are relative to the config file's directory; `repo` paths and registry rows are relative
to `root`:

    name     = Stealth Trading
    out      = ~/boards/stealth-trading
    # base for repo paths (default: this file's directory)
    root     = ..
    # optional: a scope registry (repos.tsv) — every row is a member
    registry = ../repos.tsv
    # optional, repeatable: a member repository, named after its origin remote
    # (or explicitly: repo = PATH | NAME, and optionally its domain: repo = PATH | NAME | DOMAIN)
    repo     = .
    # optional (KIT-061): group repositories into domains on the home page — a `domain`
    # column in the registry (read by header name), or the third field of a `repo =` line
    # optional (KIT-061): rows lead with the title and say where the ticket lives; the id
    # goes last in small type (default: id-first, as before)
    row_style = title-first
    # optional, repeatable: another project's board — a plain link, nothing read from it
    board    = Open Teleporter | http://192.168.1.40:8099/
    # optional: shown in the footer
    refresh  = every 5 minutes
    # optional (KIT-066): a guide page in the top menu — the file's `## ` sections, then every
    # repository's prefix and next id, then the area and tag word lists
    guide    = How tickets are made | docs/HOW-TICKETS-ARE-MADE.md
    # optional: the front door listing every board (KIT-039) — an "All boards" link in the menu
    portal   = http://192.168.1.50:8102/
    # optional: after building, rsync the board here — where kit/board/serve.sh serves it (KIT-035)
    publish  = lloyd@192.168.1.50:/home/lloyd/boards/stealth-trading
"""
import sys; sys.dont_write_bytecode = True  # KIT-011
import html
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_kit import parse_config_text, item_state, ITEM_BRANCH_RE, ID_RE as _ID, vocab_map, foreign_git_env  # noqa: E402

ID_RE = re.compile(r"\b(" + _ID + r")\b")
FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
STAMP_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?")
e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


# ─── config ───────────────────────────────────────────────────────────────────────────────

def load_board_config(path: Path) -> dict:
    here = path.resolve().parent
    raw = path.read_text(encoding="utf-8")
    cfg = parse_config_text(raw)
    repos, boards = [], []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if k == "repo" and v:
            repos.append(v)
        elif k == "board" and "|" in v:
            name, url = (x.strip() for x in v.split("|", 1))
            boards.append({"name": name, "url": url})
    rel = lambda p: (here / os.path.expanduser(p)).resolve()  # an absolute p stays absolute
    root = rel(cfg.get("root") or ".")
    members = []
    if cfg.get("registry"):
        # a scope registry (repos.tsv), every row a member. Read by the header's column names
        # (KIT-061): `repo`, `path`, and optionally `domain`; without a header, by position.
        cols = ["repo", "path"]
        for row in rel(cfg["registry"]).read_text(encoding="utf-8").splitlines():
            if not row.strip() or row.startswith("#"):
                continue
            cells = [c.strip() for c in row.split("\t")]
            if cells[0] == "repo":
                cols = [c.lower() for c in cells]
                continue
            d = dict(zip(cols, cells))
            if d.get("path"):
                members.append((d.get("repo") or None, (root / d["path"]).resolve(), d.get("domain", "")))
    for r in repos:
        parts = [x.strip() for x in r.split("|")] + ["", ""]
        members.append((parts[1] or None, (root / os.path.expanduser(parts[0])).resolve(), parts[2]))
    seen, out = {}, []
    for name, p, dom in members:
        if p in seen:
            if dom and not seen[p]["domain"]:
                seen[p]["domain"] = dom
            continue
        seen[p] = {"name": name or origin_name(p) or p.name, "path": p, "domain": dom}
        out.append(seen[p])
    return {"name": cfg.get("name", "Board"), "out": str(rel(cfg["out"]).resolve()) if cfg.get("out") else "",
            "refresh": cfg.get("refresh", ""), "repos": out, "boards": boards,
            "publish": cfg.get("publish", ""), "portal": cfg.get("portal", ""),
            "row_style": (cfg.get("row_style") or "id-first").strip(),
            "guide": (lambda lab, _, pth: {"label": lab.strip(), "path": rel(pth.strip())} if pth.strip() else None)(
                *(cfg.get("guide") or "").partition("|"))}


def origin_name(p: Path) -> str:
    """The repository's own name, from its origin remote — not the folder it happens to be
    checked out in (a worktree, a renamed clone: KIT-038)."""
    url = subprocess.run(["git", "-C", str(p), "remote", "get-url", "origin"],
                         capture_output=True, text=True, env=foreign_git_env()).stdout.strip().rstrip("/")
    return re.sub(r"\.git$", "", re.split(r"[/:]", url)[-1]) if url else ""


# ─── reading one repository at origin/<branch> ──────────────────────────────────────────

def git(repo: Path, *a) -> str:
    # Board members are foreign repositories even when the board runs in a hook (D34).
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                       env=foreign_git_env())
    return r.stdout if r.returncode == 0 else ""


def stamp(s: str):
    m = STAMP_RE.search(s or "")
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 12), int(m[5] or 0))
    except ValueError:      # the shape of a date for a day that does not exist (KIT-070)
        return None


LOG_ROW = re.compile(r"^- (?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2} ET) — (?P<actor>.+?) — (?P<note>.*?)(?: \[[0-9a-f]{8}\])?$")


def history_rows(text):
    inside, rows = False, []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "update log"
        if inside and (match := LOG_ROW.fullmatch(line)):
            rows.append({**match.groupdict(), "line": number, "raw": line})
    return rows


def commit_metadata(text):
    records = {}
    for record in text.split("\x1e"):
        fields = record.strip("\n").split("\x1f", 3)
        if len(fields) != 4:
            continue
        sha, when, subject, actor = fields
        try:
            when = datetime.fromisoformat(when).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
        except ValueError:
            when = ""
        records[sha] = {"sha": sha, "hash": sha[:8], "when": when, "subject": subject,
                        "actor": {"codex": "Codex", "claude": "Claude", "human": "Human"}.get(actor.strip().lower(), "")}
    return records


COMMIT_FORMAT = "%H%x1f%cI%x1f%s%x1f%(trailers:key=Agent,valueonly,separator=%x2c)%x1e"


def commit_base(repo):
    # Known hosting formats only; never put remote credentials into generated pages (D42).
    remote = git(repo, "remote", "get-url", "origin").strip()
    match = re.fullmatch(r"(?:https?://(?:[^/@]+@)?|git@)(github\.com|gitlab\.com)[:/]([\w./-]+)", remote)
    if not match:
        return ""
    host, path = match.groups()
    path = path.removesuffix(".git").rstrip("/")
    return f"https://{host}/{path}/" + ("-/commit/" if host == "gitlab.com" else "commit/")


def attach_history(repo, ref, ticket, metadata, base):
    """Blame the published file, following renames, to connect each durable row to its commit."""
    if not ticket["history"]:
        return
    by_line = {}
    number, sha = None, None
    for line in git(repo, "blame", "--line-porcelain", ref, "--", ticket["path"]).splitlines():
        if match := re.fullmatch(r"([0-9a-f]{40}) \d+ (\d+)(?: \d+)?", line):
            sha, number = match[1], int(match[2])
        elif line.startswith("\t") and number is not None:
            by_line[number] = sha
    for row in ticket["history"]:
        sha = by_line.get(row["line"])
        if sha and sha not in metadata:
            metadata.update(commit_metadata(git(repo, "show", "-s", "--format=" + COMMIT_FORMAT, sha)))
        info = metadata.get(sha, {})
        row.update(subject=info.get("subject", ""), sha=sha or "", url=base + sha if base and sha else "")
        # Old Human rows were guesses; the actual commit trailer supplies provenance.
        row["actor"] = info.get("actor") or row["actor"]


def parse_ticket(text: str, path: str) -> dict | None:
    m = FM_RE.match(text)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km:
            fm[km.group(1)] = km.group(2).strip()
    if not fm.get("id"):
        return None
    body = m.group(2)
    parts = re.split(r"^## ", body, flags=re.M)[1:]
    sections = []
    summary = ""
    for sec in parts:
        title, _, raw = sec.partition("\n")
        raw = raw.strip()
        paras = [p.strip() for p in raw.split("\n\n")
                 if p.strip() and not p.strip().startswith("<!--")]
        if paras:
            summary = re.sub(r"\s+", " ", paras[0])
        sections.append({"title": title.strip(), "body": raw})
    status = fm.get("status", "").upper() or ("CLOSED" if "/closed/" in path else "OPEN")
    return {"id": fm["id"], "title": fm.get("title", ""), "type": (fm.get("type") or "").upper(),
            "status": status, "priority": (fm.get("priority") or "P3").upper(), "area": fm.get("area", ""),
            "tags": list(dict.fromkeys(x for x in re.split(r"[,\s]+", fm.get("tags", "")) if x)),
            "epic": fm.get("epic", ""), "estimate": fm.get("estimate", ""),
            "created": fm.get("created") or fm.get("opened", ""), "started": fm.get("started", ""),
            "closed": fm.get("closed", ""), "summary": summary,
            "reopened": int(fm["reopened"]) if fm.get("reopened", "").strip().isdigit() else 0,
            "sections": sections, "history": history_rows(text),
            "creator": fm.get("creator", ""), "starter": fm.get("starter", ""), "closer": fm.get("closer", ""),
            "cites": sorted(set(re.findall(r"\bD\d+\b", body)), key=lambda d: int(d[1:])), "path": path}


def inline_md(raw: str) -> str:
    """Render the deliberately small, safe inline Markdown subset used on boards."""
    protected = []

    def hold(value: str) -> str:
        protected.append(value)
        return f"\x00{len(protected) - 1}\x00"

    value = e(raw)
    value = re.sub(r"`([^`]+)`", lambda m: hold(f'<code>{m.group(1)}</code>'), value)
    value = re.sub(r"\[([^]]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)",
                   lambda m: hold(f'<a href="{m.group(2)}">{m.group(1)}</a>'), value)
    value = re.sub(r"\*\*([^*]+)\*\*|(?<!\w)__([^_]+)__(?!\w)", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!\w)_([^_]+)_(?!\w)", lambda m: f"<em>{m.group(1) or m.group(2)}</em>", value)
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], value)


def markdown_body(raw: str) -> str:
    """Render ticket prose without allowing ticket text to inject HTML: paragraphs, `-` and `1.`
    lists (an indented next line continues the item), pipe tables, ###/#### headings, fenced
    code (KIT-067). Every piece of text goes through inline_md(), which escapes first."""
    lines = [line.rstrip() for line in raw.splitlines() if not line.strip().startswith("<!--")]
    out, para, table, items = [], [], [], []
    code, kind = False, None                      # kind: the open list, "ul" | "ol"

    def flush_para():
        if para:
            out.append(f'<p>{inline_md(" ".join(x.strip() for x in para))}</p>')
            para.clear()

    def flush_items():
        nonlocal kind
        if items:
            out.append(f'<{kind}>' + ''.join(f'<li>{inline_md(x)}</li>' for x in items) + f'</{kind}>')
            items.clear()
        kind = None

    def cells(row):
        # Consume escape pairs before delimiters, including inside inline code (D36).
        s = row.strip()
        parts, cell = [], []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                cell.append("|" if s[i + 1] == "|" else s[i:i + 2])
                i += 2
            elif s[i] == "|":
                parts.append("".join(cell).strip())
                cell = []
                i += 1
            else:
                cell.append(s[i])
                i += 1
        parts.append("".join(cell).strip())
        if s.startswith("|"):
            parts.pop(0)
        if s.endswith("|") and parts and parts[-1] == "":
            parts.pop()
        return parts

    def flush_table():
        if not table:
            return
        if len(table) >= 2 and re.fullmatch(r"\|?(\s*:?-{2,}:?\s*\|)*\s*:?-{2,}:?\s*\|?", table[1].strip()):
            head, body = cells(table[0]), [cells(r) for r in table[2:]]
            out.append('<div class="tbl-wrap md-table"><table><thead><tr>'
                       + "".join(f"<th>{inline_md(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table></div>")
        else:                                     # pipes, but not a table: plain text
            para.extend(table)
            flush_para()
        table.clear()

    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            flush_para(); flush_items(); flush_table()
            out.append('</code></pre>' if code else '<pre><code>')
            code = not code
        elif code:
            out.append(e(line) + "\n")
        elif s.startswith("|"):
            flush_para(); flush_items(); table.append(line)
        else:
            flush_table()
            if m := re.match(r"^(#{3,4})\s+(.*)$", s):
                flush_para(); flush_items()
                out.append(f"<h{len(m.group(1))}>{inline_md(m.group(2))}</h{len(m.group(1))}>")
            elif re.match(r"^\s*[-*+]\s+", line):
                flush_para()
                if kind != "ul":
                    flush_items(); kind = "ul"
                items.append(re.sub(r"^\s*[-*+]\s+", "", line))
            elif re.match(r"^\s*\d+[.)]\s+", line):
                flush_para()
                if kind != "ol":
                    flush_items(); kind = "ol"
                items.append(re.sub(r"^\s*\d+[.)]\s+", "", line))
            elif not s:
                flush_para(); flush_items()
            elif items and line[:1] in (" ", "\t"):   # an indented line continues the item
                items[-1] += " " + s
            else:
                flush_items(); para.append(line)
    if code:
        out.append('</code></pre>')
    flush_table(); flush_para(); flush_items()
    return "".join(out)


def section_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def read_repo(name: str, path: Path, fetch: bool) -> dict:
    rec = {"name": name, "path": str(path), "ok": path.joinpath(".git").exists(), "fetched": None,
           "adopted": False, "prefix": "", "tickets": [], "decisions": [], "commits": {}, "head": None}
    if not rec["ok"]:
        return rec
    if fetch:
        rec["fetched"] = subprocess.run(["git", "-C", str(path), "fetch", "-q", "origin"],
                                        capture_output=True, env=foreign_git_env()).returncode == 0
    cfg_text = ""
    for branch in ("master", "main"):
        if git(path, "rev-parse", "--verify", "-q", f"origin/{branch}").strip():
            cfg_text = git(path, "show", f"origin/{branch}:docs/doc-kit.config")
            conf = parse_config_text(cfg_text) if cfg_text else {}
            branch = conf.get("default_branch") or branch
            break
    else:
        rec["ok"] = False
        return rec
    conf = parse_config_text(cfg_text) if cfg_text else {}
    ref = f"origin/{branch}"
    rec["retired"] = conf.get("retired_prefixes", "").split()      # KIT-066: the next id continues past these
    rec.update(adopted=bool(cfg_text), prefix=conf.get("prefixes", ""), branch=branch,
               head=git(path, "log", "-1", "--format=%cI", ref).strip()[:16].replace("T", " "))
    vdir = (conf.get("vocabulary_dir") or "vocabulary").strip().strip("/") or "vocabulary"
    rec["vocab"] = {k: git(path, "show", f"{ref}:{vdir}/{k}.tsv") for k in ("areas", "tags")}   # KIT-061
    rec["epics_repo"] = bool(git(path, "show", f"{ref}:.epics-root"))
    tdir = conf.get("tickets_dir", "docs/tickets").rstrip("/")
    ddir = conf.get("decisions_dir", "docs/decisions").rstrip("/")
    frozen = conf.get("decisions_frozen") or "docs/DECISIONS.md"
    files = git(path, "ls-tree", "-r", "--name-only", ref, "--", tdir, ddir, frozen).splitlines()
    branches = {m.group(1) for line in git(path, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").splitlines()
                if (m := ITEM_BRANCH_RE.search(line))}
    for f in files:
        if f.startswith(tdir + "/") and f.endswith(".md") and re.search("/" + _ID + r"-[^/]*\.md$", f):
            t = parse_ticket(git(path, "show", f"{ref}:{f}"), f)
            if t:
                t["branch"] = t["id"] in branches
                t["state"] = item_state(t["status"], t["started"], t["branch"])
                rec["tickets"].append(t)
        elif f.startswith(ddir + "/") and re.search(r"/D\d+-[^/]*\.md$", f):
            head = git(path, "show", f"{ref}:{f}").split("\n", 1)[0]
            m = re.match(r"^##\s+(D\d+)\s+—\s+(.*?)(?:\s+\((\d{4}-\d{2}-\d{2})\))?\s*$", head)
            if m:
                rec["decisions"].append({"id": m[1], "title": m[2], "date": m[3] or ""})
        elif f == frozen:
            have = {d["id"] for d in rec["decisions"]}
            for line in git(path, "show", f"{ref}:{f}").splitlines():
                m = re.match(r"^##\s+(D\d+)\s+—\s+(.*)$", line)
                if m and m[1] not in have:
                    rec["decisions"].append({"id": m[1], "title": m[2].strip(), "date": "", "frozen": True})
                    have.add(m[1])
    ids = {t["id"] for t in rec["tickets"]}
    if ids:
        metadata = commit_metadata(git(path, "log", ref, "-n", "3000", "--format=" + COMMIT_FORMAT))
        base = commit_base(path)
        for sha, info in metadata.items():
            info["url"] = base + sha if base else ""
            for item in set(ID_RE.findall(info["subject"])) & ids:
                rec["commits"].setdefault(item, []).append(info)
        for ticket in rec["tickets"]:
            attach_history(path, ref, ticket, metadata, base)
    return rec


# ─── model helpers ────────────────────────────────────────────────────────────────────

class Model:
    def __init__(self, conf: dict, repos: list[dict], now: datetime):
        self.conf, self.repos, self.now = conf, repos, now
        for r in repos:
            for t in r["tickets"]:
                t["_c"], t["_s"], t["_x"] = stamp(t["created"]), stamp(t["started"]), stamp(t["closed"])
                if t["state"] == "in-progress" and not t["_s"]:
                    t["_s"] = t["_c"]
            times = [x for t in r["tickets"] for x in (t["_c"], t["_s"], t["_x"]) if x]
            r["last"] = max(times) if times else None
            r["by"] = {s: [t for t in r["tickets"] if t["state"] == s] for s in ("in-progress", "queued", "blocked", "closed")}
        self.all = [(r, t) for r in repos for t in r["tickets"]]
        self.single = len(repos) == 1
        self.active = sorted([r for r in repos if r["tickets"]], key=lambda r: r["last"] or datetime.min, reverse=True)
        self.quiet = [r for r in repos if not r["tickets"] and r["adopted"]]
        self.unset = [r for r in repos if not r["adopted"]]
        self.by_name = {r["name"]: r for r in repos}
        self.built = now.isoformat(timespec="minutes") + tzoffset()
        # KIT-061. Domains: opt-in, from the registry's `domain` column or a `repo =` line.
        self.has_domains = any(r.get("domain") for r in repos)
        self.domains: dict[str, list] = defaultdict(list)
        for r in repos:
            self.domains[r.get("domain") or "Other"].append(r)
        self.title_first = conf.get("row_style", "") == "title-first"
        # The word lists (KIT-060) at origin: the scope's epics repo first, else any repo's own.
        self.vocab: dict[str, dict] = {}
        for r in sorted(repos, key=lambda r: not r.get("epics_repo")):
            for kind, text in (r.get("vocab") or {}).items():
                if text and kind not in self.vocab:
                    self.vocab[kind] = vocab_map(text)
        for r in repos:
            for tk in r["tickets"]:
                tk["area_raw"] = tk["area"]
                tk["area"] = self.canon("areas", tk["area"])
                tk["tags"] = list(dict.fromkeys(self.canon("tags", x) for x in tk.get("tags", [])))
        self.new_words = []
        for kind, rows in self.vocab.items():
            for w, row in rows.items():
                raw = row.get("added", "")
                d = stamp(raw)
                if d is None and raw and STAMP_RE.search(raw):
                    # Already published, so the gate cannot refuse it any more: report it and
                    # build the board anyway (KIT-070). One bad row used to stop every page.
                    print(f"gen-board: {kind} word '{w}' has an impossible added date '{raw}' "
                          f"— left out of New words; correct it in the word list", file=sys.stderr)
                if d and 0 <= (now.date() - d.date()).days <= 7:   # calendar days: a word added today counts all day
                    self.new_words.append((d, kind, w, row))
        self.new_words.sort(key=lambda x: x[0], reverse=True)

    def canon(self, kind: str, w: str) -> str:
        """The live word a ticket's area/tag reads as: a merged word shows its successor, a
        synonym its word; an unlisted word shows as it is. The ticket file never changes."""
        rows = self.vocab.get(kind) or {}
        if not w or not rows:
            return w
        if w in rows:
            return rows[w].get("merged_into") or w
        for word, row in rows.items():
            if w in row["synonyms"]:
                return row.get("merged_into") or word
        return w

    def dom(self, r) -> str:
        return r.get("domain") or "Other"

    def events(self):
        ev = []
        for r, t in self.all:
            if t["_c"]:
                ev.append((t["_c"], "opened", r, t))
            if t["_s"] and t["_c"] and (t["_s"] - t["_c"]) > timedelta(minutes=1) and t["started"]:
                ev.append((t["_s"], "started", r, t))
            if t["_x"]:
                ev.append((t["_x"], "closed", r, t))
        return sorted(ev, key=lambda x: x[0], reverse=True)


def median(xs: list[float]) -> float:
    """open-teleporter gen-dashboard.py's median: the middle value, or the mean of the two middles."""
    s, n = sorted(xs), len(xs)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def tzoffset() -> str:
    """The build's UTC offset as ±HH:MM (ET), so board.json's `built` is unambiguous."""
    try:
        from zoneinfo import ZoneInfo
        o = datetime.now(ZoneInfo("America/New_York")).strftime("%z")
        return o[:3] + ":" + o[3:]
    except Exception:
        return ""


def ago(now: datetime, d) -> str:
    if not d:
        return ""
    m = (now - d).total_seconds() / 60
    if m < 60:
        return f"{max(1, round(m))}m ago"
    if m < 36 * 60:
        return f"{round(m / 60)}h ago"
    if m < 21 * 1440:
        return f"{round(m / 1440)}d ago"
    return d.strftime("%b %-d")


def span(a, b) -> str:
    if not a or not b:
        return ""
    h = (b - a).total_seconds() / 3600
    if h < 1:
        return f"{max(1, round(h * 60))} min"
    return f"{round(h)} h" if h < 48 else f"{round(h / 24)} days"


def at(d) -> str:
    return d.strftime("%b %-d, %H:%M ET") if d else "—"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "no-area").lower()).strip("-") or "no-area"


def weekly(tickets, now, weeks=12):
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(weeks - 1, -1, -1):
        s = start - timedelta(days=7 * i)
        e_ = s + timedelta(days=7)
        out.append((s, sum(1 for t in tickets if t["_x"] and s <= t["_x"] < e_)))
    return out


def sparkline(vals) -> str:
    w, h, mx = 96, 24, max([1, *vals])
    x = lambda i: (i / (len(vals) - 1)) * (w - 4) + 2
    y = lambda v: h - 3 - (v / mx) * (h - 7)
    pts = [f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals)]
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<path class="a" d="M{pts[0]} L{" L".join(pts)} L{x(len(vals)-1):.1f},{h-2} L2,{h-2} Z"/>'
            f'<path d="M{" L".join(pts)}"/><circle r="2.2" cx="{x(len(vals)-1):.1f}" cy="{y(vals[-1]):.1f}"/></svg>')


def nice_max(v: int) -> int:
    if v <= 5:
        return 5
    p = 10 ** (len(str(int(v))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if m * p >= v:
            return int(m * p)
    return 10 * p


# ─── rendering ─────────────────────────────────────────────────────────────────────────

LABEL = {"in-progress": "In progress", "queued": "Backlog",   # KIT-065: the owner's word
         "blocked": "Blocked", "closed": "Closed"}


class Site:
    def __init__(self, m: Model, out: Path):
        self.m, self.out = m, out

    # links are relative to the page being written (so the site works from any base path)
    def rel(self, frm: str, to: str) -> str:
        return os.path.relpath(to, os.path.dirname(frm) or ".").replace(os.sep, "/")

    def p_repo(self, r):
        return f"r/{slug(r['name'])}/index.html"

    def p_ticket(self, r, t):
        return f"t/{slug(r['name'])}/{t['id']}.html"

    def p_area(self, r, a):
        return f"r/{slug(r['name'])}/area-{slug(a)}.html"

    def p_domain(self, d):
        return f"d/{slug(d)}.html"

    def p_tag(self, tag):
        return f"tag/{slug(tag)}.html"

    def where(self, r, plain=False):
        """`Domain › repository` for a title-first row (KIT-061); the repo alone without domains."""
        if self.m.single:
            return ""
        if plain:
            return (f"{self.m.dom(r)} › " if self.m.has_domains else "") + r["name"]
        d = f'<b>{e(self.m.dom(r))}</b><span class="sep">›</span>' if self.m.has_domains else ""
        return f'<span class="where">{d}<span>{e(r["name"])}</span></span>'

    def repo_note(self, r):
        """The repository name callers add to an id-first row on a many-repo page."""
        return "" if self.m.single or self.m.title_first else f"<span>{e(r['name'])}</span><span>·</span>"

    def pill(self, s):
        return f'<span class="st {s}">{LABEL[s]}</span>'

    def typ(self, t):
        return f'<span class="type {e(t["type"])}">{e(t["type"].lower())}</span>'

    def pri(self, t):
        return f'<span class="pri {e(t["priority"])}">{e(t["priority"])}</span>'

    def row(self, page, r, t, extra=""):
        now = self.m.now
        age = (f"started {ago(now, t['_s'])}" if t["state"] == "in-progress" else
               f"closed {ago(now, t['_x'])}" if t["state"] == "closed" else f"opened {ago(now, t['_c'])}")
        tags = "".join(f'<span class="tagchip">{e(x)}</span>' for x in t.get("tags", []))
        if self.m.title_first:   # KIT-061: what it is, then where it lives; the id last, small
            area = f'<span class="areachip">{e(t["area"])}</span>' if t["area"] else ""
            return (f'<a class="row tf" href="{self.rel(page, self.p_ticket(r, t))}">'
                    f'<span class="t">{e(t["title"])}</span><span class="meta">{self.pri(t)}{self.typ(t)}</span>'
                    f'<span class="sub2">{extra}{self.where(r)}{area}{tags}<span>{age}</span>'
                    f'<span class="fid">{e(t["id"])}</span></span></a>')
        area = f"<span>{e(t['area'])}</span><span>·</span>" if t["area"] else ""
        tags = f"{tags}<span>·</span>" if tags else ""
        return (f'<a class="row" href="{self.rel(page, self.p_ticket(r, t))}"><span class="id">{e(t["id"])}</span>'
                f'<span class="t">{e(t["title"])}</span><span class="meta">{self.pri(t)}{self.typ(t)}</span>'
                f'<span class="sub2">{extra}{area}{tags}<span>{age}</span></span></a>')

    def frame(self, page: str, title: str, nav: str, body: str) -> str:
        m, R = self.m, lambda to: self.rel(page, to)
        items = [("home", "Home", "index.html"), ("epics", "Epics", "epics.html"),
                 ("decisions", "Decisions", "decisions.html"), ("metrics", "Metrics", "metrics.html")]
        if m.conf.get("guide"):                                    # KIT-066
            items.append(("guide", m.conf["guide"]["label"], "guide.html"))
        navhtml = "".join(f'<a href="{R(h)}"{" aria-current=\"page\"" if k == nav else ""}>{l}</a>' for k, l, h in items)
        c = defaultdict(int)
        for _, t in m.all:
            c[t["state"]] += 1
        others = "".join(f'<a class="pm" href="{e(b["url"])}"><b>{e(b["name"])}</b>'
                         f'<span class="c">its own board</span></a>' for b in m.conf["boards"])
        portal = (f'<a class="pm" href="{e(m.conf["portal"])}"><b>All boards</b><span class="c">list</span></a><div class="pm-sep"></div>'
                  if m.conf["portal"] else "")
        switch = (f'<details class="proj" id="proj"><summary aria-label="Switch board"><span class="proj-k">Project</span>'
                  f'<span class="proj-v">{e(m.conf["name"])}</span><span class="caret" aria-hidden="true">▾</span></summary>'
                  f'<div class="proj-menu">{portal}<a class="pm" href="{R("index.html")}" aria-current="page"><b>{e(m.conf["name"])}</b>'
                  f'<span class="c">this board</span><span class="s">{len(m.repos)} repositor{"y" if m.single else "ies"} · '
                  f'{c["in-progress"]} in progress · {c["queued"]} in backlog</span></a>'
                  + (f'<div class="pm-sep"></div><div class="pm-h">Other boards — separate, nothing shared</div>{others}' if others else "")
                  + '</div></details>'
                  if (m.conf["boards"] or m.conf["portal"]) else
                  f'<div class="proj"><span class="proj-k">Project</span><span class="proj-v">{e(m.conf["name"])}</span></div>')
        opt = lambda r: f'<option value="{R(self.p_repo(r))}">{e(r["name"])}{f" ({len(r["tickets"])})" if r["tickets"] else ""}</option>'
        order = [*m.active, *m.quiet, *m.unset]
        if m.has_domains:   # KIT-061: grouped by domain
            opts = "".join(f'<optgroup label="{e(d)}">' + "".join(opt(r) for r in order if m.dom(r) == d) + "</optgroup>"
                           for d in sorted(m.domains, key=lambda d: (d == "Other", d.lower())))
        else:
            opts = "".join(opt(r) for r in order)
        jump = "" if m.single else (
            '<select class="jump" id="jump" aria-label="Go to a repository"><option value="">Go to repository…</option>' + opts + "</select>")
        lookup = ('<form class="lookup" id="ticket-lookup"><label for="ticket-id">Find ticket</label>'
                  '<input id="ticket-id" placeholder="UOASIG-008" autocomplete="off" spellcheck="false">'
                  '<button type="submit">Find</button></form><div id="ticket-results" role="status"></div>')
        themes = ("current", "Jira", "Linear", "GitHub Issues", "GitLab", "Azure DevOps")
        theme = ('<label class="theme-choice" for="theme"><span>Theme</span><select id="theme" aria-label="Choose board theme">' +
                 "".join(f'<option value="{slug(label)}">{e(label)}</option>' for label in themes) + '</select></label>')
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<meta name="board-built" content="{e(m.built)}"><title>{e(title)} · {e(m.conf["name"])}</title>'
                f'<script>try{{var t=localStorage.getItem("board-theme");if(t)document.documentElement.dataset.themePreset=t}}catch(e){{}}</script>'
                f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="{e(FONTS)}">'
                f'<link rel="stylesheet" href="{R("board.css")}"></head><body>'
                f'<header class="top"><div class="top-in">{switch}<nav class="nav" aria-label="Board sections">{navhtml}</nav>{jump}{theme}</div></header>'
                f'<main class="wrap">{lookup}{body}</main>'
                f'<footer class="foot">Built {at(m.now)} from each repository\'s <span class="mono">origin</span>'
                f'{(" · refreshed " + e(m.conf["refresh"])) if m.conf["refresh"] else ""}</footer>'
                f'<script src="{R("board.js")}"></script></body></html>')

    def crumbs(self, page, parts):
        links = [f'<a href="{self.rel(page, "index.html")}">{e(self.m.conf["name"])}</a>']
        for label, to in parts:
            links.append(f'<a href="{self.rel(page, to)}">{e(label)}</a>' if to else f"<span>{e(label)}</span>")
        return '<div class="crumbs">' + "<span>/</span>".join(links) + "</div>"

    def counter_groups(self):
        """D41: the counter and its page consume the same selection and ordering."""
        all_ = self.m.all
        oldest = lambda rt: (rt[1]["_c"] or datetime.min, rt[0]["name"], rt[1]["id"])
        priority = lambda rt: (rt[1]["priority"], *oldest(rt))
        return [
            ("in-progress", "In progress", "Nothing is in progress.", "live",
             sorted((rt for rt in all_ if rt[1]["state"] == "in-progress"),
                    key=lambda rt: (rt[1]["_s"] or datetime.min, *oldest(rt)))),
            ("backlog", "Backlog", "Nothing is in the backlog.", "",
             sorted((rt for rt in all_ if rt[1]["state"] == "queued"), key=priority)),
            ("blocked", "Blocked", "Nothing is blocked.", "hot",
             sorted((rt for rt in all_ if rt[1]["state"] == "blocked"), key=priority)),
            ("closed-week", "Closed in the last 7 days", "Nothing closed in the last 7 days.", "",
             sorted((rt for rt in all_ if rt[1]["_x"] and self.m.now - rt[1]["_x"] < timedelta(days=7)),
                    key=lambda rt: (rt[1]["_x"], *oldest(rt)), reverse=True)),
            ("open-epics", "Open epics", "There are no open epics.", "",
             sorted((rt for rt in all_ if rt[1]["type"] == "EPIC" and rt[1]["state"] != "closed"),
                    key=oldest, reverse=True)),
        ]

    def counts(self):
        return '<div class="counts">' + "".join(
            f'<a class="count {cls if rows else ""}" href="lists/{key}.html">'
            f'<b>{len(rows)}</b><span>{label}</span></a>'
            for key, label, _, cls, rows in self.counter_groups()) + "</div>"

    def counter_pages(self):
        pages = []
        for key, label, empty, _, tickets in self.counter_groups():
            page = f"lists/{key}.html"
            rows = ""
            for r, t in tickets:
                extra = self.repo_note(r)
                if key == "open-epics":
                    kids = [kt for _, kt in self.m.all if kt["epic"] == t["id"]]
                    done = sum(kt["state"] == "closed" for kt in kids)
                    extra += f'<span class="prog">{done} of {len(kids)} stories closed</span>'
                rows += self.row(page, r, t, extra)
            body = (f'{self.crumbs(page, [(label, None)])}<h1>{label} '
                    f'<span class="num">{len(tickets)}</span></h1>'
                    f'<section class="panel spaced"><div class="panel-b"><div class="rows">{rows}</div>'
                    + (f'<div class="empty">{empty}</div>' if not tickets else "") + '</div></section>')
            pages.append((page, self.frame(page, label, "home", body)))
        return pages

    def attention(self):
        out, now = [], self.m.now
        for r, t in self.m.all:
            if t["state"] == "blocked":
                out.append((0, r, t, "blocked", "Blocked"))
            elif t["state"] != "closed" and t["priority"] == "P1":
                out.append((1, r, t, "p1", "P1 open"))
            elif t["state"] == "in-progress" and t["_s"] and now - t["_s"] > timedelta(days=7):
                out.append((2, r, t, "stalled", f"In progress {(now - t['_s']).days} days"))
        return sorted(out, key=lambda x: (x[0], x[2]["_s"] or x[2]["_c"] or datetime.min))

    def areas(self, r):
        g = defaultdict(list)
        for t in r["tickets"]:
            g[t["area"] or "(no area)"].append(t)
        rows = []
        for a, ts in g.items():
            times = [x for t in ts for x in (t["_c"], t["_s"], t["_x"]) if x]
            n_open = sum(1 for t in ts if t["state"] != "closed")
            rows.append((a, ts, n_open, max(times) if times else None))
        return sorted(rows, key=lambda x: (-x[2], -(x[3].timestamp() if x[3] else 0)))

    # ── pages ──
    def home(self):
        page, m = "index.html", self.m
        att = self.attention()
        shown = att[:8]
        att_html = ('<div class="rows">' + "".join(
            self.row(page, r, t, f'<span class="reason {w}">{e(lab)}</span>' + self.repo_note(r))
            for _, r, t, w, lab in shown) + "</div>" +
            (f'<div class="empty">+ {len(att) - len(shown)} more — see the {"areas" if m.single else "repositories"} below</div>' if len(att) > len(shown) else "")
        ) if att else '<div class="empty">Nothing blocked, no P1s open, nothing in progress for more than a week.</div>'
        table = (self.area_table(page, m.repos[0]) if m.single else
                 self.domain_table(page) if m.has_domains else self.repo_table(page))
        table += self.words_panel(page)
        if m.title_first:   # KIT-061: what happened, to what, where; the id last
            feed = "".join(
                f'<div class="ev"><span class="when">{ago(m.now, when)}</span><a href="{self.rel(page, self.p_ticket(r, t))}">'
                f'<span class="verb {verb}">{verb}</span> <span class="ttl strong">{e(t["title"])}</span><br>'
                f'<span class="rp">{e(" · ".join(x for x in (self.where(r, plain=True), t["area"]) if x))}</span> '
                f'<span class="fid">{e(t["id"])}</span></a></div>'
                for when, verb, r, t in m.events()[:14]) or '<div class="empty">No activity yet.</div>'
        else:
            feed = "".join(
                f'<div class="ev"><span class="when">{ago(m.now, when)}</span><a href="{self.rel(page, self.p_ticket(r, t))}">'
                f'<span class="verb {verb}">{verb}</span> <span class="id">{e(t["id"])}</span><br><span class="ttl">{e(t["title"])}</span>'
                f'<br><span class="rp">{e(t["area"] if m.single else r["name"])}</span></a></div>'
                for when, verb, r, t in m.events()[:14]) or '<div class="empty">No activity yet.</div>'
        tickets = [t for _, t in m.all]
        body = (f'<h1>Right now</h1><div class="sub">{len(m.repos)} repositor{"y" if m.single else "ies"} · {len(tickets)} tickets · '
                f'as of {at(m.now)}, from each repository\'s <span class="mono">origin</span></div>{self.counts()}'
                f'<div class="cols"><div class="stack"><section class="panel"><div class="panel-h"><h2>Needs attention</h2>'
                f'<span class="prog">{len(att) or ""}</span></div><div class="panel-b">{att_html}</div></section>{self.open_panel(page)}{table}</div>'
                f'<aside class="panel"><div class="panel-h"><h2>Latest activity</h2></div><div class="panel-b"><div class="feed">{feed}</div></div></aside></div>')
        return page, self.frame(page, "Home", "home", body)

    def repo_table(self, page, subset=None, heading="Repositories with work"):
        m = self.m
        active = [r for r in m.active if subset is None or r in subset]
        quiet_r = [r for r in m.quiet if subset is None or r in subset]
        unset_r = [r for r in m.unset if subset is None or r in subset]
        total = len(m.repos) if subset is None else len(subset)
        z = lambda n: str(n) if n else '<span class="z">0</span>'
        rows = "".join(
            f'<tr><td><a href="{self.rel(page, self.p_repo(r))}"><span class="rname">{e(r["name"])}</span>'
            f'{f"<span class=rpfx>{e(r["prefix"])}</span>" if r["prefix"] else ""}</a>{self.stale(r)}</td>'
            f'<td class="n">{z(len(r["by"]["in-progress"]))}</td><td class="n">{z(len(r["by"]["queued"]))}</td>'
            f'<td class="n">{(f"<b class=bad>{len(r["by"]["blocked"])}</b>") if r["by"]["blocked"] else z(0)}</td>'
            f'<td class="n hide-sm">{len(r["by"]["closed"])}</td><td class="hide-sm">{sparkline([n for _, n in weekly(r["tickets"], m.now)])}</td>'
            f'<td class="hide-sm when2">{ago(m.now, r["last"])}</td></tr>' for r in active)
        quiet = ""
        if quiet_r or unset_r:
            chips = "".join(f'<a class="chip" href="{self.rel(page, self.p_repo(r))}">{e(r["name"])}'
                            f'{f"<span class=num>{len(r["decisions"])} dec</span>" if r["decisions"] else ""}</a>' for r in quiet_r)
            unset = "".join(f'<a class="chip dash" href="{self.rel(page, self.p_repo(r))}">{e(r["name"])}</a>' for r in unset_r)
            quiet = (f'<details class="quiet"><summary>{len(quiet_r)} more repositories use the kit but have no tickets yet'
                     f'{f" · {len(unset_r)} not set up" if unset_r else ""}</summary><div class="chips">{chips}</div>'
                     f'{f"<div class=chips>{unset}</div>" if unset else ""}</details>')
        return (f'<section class="panel"><div class="panel-h"><h2>{e(heading)}</h2><span class="prog">{len(active)} of {total}</span></div>'
                f'<div class="tbl-wrap"><table><thead><tr><th>Repository</th><th class="n">In&nbsp;progress</th><th class="n">Backlog</th>'
                f'<th class="n">Blocked</th><th class="n hide-sm">Closed</th><th class="hide-sm">Closed / week, 12 wks</th><th class="hide-sm">Last activity</th>'
                f'</tr></thead><tbody>{rows or "<tr><td colspan=7 class=empty>No tickets in this project yet.</td></tr>"}</tbody></table></div>{quiet}</section>')

    def area_table(self, page, r):
        z = lambda n: str(n) if n else '<span class="z">0</span>'
        rows = ""
        for a, ts, _, last in self.areas(r):
            n = defaultdict(int)
            for t in ts:
                n[t["state"]] += 1
            rows += (f'<tr><td><a class="rname" href="{self.rel(page, self.p_area(r, a))}">{e(a)}</a></td>'
                     f'<td class="n">{z(n["in-progress"])}</td><td class="n">{z(n["queued"])}</td>'
                     f'<td class="n">{(f"<b class=bad>{n["blocked"]}</b>") if n["blocked"] else z(0)}</td><td class="n hide-sm">{n["closed"]}</td>'
                     f'<td class="hide-sm">{sparkline([c for _, c in weekly(ts, self.m.now)])}</td><td class="hide-sm when2">{ago(self.m.now, last)}</td></tr>')
        return (f'<section class="panel"><div class="panel-h"><h2>By area</h2><span class="prog">{len(self.areas(r))} areas</span></div>'
                f'<div class="tbl-wrap"><table><thead><tr><th>Area</th><th class="n">In&nbsp;progress</th><th class="n">Backlog</th><th class="n">Blocked</th>'
                f'<th class="n hide-sm">Closed</th><th class="hide-sm">Closed / week, 12 wks</th><th class="hide-sm">Last activity</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div></section>')

    # ── KIT-061: domains, tags, new words ──
    def domain_stats(self, repos):
        ts = [t for r in repos for t in r["tickets"]]
        n = defaultdict(int)
        for t in ts:
            n[t["state"]] += 1
        lasts = [r["last"] for r in repos if r["last"]]
        return ts, n, (max(lasts) if lasts else None)

    def domain_table(self, page):
        m = self.m
        z = lambda n: str(n) if n else '<span class="z">0</span>'
        busy, idle = [], []
        for d, repos in m.domains.items():
            ts, n, last = self.domain_stats(repos)
            (busy if ts else idle).append((d, repos, ts, n, last))
        busy.sort(key=lambda x: x[4] or datetime.min, reverse=True)
        rows = "".join(
            f'<tr><td><a href="{self.rel(page, self.p_domain(d))}"><span class="dname">{e(d)}</span></a>'
            f'<span class="dsub">{" · ".join(e(r["name"]) for r in repos if r["tickets"])}</span></td>'
            f'<td class="n">{z(n["in-progress"])}</td><td class="n">{z(n["queued"])}</td>'
            f'<td class="n">{(f"<b class=bad>{n["blocked"]}</b>") if n["blocked"] else z(0)}</td><td class="n hide-sm">{n["closed"]}</td>'
            f'<td class="hide-sm">{sparkline([c for _, c in weekly(ts, m.now)])}</td><td class="hide-sm when2">{ago(m.now, last)}</td></tr>'
            for d, repos, ts, n, last in busy)
        idle_html = (f'<div class="panel-foot">No tickets yet: ' + " · ".join(
            f'<a href="{self.rel(page, self.p_domain(d))}">{e(d)}</a>' for d, *_ in sorted(idle, key=lambda x: x[0].lower())) + "</div>") if idle else ""
        return (f'<section class="panel"><div class="panel-h"><h2>By domain</h2><span class="prog">{len(busy)} of {len(m.domains)} with work</span></div>'
                f'<div class="tbl-wrap"><table><thead><tr><th>Domain</th><th class="n">In&nbsp;progress</th><th class="n">Backlog</th>'
                f'<th class="n">Blocked</th><th class="n hide-sm">Closed</th><th class="hide-sm">Closed / week, 12 wks</th><th class="hide-sm">Last activity</th>'
                f'</tr></thead><tbody>{rows or "<tr><td colspan=7 class=empty>No tickets in this project yet.</td></tr>"}</tbody></table></div>{idle_html}</section>')

    def words_panel(self, page):
        m = self.m
        if not m.vocab:
            return ""
        ids = {t["id"]: (r, t) for r, t in m.all}
        def first(row):
            ft = row.get("first_ticket", "")
            return (f' · for <a href="{self.rel(page, self.p_ticket(*ids[ft]))}">{e(ft)}</a>' if ft in ids else
                    (f" · for {e(ft)}" if ft and ft != "-" else ""))
        # KIT-063: words added FOR a ticket are what an agent decided on its own — list them;
        # a starter list (first_ticket `-`) would bury them, so it is counted on one line.
        ticketed = [x for x in m.new_words if re.fullmatch(_ID, x[3].get("first_ticket", ""))]
        starter = len(m.new_words) - len(ticketed)
        items = "".join(
            f'<div class="wl"><span class="{"areachip" if kind == "areas" else "tagchip"}">{e(w)}</span>'
            f'<span>{kind[:-1]} · “{e(row.get("meaning", ""))}” · added {ago(m.now, d)}{first(row)}</span></div>'
            for d, kind, w, row in ticketed)
        if starter:
            items += f'<div class="wl muted">{starter} starter word{"s" if starter != 1 else ""} added (see the lists)</div>'
        return (f'<section class="panel words"><div class="panel-h"><h2>Words added this week</h2><span class="prog">{len(ticketed) or ""}</span></div>'
                f'<div class="panel-b">{items or "<div class=empty>No new area or tag words this week.</div>"}</div></section>')

    def open_panel(self, page):
        """Every open ticket on a many-repository board's home page (KIT-063): In progress,
        Blocked, Backlog; by priority, then most recent activity; 15 shown, the rest folded."""
        m = self.m
        if m.single:
            return ""
        prio = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
        last = lambda t: max([x for x in (t["_c"], t["_s"], t["_x"]) if x] or [datetime.min])
        groups = []
        for s in ("in-progress", "blocked", "queued"):
            ps = sorted([(r, t) for r, t in m.all if t["state"] == s],
                        key=lambda p: (prio.get(p[1]["priority"], 2), -last(p[1]).timestamp() if last(p[1]) != datetime.min else 0))
            groups += [(s, r, t) for r, t in ps]
        if not groups:
            return ('<section class="panel"><div class="panel-h"><h2>Open now</h2></div>'
                    '<div class="panel-b"><div class="empty">Nothing open.</div></div></section>')
        def render(items):
            out, cur = "", None
            for s, r, t in items:
                if s != cur:
                    out += f'<div class="lane-h open-h">{self.pill(s)}<span class="num">{sum(1 for g in groups if g[0] == s)}</span></div>'
                    cur = s
                out += self.row(page, r, t, self.repo_note(r))
            return out
        cap = 15
        more = (f'<details class="more-items"><summary>Show all {len(groups)}</summary><div class="rows">{render(groups[cap:])}</div></details>'
                if len(groups) > cap else "")
        return (f'<section class="panel open-now"><div class="panel-h"><h2>Open now</h2><span class="prog">{len(groups)}</span></div>'
                f'<div class="panel-b"><div class="rows">{render(groups[:cap])}</div>{more}</div></section>')

    def pairs_lane(self, page, key, pairs):
        cap = 8
        head = f'<div class="lane-h">{self.pill(key)}<span class="num">{len(pairs)}</span></div>'
        if not pairs:
            return f'<section class="lane">{head}<div class="empty">{"Nothing in the backlog" if key == "queued" else "Nothing " + LABEL[key].lower()}.</div></section>'
        first = "".join(self.row(page, r, t, self.repo_note(r)) for r, t in pairs[:cap])
        rest = "".join(self.row(page, r, t, self.repo_note(r)) for r, t in pairs[cap:])
        more = f'<details class="more-items"><summary>Show all {len(pairs)}</summary><div class="rows">{rest}</div></details>' if rest else ""
        return f'<section class="lane">{head}<div class="rows">{first}</div>{more}</section>'

    def domain_page(self, d):
        m, page = self.m, self.p_domain(d)
        repos = m.domains[d]
        ts, n, last = self.domain_stats(repos)
        prio = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
        pick = lambda s: [(r, t) for r in repos for t in r["by"][s]]
        prog = sorted(pick("in-progress"), key=lambda p: p[1]["_s"] or datetime.min)
        que = sorted(pick("queued"), key=lambda p: (prio.get(p[1]["priority"], 2), p[1]["_c"] or datetime.min))
        sub = (f'{len(repos)} repositor{"y" if len(repos) == 1 else "ies"} · {len(ts)} tickets'
               f'{f" · last activity {ago(m.now, last)}" if last else ""}')
        body = (f'{self.crumbs(page, [(d, None)])}<h1>{e(d)}</h1><div class="sub">{sub}</div>'
                f'<div class="lanes">{self.pairs_lane(page, "in-progress", prog)}{self.pairs_lane(page, "queued", que)}'
                f'{self.pairs_lane(page, "blocked", pick("blocked"))}</div>'
                f'<div class="stack" style="margin-top:22px">{self.repo_table(page, repos, "Repositories")}</div>')
        return page, self.frame(page, d, "", body)

    def tag_page(self, tag):
        m, page = self.m, self.p_tag(tag)
        pairs = [(r, t) for r, t in m.all if tag in t.get("tags", [])]
        live = [p for p in pairs if p[1]["state"] != "closed"]
        done = sorted([p for p in pairs if p[1]["state"] == "closed"], key=lambda p: p[1]["_x"] or datetime.min, reverse=True)
        meaning = ((m.vocab.get("tags") or {}).get(tag) or {}).get("meaning", "")
        rows = lambda ps: '<div class="rows">' + "".join(self.row(page, r, t, self.pill(t["state"]) + self.repo_note(r)) for r, t in ps) + "</div>"
        body = (f'{self.crumbs(page, [("tag: " + tag, None)])}<h1><span class="tagchip big">{e(tag)}</span></h1>'
                f'<div class="sub">{f"“{e(meaning)}” · " if meaning else ""}{len(pairs)} tickets across '
                f'{len({r["name"] for r, _ in pairs})} repositories</div><div class="stack" style="margin-top:22px">'
                f'<section class="panel"><div class="panel-h"><h2>Open</h2><span class="prog">{len(live)}</span></div><div class="panel-b">'
                f'{rows(live) if live else "<div class=empty>Nothing open.</div>"}</div></section>'
                f'<section class="panel"><div class="panel-h"><h2>Closed</h2><span class="prog">{len(done)}</span></div><div class="panel-b">'
                f'{rows(done) if done else "<div class=empty>Nothing closed yet.</div>"}</div></section></div>')
        return page, self.frame(page, "tag: " + tag, "", body)

    def next_id(self, r):
        """The next ticket id in a repository, by wi.py's rule (KIT-059): its first active prefix,
        numbered past everything used under it and under its retired prefixes."""
        active = (r.get("prefix") or "").split()
        if not active:
            return ""
        pool = {active[0], *r.get("retired", [])}
        used = [int(t["id"].rsplit("-", 1)[1]) for t in r["tickets"] if t["id"].rsplit("-", 1)[0] in pool]
        return f"{active[0]}-{(max(used) if used else 0) + 1:03d}"

    def guide_page(self):
        """KIT-066: the project's own words on how tickets are made, then the facts a reader
        cannot work out alone — every repository's prefix and next id — and the word lists."""
        m, page, g = self.m, "guide.html", self.m.conf["guide"]
        try:
            text = Path(g["path"]).read_text(encoding="utf-8")
        except OSError:
            text = f"## Missing\n\nThe guide file {g['path']} could not be read."
        secs = re.split(r"^## ", re.sub(r"^# .*\n", "", text, count=1, flags=re.M), flags=re.M)
        intro, secs = secs[0].strip(), secs[1:]
        panels = (f'<section class="panel"><div class="panel-b pad guide">{markdown_body(intro)}</div></section>' if intro else "")
        for s in secs:
            title, _, body = s.partition("\n")
            panels += (f'<section class="panel"><div class="panel-b pad guide"><h2>{e(title.strip())}</h2>'
                       f'{markdown_body(body)}</div></section>')
        order = sorted(m.repos, key=lambda r: (m.dom(r) == "Other", m.dom(r).lower(), r["name"]))
        rows = "".join(
            f'<tr><td>{e(m.dom(r)) if m.has_domains else ""}</td><td><a class="rname" href="{self.rel(page, self.p_repo(r))}">{e(r["name"])}</a></td>'
            f'<td class="mono">{e(r.get("prefix") or "—")}</td><td class="mono muted">{e(" ".join(r.get("retired", [])) or "—")}</td>'
            f'<td class="mono"><b>{e(self.next_id(r)) or "not set up"}</b></td></tr>' for r in order)
        table = (f'<section class="panel"><div class="panel-h"><h2>Every repository: its prefix and next ticket id</h2>'
                 f'<span class="prog">{len(m.repos)}</span></div><div class="tbl-wrap"><table><thead><tr>'
                 f'<th>{"Domain" if m.has_domains else ""}</th><th>Repository</th><th>Prefix</th><th>Retired prefix (old ids stay valid)</th>'
                 f'<th>Next id</th></tr></thead><tbody>{rows}</tbody></table></div></section>')
        words = ""
        for kind in ("areas", "tags"):
            rows_ = m.vocab.get(kind)
            if not rows_:
                continue
            items = "".join(
                f'<tr><td><span class="{"areachip" if kind == "areas" else "tagchip"}">{e(w)}</span></td>'
                + (f'<td class="muted" colspan="2">merged into <b>{e(r["merged_into"])}</b></td>' if r.get("merged_into") else
                   f'<td>{e(r.get("meaning", ""))}</td><td class="muted">{e(", ".join(r["synonyms"]))}</td>') + "</tr>"
                for w, r in sorted(rows_.items()))
            words += (f'<section class="panel"><div class="panel-h"><h2>{"Areas — one per ticket" if kind == "areas" else "Tags — none or several"}</h2>'
                      f'<span class="prog">{len(rows_)}</span></div>'
                      # D38: synonyms explain the canonical word; creation requires it.
                      f'<div class="panel-b pad"><p>For new tickets, use the word in the first column. '
                      f'Alternative names help you find it; they are not accepted when creating a ticket.</p></div>'
                      f'<div class="tbl-wrap"><table><thead><tr><th>Word</th><th>Meaning</th>'
                      f'<th>Alternative names (synonyms)</th></tr></thead><tbody>{items}</tbody></table></div></section>')
        body = (f'{self.crumbs(page, [(g["label"], None)])}<h1>{e(g["label"])}</h1>'
                f'<div class="stack" style="margin-top:18px">{panels}{table}{words}</div>')
        return page, self.frame(page, g["label"], "guide", body)

    def stale(self, r):
        return ' <span class="stale" title="git fetch failed — showing what origin last held">stale</span>' if r["fetched"] is False else ""

    def lane(self, page, r, key, items):
        cap = 8
        head = f'<div class="lane-h">{self.pill(key)}<span class="num">{len(items)}</span></div>'
        if not items:
            return f'<section class="lane">{head}<div class="empty">{"Nothing in the backlog" if key == "queued" else "Nothing " + LABEL[key].lower()}.</div></section>'
        first = "".join(self.row(page, r, t) for t in items[:cap])
        rest = "".join(self.row(page, r, t) for t in items[cap:])
        more = f'<details class="more-items"><summary>Show all {len(items)}</summary><div class="rows">{rest}</div></details>' if rest else ""
        return f'<section class="lane">{head}<div class="rows">{first}</div>{more}</section>'

    def repo_page(self, r, area=None):
        m = self.m
        page = self.p_area(r, area) if area else self.p_repo(r)
        pick = (lambda ts: [t for t in ts if (t["area"] or "(no area)") == area]) if area else (lambda ts: ts)
        prio = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
        prog = sorted(pick(r["by"]["in-progress"]), key=lambda t: t["_s"] or datetime.min)
        que = sorted(pick(r["by"]["queued"]), key=lambda t: (prio.get(t["priority"], 2), t["_c"] or datetime.min))
        blk, clo = pick(r["by"]["blocked"]), pick(r["by"]["closed"])
        recent = sorted(clo, key=lambda t: t["_x"] or datetime.min, reverse=True)[:10]
        decs = list(reversed(r["decisions"]))[:6]
        dcrumb = [(m.dom(r), self.p_domain(m.dom(r)))] if m.has_domains else []
        crumbs = self.crumbs(page, dcrumb + ([(r["name"], self.p_repo(r)), (area, None)] if area else [(r["name"], None)]))
        title = f'{e(area)} <span class="h1-sub">in {e(r["name"])}</span>' if area else e(r["name"])
        sub = (f'{f"Ticket prefix <span class=mono>{e(r["prefix"])}</span> · " if r["prefix"] else ""}{len(pick(r["tickets"]))} tickets'
               f'{"" if area else f" · {len(r["decisions"])} decisions"}{f" · last activity {ago(m.now, r["last"])}" if r["last"] else ""}'
               f'{"" if r["adopted"] else " · <b>kit not set up</b>"}{self.stale(r)}'
               f'{f" · <a class=more href={self.rel(page, self.p_repo(r))}>all areas</a>" if area else ""}')
        dec_panel = (f'<section class="panel"><div class="panel-h"><h2>Latest decisions</h2><a class="more" href="{self.rel(page, "decisions.html")}#repo={slug(r["name"])}">All {len(r["decisions"])}</a></div>'
                     f'<div class="panel-b">' + ('<div class="rows">' + "".join(
                         f'<div class="row two"><span class="id">{e(d["id"])}</span><span class="t dtitle">{e(d["title"])}</span></div>' for d in decs) + "</div>"
                         if decs else '<div class="empty">No decisions recorded.</div>') + "</div></section>")
        if not r["tickets"]:
            hint = (f'Work items appear here as soon as one is filed — <span class="mono">python3 scripts/wi.py new {e((r["prefix"] or "PFX").split()[0])}-001 STORY "…"</span>.'
                    if r["adopted"] else "This repository has not installed the kit.")
            body = f'{crumbs}<h1>{title}</h1><div class="sub">{sub}</div><div class="cols"><section class="panel"><div class="panel-b"><p class="lede">No tickets yet. {hint}</p></div></section>{dec_panel}</div>'
            return page, self.frame(page, r["name"], "", body)
        areas = defaultdict(int)
        for t in r["tickets"]:
            if t["state"] != "closed" and t["area"]:
                areas[t["area"]] += 1
        area_panel = "" if area or not areas else (
            '<section class="panel"><div class="panel-h"><h2>Open by area</h2></div><div class="panel-b"><dl class="facts">' + "".join(
                f'<dt><a href="{self.rel(page, self.p_area(r, a))}">{e(a)}</a></dt><dd class="num">{n}</dd>'
                for a, n in sorted(areas.items(), key=lambda x: -x[1])) + "</dl></div></section>")
        closed_rows = "".join(
            f'<tr><td><a class="id" href="{self.rel(page, self.p_ticket(r, t))}">{e(t["id"])}</a></td><td>{e(t["title"])}</td>'
            f'<td class="hide-sm">{self.typ(t)}</td><td class="when2">{t["_x"].strftime("%b %-d") if t["_x"] else "—"}</td>'
            f'<td class="n hide-sm">{span(t["_c"], t["_x"]) or "—"}</td></tr>' for t in recent) or '<tr><td colspan="5" class="empty">Nothing closed yet.</td></tr>'
        body = (f'{crumbs}<h1>{title}</h1><div class="sub">{sub}</div>'
                f'<div class="lanes">{self.lane(page, r, "in-progress", prog)}{self.lane(page, r, "queued", que)}{self.lane(page, r, "blocked", blk)}</div>'
                f'<div class="cols"><section class="panel"><div class="panel-h"><h2>Recently closed</h2><span class="prog">{len(clo)} closed in total</span></div>'
                f'<div class="tbl-wrap"><table><thead><tr><th>Ticket</th><th>Title</th><th class="hide-sm">Type</th><th>Closed</th><th class="n hide-sm">Opened → closed</th></tr></thead>'
                f'<tbody>{closed_rows}</tbody></table></div></section><div class="stack">{area_panel}{dec_panel}</div></div>')
        return page, self.frame(page, area or r["name"], "", body)

    def update_log_table(self, t, raw=""):
        # Non-log prose belongs above the table; committed source lines stay untouched (D42).
        prose = "\n".join(line for line in raw.splitlines() if not LOG_ROW.fullmatch(line))
        rendered = []
        for row in t.get("history", []):
            note = row["note"]
            event = ("Reopened" if "reopened:" in note or "status: CLOSED -> OPEN" in note else
                     "Created" if note.startswith("created") else "Work started" if note.startswith("started") else
                     "Closed" if note.startswith("closed") else "Updated")
            subject = re.sub(r"^" + re.escape(t["id"]) + r"\s*:\s*", "", row.get("subject", ""))
            what = e(subject or event)
            if row.get("url"):
                what = f'<a href="{e(row["url"])}">{what}</a>'
            detail = " ".join(note.split()[:12])
            if len(detail) > 90:
                detail = detail[:87] + "…"
            if detail != note:
                detail = detail.rstrip("…") + "…"
            rendered.append(f'<tr><td class="nowrap">{e(row["when"])}</td><td>{e(row["actor"])}</td>'
                            f'<td>{what}<div class="log-detail">{event}'
                            + (f' · {e(detail)}' if event == "Updated" else "") + '</div></td></tr>')
        return (markdown_body(prose) + '<div class="tbl-wrap update-log"><table><thead><tr>'
                '<th>When (ET)</th><th>Who</th><th>What happened</th></tr></thead><tbody>'
                + "".join(rendered) + '</tbody></table></div>'
                + ('<p class="empty">No update history has been recorded for this ticket.</p>' if not rendered else ""))

    def ticket_page(self, r, t):
        m, page = self.m, self.p_ticket(r, t)
        epic = next(((er, et) for er, et in m.all if et["id"] == t["epic"] and et["type"] == "EPIC"), None) if t["epic"] else None
        dt = {d["id"]: d["title"] for d in r["decisions"]}
        cited = [d for d in t["cites"] if d in dt]
        steps = [("Opened", t["_c"]), ("Started", t["_s"] if t["started"] else None), ("Closed", t["_x"])]
        tl = "".join(
            f'<div class="step{" done" if d else ""}"><div class="lab">{lab}</div><div class="at">{at(d)}</div>'
            + (f'<div class="gap">{span(steps[i-1][1], d)} after {steps[i-1][0].lower()}</div>' if i and d and steps[i-1][1] else "")
            + "</div>" for i, (lab, d) in enumerate(steps))
        kids = [(kr, kt) for kr, kt in m.all if kt["epic"] == t["id"]] if t["type"] == "EPIC" else []
        kids_html = (f'<section class="panel"><div class="panel-b pad"><h2>Stories in this epic</h2><div class="rows">' + "".join(
            self.row(page, kr, kt, self.pill(kt["state"]) + self.repo_note(kr)) for kr, kt in kids)
            + "</div></div></section>") if kids else ""
        seen_slugs = set()
        section_links = ['<a class="sec" href="#ticket-content">All</a>']
        rendered_sections = []
        for section in t["sections"]:
            base = section_slug(section["title"])
            sid, n = base, 2
            while sid in seen_slugs:
                sid = f"{base}-{n}"; n += 1
            seen_slugs.add(sid)
            section_links.append(f'<a class="sec" href="#{e(sid)}">{e(section["title"])}</a>')
            content = (self.update_log_table(t, section["body"]) if section["title"].lower() == "update log"
                       else markdown_body(section["body"]))
            rendered_sections.append(f'<section class="ticket-section" id="{e(sid)}"><h2>{e(section["title"])}</h2>{content}</section>')
        if not any(section["title"].lower() == "update log" for section in t["sections"]):
            section_links.append('<a class="sec" href="#update-log">Update Log</a>')
            rendered_sections.append('<section class="ticket-section" id="update-log"><h2>Update Log</h2>'
                                     + self.update_log_table(t) + '</section>')
        commits = r["commits"].get(t["id"], [])
        modifier = (t["history"][-1]["actor"] if t.get("history") else
                    next((c["actor"] for c in commits if c.get("actor")), ""))
        crumbs = self.crumbs(page, ([(m.dom(r), self.p_domain(m.dom(r)))] if m.has_domains else []) + [(r["name"], self.p_repo(r))]
                             + ([(t["area"], self.p_area(r, t["area"]))] if m.single and t["area"] else []) + [(t["id"], None)])
        tag_links = " ".join(f'<a class="tagchip" href="{self.rel(page, self.p_tag(x))}">{e(x)}</a>' for x in t.get("tags", []))
        branch_note = ' <span class="stale ok">branch at origin</span>' if t["branch"] and t["state"] == "in-progress" else ""
        body = (f'{crumbs}<div class="thead"><span class="id">{e(t["id"])}</span>{self.typ(t)}{self.pill(t["state"])}{self.pri(t)}{branch_note}</div>'
                f'<h1>{e(t["title"])}</h1><div class="cols"><div class="stack">'
                f'<section class="panel"><div class="panel-b pad"><h2>Summary</h2><p class="lede">{e(t["summary"]) or "<span class=empty>No summary section.</span>"}</p></div></section>'
                f'<section class="panel"><div class="panel-b pad"><h2>Timeline</h2><div class="timeline">{tl}</div></div></section>{kids_html}'
                f'<section class="panel"><div class="panel-b pad"><h2>In the ticket</h2><div class="secs">{"".join(section_links)}</div>'
                f'<div class="path">{e(r["name"])}/{e(t["path"])}</div></div></section>'
                f'<section class="panel" id="ticket-content"><div class="panel-b pad">{"".join(rendered_sections)}</div></section></div><div class="stack">'
                f'<section class="panel"><div class="panel-b pad"><dl class="facts">'
                + (f'<dt>Domain</dt><dd><a href="{self.rel(page, self.p_domain(m.dom(r)))}">{e(m.dom(r))}</a></dd>' if m.has_domains else "")
                + f'<dt>Repository</dt><dd><a href="{self.rel(page, self.p_repo(r))}">{e(r["name"])}</a></dd>'
                f'<dt>Area</dt><dd>{f"<a href={self.rel(page, self.p_area(r, t["area"]))}>{e(t["area"])}</a>" if t["area"] else "—"}</dd>'
                + (f'<dt>Tags</dt><dd>{tag_links}</dd>' if tag_links else "")
                + f'<dt>Epic</dt><dd>{f"<a href={self.rel(page, self.p_ticket(*epic))}>{e(t["epic"])}</a>" if epic else (e(t["epic"]) or "—")}</dd>'
                f'<dt>Created by</dt><dd>{e(t.get("creator", "")) or "—"}</dd>'
                f'<dt>Last updated by</dt><dd>{e(modifier) or "—"}</dd>'
                f'<dt>Closed by</dt><dd>{e(t.get("closer", "")) or "—"}</dd>'
                f'<dt>Estimate</dt><dd class="num">{e(t["estimate"]) or "—"}</dd><dt>Priority</dt><dd>{self.pri(t)}</dd></dl></div></section>'
                f'<section class="panel"><div class="panel-h"><h2>Decisions carried</h2></div><div class="panel-b">' + (
                    '<div class="rows">' + "".join(f'<div class="row two"><span class="id">{e(d)}</span><span class="t dtitle">{e(dt[d])}</span></div>' for d in cited) + "</div>"
                    if cited else '<div class="empty">None cited.</div>') +
                f'</div></section><section class="panel"><div class="panel-h"><h2>Commits</h2><span class="prog">{len(commits)}</span></div><div class="panel-b">' + (
                    "".join(f'<div class="commit"><span class="h">{e(c["hash"])}</span><span class="s">'
                            + (f'<a href="{e(c["url"])}">{e(c["subject"])}</a>' if c.get("url") else e(c["subject"]))
                            + f'</span><span class="d">{e(c["when"][5:10])}</span></div>'
                            for c in commits[:15]) or '<div class="empty">No commits name this ticket yet.</div>') + "</div></section></div></div>")
        return page, self.frame(page, t["id"], "", body)

    def epics_page(self):
        m, page = self.m, "epics.html"
        epics = sorted([(r, t) for r, t in m.all if t["type"] == "EPIC"], key=lambda x: (x[1]["state"] == "closed", -(x[1]["_c"].timestamp() if x[1]["_c"] else 0)))
        arts = ""
        for r, t in epics:
            kids = [(kr, kt) for kr, kt in m.all if kt["epic"] == t["id"]]
            done = sum(1 for _, kt in kids if kt["state"] == "closed")
            nrepo = len({kr["name"] for kr, _ in kids})
            kids_html = (f'<div class="bar" role="img" aria-label="{done} of {len(kids)} stories closed"><i style="width:{done / len(kids) * 100:.0f}%"></i></div>'
                         f'<div class="prog">{done} of {len(kids)} stories closed{"" if m.single else f" · {nrepo} repositor" + ("ies" if nrepo > 1 else "y")}</div>'
                         f'<div class="kids">' + "".join(f'<a class="kid" href="{self.rel(page, self.p_ticket(kr, kt))}">{self.pill(kt["state"])}<span class="id">{e(kt["id"])}</span>'
                                                         f'<span class="t">{e(kt["title"])}</span></a>' for kr, kt in kids) + "</div>"
                         ) if kids else '<div class="prog">No stories name this epic yet.</div>'
            arts += (f'<article class="ep"><div class="ep-h"><a class="id" href="{self.rel(page, self.p_ticket(r, t))}">{e(t["id"])}</a>{self.pill(t["state"])}'
                     f'<span class="prog">{e(t["area"] if m.single else r["name"])}</span></div><h3><a href="{self.rel(page, self.p_ticket(r, t))}">{e(t["title"])}</a></h3>{kids_html}</article>')
        body = (f'{self.crumbs(page, [("Epics", None)])}<h1>Epics</h1><div class="sub">A larger piece of work broken into stories; a story joins an epic by naming it in its '
                f'<span class="mono">epic:</span> field.</div><section class="panel spaced">{arts or "<div class=panel-b><div class=empty>No epics in this project.</div></div>"}</section>')
        return page, self.frame(page, "Epics", "epics", body)

    def decisions_page(self):
        m, page = self.m, "decisions.html"
        rows = [(r, d) for r in m.repos for d in r["decisions"]]
        rows.sort(key=lambda x: (x[1]["date"] or "", int(x[1]["id"][1:])), reverse=True)
        with_dec = sorted([r for r in m.repos if r["decisions"]], key=lambda r: -len(r["decisions"]))
        sel = "" if m.single else (
            '<label for="dec-repo">Repository <select id="dec-repo"><option value="">All (' + str(len(rows)) + ")</option>" +
            "".join(f'<option value="{slug(r["name"])}">{e(r["name"])} ({len(r["decisions"])})</option>' for r in with_dec) + "</select></label>")
        trs = "".join(
            f'<tr data-repo="{slug(r["name"])}" data-q="{e((d["id"] + " " + d["title"]).lower())}">'
            + ("" if m.single else f'<td class="nowrap"><a class="muted" href="{self.rel(page, self.p_repo(r))}">{e(r["name"])}</a></td>')
            + f'<td class="id">{e(d["id"])}</td><td class="dtitle">{e(d["title"])}</td>'
            f'<td class="hide-sm when2">{datetime.strptime(d["date"], "%Y-%m-%d").strftime("%b %-d") if d["date"] else "<span class=frozen>earlier log</span>"}</td></tr>'
            for r, d in rows)
        body = (f'{self.crumbs(page, [("Decisions", None)])}<h1>Decisions</h1><div class="sub">Every recorded decision'
                f'{"" if m.single else f", across {len(with_dec)} repositories"}. Newest first.</div>'
                f'<div class="controls">{sel}<label for="dec-q">Find <input id="dec-q" type="search" placeholder="words in the title"></label>'
                f'<span class="prog" id="dec-count">{len(rows)} shown</span></div>'
                f'<section class="panel spaced"><div class="tbl-wrap"><table id="dec-table"><thead><tr>{"" if m.single else "<th>Repository</th>"}<th>Decision</th><th>Title</th>'
                f'<th class="hide-sm">Recorded</th></tr></thead><tbody>{trs or "<tr><td colspan=4 class=empty>No decisions recorded.</td></tr>"}</tbody></table></div></section>')
        return page, self.frame(page, "Decisions", "decisions", body)

    def metrics_page(self, scope=None, pool=None, page="metrics.html"):
        m = self.m
        pool = pool if pool is not None else [t for _, t in m.all]
        if m.single:
            opts = [(a, self.p_metrics(a)) for a, *_ in self.areas(m.repos[0])]
        else:
            opts = [(r["name"], self.p_metrics(r["name"])) for r in m.active]
        sel = (f'<label for="met-scope">{"Area" if m.single else "Repository"} <select id="met-scope"><option value="{self.rel(page, "metrics.html")}">'
               f'{"All areas" if m.single else "All repositories"}</option>' + "".join(
                   f'<option value="{self.rel(page, p)}"{" selected" if n == scope else ""}>{e(n)}</option>' for n, p in opts) + "</select></label>")
        body = (f'{self.crumbs(page, [("Metrics", "metrics.html" if scope else None)] + ([(scope, None)] if scope else []))}'
                f'<h1>Metrics{f" <span class=h1-sub>{e(scope)}</span>" if scope else ""}</h1>'
                f'<div class="sub">From each ticket\'s machine-written stamps — opened, started, closed.</div><div class="controls">{sel}</div>'
                f'<div class="charts"><section class="panel chart"><h2>Closed per week</h2>{self.chart_weekly(pool)}<div class="cap">{"Last 12 weeks, Monday-aligned; the pale bar is this unfinished week." if any(t["_x"] for t in pool) else "Nothing has been closed in this scope yet."}</div></section>'
                f'<section class="panel chart"><h2>Open over time</h2>{self.chart_open(pool)}<div class="cap">Tickets open at the end of each day, last 30 days.</div></section></div>'
                f'<section class="panel spaced"><div class="panel-h"><h2>Filed → closed, by type</h2></div>{self.lead_table(pool)}</section>{self.defects(pool)}')
        return page, self.frame(page, "Metrics", "metrics", body)

    def p_metrics(self, scope):
        return f"metrics/{slug(scope)}.html"

    def chart_weekly(self, pool):
        wk = weekly(pool, self.m.now)
        W, H, L, R_, T, B = 520, 210, 34, 8, 10, 28
        mx = nice_max(max(n for _, n in wk))
        bw = (W - L - R_) / len(wk)
        y = lambda v: T + (H - T - B) * (1 - v / mx)
        grid = "".join(f'<line class="grid" x1="{L}" x2="{W-R_}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text class="ax" x="{L-6}" y="{y(v)+4:.1f}" text-anchor="end">{v:g}</text>' for v in (0, mx / 2, mx))
        bars = "".join(
            f'<rect class="barm{" now" if i == len(wk) - 1 else ""}" x="{L + i*bw + bw*.18:.1f}" width="{bw*.64:.1f}" y="{y(n):.1f}" height="{H - B - y(n):.1f}" rx="2"><title>{n} closed, week of {s.strftime("%b %-d")}</title></rect>'
            + (f'<text class="ax" x="{L + i*bw + bw/2:.1f}" y="{H-8}" text-anchor="middle">{s.strftime("%b %-d")}</text>' if i % 3 == 0 or i == len(wk) - 1 else "")
            for i, (s, n) in enumerate(wk))
        return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Tickets closed per week">{grid}{bars}</svg>'

    def chart_open(self, pool):
        now, days = self.m.now, 30
        W, H, L, R_, T, B = 520, 210, 34, 8, 10, 28
        pts = []
        for i in range(days - 1, -1, -1):
            end = (now - timedelta(days=i)).replace(hour=23, minute=59)
            pts.append((end, sum(1 for t in pool if t["_c"] and t["_c"] <= end and not (t["_x"] and t["_x"] <= end))))
        mx = nice_max(max([1] + [n for _, n in pts]))
        x = lambda i: L + (W - L - R_) * i / (days - 1)
        y = lambda v: T + (H - T - B) * (1 - v / mx)
        line = " L".join(f"{x(i):.1f},{y(n):.1f}" for i, (_, n) in enumerate(pts))
        grid = "".join(f'<line class="grid" x1="{L}" x2="{W-R_}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text class="ax" x="{L-6}" y="{y(v)+4:.1f}" text-anchor="end">{v:g}</text>' for v in (0, mx / 2, mx))
        labels = "".join(f'<text class="ax" x="{x(i):.1f}" y="{H-8}" text-anchor="{"end" if i == days-1 else "start" if i == 0 else "middle"}">{pts[i][0].strftime("%b %-d")}</text>' for i in (0, 10, 20, days - 1))
        last = pts[-1][1]
        return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Open tickets over the last 30 days">{grid}'
                f'<path class="aream" d="M{line} L{x(days-1):.1f},{y(0):.1f} L{x(0):.1f},{y(0):.1f} Z"/><path class="linem" d="M{line}"/>'
                f'<circle class="dot" r="3.5" cx="{x(days-1):.1f}" cy="{y(last):.1f}"/><text class="ax ink" x="{x(days-1)-6:.1f}" y="{max(12, y(last)-10):.1f}" text-anchor="end">{last} open</text>{labels}</svg>')

    def lead_table(self, pool):
        # Ported from open-teleporter's gen-dashboard.py lead_times(): per type, n, median,
        # fastest, slowest — median not mean (one item left open over a weekend drags a mean),
        # every row with its n. Wall clock from created to closed: elapsed time, NOT effort.
        fmt = lambda h: (f"{h:.1f} h" if h < 10 else f"{h:.0f} h") if h < 48 else f"{h/24:.1f} d"
        rows = ""
        for ty in ("STORY", "BUG", "TASK", "SPIKE", "EPIC"):
            c = [(t["_x"] - t["_c"]).total_seconds() / 3600 for t in pool if t["type"] == ty and t["_c"] and t["_x"]]
            if c:
                rows += (f'<tr><td><span class="type {ty}">{ty.lower()}</span></td><td class="n">{len(c)}</td><td class="n">{fmt(median(c))}</td>'
                         f'<td class="n hide-sm">{fmt(min(c))}</td><td class="n hide-sm">{fmt(max(c))}</td></tr>')
        return ('<div class="tbl-wrap"><table class="lt"><thead><tr><th>Type</th><th class="n">n</th><th class="n">Median</th>'
                '<th class="n hide-sm">Fastest</th><th class="n hide-sm">Slowest</th></tr></thead><tbody>'
                + (rows or '<tr><td colspan="5" class="empty">Nothing closed in this scope yet.</td></tr>') + "</tbody></table></div>"
                '<div class="cap pad-x">Wall-clock time from filed to closed, nights and pauses included — elapsed time, not effort.</div>')

    def defects(self, pool):
        # Ported from open-teleporter's defect_rate(): the share of work OPENED in the window
        # that is a bug. With nothing opened there is no rate — say "0 of 0", never a percentage.
        since = self.m.now - timedelta(weeks=12)
        opened = [t for t in pool if t["_c"] and t["_c"] >= since]
        bugs = sum(1 for t in opened if t["type"] == "BUG")
        share = f"{100 * bugs / len(opened):.0f}%" if opened else "no rate"
        return (f'<section class="panel spaced"><div class="panel-h"><h2>Bugs among new work</h2></div><div class="panel-b">'
                f'<p class="lede"><b class="num">{bugs}</b> of <b class="num">{len(opened)}</b> items filed in the last 12 weeks are bugs '
                f'<span class="muted">({share})</span>.</p></div></section>')

    # ── write everything ──
    def build(self) -> list[str]:
        m, pages = self.m, []
        pages.append(self.home())
        pages += self.counter_pages()
        for r in m.repos:
            pages.append(self.repo_page(r))
            for t in r["tickets"]:
                pages.append(self.ticket_page(r, t))
            if m.single or len(r["tickets"]):
                for a, *_ in self.areas(r):
                    pages.append(self.repo_page(r, a))
        pages += [self.epics_page(), self.decisions_page(), self.metrics_page()]
        if m.conf.get("guide"):                             # KIT-066
            pages.append(self.guide_page())
        if m.has_domains:                                   # KIT-061
            pages += [self.domain_page(d) for d in m.domains]
        pages += [self.tag_page(g) for g in sorted({x for _, t in m.all for x in t.get("tags", [])})]
        if m.single:
            for a, ts, *_ in self.areas(m.repos[0]):
                pages.append(self.metrics_page(a, ts, self.p_metrics(a)))
        else:
            for r in m.active:
                pages.append(self.metrics_page(r["name"], r["tickets"], self.p_metrics(r["name"])))
        for rel, text in pages:
            p = self.out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        (self.out / "board.css").write_text(CSS, encoding="utf-8")
        (self.out / "board.js").write_text(JS, encoding="utf-8")
        summary = {"project": m.conf["name"], "built": m.built,
                   "repos": [{"name": r["name"], "domain": r.get("domain", ""), "fetched": r["fetched"], "adopted": r["adopted"],
                              "tickets": {t["id"]: t["state"] for t in r["tickets"]},
                              "ticket_pages": [{"id": t["id"], "title": t["title"], "state": t["state"],
                                                "url": self.p_ticket(r, t)} for t in r["tickets"]],
                              "decisions": len(r["decisions"])} for r in m.repos]}
        (self.out / "board.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        return [rel for rel, _ in pages]


# Linked from every page's <head>, never @import-ed from board.css: an @import after any
# rule is dropped by the browser without an error (KIT-034). Falls back to system fonts offline.
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600"
         "&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600"
         "&family=Inter:wght@400;500;600&display=swap")

CSS = r"""
:root{--ink:#14181c;--ink-soft:#26313a;--muted:#5c6772;--faint:#8b959f;--surface:#fff;--surface-2:#f3f6f8;--surface-3:#e9eef1;--rule:#dde4e9;--rule-soft:#eaeff2;
--accent:#0d6e7d;--accent-soft:#e2f1f3;--story:#2f6ea8;--story-soft:#e6eef6;--bug:#a8443a;--bug-soft:#f8e9e7;--task:#5a6470;--task-soft:#eceff2;
--spike:#7a5aa8;--spike-soft:#efe9f7;--epic:#3f7a52;--epic-soft:#e6f2ea;--warn:#8a6410;--warn-soft:#faf0d9;
--shadow:0 1px 2px rgba(20,24,28,.05),0 8px 24px -14px rgba(20,24,28,.22);
--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--serif:"IBM Plex Serif",Georgia,"Times New Roman",serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#e6ebef;--ink-soft:#c2ccd4;--muted:#93a0ab;--faint:#6c7883;--surface:#12171b;--surface-2:#181e23;--surface-3:#212930;--rule:#28313a;--rule-soft:#1f272e;
--accent:#4fc0d0;--accent-soft:#123037;--story:#79b0e0;--story-soft:#14232f;--bug:#e0857a;--bug-soft:#2c1a18;--task:#9aa5b1;--task-soft:#20262c;--spike:#b79ce0;--spike-soft:#231c2e;--epic:#7fc394;--epic-soft:#16261c;--warn:#d9a94a;--warn-soft:#2a2214;
--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -16px rgba(0,0,0,.75)}}
:root[data-theme="dark"]{--ink:#e6ebef;--ink-soft:#c2ccd4;--muted:#93a0ab;--faint:#6c7883;--surface:#12171b;--surface-2:#181e23;--surface-3:#212930;--rule:#28313a;--rule-soft:#1f272e;
--accent:#4fc0d0;--accent-soft:#123037;--story:#79b0e0;--story-soft:#14232f;--bug:#e0857a;--bug-soft:#2c1a18;--task:#9aa5b1;--task-soft:#20262c;--spike:#b79ce0;--spike-soft:#231c2e;--epic:#7fc394;--epic-soft:#16261c;--warn:#d9a94a;--warn-soft:#2a2214;
--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -16px rgba(0,0,0,.75)}
:root[data-theme-preset="jira"]{--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;--serif:var(--sans);--surface:#fff;--surface-2:#f4f5f7;--surface-3:#ebecf0;--rule:#dfe1e6;--rule-soft:#ebecf0;--ink:#172b4d;--ink-soft:#344563;--muted:#5e6c84;--faint:#7a869a;--accent:#0c66e4;--accent-soft:#deebff;--story:#0052cc;--story-soft:#deebff;--bug:#de350b;--bug-soft:#ffebe6;--task:#5e6c84;--task-soft:#ebecf0;--epic:#00875a;--epic-soft:#e3fcef;--warn:#ff991f;--warn-soft:#fff0b3}
:root[data-theme-preset="linear"]{--sans:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;--serif:var(--sans);--surface:#fff;--surface-2:#f7f7f8;--surface-3:#ededf0;--rule:#e5e5e7;--rule-soft:#eeeeef;--ink:#1d1d1f;--ink-soft:#3f3f46;--muted:#71717a;--faint:#a1a1aa;--accent:#5e6ad2;--accent-soft:#eeefff;--story:#5e6ad2;--story-soft:#eeefff;--bug:#d92d20;--bug-soft:#fef3f2;--task:#71717a;--task-soft:#f4f4f5;--epic:#087443;--epic-soft:#ecfdf3;--warn:#b54708;--warn-soft:#fffaeb}
:root[data-theme-preset="github-issues"]{--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--serif:var(--sans);--surface:#fff;--surface-2:#f6f8fa;--surface-3:#eaeef2;--rule:#d0d7de;--rule-soft:#d8dee4;--ink:#1f2328;--ink-soft:#424a53;--muted:#656d76;--faint:#818b98;--accent:#0969da;--accent-soft:#ddf4ff;--story:#0969da;--story-soft:#ddf4ff;--bug:#cf222e;--bug-soft:#ffebe9;--task:#656d76;--task-soft:#eaeef2;--epic:#1a7f37;--epic-soft:#dafbe1;--warn:#9a6700;--warn-soft:#fff8c5}
:root[data-theme-preset="gitlab"]{--sans:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;--serif:var(--sans);--surface:#fff;--surface-2:#f5f5f5;--surface-3:#e9e9e9;--rule:#dcdcde;--rule-soft:#ececef;--ink:#18171a;--ink-soft:#333238;--muted:#66666e;--faint:#8c8c94;--accent:#7759c2;--accent-soft:#eeeaff;--story:#1f75cb;--story-soft:#e8f3ff;--bug:#ce291f;--bug-soft:#fcebea;--task:#66666e;--task-soft:#e9e9e9;--epic:#108548;--epic-soft:#e7f6ec;--warn:#a45b00;--warn-soft:#fff1d6}
:root[data-theme-preset="azure-devops"]{--sans:"Segoe UI",system-ui,-apple-system,sans-serif;--serif:var(--sans);--surface:#fff;--surface-2:#faf9f8;--surface-3:#f3f2f1;--rule:#e1dfdd;--rule-soft:#edebe9;--ink:#323130;--ink-soft:#484644;--muted:#605e5c;--faint:#797775;--accent:#0078d4;--accent-soft:#deecf9;--story:#0078d4;--story-soft:#deecf9;--bug:#d13438;--bug-soft:#fde7e9;--task:#605e5c;--task-soft:#f3f2f1;--epic:#107c10;--epic-soft:#dff6dd;--warn:#986f0b;--warn-soft:#fff4ce}
*{box-sizing:border-box}html{background:var(--surface-2)}
body{margin:0;background:var(--surface-2);color:var(--ink);font:16px/1.55 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}a:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
.mono{font-family:var(--mono)}.num{font-family:var(--mono);font-variant-numeric:tabular-nums}.nowrap{white-space:nowrap}.muted{color:var(--muted)}.bad{color:var(--bug)}
.top{position:sticky;top:0;z-index:5;background:var(--surface);border-bottom:1px solid var(--rule)}
.top-in{max-width:1200px;margin:0 auto;padding-inline:20px;display:flex;align-items:center;gap:22px;min-height:54px;flex-wrap:wrap;padding-block:6px}
.nav{display:flex;gap:4px;flex:1;overflow-x:auto}.nav a{padding:7px 11px;border-radius:6px;color:var(--muted);font-weight:500;font-size:14px;white-space:nowrap}
.nav a:hover{color:var(--ink);background:var(--surface-2)}.nav a[aria-current="page"]{color:var(--ink);background:var(--surface-3)}
.jump{font:13px var(--sans);color:var(--ink);background:var(--surface-2);border:1px solid var(--rule);border-radius:6px;padding:6px 8px;max-width:220px}
.lookup{display:flex;align-items:center;gap:8px;margin:18px 0 22px}.lookup label{font-size:13px;color:var(--muted)}.lookup input{width:180px;font:13px var(--mono);text-transform:uppercase;color:var(--ink);background:var(--surface);border:1px solid var(--rule);border-radius:7px;padding:7px 9px}.lookup button{font:600 13px var(--sans);color:var(--surface);background:var(--accent);border:0;border-radius:7px;padding:8px 11px;cursor:pointer}.lookup button:hover{filter:brightness(.92)}#ticket-results{font-size:14px;color:var(--muted);margin:-10px 0 20px}#ticket-results a{color:var(--accent);font-weight:600}
.proj{position:relative;flex:none;display:flex;align-items:baseline;gap:8px}
.proj summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:8px;padding:6px 11px;border:1px solid var(--rule);border-radius:8px;background:var(--surface);white-space:nowrap}
.proj summary::-webkit-details-marker{display:none}.proj summary:hover{border-color:var(--accent)}
.proj-k{font-size:10.5px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--faint)}.proj-v{font-family:var(--serif);font-weight:600;font-size:17px}.caret{color:var(--faint);font-size:11px}
.proj-menu{position:absolute;top:calc(100% + 6px);left:0;width:min(340px,calc(100vw - 32px));background:var(--surface);border:1px solid var(--rule);border-radius:10px;box-shadow:var(--shadow);padding:6px;z-index:10}
.pm{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;padding:9px 10px;border-radius:7px;align-items:baseline}.pm:hover{background:var(--surface-2)}.pm[aria-current="page"]{background:var(--accent-soft)}
.pm b{font-weight:600}.pm .s{grid-column:1 / -1;font-size:12.5px;color:var(--muted)}.pm .c{font-family:var(--mono);font-size:12px;color:var(--faint)}
.pm-sep{height:1px;background:var(--rule-soft);margin:4px 6px}.pm-h{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);padding:6px 10px 2px}
.wrap{max-width:1200px;margin:0 auto;padding-inline:20px;padding-block:26px 40px}
.foot{max-width:1200px;margin:0 auto;padding-inline:20px;padding-block:0 32px;color:var(--faint);font-size:12.5px}
.crumbs{font-size:13px;color:var(--faint);margin-bottom:10px;display:flex;gap:6px;flex-wrap:wrap}.crumbs a{color:var(--muted)}.crumbs a:hover{color:var(--accent)}
h1{font-family:var(--serif);font-weight:600;font-size:30px;line-height:1.2;margin:0;text-wrap:balance}.h1-sub{font-family:var(--sans);font-weight:400;font-size:18px;color:var(--muted)}
h2{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
.sub{color:var(--muted);font-size:14px;margin-top:6px}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:28px;margin-top:22px;align-items:start}.stack{display:flex;flex-direction:column;gap:26px}
.panel{background:var(--surface);border:1px solid var(--rule);border-radius:10px;box-shadow:var(--shadow)}.spaced{margin-top:18px}
.panel-h{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:14px 16px 0}.panel-b{padding:10px 16px 14px}.pad{padding:18px}
.more{font-size:13px;color:var(--accent);font-weight:500}.more:hover{text-decoration:underline}.empty{color:var(--muted);font-size:14px;padding:6px 0}
.counts{display:flex;flex-wrap:wrap;margin-top:20px;background:var(--surface);border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.count{flex:1 1 150px;padding:14px 18px;border-right:1px solid var(--rule-soft);display:flex;flex-direction:column;gap:2px}.count:last-child{border-right:0}
.count b{font-family:var(--mono);font-size:26px;font-weight:500;font-variant-numeric:tabular-nums;line-height:1.1}.count span{font-size:12.5px;color:var(--muted)}
.count{color:var(--ink);text-decoration:none;cursor:pointer}.count:hover{background:var(--surface-2);text-decoration:none}.count:focus-visible{outline:3px solid var(--accent);outline-offset:-3px}
.log-detail{font-size:12px;color:var(--muted);margin-top:3px}.update-log td{vertical-align:top}
.count.hot b{color:var(--bug)}.count.live b{color:var(--accent)}
.id{font-family:var(--mono);font-size:13px;font-weight:500;color:var(--ink-soft);white-space:nowrap}
.type{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:1px 6px;border-radius:4px;white-space:nowrap}
.type.STORY{color:var(--story);background:var(--story-soft)}.type.BUG{color:var(--bug);background:var(--bug-soft)}.type.TASK{color:var(--task);background:var(--task-soft)}
.type.SPIKE{color:var(--spike);background:var(--spike-soft)}.type.EPIC{color:var(--epic);background:var(--epic-soft)}
.st{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:500;padding:2px 8px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.st::before{content:"";width:7px;height:7px;border-radius:50%}
.st.in-progress{color:var(--accent);background:var(--accent-soft)}.st.in-progress::before{background:var(--accent)}
.st.queued{color:var(--muted);border-color:var(--rule)}.st.queued::before{border:1.5px solid var(--faint);width:5px;height:5px}
.st.blocked{color:var(--bug);background:var(--bug-soft)}.st.blocked::before{background:var(--bug);border-radius:1px}
.st.closed{color:var(--faint)}.st.closed::before{background:var(--faint);opacity:.6}
.pri{font-family:var(--mono);font-size:12px;font-weight:600}.pri.P1{color:var(--bug)}.pri.P2{color:var(--warn)}.pri.P3{color:var(--muted)}.pri.P4{color:var(--faint)}
.reason{font-size:11.5px;font-weight:600;padding:1px 7px;border-radius:4px;white-space:nowrap}.reason.blocked,.reason.p1{color:var(--bug);background:var(--bug-soft)}.reason.stalled{color:var(--warn);background:var(--warn-soft)}
.stale{font-size:11px;font-weight:600;padding:1px 6px;border-radius:4px;color:var(--warn);background:var(--warn-soft);margin-left:6px}.stale.ok{color:var(--accent);background:var(--accent-soft)}
.rows{display:flex;flex-direction:column}
.row{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:4px 12px;align-items:baseline;padding:9px 10px;margin-inline:-10px;border-radius:7px}
.row:hover{background:var(--surface-2)}.row + .row{border-top:1px solid var(--rule-soft)}.row.two{grid-template-columns:auto minmax(0,1fr)}
.row .t{min-width:0;overflow-wrap:anywhere}.row .meta{display:flex;gap:8px;align-items:center;justify-content:flex-end;white-space:nowrap}
.row .sub2{grid-column:2 / -1;display:flex;gap:8px;flex-wrap:wrap;align-items:center;color:var(--faint);font-size:12.5px}
.tbl-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}
th{font-size:11.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);text-align:left;padding:10px 12px;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--rule-soft);vertical-align:middle}tr:last-child td{border-bottom:0}tbody tr:hover td{background:var(--surface-2)}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}td .z{color:var(--faint)}.when2{color:var(--muted);font-size:13px;white-space:nowrap}
.rname{font-weight:600}.rpfx{font-family:var(--mono);font-size:12px;color:var(--faint);margin-left:6px}
.quiet{padding:12px 16px;border-top:1px solid var(--rule-soft);font-size:13.5px;color:var(--muted)}
.quiet summary,.more-items summary{cursor:pointer;list-style:none;display:flex;gap:8px;align-items:center}
.quiet summary::-webkit-details-marker,.more-items summary::-webkit-details-marker{display:none}
.quiet summary::before{content:"▸";color:var(--faint);font-size:11px}.quiet[open] summary::before{transform:rotate(90deg)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chip{font-size:12.5px;padding:3px 9px;border:1px solid var(--rule);border-radius:999px;color:var(--ink-soft);background:var(--surface)}
.chip:hover{border-color:var(--accent);color:var(--accent)}.chip .num{color:var(--faint);margin-left:4px}.chip.dash{border-style:dashed}
.feed{display:flex;flex-direction:column}.ev{display:grid;grid-template-columns:62px minmax(0,1fr);gap:10px;padding:8px 0;border-top:1px solid var(--rule-soft);font-size:13.5px}
.ev:first-child{border-top:0}.ev .when{font-family:var(--mono);font-size:11.5px;color:var(--faint);padding-top:2px}.ev .verb{font-weight:600}
.ev .verb.closed{color:var(--epic)}.ev .verb.started{color:var(--accent)}.ev .verb.opened{color:var(--ink-soft)}.ev a:hover .ttl{color:var(--accent)}.ev .ttl{color:var(--ink-soft)}.ev .rp{color:var(--faint);font-size:12px}
.lanes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:22px}
.lane{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:12px 14px 6px;min-width:0}.lane-h{display:flex;align-items:center;gap:8px;margin-bottom:4px}.lane-h .num{color:var(--faint);font-size:13px}
.lane .row{grid-template-columns:minmax(0,1fr) auto}.lane .row .t{grid-column:1 / -1;font-size:14px}.lane .row .meta{grid-column:2;grid-row:1}.lane .row .sub2{grid-column:1 / -1}
.row.tf{grid-template-columns:minmax(0,1fr) auto}.row.tf .t{font-weight:500;color:var(--ink)}.row.tf .sub2{grid-column:1 / -1}.lane .row.tf .t{grid-column:1;grid-row:1}
.row .fid,.ev .fid{margin-left:auto;font-family:var(--mono);font-size:11.5px;color:var(--faint);white-space:nowrap}
.where{display:inline-flex;gap:5px;align-items:baseline;color:var(--muted)}.where b{font-weight:600;color:var(--ink-soft)}.where .sep{color:var(--faint)}
.areachip{border:1px solid var(--rule);border-radius:4px;padding:0 6px;color:var(--ink-soft);background:var(--surface-2);font-size:12px;white-space:nowrap}
.tagchip{border-radius:4px;padding:0 6px;color:var(--accent);background:var(--accent-soft);font-size:12px;white-space:nowrap}a.tagchip:hover{text-decoration:underline}
.tagchip.big{font-size:22px;padding:2px 10px}.ev .ttl.strong{color:var(--ink)}
.dname{font-weight:600;display:block}.dsub{display:block;color:var(--faint);font-size:12.5px;margin-top:1px}
.panel-foot{padding:10px 16px 14px;color:var(--faint);font-size:13px;border-top:1px solid var(--rule-soft)}.panel-foot a{color:var(--muted)}.panel-foot a:hover{color:var(--accent)}
.md-table{margin:10px 0 14px}.md-table table{font-size:13.5px}.ticket-section ol,.guide ol{padding-left:22px}.ticket-section h3,.guide h3{font-size:15px;margin:16px 0 6px}
.guide p{max-width:78ch}.guide ul{max-width:78ch;padding-left:20px}.guide h2{margin-bottom:8px}.guide pre{overflow-x:auto}
.words .wl{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;padding:5px 0;font-size:13px;color:var(--muted)}.words .wl a{color:var(--accent)}
.open-now .open-h{margin:10px 0 2px}.open-now .rows .open-h:first-child{margin-top:0}
.more-items summary{justify-content:center;margin:6px 0 8px;padding:7px;border:1px dashed var(--rule);border-radius:7px;color:var(--muted);font-weight:500;font-size:13px}
.more-items summary:hover{color:var(--accent);border-color:var(--accent)}.more-items[open] summary{display:none}
dl.facts{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 14px;margin:0;font-size:14px}dl.facts dt{color:var(--faint);font-size:12.5px;padding-top:1px}
dl.facts dd{margin:0;min-width:0;overflow-wrap:anywhere}dl.facts a{color:var(--accent)}
.thead{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}.thead .id{font-size:15px}
.lede{font-size:16.5px;line-height:1.65;color:var(--ink-soft);max-width:68ch;margin:0}
.timeline{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:4px}
.step{padding:14px 12px 4px 0;position:relative}.step::before{content:"";position:absolute;top:4px;left:0;right:0;height:2px;background:var(--rule)}.step.done::before{background:var(--accent)}
.step::after{content:"";position:absolute;top:0;left:0;width:10px;height:10px;border-radius:50%;background:var(--surface);border:2px solid var(--rule)}.step.done::after{border-color:var(--accent);background:var(--accent)}
.step .lab{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}.step .at{font-family:var(--mono);font-size:13px;margin-top:2px}.step .gap{font-size:12.5px;color:var(--faint);margin-top:2px}
.secs{display:flex;flex-wrap:wrap;gap:6px}.sec{font-size:12.5px;padding:3px 8px;background:var(--surface-2);border-radius:5px;color:var(--ink-soft)}.sec:hover{color:var(--accent);background:var(--surface-3)}
.ticket-section{scroll-margin-top:24px}.ticket-section h2{font-family:var(--serif);font-size:20px;margin:0 0 10px}.ticket-section + .ticket-section{border-top:1px solid var(--rule);margin-top:20px;padding-top:20px}.ticket-section p{line-height:1.65;color:var(--ink-soft);max-width:75ch}.ticket-section ul{padding-left:22px;line-height:1.6;color:var(--ink-soft)}.ticket-section code{font:12.5px var(--mono);background:var(--surface-2);padding:1px 4px;border-radius:4px}.ticket-section pre{overflow:auto;background:var(--surface-2);padding:12px;border-radius:7px}.ticket-section pre code{background:none;padding:0}
.path{font-family:var(--mono);font-size:12px;color:var(--faint);overflow-wrap:anywhere;margin-top:12px}
.commit{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 10px;padding:7px 0;border-top:1px solid var(--rule-soft);font-size:13px}.commit:first-child{border-top:0}
.commit .h{font-family:var(--mono);font-size:12px;color:var(--accent)}.commit .d{font-family:var(--mono);font-size:11.5px;color:var(--faint);grid-column:1}
.commit .s{grid-column:2;grid-row:1 / span 2;color:var(--ink-soft);overflow-wrap:anywhere}
.ep{padding:16px 18px}.ep + .ep{border-top:1px solid var(--rule)}.ep-h{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.ep h3{font-family:var(--serif);font-weight:600;font-size:19px;margin:6px 0 4px;text-wrap:balance}
.bar{height:6px;background:var(--surface-3);border-radius:99px;overflow:hidden;margin:10px 0 4px;max-width:420px}.bar i{display:block;height:100%;background:var(--epic);border-radius:99px}
.prog{font-size:13px;color:var(--muted)}.kids{margin-top:10px;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:4px 18px}
.kid{display:flex;gap:8px;align-items:baseline;font-size:13.5px;padding:3px 0;min-width:0}.kid .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink-soft)}.kid:hover .t{color:var(--accent)}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:18px}.controls label{font-size:12.5px;color:var(--muted);display:flex;gap:8px;align-items:center}
.controls select,.controls input{font:14px var(--sans);color:var(--ink);background:var(--surface);border:1px solid var(--rule);border-radius:7px;padding:7px 10px}.controls input{min-width:min(280px,100%)}
.theme-choice{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12.5px;white-space:nowrap}.theme-choice select{font:13px var(--sans);color:var(--ink);background:var(--surface);border:1px solid var(--rule);border-radius:7px;padding:6px 8px}
.dtitle{color:var(--ink-soft)}.frozen{font-size:11.5px;color:var(--faint)}
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-top:20px}.chart{padding:16px 16px 10px}.chart svg{width:100%;height:auto;display:block;overflow:visible}
.chart .cap,.cap{font-size:13px;color:var(--muted);margin-top:4px}.pad-x{padding:0 16px 12px}.ax{fill:var(--faint);font:11px var(--mono)}.ax.ink{fill:var(--ink)}.grid{stroke:var(--rule-soft);stroke-width:1}
.barm{fill:var(--accent)}.barm.now{opacity:.55}.linem{fill:none;stroke:var(--accent);stroke-width:2}.aream{fill:var(--accent);opacity:.12}.dot{fill:var(--accent)}.lt td,.lt th{padding:8px 10px}
.spark{display:block}.spark path{fill:none;stroke:var(--accent);stroke-width:1.5}.spark .a{fill:var(--accent);opacity:.12;stroke:none}.spark circle{fill:var(--accent)}
@media (max-width:980px){.cols,.lanes,.charts{grid-template-columns:minmax(0,1fr)}}
@media (max-width:620px){h1{font-size:25px}.timeline{grid-template-columns:minmax(0,1fr)}.row{grid-template-columns:minmax(0,1fr) auto}.row .t{grid-column:1 / -1}.hide-sm{display:none}.jump{max-width:100%;flex:1}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = r"""
// Appearance is a local browser preference; the board remains static and view-only.
(function(){
  var t=document.getElementById('theme');
  if(t){
    var current=document.documentElement.dataset.themePreset||'current';
    t.value=current;
    t.addEventListener('change',function(){
      var value=t.value;
      if(value==='current') delete document.documentElement.dataset.themePreset;
      else document.documentElement.dataset.themePreset=value;
      try{ localStorage.setItem('board-theme',value); }catch(e){}
    });
  }
})();
// A view-only board that keeps itself current (KIT-039): once a minute, while the tab is visible,
// compare this page's build time with board.json's; reload when a newer build has landed,
// keeping the scroll position. Nothing on the board edits anything (D15).
(function(){
  var meta=document.querySelector('meta[name="board-built"]'), me=document.currentScript;
  if(!meta||!me||!window.fetch) return;
  var url=new URL('board.json', me.src).href, key='board-scroll:'+location.pathname;
  try{ var y=sessionStorage.getItem(key); if(y!==null){ sessionStorage.removeItem(key); window.scrollTo(0, +y); } }catch(e){}
  setInterval(function(){
    if(document.hidden) return;
    fetch(url,{cache:'no-store'}).then(function(r){ return r.ok?r.json():null; }).then(function(b){
      if(b && b.built && b.built!==meta.content){ try{ sessionStorage.setItem(key, String(window.scrollY)); }catch(e){} location.reload(); }
    }).catch(function(){});
  }, 60000);
})();
// Exact ticket lookup uses this project's own static board metadata (KIT-048).
(function(){
  var form=document.getElementById('ticket-lookup'), input=document.getElementById('ticket-id'), out=document.getElementById('ticket-results');
  if(!form||!input||!out||!window.fetch) return;
  var boardUrl=new URL('board.json', document.currentScript.src).href;
  fetch(boardUrl,{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(b){
    var pages=[]; (b&&b.repos||[]).forEach(function(repo){ pages=pages.concat(repo.ticket_pages||[]); });
    form.addEventListener('submit',function(ev){
      ev.preventDefault(); var id=input.value.trim().toUpperCase();
      if(!id){out.textContent='Paste a ticket ID.';return;}
      var hits=pages.filter(function(t){return String(t.id).toUpperCase()===id;});
      out.innerHTML=hits.length?hits.map(function(t){return '<div><a href="'+new URL(t.url,boardUrl).href+'">'+esc(t.id)+'</a> — '+esc(t.title)+'</div>';}).join(''):'No exact ticket ID found on this board.';
    });
  }).catch(function(){out.textContent='Ticket lookup is temporarily unavailable.';});
  function esc(s){return String(s==null?'':s).replace(/[&<>\"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c];});}
})();
(function(){
  var j=document.getElementById('jump'); if(j) j.addEventListener('change',function(){ if(j.value) location.href=j.value; });
  var s=document.getElementById('met-scope'); if(s) s.addEventListener('change',function(){ location.href=s.value; });
  var p=document.getElementById('proj');
  if(p){ document.addEventListener('click',function(e){ if(p.open && !p.contains(e.target)) p.open=false; });
         document.addEventListener('keydown',function(e){ if(e.key==='Escape') p.open=false; }); }
  var q=document.getElementById('dec-q'), r=document.getElementById('dec-repo'), c=document.getElementById('dec-count');
  if(q){
    var rows=[].slice.call(document.querySelectorAll('#dec-table tbody tr[data-q]'));
    var m=location.hash.match(/repo=([\w-]+)/); if(m && r) r.value=m[1];
    var f=function(){ var t=q.value.trim().toLowerCase(), rv=r?r.value:'', n=0;
      rows.forEach(function(x){ var ok=(!rv||x.dataset.repo===rv)&&(!t||x.dataset.q.indexOf(t)>=0); x.hidden=!ok; if(ok) n++; });
      if(c) c.textContent=n+' shown'; };
    q.addEventListener('input',f); if(r) r.addEventListener('change',f); f();
  }
})();
"""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    conf_path = Path(argv[0])
    conf = load_board_config(conf_path)
    fetch = "--no-fetch" not in argv
    if "--out" in argv:
        conf["out"] = str(Path(argv[argv.index("--out") + 1]).resolve())
    if not conf["out"]:
        print("gen-board: no output directory (set `out =` in the config, or pass --out DIR)", file=sys.stderr)
        return 2
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        now = datetime.now()
    repos = [dict(read_repo(r["name"], r["path"], fetch), domain=r.get("domain", "")) for r in conf["repos"]]
    missing = [r["name"] for r in repos if not r["ok"]]
    model = Model(conf, [r for r in repos if r["ok"]], now)
    out = Path(conf["out"])
    tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    pages = Site(model, tmp).build()
    # swap into place so a server never serves a half-written board
    old = out.with_name(out.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    if old.exists():
        shutil.rmtree(old)
    n_t = sum(len(r["tickets"]) for r in model.repos)
    stale = [r["name"] for r in model.repos if r["fetched"] is False]
    print(f"gen-board: {conf['name']} — {len(model.repos)} repositories, {n_t} tickets, {len(pages)} pages → {out}"
          + (f"; stale (fetch failed): {', '.join(stale)}" if stale else "")
          + (f"; skipped (no repo / no origin branch): {', '.join(missing)}" if missing else ""))
    if conf["publish"] and "--no-publish" not in argv:
        return publish(out, conf["publish"])
    return 0


def publish(out: Path, target: str) -> int:
    """Copy the built board to where it is served (KIT-035, D21). `--delay-updates` puts every
    changed file in place at the end, so the served folder flips at once rather than being
    half-new while a browser reads it; `--delete-after` drops pages that no longer exist."""
    ssh = "ssh -o BatchMode=yes -o ConnectTimeout=10"
    r = subprocess.run(["rsync", "-a", "--mkpath", "--delete-after", "--delay-updates", "-e", ssh,
                        f"{out}/", target.rstrip("/") + "/"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"gen-board: publish to {target} FAILED (rsync exit {r.returncode}): {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"gen-board: published → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
