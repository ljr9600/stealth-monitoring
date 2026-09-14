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
from doc_kit import load_config  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".claude", "__pycache__", "closed"}


def read_tsv(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("repo\t"):
            continue
        cells = line.split("\t")
        cells += [""] * (4 - len(cells))
        rows.append({"repo": cells[0].strip(), "path": cells[1].strip(), "remote": cells[2].strip(),
                     "prefix": cells[3].strip()})
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
                           capture_output=True, text=True)
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
        for p in r["prefix"].split():
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
        if not rowp and declared:
            problems.append(f"{r['repo']}: the repo declares {' '.join(sorted(declared))} but its repos.tsv row has no prefix")
        if cfg.get("epics_scope", "").strip() != scope:
            problems.append(f"{r['repo']}: epics_scope is '{cfg.get('epics_scope', '')}', not '{scope}'")
    listed = {r["path"] for r in rows}
    for path, cfg in scan(root, scope).items():
        if path == here.relative_to(root).as_posix():
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
