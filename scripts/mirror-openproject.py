#!/usr/bin/env python3
"""Mirror one repository's tickets into OpenProject — one way, idempotent (D15, KIT-007).

Files stay the source of truth. This reads COMMITTED state at a git ref (default
origin/master), never a working tree, so worktree branches and dirty checkouts cannot
leak onto the board. It creates the OpenProject project if missing, creates a work
package per ticket it has not seen (matched by the Legacy ID custom field), PATCHes
only the fields that differ, and sets Closed when the file has moved to closed/.
Edits made in OpenProject are overwritten on the next run — by design.

    mirror-openproject.py --repo PATH --project IDENT [--name NAME] [--ref origin/master]
                          [--include-closed] [--epics-project IDENT] [--dry-run]

Connection: $TT_OPENPROJECT_ENV, else ~/.config/ticketing-template/openproject.env,
else ./.op.env — KEY=VALUE lines: OP_URL, OP_TOKEN, and optionally the custom-field
keys (OP_CF_LEGACY=customField1 OP_CF_AREA=customField2 OP_CF_ESTIMATE=customField3
OP_CF_OPENED=customField4). Nothing an agent does depends on this running.
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

import requests

TYPE_NAME = {"EPIC": "Epic", "STORY": "Story", "TASK": "Task", "BUG": "Bug", "SPIKE": "Spike"}
STATUS_NAME = {"OPEN": "New", "BLOCKED": "Blocked", "CLOSED": "Closed", "IN-PROGRESS": "In progress"}


def load_env():
    cands = [os.environ.get("TT_OPENPROJECT_ENV", ""),
             str(pathlib.Path.home() / ".config/ticketing-template/openproject.env"), ".op.env"]
    env = {}
    for c in cands:
        if c and pathlib.Path(c).is_file():
            for line in pathlib.Path(c).read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
            break
    if "OP_URL" not in env or "OP_TOKEN" not in env:
        sys.exit("mirror: no OpenProject connection (OP_URL/OP_TOKEN) — see the docstring")
    env.setdefault("OP_CF_LEGACY", "customField1"); env.setdefault("OP_CF_AREA", "customField2")
    env.setdefault("OP_CF_ESTIMATE", "customField3"); env.setdefault("OP_CF_OPENED", "customField4")
    return env


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def tickets_at(repo, ref):
    cfg = git(repo, "show", f"{ref}:docs/doc-kit.config")
    m = re.search(r"^\s*tickets_dir\s*=\s*(\S+)", cfg, re.M)
    tdir = (m.group(1) if m else "docs/tickets").strip("/")
    out = []
    for p in git(repo, "ls-tree", "-r", "--name-only", ref, "--", tdir).splitlines():
        if not p.endswith(".md"):
            continue
        text = git(repo, "show", f"{ref}:{p}")
        mm = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not mm:
            continue
        fm = {}
        for line in mm.group(1).splitlines():
            km = re.match(r"^([a-z-]+):[ \t]*(.*?)[ \t]*$", line)
            if km:
                fm[km.group(1)] = km.group(2)
        if not fm.get("id"):
            continue
        fm["_body"] = mm.group(2).strip()
        fm["_closed_dir"] = f"/{tdir}/closed/".find("/closed/") >= 0 and p.startswith(f"{tdir}/closed/")
        out.append(fm)
    return out


class OP:
    def __init__(self, env):
        self.url = env["OP_URL"].rstrip("/")
        self.s = requests.Session(); self.s.auth = ("apikey", env["OP_TOKEN"])
        self.env = env
        self._by_name = {}

    def get(self, path, **params):
        r = self.s.get(self.url + path, params=params); r.raise_for_status(); return r.json()

    def post(self, path, body):
        return self.s.post(self.url + path, json=body)

    def patch(self, path, body):
        return self.s.patch(self.url + path, json=body)

    def id_by_name(self, kind, name):
        if kind not in self._by_name:
            self._by_name[kind] = {e["name"]: e["id"] for e in self.get(f"/api/v3/{kind}")["_embedded"]["elements"]}
        ids = self._by_name[kind]
        if name not in ids:
            sys.exit(f"mirror: OpenProject has no {kind[:-1]} named {name!r} (have {sorted(ids)})")
        return ids[name]

    def ensure_project(self, ident, name, dry):
        r = self.s.get(f"{self.url}/api/v3/projects/{ident}")
        if r.status_code == 200:
            return r.json()["id"]
        if dry:
            print(f"  would create project {ident}"); return None
        r = self.post("/api/v3/projects", {"identifier": ident, "name": name or ident, "public": True})
        if r.status_code >= 300:
            sys.exit(f"mirror: cannot create project {ident}: {r.status_code} {r.text[:300]}")
        print(f"  created project {ident}")
        return r.json()["id"]

    def existing(self, ident):
        allst = json.dumps([{"status": {"operator": "*", "values": []}}])
        by = {}
        offset = 1
        while True:
            page = self.get(f"/api/v3/projects/{ident}/work_packages", pageSize=500, offset=offset, filters=allst)
            for e in page["_embedded"]["elements"]:
                if e.get(self.env["OP_CF_LEGACY"]):
                    by[e[self.env["OP_CF_LEGACY"]]] = e
            if offset * 500 >= page["total"]:
                break
            offset += 1
        return by

    def version_id(self, project_id, name, cache, dry):
        if not name:
            return None
        if name not in cache:
            if dry:
                return None
            r = self.post("/api/v3/versions", {"name": name, "_links": {"definingProject": {"href": f"/api/v3/projects/{project_id}"}}})
            r.raise_for_status(); cache[name] = r.json()["id"]
        return cache[name]


def desired(op, fm, project_id, versions, dry):
    e = op.env
    status = "CLOSED" if fm["_closed_dir"] else fm.get("status", "OPEN").upper()
    d = {
        "subject": fm["title"],
        "description": {"format": "markdown", "raw": fm["_body"]},
        e["OP_CF_LEGACY"]: fm["id"],
        e["OP_CF_AREA"]: fm.get("area") or None,
        e["OP_CF_ESTIMATE"]: fm.get("estimate") or None,
        e["OP_CF_OPENED"]: fm.get("opened") or None,
        "_links": {
            "type": {"href": f"/api/v3/types/{op.id_by_name('types', TYPE_NAME.get(fm.get('type', '').upper(), 'Task'))}"},
            "status": {"href": f"/api/v3/statuses/{op.id_by_name('statuses', STATUS_NAME.get(status, 'New'))}"},
            "priority": {"href": f"/api/v3/priorities/{op.id_by_name('priorities', fm.get('priority', 'P3').upper() or 'P3')}"},
        },
    }
    vid = op.version_id(project_id, fm.get("milestone"), versions, dry)
    d["_links"]["version"] = {"href": f"/api/v3/versions/{vid}" if vid else None}
    return d


def diff(op, want, have):
    e = op.env
    patch = {}
    if want["subject"] != have["subject"]:
        patch["subject"] = want["subject"]
    if want["description"]["raw"].strip() != (have["description"]["raw"] or "").strip():
        patch["description"] = want["description"]
    for k in (e["OP_CF_AREA"], e["OP_CF_ESTIMATE"], e["OP_CF_OPENED"]):
        if (want[k] or None) != (have.get(k) or None):
            patch[k] = want[k]
    links = {}
    for k in ("type", "status", "priority", "version"):
        if want["_links"][k]["href"] != have["_links"].get(k, {}).get("href"):
            links[k] = want["_links"][k]
    if links:
        patch["_links"] = links
    return patch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True); ap.add_argument("--project", required=True)
    ap.add_argument("--name", default=""); ap.add_argument("--ref", default="origin/master")
    ap.add_argument("--include-closed", action="store_true"); ap.add_argument("--epics-project", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    op = OP(load_env())
    files = tickets_at(a.repo, a.ref)
    if not files:
        print(f"mirror {a.project}: no tickets at {a.ref} (or ref missing) — nothing to do"); return 0
    project_id = op.ensure_project(a.project, a.name, a.dry_run)
    have = op.existing(a.project) if project_id else {}
    versions = {v["name"]: v["id"] for v in op.get(f"/api/v3/projects/{a.project}/versions")["_embedded"]["elements"]} if project_id else {}
    n = {"created": 0, "updated": 0, "unchanged": 0, "skipped_closed": 0, "failed": 0}
    id_to_wp = {k: v["id"] for k, v in have.items()}
    for fm in files:
        lid = fm["id"]
        want = desired(op, fm, project_id, versions, a.dry_run)
        if lid in have:
            patch = diff(op, want, have[lid])
            if not patch:
                n["unchanged"] += 1; continue
            patch["lockVersion"] = have[lid]["lockVersion"]
            print(f"  update {lid} -> WP#{have[lid]['id']}: {', '.join(k for k in patch if k != 'lockVersion')}")
            if a.dry_run:
                n["updated"] += 1; continue
            r = op.patch(f"/api/v3/work_packages/{have[lid]['id']}", patch)
            if r.status_code >= 300:
                print(f"  ! update failed {lid}: {r.status_code} {r.text[:200]}", file=sys.stderr); n["failed"] += 1
            else:
                n["updated"] += 1
        else:
            if fm["_closed_dir"] and not a.include_closed:
                n["skipped_closed"] += 1; continue
            print(f"  create {lid}")
            if a.dry_run:
                n["created"] += 1; continue
            r = op.post(f"/api/v3/projects/{a.project}/work_packages", want)
            if r.status_code >= 300:
                print(f"  ! create failed {lid}: {r.status_code} {r.text[:200]}", file=sys.stderr); n["failed"] += 1
            else:
                id_to_wp[lid] = r.json()["id"]; n["created"] += 1
    # epic -> parent, derived from each child's own `epic:` field; the parent may live in another project
    if not a.dry_run:
        epics = op.existing(a.epics_project) if a.epics_project else have
        for fm in files:
            parent = fm.get("epic", "").strip()
            if not parent or fm["id"] not in id_to_wp or parent not in ({k: v["id"] for k, v in epics.items()} | id_to_wp):
                continue
            pid = epics[parent]["id"] if parent in epics else id_to_wp[parent]
            cur = have.get(fm["id"], {}).get("_links", {}).get("parent", {}).get("href")
            want_href = f"/api/v3/work_packages/{pid}"
            if cur == want_href:
                continue
            wp = op.get(f"/api/v3/work_packages/{id_to_wp[fm['id']]}")
            r = op.patch(f"/api/v3/work_packages/{wp['id']}", {"lockVersion": wp["lockVersion"], "_links": {"parent": {"href": want_href}}})
            print(f"  parent {fm['id']} -> {parent}: {r.status_code}")
            if r.status_code >= 300:
                print(f"    {r.text[:200]}", file=sys.stderr)
    print(f"mirror {a.project} [{a.ref}]: " + " ".join(f"{k}={v}" for k, v in n.items()))
    return 1 if n["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
