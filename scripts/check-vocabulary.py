#!/usr/bin/env python3
"""Areas and tags come from word lists, when a project keeps them (KIT-060).

Run by the pre-commit hook against the STAGED commit. Two checks:

  1. Tickets. Every staged ticket whose `area:` or `tags:` is new or CHANGED must use live
     words from the lists: a synonym is refused naming its word, a merged word naming its
     successor, an unknown word listing the choices. An old ticket that keeps an old word
     (an `area: misc` from before the lists) is not re-judged -- it can still be worked and
     closed. The lists are the scope's (the epics repo at origin/<default>) in a scoped
     work repo, else this repo's own.
  2. The lists themselves, when THIS repo holds them and a list file is staged: the word
     form, a meaning, no pile words (misc/other/general/various), no duplicate, no synonym
     that is also a word or belongs to two words, `merged_into` naming a live word, and no
     row deleted since HEAD -- a word is merged, never removed, so old tickets still render.

    python3 scripts/check-vocabulary.py        # exit 1 on any problem; lists them
"""
import sys; sys.dont_write_bytecode = True  # no __pycache__ litter in adopting repos (KIT-011)
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_kit import (ID_RE, NOT_WORDS, VOCAB_KINDS, WORD_MAX, WORD_RE, check_word,  # noqa: E402
                     epics_root, load_config, load_vocab, vocab_rows)

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT)
TDIR = CFG.get("tickets_dir", "docs/tickets").rstrip("/")
VDIR = (CFG.get("vocabulary_dir") or "vocabulary").strip().strip("/") or "vocabulary"


def git(*a) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True)
    return r.returncode, r.stdout


def front(text: str | None) -> dict:
    fm = {}
    m = re.match(r"^---\n(.*?)\n---", text or "", re.S)
    for line in (m.group(1).splitlines() if m else []):
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km:
            fm[km.group(1)] = km.group(2).strip()
    return fm


def tags_of(fm: dict) -> set[str]:
    return {t for t in re.split(r"[,\s]+", fm.get("tags", "")) if t}


def check_tickets(problems: list[str], notes: list[str]) -> int:
    vocab, unavailable = load_vocab(ROOT, CFG, staged=True)   # the commit, not the working tree
    if unavailable:
        notes.append(f"NOTE — word lists not checked: {unavailable.removeprefix('UNAVAILABLE: ')}")
        return 0
    if not vocab:
        return 0
    _, out = git("diff", "--cached", "--name-status", "-M", "--diff-filter=AMR", "--", TDIR)
    n = 0
    for line in out.splitlines():
        parts = line.split("\t")
        new, old = parts[-1], (parts[1] if parts[0][0] in "MR" else None)
        if not new.endswith(".md") or "/templates/" in new:
            continue
        rc, staged = git("show", f":{new}")
        fm = front(staged if rc == 0 else "")
        if not fm.get("id"):
            continue
        head = front(git("show", f"HEAD:{old}")[1]) if old else {}
        n += 1
        if "areas" in vocab and (not head or fm.get("area", "") != head.get("area", "")):
            why = check_word(vocab["areas"][0], "areas", fm.get("area", ""))
            if why:
                problems.append(f"{fm['id']}: area — {why}")
        if "tags" in vocab:
            for tag in sorted(tags_of(fm) - tags_of(head)):
                why = check_word(vocab["tags"][0], "tags", tag)
                if why:
                    problems.append(f"{fm['id']}: tag — {why}")
    return n


def check_lists(problems: list[str]) -> int:
    eroot, why = epics_root(ROOT, CFG)
    if eroot is not None or why != "local":
        return 0                                    # a scoped repo's lists live in its epics repo
    n = 0
    for kind in VOCAB_KINDS:
        rel = f"{VDIR}/{kind}.tsv"
        rc, out = git("diff", "--cached", "--name-only", "--", rel)
        if not out.strip():
            continue
        n += 1
        rc, staged = git("show", f":{rel}")
        if rc != 0:                                  # the whole file deleted
            problems.append(f"{rel}: a word list is never deleted — merge words instead")
            continue
        rows = vocab_rows(staged)
        cols = rows[0]["_cols"] if rows else [c.lower() for c in (staged.strip().splitlines() or [""])[0].split("\t")]
        for need in ("word", "meaning"):
            if need not in cols:
                problems.append(f"{rel}: the header row must name a '{need}' column")
        words: dict[str, int] = {}
        syn_owner: dict[str, str] = {}
        for r in rows:
            w = r.get("word", "")
            if not re.fullmatch(WORD_RE, w) or len(w) > WORD_MAX:
                problems.append(f"{rel}: '{w}' is not a word — lowercase letters/digits, words joined by '-', "
                                f"at most {WORD_MAX} characters")
            if w in NOT_WORDS:
                problems.append(f"{rel}: '{w}' is a pile, not a word — name the part of the system")
            if not r.get("meaning"):
                problems.append(f"{rel}: '{w}' has no meaning — say, in one line a person understands, what it covers")
            words[w] = words.get(w, 0) + 1
            for s in r["synonyms"]:
                if s in NOT_WORDS:
                    problems.append(f"{rel}: '{s}' (a synonym of '{w}') is a pile — it cannot stand for one word")
                if s in syn_owner and syn_owner[s] != w:
                    problems.append(f"{rel}: synonym '{s}' is listed under both '{syn_owner[s]}' and '{w}'")
                syn_owner[s] = w
        for w, c in words.items():
            if c > 1:
                problems.append(f"{rel}: '{w}' is listed {c} times — one row per word")
        for s, w in syn_owner.items():
            if s in words:
                problems.append(f"{rel}: '{s}' is a word AND a synonym of '{w}' — keep one (merge '{s}' into '{w}')")
        live = {r["word"] for r in rows if not r.get("merged_into")}
        for r in rows:
            m = r.get("merged_into", "")
            if m and (m not in live or m == r.get("word")):
                problems.append(f"{rel}: '{r.get('word')}' is merged into '{m}', which is not a live word on the list")
        rc, head = git("show", f"HEAD:{rel}")
        before = {r["word"] for r in vocab_rows(head)} if rc == 0 else set()
        # A NEW word carries its history (the rules in TICKETING.md): when it was added, why,
        # and the ticket that needed it -- which may be filed right after this commit, or `-`
        # for a starter list written before any ticket.
        for r in rows:
            w = r.get("word", "")
            if w in before:
                continue
            miss = [c for c in ("added", "first_ticket", "reason") if not r.get(c)]
            if miss:
                problems.append(f"{rel}: new word '{w}' needs {', '.join(miss)} (when, which ticket needed it or -, why)")
            if r.get("added") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["added"]):
                problems.append(f"{rel}: new word '{w}': added must be a date, YYYY-MM-DD")
            ft = r.get("first_ticket", "")
            if ft and ft != "-" and not re.fullmatch(ID_RE, ft):
                problems.append(f"{rel}: new word '{w}': first_ticket must be a ticket id or -")
        if rc == 0:
            gone = sorted(before - set(words))
            if gone:
                problems.append(f"{rel}: {', '.join(gone)} removed — a word is never deleted (old tickets use it); "
                                f"set its merged_into instead")
    return n


def main() -> int:
    problems: list[str] = []
    notes: list[str] = []
    tickets = check_tickets(problems, notes)
    lists = check_lists(problems)
    for n in notes:
        print(f"word lists: {n}", file=sys.stderr)
    if problems:
        print("word lists: REJECTED", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    if tickets or lists:
        print(f"word lists: {tickets} ticket(s) and {lists} list(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
