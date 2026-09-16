#!/usr/bin/env python3
"""The scope registry is checked both ways (KIT-018). Run from an epics repo.

`repos.tsv` (repo · path · remote · story_prefix) is the ONE list of a scope's members.
The first adoption drifted within an hour — the registry said SUOA while the repo
declared UOASIG — and nothing noticed. So:

  1. every row whose path exists here must declare exactly the row's prefix(es) in its
     docs/doc-kit.config, and claim this scope (epics_scope);
  2. no prefix appears twice across rows, and none collides with this repo's epic prefix;
  3. every repository under the scope root that claims `epics_scope = <scope>` has a row
     (the scan reads docs/doc-kit.config files up to a few levels deep);
  4. rows whose path is not present here are reported as UNVERIFIED, not failed — a
     clone elsewhere is normal; the machine with the clones is the one that checks.

    python3 scripts/check-scope.py            # exit 1 on any problem; lists them
    DEV_ROOT=/path python3 scripts/check-scope.py   # scope root other than this repo's parent
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import os
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_kit import load_config, foreign_git_env  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".claude", "__pycache__", "closed"}


COLUMNS = ["repo", "path", "remote", "story_prefix"]   # the order when there is no header row


def read_tsv(path: Path):
    """Rows keyed by the header's column names (KIT-059), so optional columns such as
    `retired_prefixes` can be added in any order; a file without a header reads by position.
    Every row gets `retired_col` — whether the registry records retired prefixes at all."""
    rows, cols = [], COLUMNS
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if cells[0] == "repo":                      # the header row names the columns
            cols = cells
            continue
        cells += [""] * (len(cols) - len(cells))
        d = dict(zip(cols, cells))
        rows.append({**d, "repo": d.get("repo", ""), "path": d.get("path", ""), "remote": d.get("remote", ""),
                     "prefix": d.get("story_prefix", ""), "retired": d.get("retired_prefixes", ""),
                     "retired_col": "retired_prefixes" in cols})
    return rows


def member_config(repo: Path):
    """A member's docs/doc-kit.config as COMMITTED — origin/<default>, then master — so the
    checkout's branch does not matter (a repo on a pre-kit feature branch still verifies,
    KIT-021); the working tree only when nothing is committed yet. None = not a member here."""
    import subprocess
    if not (repo / ".git").exists():
        return None
    for ref in ("origin/master", "origin/main", "master", "main"):
        r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:docs/doc-kit.config"],
                           capture_output=True, text=True, env=foreign_git_env())
        if r.returncode == 0:
            return parse_config(r.stdout)
    if (repo / "docs" / "doc-kit.config").is_file():
        return load_config(repo)
    return None


def parse_config(text: str) -> dict:
    cfg = load_config(Path("/nonexistent"))          # the defaults
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = "" if v.strip().startswith("@") and v.strip().endswith("@") else v.strip()
    return cfg


def scan(root: Path, scope: str, depth: int = 8):
    """Every repo under root whose docs/doc-kit.config claims epics_scope=<scope>."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) > depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if Path(dirpath).name == "docs" and "doc-kit.config" in filenames:
            cfg = load_config(Path(dirpath).parent)
            if cfg.get("epics_scope", "").strip() == scope:
                found[Path(dirpath).parent.relative_to(root).as_posix()] = cfg
            dirnames[:] = []
    return found


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    marker = here / ".epics-root"
    if not marker.is_file():
        print("check-scope: run this from an epics repo (no .epics-root here)", file=sys.stderr)
        return 2
    scope = next((l.split("=", 1)[1].strip() for l in marker.read_text().splitlines() if l.startswith("scope")), "")
    root = Path(os.environ.get("DEV_ROOT", here.parent)).resolve()
    own = set(load_config(here).get("prefixes", "").split())
    tsv = here / "repos.tsv"
    if not tsv.is_file():
        print("check-scope: no repos.tsv", file=sys.stderr)
        return 1
    rows = read_tsv(tsv)
    problems, unverified = [], []
    seen: dict[str, str] = {}
    for r in rows:
        for p in r["prefix"].split() + r["retired"].split():   # a retired prefix is still owned (KIT-059)
            if p in seen:
                problems.append(f"prefix {p} is claimed by both {seen[p]} and {r['repo']} (repos.tsv)")
            seen[p] = r["repo"]
            if p in own:
                problems.append(f"{r['repo']}: prefix {p} collides with this epics repo's own prefix")
        repo = root / r["path"]
        cfg = member_config(repo)
        if cfg is None:
            if r["prefix"]:
                unverified.append(f"{r['repo']} ({r['path']}: not cloned here)")
            continue
        declared = set(cfg.get("prefixes", "").split())
        rowp = set(r["prefix"].split())
        if rowp and declared != rowp:
            problems.append(f"{r['repo']}: repos.tsv says prefix {' '.join(sorted(rowp)) or '(none)'} "
                            f"but the repo declares {' '.join(sorted(declared)) or '(none)'} — fix one")
        retired = set(cfg.get("retired_prefixes", "").split())
        rowr = set(r["retired"].split())
        if retired & declared:
            problems.append(f"{r['repo']}: prefix {' '.join(sorted(retired & declared))} is both declared "
                            f"and retired in its docs/doc-kit.config")
        if rowr != retired and (r["retired_col"] or retired):
            problems.append(f"{r['repo']}: repos.tsv retires {' '.join(sorted(rowr)) or '(none)'} but the "
                            f"repo retires {' '.join(sorted(retired)) or '(none)'} — fix one"
                            + ("" if r["retired_col"] else " (add a retired_prefixes column to the header)"))
        if not rowp and declared:
            problems.append(f"{r['repo']}: the repo declares {' '.join(sorted(declared))} but its repos.tsv row has no prefix")
        if cfg.get("epics_scope", "").strip() != scope:
            problems.append(f"{r['repo']}: epics_scope is '{cfg.get('epics_scope', '')}', not '{scope}'")
    listed = {r["path"] for r in rows}
    for path, cfg in scan(root, scope).items():
        if (root / path).resolve() == here.resolve():  # outside-root worktrees are valid (D40)
            continue
        if path not in listed:
            problems.append(f"{path}: claims epics_scope={scope} but has no row in repos.tsv "
                            f"(prefix {cfg.get('prefixes', '') or '?'})")
    if problems:
        print(f"scope '{scope}' registry check FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    more = f" (+{len(unverified) - 3} more)" if len(unverified) > 3 else ""
    print(f"scope '{scope}': {len(rows)} rows, {sum(1 for r in rows if r['prefix'])} adopted, "
          f"{len(unverified)} unverified here" + (": " + "; ".join(unverified[:3]) + more if unverified else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
