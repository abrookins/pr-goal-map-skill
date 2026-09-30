"""Deterministic pre-passes for collect.py.

These do the parts of pass B and pass E that need no judgment, so the agent reads less
and writes fewer notes:

  import_only / mechanical groups  pre-fill role and note for hunks that only touch imports,
                                   or that repeat the same small edit across many hunks
  test_index                       which test functions the diff adds, removes, renames or edits
  undone_files                     files a commit changed that are identical at base and head
  stale_refs                       definitions the diff removes that are still referenced at head
  compact_diff                     changed lines only, auto-noted hunks collapsed to one line

Stdlib only.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from collections import OrderedDict, defaultdict

IMPORT_LINE_RE = re.compile(
    r"^\s*(?:$|import\b|from\s+\S+\s+import\b|use\s+[\w:]+|#include\b|require\(|const\s+\w+\s*=\s*require\(|"
    r"\)$|(?:[\w.]+\s+)?\"[^\"]+\"\s*;?$|'[^']+'\s*;?$)"
)
IMPORT_CONTEXT_RE = re.compile(r"^(?:import\b|package\b|from\s|use\s|#include)")
TOKEN_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`|\w+|\S")
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
MECH_MAX_CHANGED = 8     # a hunk with more changed lines than this is never "mechanical"
MECH_MIN_GROUP = 3       # an edit must repeat in at least this many hunks

TEST_DEF_RES = [
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(Test\w+|Benchmark\w+|Fuzz\w+|Example\w*)\s*\("),  # Go
    re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\("),                                     # Python
    re.compile(r"^\s*(?:it|test|describe)(?:\.\w+)?\(\s*['\"`]([^'\"`]{3,120})['\"`]"),       # JS/TS
    re.compile(r"^\s*(?:public\s+)?(?:void|fun)\s+(test\w+)\s*\("),                             # Java/Kotlin
    re.compile(r"^\s*#\[test\][\s\S]*?fn\s+(\w+)|^\s*fn\s+(test_\w+)\s*\("),                    # Rust
]
DEF_RES = [
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]"),   # Go func / method
    re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s"),                         # Go type
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),           # Python def
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),                          # Python / JS / Java class
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_]\w*)"),
]
FIELD_TAG_RE = re.compile(r"^\s*([A-Z]\w*)\s+[\w\[\]*.{}]+\s+`[^`]*(?:mapstructure|json|yaml|toml|env):\"([\w.-]+)")


def _changed(h):
    return [t for t in h["text"] if t[:1] in "+-" and not t.startswith(("+++", "---"))]


# ---------------------------------------------------------------- import-only hunks
def import_only(h):
    """Return a note if every changed line of the hunk is an import line, else None."""
    ch = _changed(h)
    if not ch or not all(IMPORT_LINE_RE.match(t[1:]) for t in ch):
        return None
    in_block = IMPORT_CONTEXT_RE.match(h.get("header") or "") or any(
        re.match(r"^\s*import\b", t[1:]) for t in h["text"])
    if not in_block and not any(re.match(r"^\s*(import|from|use|#include)\b", t[1:]) for t in ch):
        # quoted strings alone could be anything (a list of names); require an import context
        ctx = "\n".join(t[1:] for t in h["text"] if t[:1] == " ")
        if not re.search(r"^\s*import\s*\($|^import\b", ctx, re.M):
            return None

    def names(sign):
        out = []
        for t in ch:
            if t[0] != sign:
                continue
            m = re.search(r"\"([^\"]+)\"|'([^']+)'|import\s+([\w.]+)|from\s+([\w.]+)", t)
            if m:
                segs = next(g for g in m.groups() if g).split("/")
                out.append(segs[-2] if len(segs) > 1 and re.fullmatch(r"v\d+", segs[-1]) else segs[-1])
        return out

    add, rem = names("+"), names("-")
    parts = []
    if add:
        parts.append("adds " + ", ".join(add))
    if rem:
        parts.append("drops " + ", ".join(rem))
    return "Import changes only: " + "; ".join(parts) + "." if parts else "Import block reformatted."


# ---------------------------------------------------------------- repeated small edits
def _anchor(tokens, pos):
    """Innermost call or literal name enclosing token position pos within one line."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        t = tokens[i]
        if t in ")]}":
            depth += 1
        elif t in "([{":
            if depth == 0:
                j = i - 1
                while j >= 0 and tokens[j] in (".",):
                    j -= 1
                if j >= 0 and IDENT_RE.fullmatch(tokens[j]):
                    return tokens[j]
            else:
                depth -= 1
    return None


def _norm_run(run):
    out = []
    for t in run:
        if t[:1] in "\"'`" or re.fullmatch(r"\d[\w.]*", t):
            out.append("_")
        else:
            out.append(t)
    return "".join(out)


def edit_signature(h):
    """A stable description of a small, line-for-line edit, or None if the hunk isn't one."""
    ch = _changed(h)
    if not ch or len(ch) > MECH_MAX_CHANGED:
        return None
    minus = [t[1:] for t in ch if t[0] == "-"]
    plus = [t[1:] for t in ch if t[0] == "+"]
    if not minus or not plus:
        return None
    if any(d.match(t) for t in minus + plus for d in DEF_RES):
        return None   # adds or removes a declaration: not mechanical
    anchors = set()
    sm = difflib.SequenceMatcher(a=minus, b=plus, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            continue
        if op != "replace":
            # a whole line added or dropped: describe by its first identifier
            for t in (minus[a0:a1] + plus[b0:b1]):
                ids = IDENT_RE.findall(t)
                if ids:
                    anchors.add(("line", ids[0]))
            continue
        n = min(a1 - a0, b1 - b0)
        for t in minus[a0 + n:a1] + plus[b0 + n:b1]:
            ids = IDENT_RE.findall(t)
            if ids:
                anchors.add(("line", ids[0]))
        for ml, pl in zip(minus[a0:a1], plus[b0:b1]):
            mt, pt = TOKEN_RE.findall(ml), TOKEN_RE.findall(pl)
            tsm = difflib.SequenceMatcher(a=mt, b=pt, autojunk=False)
            for top, x0, x1, y0, y1 in tsm.get_opcodes():
                if top == "equal":
                    continue
                name = _anchor(mt, x0) or _anchor(pt, y0)
                if not name:
                    ids = [t for t in (mt[x0:x1] + pt[y0:y1]) if IDENT_RE.fullmatch(t)]
                    name = ids[0] if ids else _norm_run(mt[x0:x1]) + "→" + _norm_run(pt[y0:y1])
                anchors.add(("call", name))
    if not anchors:
        return None
    return tuple(sorted(anchors))


def first_pair(h):
    ch = _changed(h)
    old = next((t[1:].strip() for t in ch if t[0] == "-"), "")
    new = next((t[1:].strip() for t in ch if t[0] == "+"), "")
    return old, new


def mechanical_groups(hunks, files_by_id):
    """Group hunks sharing an edit signature across at least MECH_MIN_GROUP hunks."""
    groups = defaultdict(list)
    for h in hunks:
        if h.get("auto"):
            continue
        sig = edit_signature(h)
        if sig:
            groups[sig].append(h)
    out = []
    for sig, hs in groups.items():
        if len(hs) < MECH_MIN_GROUP:
            continue
        example, example_new = first_pair(hs[0])
        names = sorted({n for _, n in sig})
        out.append({"signature": names, "hunks": [h["id"] for h in hs],
                    "files": list(OrderedDict((h["file"], 1) for h in hs)),
                    "example": [example.strip()[:160], example_new.strip()[:160]]})
    out.sort(key=lambda g: -len(g["hunks"]))
    return out


def apply_auto_notes(hunks, files_by_id):
    """Pre-fill role/note for import-only and mechanical hunks. Returns mechanical groups."""
    for h in hunks:
        note = import_only(h)
        if note:
            h.update(role="incidental", note=note, auto="import")
    groups = mechanical_groups(hunks, files_by_id)
    for i, g in enumerate(groups, 1):
        where = ", ".join(g["signature"][:3])
        for hid in g["hunks"]:
            h = next(x for x in hunks if x["id"] == hid)
            is_test = files_by_id[h["file"]]["kind"] == "test"
            old, new = first_pair(h)
            h.update(role="test" if is_test else "refactor", auto=f"mech{i}",
                     note=f"Mechanical edit mech{i} ({len(g['hunks'])} hunks, at {where}): "
                          f"`{old[:90]}` → `{new[:90]}`")
        g["id"] = f"mech{i}"
    return groups


# ---------------------------------------------------------------- tests
def test_index(hunks, files_by_id):
    """Test functions per hunk: added, removed, renamed (removed+added in one hunk) or edited."""
    rows = []
    for h in hunks:
        if files_by_id[h["file"]]["kind"] != "test":
            continue
        added, removed = [], []
        for t in h["text"]:
            if t[:1] not in "+-":
                continue
            for r in TEST_DEF_RES:
                m = r.match(t[1:])
                if m:
                    name = next(g for g in m.groups() if g)
                    (added if t[0] == "+" else removed).append(name)
                    break
        edited = []
        if not added and not removed:
            m = None
            for r in TEST_DEF_RES:
                m = r.match(h.get("header") or "")
                if m:
                    break
            if not m:
                ctx = [t[1:] for t in h["text"] if t[:1] == " "]
                for line in reversed(ctx[:3]):
                    for r in TEST_DEF_RES:
                        m = r.match(line)
                        if m:
                            break
                    if m:
                        break
            if m:
                edited.append(next(g for g in m.groups() if g))
        renamed = []
        if len(added) == 1 and len(removed) == 1:
            renamed.append((removed[0], added[0]))
            added, removed = [], []
        for n in added:
            rows.append({"hunk": h["id"], "name": n, "change": "added"})
        for n in removed:
            rows.append({"hunk": h["id"], "name": n, "change": "removed"})
        for old, new in renamed:
            rows.append({"hunk": h["id"], "name": new, "change": f"renamed from {old}"})
        for n in edited:
            rows.append({"hunk": h["id"], "name": n, "change": "edited"})
    return rows


# ---------------------------------------------------------------- undone within range
def undone_files(git, repo, base, head, commits, final_paths):
    """Files some commit in the range changed that end up identical at base and head."""
    touched = OrderedDict()
    for c in commits:
        out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", c["full"], check=False)
        for p in out.split("\n"):
            if p and p not in final_paths:
                touched.setdefault(p, []).append(c["sha"])
    rows = []
    for p, shas in touched.items():
        rows.append({"path": p, "commits": shas,
                     "note": "changed by " + ", ".join(shas) + "; no net change in the final diff"})
    return rows


# ---------------------------------------------------------------- removed definitions
def removed_definitions(hunks):
    """Names the diff deletes a definition for (and doesn't re-add anywhere)."""
    removed, added = OrderedDict(), set()
    for h in hunks:
        for t in h["text"]:
            if t[:1] not in "+-":
                continue
            line = t[1:]
            names = []
            for r in DEF_RES:
                m = r.match(line)
                if m:
                    names.append(m.group(1))
            m = FIELD_TAG_RE.match(line)
            if m:
                names += [m.group(1), m.group(2)]
            for n in names:
                if t[0] == "-":
                    removed.setdefault(n, h["id"])
                else:
                    added.add(n)
    return OrderedDict((n, hid) for n, hid in removed.items() if n not in added and len(n) >= 4)


def stale_refs(repo, head, hunks, limit=40):
    """For each removed definition, where the name still appears at head (git grep -w)."""
    rows = []
    for name, hid in list(removed_definitions(hunks).items())[:limit]:
        p = subprocess.run(["git", "grep", "-n", "-w", "-I", "-e", name, head, "--"], cwd=repo,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
        hits = [l[len(head) + 1:] if l.startswith(head + ":") else l for l in p.stdout.split("\n") if l]
        rows.append({"name": name, "removedIn": hid, "stillReferenced": hits[:8], "refCount": len(hits)})
    return rows


# ---------------------------------------------------------------- compact diff
def compact_diff(files, hunks_by_id):
    """Changed lines only; auto-noted hunks collapse to their note."""
    out = []
    for f in files:
        if f["kind"] == "generated":
            out.append(f"# {f['id']} {f['path']}  (generated, +{f['add']} −{f['del']}; skipped)")
            continue
        out.append(f"# {f['id']} {f['path']}  (+{f['add']} −{f['del']}, {f['kind']}, {f['status']})")
        for hid in f["hunks"]:
            h = hunks_by_id[hid]
            head = f"## {hid} · {h['header'][:100]}  (+{h['add']} −{h['del']}; {', '.join(h['commits']) or 'n/a'})"
            if h.get("auto"):
                out.append(f"{head}  [auto {h['auto']}] {h['note']}")
                continue
            out.append(head)
            gap = False
            for t in h["text"]:
                if t[:1] in "+-":
                    out.append(t)
                    gap = False
                elif not gap:
                    out.append(" …")
                    gap = True
        out.append("")
    return "\n".join(out) + "\n"
