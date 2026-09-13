# CLAUDE.md

<!-- ticketing-template:begin (written by install.sh — edit the kit, not this block) -->
## 🎫 Work items — this repo runs ticketing-template

**Read `docs/TICKETING.md` before your first commit here.** It is the complete procedure:
create → push → branch → work → close → merge, and epics. The git hooks enforce it and
print the fixing command when you break a rule.

- This repo's ticket prefix(es): `MONIT`. Epics scope: `stealth`.
- Board: `python3 scripts/wi.py` · new item: `python3 scripts/wi.py new <PFX>-NNN <TYPE> "<title>"`
- One ticket creation per commit, on master, **pushed before you branch**; every commit's
  subject names its item; work never lands on master directly.
- Multi-repo work is an EPIC in `stealth-epics` (beside this repo or an ancestor), one STORY per repo (`epic: <ID>`).
- Decisions: `python3 scripts/wi.py decision new "<title>"` → one file in `docs/decisions/`, same commit, four parts; it is copied in full into the ticket you are on; cite an earlier one with `{{Dn}}`. Status: `OPEN | BLOCKED | CLOSED`.
- After every clone: `bash scripts/install-git-hooks.sh`, then `bash scripts/epics.sh ensure`
- New here? `docs/TICKETING.md` is the procedure; the kit is https://github.com/ljr9600/ticketing-template.git — start at its `START-HERE.md`.
<!-- ticketing-template:end -->
