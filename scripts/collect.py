#!/usr/bin/env python3
"""Collect the evidence bundle for a PR goal map.

Stdlib only; needs git, and optionally the GitHub CLI (gh) for PR metadata and issues.

Usage:
  collect.py --pr 1534 [--repo PATH] [--out DIR]
  collect.py --pr-json pr.json [--repo PATH] [--out DIR]   # PR metadata saved from gh, e.g. where gh can't run
  collect.py --base main --head feature-branch [--repo PATH] [--out DIR]
  collect.py --pr 1534 --base <sha>          # override the base (e.g. stacked or re-based branches)

Writes into --out:
  evidence.json       machine-readable bundle (pr, commits, files, hunks, sources, ticket keys)
  evidence.md         digest for the agent to read first
  diffs/<file-id>.diff  per-file diffs, split into symbol-level hunks marked "### H12"
  sources/            local copies of every text the goal map may quote (PR body, issues, docs)
  diffs/compact.diff  every file's changed lines only; auto-noted hunks collapsed to one line
  throughline.json    skeleton for the agent to fill in (pr, files, hunks, commits, sources pre-filled;
                      import-only and mechanical hunks carry a pre-filled role and note, marked "auto")
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import OrderedDict, defaultdict

import autonotes

TICKET_RE_DEFAULT = r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b"
GH_ISSUE_RE = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d+)\b")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
DOC_EXT = (".md", ".mdx", ".rst", ".adoc", ".txt")
SPLIT_MIN_ADDED = 60     # only split pure-addition hunks at least this long
CHUNK_MIN_LINES = 6      # merge chunks smaller than this into the previous one
MAX_HUNK_TEXT = 400      # lines of diff text kept per hunk in evidence.json

DECL_RE = re.compile(
    r"^(?:func |type |var \(|const \(|var [A-Za-z_]|const [A-Za-z_]|class |def |async def |export |"
    r"public |private |protected |internal |static |interface |enum |struct |impl |fn |pub |"
    r"module |describe\(|it\(|test\(|@[A-Za-z]|CREATE |ALTER |resource |data \"|[A-Za-z_][\w.-]*:\s*$)"
)
MD_HEAD_RE = re.compile(r"^#{1,4} \S")


# ---------------------------------------------------------------- helpers
def run(args, cwd, check=True, input_text=None):
    p = subprocess.run(args, cwd=cwd, input=input_text, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {p.stderr.strip()[:500]}")
    return p.stdout


def git(repo, *args, check=True):
    return run(["git", "-c", "core.quotepath=off", *args], cwd=repo, check=check)


def have(cmd):
    return shutil.which(cmd) is not None


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def classify(path):
    p = path.lower()
    name = os.path.basename(p)
    if (re.search(r"(^|/)(vendor|node_modules|third_party)/", p) or name.endswith((".lock", ".sum"))
            or name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "cargo.lock")
            or re.search(r"\.(pb|gen|generated)\.\w+$|_generated\.\w+$|\.min\.(js|css)$", p)):
        return "generated"
    if (re.search(r"(^|/)(tests?|__tests__|spec|e2e|integration|testdata|fixtures)/", p)
            or re.search(r"(_test\.\w+|\.test\.\w+|\.spec\.\w+|^test_.*\.py)$", name)):
        return "test"
    if name.endswith(DOC_EXT) or re.search(r"(^|/)(docs?|adr|adrs|rfcs?|specs?|design)/", p):
        return "doc" if name.endswith(DOC_EXT) or "." not in name else "config"
    if re.search(r"\.(ya?ml|json|toml|ini|cfg|conf|env|sh|bash|mk|tf|hcl|dockerfile)$", name) \
            or name in ("dockerfile", "makefile", "taskfile.yml") or p.startswith(".github/"):
        return "config"
    return "code"


def group_of(path):
    parts = path.split("/")
    if len(parts) <= 2:
        return parts[0] if len(parts) == 2 else "(root)"
    return "/".join(parts[:-1][:3]) + ("/…" if len(parts) > 4 else "")


def short_name(path):
    parts = path.split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 and len(parts[-1]) < 14 else parts[-1]


# ---------------------------------------------------------------- PR / range
def gh_json(repo, args):
    out = run(["gh", *args], cwd=repo, check=False)
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        return None


def resolve_rev(repo, rev):
    return git(repo, "rev-parse", "--verify", rev + "^{commit}").strip()


PR_FIELDS = ("number,title,body,url,author,baseRefName,headRefName,headRefOid,state,mergedAt,"
             "closingIssuesReferences,labels,additions,deletions,changedFiles")


def pr_json_hint(number):
    return (f"Save the PR metadata where gh works and pass it with --pr-json:\n"
            f"  gh pr view {number or '<N>'} --json {PR_FIELDS} > pr.json")


def load_pr(repo, number):
    if not have("gh"):
        raise SystemExit("--pr needs the GitHub CLI (gh). Use --base/--head for a local range instead.\n"
                         + pr_json_hint(number))
    pr = gh_json(repo, ["pr", "view", str(number), "--json", PR_FIELDS])
    if not pr:
        raise SystemExit(f"gh could not read PR {number}. Is gh authenticated for this repo? "
                         f"(In a sandbox, gh can fail on TLS or keychain access.)\n" + pr_json_hint(number))
    return ensure_head(repo, pr, number)


def load_pr_json(repo, path, number):
    try:
        pr = json.load(open(path))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"cannot read --pr-json {path}: {e}")
    if number and pr.get("number") and int(pr["number"]) != number:
        raise SystemExit(f"--pr {number} does not match number {pr['number']} in {path}")
    pr.setdefault("number", number)
    if not pr.get("number"):
        m = re.search(r"/pull/(\d+)", pr.get("url") or "")
        if not m:
            raise SystemExit(f"{path} has no number or url; pass --pr N as well")
        pr["number"] = int(m.group(1))
    if not pr.get("changedFiles") and isinstance(pr.get("files"), list):
        pr["changedFiles"] = len(pr["files"])
    if not pr.get("headRefOid"):
        # read-only lookup; works where gh can't but git over HTTPS or SSH can
        out = git(repo, "ls-remote", "origin", f"refs/pull/{pr['number']}/head", check=False).split()
        if out:
            pr["headRefOid"] = out[0]
    return ensure_head(repo, pr, pr["number"])


def ensure_head(repo, pr, number):
    head = pr.get("headRefOid")
    if head and subprocess.run(["git", "cat-file", "-e", head + "^{commit}"], cwd=repo,
                               stderr=subprocess.DEVNULL).returncode != 0:
        warn(f"head {head[:9]} not in local repo; fetching pull/{number}/head")
        git(repo, "fetch", "origin", f"pull/{number}/head:refs/pr-goal-map/pr-{number}", check=False)
        if subprocess.run(["git", "cat-file", "-e", head + "^{commit}"], cwd=repo,
                          stderr=subprocess.DEVNULL).returncode != 0:
            raise SystemExit(f"head {head[:9]} is not in the local repo and could not be fetched "
                             f"(read-only checkout?). Run `git fetch origin pull/{number}/head` where you can, "
                             f"or pass --head <sha> for a commit you have.")
    return pr


# ---------------------------------------------------------------- diff parsing
def parse_diff(text):
    """Yield file dicts with hunks: {old, new, status, binary, hunks:[{header, old_start, new_start, lines}]}"""
    files = []
    cur = None
    hunk = None
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            cur = {"old": None, "new": None, "status": "M", "binary": False, "hunks": []}
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            if m:
                cur["old"], cur["new"] = m.group(1), m.group(2)
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if hunk is None:
            if line.startswith("new file mode"):
                cur["status"] = "A"
            elif line.startswith("deleted file mode"):
                cur["status"] = "D"
            elif line.startswith("rename from "):
                cur["status"] = "R"; cur["old"] = line[len("rename from "):]
            elif line.startswith("rename to "):
                cur["new"] = line[len("rename to "):]
            elif line.startswith("Binary files"):
                cur["binary"] = True
            elif line.startswith("--- "):
                v = line[4:]
                cur["old"] = None if v == "/dev/null" else re.sub(r"^a/", "", v)
                continue
            elif line.startswith("+++ "):
                v = line[4:]
                cur["new"] = None if v == "/dev/null" else re.sub(r"^b/", "", v)
                continue
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$", line)
        if m:
            hunk = {"old_start": int(m.group(1)), "new_start": int(m.group(3)),
                    "context": m.group(5).strip(), "lines": []}
            cur["hunks"].append(hunk)
            continue
        if hunk is not None and line[:1] in (" ", "+", "-", "\\"):
            hunk["lines"].append(line)
    return files


def annotate_lines(h):
    """Return list of (kind, text, new_lineno or None)."""
    out = []
    n = h["new_start"]
    for l in h["lines"]:
        k = l[:1]
        if k == "+":
            out.append(("+", l[1:], n)); n += 1
        elif k == " ":
            out.append((" ", l[1:], n)); n += 1
        elif k == "-":
            out.append(("-", l[1:], None))
        else:
            out.append(("\\", l, None))
    return out


def split_hunk(h, is_md):
    """Split a pure-addition hunk at top-level declarations or markdown headings.

    Returns [(label, rows)], where rows are (kind, text, new_lineno)."""
    rows = annotate_lines(h)
    added = sum(1 for r in rows if r[0] == "+")
    removed = sum(1 for r in rows if r[0] == "-")
    if removed or added < SPLIT_MIN_ADDED:
        # prefer a declaration the hunk itself adds over git's "enclosing function" context,
        # which names the function *before* the hunk when the hunk adds a new one
        pat0 = MD_HEAD_RE if is_md else DECL_RE
        own = next((t.strip() for k, t, _ in rows if k == "+" and pat0.match(t) and not t.startswith("@")
                    and not re.match(r"^[A-Za-z_][\w.-]*:\s*$", t)), "")
        return [((own or h["context"])[:120], rows)]
    pat = MD_HEAD_RE if is_md else DECL_RE
    cuts = [0]
    in_fence = False
    decorator_open = False
    for i, (k, t, _) in enumerate(rows):
        if is_md and t.startswith("```"):
            in_fence = not in_fence
        if not is_md and decorator_open and t and not t[0].isspace():
            if t.startswith((")", "]", "}", "@")):
                continue
            if re.match(r"^(def |async def |class |func |fn |pub |export |public |private |protected )", t):
                decorator_open = False
                continue
            decorator_open = False
        if i and k == "+" and not in_fence and pat.match(t):
            # pull leading comment lines into the new chunk
            j = i
            while (not is_md and j - 1 > cuts[-1] and rows[j - 1][0] == "+"
                   and re.match(r"^(//|#|/\*|\*|--|\"\"\")", rows[j - 1][1].strip() or "x")):
                j -= 1
            cuts.append(j)
            decorator_open = (not is_md) and t.startswith("@")
    cuts.append(len(rows))
    segs = [rows[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]
    # merge segments with no changed lines or too few lines into a neighbour
    merged = []
    for seg in segs:
        changed = sum(1 for r in seg if r[0] in "+-")
        if merged and (changed == 0 or len(seg) < CHUNK_MIN_LINES):
            merged[-1] = merged[-1] + seg
        elif merged and sum(1 for r in merged[-1] if r[0] in "+-") == 0:
            merged[-1] = merged[-1] + seg
        else:
            merged.append(seg)
    chunks = []
    for seg in merged:
        label = ""
        for k, t, _ in seg:
            if k == "+" and pat.match(t) and not t.startswith("@"):
                label = t.strip()
                break
        if not label:
            for k, t, _ in seg:
                if k == "+" and pat.match(t):
                    label = t.strip()
                    break
        chunks.append((label[:120], seg))
    return chunks


def enclosing_label(lines, lineno, is_md):
    """Nearest heading/declaration at or above 1-based lineno in the head file."""
    pat = MD_HEAD_RE if is_md else DECL_RE
    for i in range(min(lineno, len(lines)) - 1, -1, -1):
        t = lines[i]
        if pat.match(t) and not t.startswith("@") and not re.match(r"^[A-Za-z_][\w.-]*:\s*$", t) or (is_md and MD_HEAD_RE.match(t)):
            return t.strip()[:120]
    return ""


# ---------------------------------------------------------------- blame
def blame_commits(repo, head, path, line_numbers, range_shas):
    """Map each new-side line number to the range commit that last touched it."""
    if not line_numbers:
        return {}
    nums = sorted(set(line_numbers))
    ranges = []
    s = p = nums[0]
    for n in nums[1:]:
        if n == p + 1:
            p = n
            continue
        ranges.append((s, p)); s = p = n
    ranges.append((s, p))
    args = ["blame", "--line-porcelain", "-w"]
    for a, b in ranges[:400]:
        args += ["-L", f"{a},{b}"]
    args += [head, "--", path]
    out = git(repo, *args, check=False)
    res = {}
    for line in out.split("\n"):
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
        if m:
            sha, final = m.group(1), int(m.group(2))
            if sha in range_shas:
                res[final] = sha
    return res


# ---------------------------------------------------------------- docs discovery
def outline(text, limit=60):
    heads = [l.strip() for l in text.split("\n") if MD_HEAD_RE.match(l)]
    return heads[:limit]


def show_file(repo, rev, path):
    return git(repo, "show", f"{rev}:{path}", check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--pr-json", help="PR metadata from `gh pr view N --json ...`, for when gh can't run here")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--out")
    ap.add_argument("--ticket-pattern", default=TICKET_RE_DEFAULT,
                    help="regex for tracker keys like RED-216616 (default: %(default)s)")
    ap.add_argument("--max-prior-docs", type=int, default=12)
    ap.add_argument("--title", help="title for a range without a PR (default: branch name or last commit subject)")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    repo = git(repo, "rev-parse", "--show-toplevel").strip()
    ticket_re = re.compile(args.ticket_pattern)

    pr = None
    if args.pr_json:
        pr = load_pr_json(repo, args.pr_json, args.pr)
        args.pr = pr["number"]
    elif args.pr:
        pr = load_pr(repo, args.pr)
    if not args.pr and not (args.base and args.head):
        raise SystemExit("give --pr N, --pr-json FILE, or --base and --head")

    head_rev = args.head or (pr and (pr.get("headRefOid") or f"refs/pr-goal-map/pr-{args.pr}"))
    head = resolve_rev(repo, head_rev)
    if args.base:
        base_tip = resolve_rev(repo, args.base)
    else:
        bname = pr.get("baseRefName") or "main"
        base_tip = None
        for cand in (f"origin/{bname}", bname):
            try:
                base_tip = resolve_rev(repo, cand)
                break
            except RuntimeError:
                pass
        if not base_tip:
            raise SystemExit(f"cannot resolve base branch {bname}; pass --base")
    base = git(repo, "merge-base", base_tip, head).strip()

    repo_name = os.path.basename(repo)
    if pr and pr.get("url"):
        m = re.search(r"github\.com/([^/]+/[^/]+)/pull", pr["url"])
        if m:
            repo_name = m.group(1)
    slug = f"pr-{args.pr}" if args.pr else f"{head[:9]}"
    out = os.path.abspath(args.out or os.path.join(os.path.expanduser("~"), "pr-goal-maps",
                                                    f"{os.path.basename(repo)}-{slug}"))
    for d in ("diffs", "sources/docs", "sources/prior", "sources/issues"):
        os.makedirs(os.path.join(out, d), exist_ok=True)

    # ---- commits
    fmt = "%H%x1f%an%x1f%aI%x1f%s%x1f%b%x1e"
    raw = git(repo, "log", "--reverse", "--no-merges", f"--format={fmt}", f"{base}..{head}")
    commits = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, an, date, subj, body = (rec.split("\x1f") + [""] * 5)[:5]
        commits.append({"sha": sha[:9], "full": sha, "author": an, "date": date,
                        "subject": subj, "body": body.strip(), "changeSets": []})
    range_shas = {c["full"] for c in commits}
    short_of = {c["full"]: c["sha"] for c in commits}
    merges = git(repo, "rev-list", "--merges", "--count", f"{base}..{head}").strip()

    # commits whose changes are already on the base branch (rebased, cherry-picked or squash-merged
    # work riding along): patch-equivalent per git cherry, or reverse-applicable to the base tip
    cherry = git(repo, "cherry", base_tip, head, base, check=False)
    already = [l[2:11] for l in cherry.split("\n") if l.startswith("- ")]
    # Work already on the base branch (squash-merged or rebased elsewhere) makes the range too wide.
    # Signal: files changed in base..head that are already identical between the base tip and head.
    same_on_base = set()
    if base_tip != base:
        range_files = set(git(repo, "diff", "--name-only", base, head).split("\n")) - {""}
        still_diff = set(git(repo, "diff", "--name-only", base_tip, head).split("\n")) - {""}
        same_on_base = range_files - still_diff
        if same_on_base:
            for c in commits:
                touched = set(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", c["full"]).split("\n")) - {""}
                if touched and touched <= same_on_base and c["sha"] not in already:
                    already.append(c["sha"])

    # ---- files + hunks
    numstat = git(repo, "diff", "--numstat", "-M", base, head)
    stats = {}
    for l in numstat.strip().split("\n"):
        if not l:
            continue
        a, d, p = l.split("\t", 2)
        if " => " in p:
            p = re.sub(r"\{([^{}]*) => ([^{}]*)\}", r"\2", p)
            p = p.split(" => ")[-1]
            p = p.replace("//", "/")
        stats[p] = (0 if a == "-" else int(a), 0 if d == "-" else int(d))
    diff_text = git(repo, "diff", "--no-color", "--no-ext-diff", "-U3", "-M", base, head)
    parsed = parse_diff(diff_text)

    files, hunks = [], []
    hid = 0
    for i, f in enumerate(parsed, 1):
        path = f["new"] or f["old"]
        fid = f"f{i}"
        kind = classify(path)
        add, dele = stats.get(path, (0, 0))
        frec = {"id": fid, "path": path, "oldPath": f["old"] if f["status"] == "R" else None,
                "name": short_name(path), "group": group_of(path), "kind": kind, "status": f["status"],
                "add": add, "del": dele, "binary": f["binary"], "hunks": []}
        is_md = path.lower().endswith((".md", ".mdx"))
        file_chunks = []
        head_lines = None
        for h in f["hunks"]:
            for label, rows in split_hunk(h, is_md):
                if not label and f["status"] != "D" and not f["binary"]:
                    if head_lines is None:
                        head_lines = show_file(repo, head, path).split("\n")
                    first = next((n for k, _, n in rows if k in "+ " and n), None)
                    firstc = next((n for k, _, n in rows if k == "+" and n), first)
                    label = enclosing_label(head_lines, firstc or 1, is_md) or h["context"]
                chunks_label = label or h["context"]
                file_chunks.append((chunks_label, rows))
        added_lines = [n for _, rows in file_chunks for k, _, n in rows if k == "+"]
        blame = {}
        if f["status"] != "D" and added_lines and not f["binary"] and kind != "generated":
            try:
                blame = blame_commits(repo, head, path, added_lines, range_shas)
            except RuntimeError as e:
                warn(str(e))
        dtext = [f"# {path}  (+{add} −{dele}, {kind})\n"]
        for label, rows in file_chunks:
            hid += 1
            hk = f"H{hid}"
            a = sum(1 for r in rows if r[0] == "+")
            d = sum(1 for r in rows if r[0] == "-")
            news = [n for _, _, n in rows if n]
            cs = OrderedDict()
            for k, _, n in rows:
                if k == "+" and n in blame:
                    cs[short_of[blame[n]]] = 1
            text = [f"{k}{t}" if k != "\\" else t for k, t, _ in rows]
            hunks.append({"id": hk, "file": fid, "header": label, "newStart": news[0] if news else None,
                          "newEnd": news[-1] if news else None, "add": a, "del": d,
                          "commits": list(cs), "role": "", "note": "",
                          "text": text[:MAX_HUNK_TEXT], "truncated": len(text) > MAX_HUNK_TEXT})
            frec["hunks"].append(hk)
            dtext.append(f"\n### {hk} · {label}  (+{a} −{d}; lines {news[0] if news else '-'}–{news[-1] if news else '-'}; commits: {', '.join(cs) or 'n/a'})\n")
            dtext.extend(text)
        if f["binary"]:
            dtext.append("(binary file)")
        with open(os.path.join(out, "diffs", f"{fid}.diff"), "w") as fh:
            fh.write("\n".join(dtext) + "\n")
        files.append(frec)

    # ---- deterministic pre-passes (see autonotes.py)
    files_by_id = {f["id"]: f for f in files}
    hunks_by_id = {h["id"]: h for h in hunks}
    mech_groups = autonotes.apply_auto_notes(hunks, files_by_id)
    tests_idx = autonotes.test_index(hunks, files_by_id)
    undone = autonotes.undone_files(git, repo, base, head, commits, {f["path"] for f in files} |
                                    {f["oldPath"] for f in files if f.get("oldPath")})
    removed_defs = autonotes.stale_refs(repo, head, hunks)
    stale = [r for r in removed_defs if r["refCount"]]
    clean_removals = [r["name"] for r in removed_defs if not r["refCount"]]
    with open(os.path.join(out, "diffs", "compact.diff"), "w") as fh:
        fh.write(autonotes.compact_diff(files, hunks_by_id))

    # ---- sources
    sources = []
    corpus = []   # text used to find ticket keys and links
    if pr:
        body = pr.get("body") or ""
        with open(os.path.join(out, "sources", "pr.md"), "w") as fh:
            fh.write(f"# {pr.get('title','')}\n\n{body}\n")
        sources.append({"id": "S-pr", "kind": "Pull request", "title": f"#{pr.get('number')}",
                        "sub": pr.get("title", ""), "inPR": True, "url": pr.get("url"),
                        "path": "sources/pr.md", "desc": "PR title and description."})
        corpus.append(pr.get("title", "")); corpus.append(body); corpus.append(pr.get("headRefName") or "")
    for c in commits:
        corpus.append(c["subject"]); corpus.append(c["body"])
    with open(os.path.join(out, "sources", "commits.md"), "w") as fh:
        for c in commits:
            fh.write(f"## {c['sha']} {c['date']} {c['author']}\n{c['subject']}\n\n{c['body']}\n\n")

    # GitHub issues (closing refs + #refs in body)
    issue_nums = OrderedDict()
    if pr:
        for ref in pr.get("closingIssuesReferences") or []:
            issue_nums[ref.get("number")] = "closes"
        for m in GH_ISSUE_RE.finditer(pr.get("body") or ""):
            if not m.group("repo"):
                issue_nums.setdefault(int(m.group("num")), "mentioned")
    if issue_nums and have("gh"):
        for n, rel in issue_nums.items():
            iss = gh_json(repo, ["issue", "view", str(n), "--json", "number,title,body,url,state,labels"])
            if not iss:
                continue  # may be a PR number
            p = f"sources/issues/gh-{n}.md"
            with open(os.path.join(out, p), "w") as fh:
                fh.write(f"# #{n} {iss.get('title','')}\n\n{iss.get('body') or ''}\n")
            sources.append({"id": f"S-gh{n}", "kind": "GitHub issue", "title": f"#{n}",
                            "sub": iss.get("title", ""), "inPR": False, "url": iss.get("url"),
                            "path": p, "desc": f"Linked issue ({rel})."})
            corpus.append(iss.get("body") or "")

    # docs changed in this PR
    doc_files = [f for f in files if f["kind"] == "doc" and f["status"] != "D"]
    for f in doc_files:
        text = show_file(repo, head, f["path"])
        p = "sources/docs/" + f["path"].replace("/", "__")
        with open(os.path.join(out, p), "w") as fh:
            fh.write(text)
        corpus.append(text)
        kind = "ADR" if re.search(r"(^|/)adrs?/", f["path"], re.I) else \
               "Spec" if re.search(r"(^|/)(specs?|rfcs?|design)/", f["path"], re.I) else "Doc"
        sources.append({"id": f"S-{f['id']}", "kind": kind, "title": os.path.basename(f["path"]),
                        "sub": f["path"], "inPR": True, "path": p, "file": f["id"],
                        "desc": f"Changed in this PR (+{f['add']} −{f['del']}).", "outline": outline(text, 40)})

    # ticket keys
    keys = OrderedDict()
    for t in corpus:
        for m in ticket_re.finditer(t or ""):
            k = m.group(0)
            if not re.match(r"^(UTF|ISO|RFC|SHA|HTTP|TLS|X|MD|AES|CVE|GPL|AGPL|LGPL|CC|BSD|MIT|SPDX|UTC|GMT|PEP|ES|ECMA)-", k):
                keys[k] = keys.get(k, 0) + 1
    ticket_keys = list(keys)

    # prior docs: linked from changed docs / PR body, or named for a ticket key
    changed_paths = {f["path"] for f in files}
    head_files = git(repo, "ls-tree", "-r", "--name-only", head).split("\n")
    prior = OrderedDict()
    for f in doc_files:
        text = show_file(repo, head, f["path"])
        for m in MD_LINK_RE.finditer(text):
            target = m.group(1)
            if re.match(r"^[a-z]+://", target):
                continue
            cand = os.path.normpath(os.path.join(os.path.dirname(f["path"]), target))
            if cand in changed_paths:
                continue
            if cand.lower().endswith(DOC_EXT) and cand in head_files:
                prior.setdefault(cand, f"linked from {f['path']}")
            elif any(h.startswith(cand + "/") for h in head_files):
                for h in head_files:
                    if h.startswith(cand + "/") and h.lower().endswith(DOC_EXT):
                        prior.setdefault(h, f"in folder linked from {f['path']}")
    for k in ticket_keys:
        for h in head_files:
            if k in h and h.lower().endswith(DOC_EXT) and h not in changed_paths:
                prior.setdefault(h, f"named for {k}")
    for i, (p, why) in enumerate(list(prior.items())[: args.max_prior_docs], 1):
        text = show_file(repo, head, p)
        lp = "sources/prior/" + p.replace("/", "__")
        with open(os.path.join(out, lp), "w") as fh:
            fh.write(text)
        last = git(repo, "log", "-1", "--format=%h %as %s", base, "--", p, check=False).strip()
        sources.append({"id": f"S-prior{i}", "kind": "Prior doc", "title": os.path.basename(p), "sub": p,
                        "inPR": False, "path": lp, "desc": f"Existed before this PR ({why}). Last change: {last}",
                        "outline": outline(text, 30)})
    if len(prior) > args.max_prior_docs:
        warn(f"{len(prior)} prior docs found; kept {args.max_prior_docs}")

    for k in ticket_keys:
        sources.append({"id": f"S-{k}", "kind": "Tracker issue", "title": k, "sub": "",
                        "inPR": False, "path": f"sources/issues/{k}.md", "pending": True,
                        "desc": "Fetch with the tracker's tool (e.g. Jira MCP) and save the text to this path."})

    # ---- warnings about scope
    notes = []
    if merges != "0":
        notes.append(f"{merges} merge commit(s) in range were skipped; their content still shows in the diff.")
    if already or same_on_base:
        last = already[-1] if already else None
        notes.append(f"{len(same_on_base)} of the range's changed files already match the base branch, and "
                     f"{len(already)} of {len(commits)} commits touch only such files"
                     f"{' (' + ', '.join(already[:6]) + (' …' if len(already) > 6 else '') + ')' if already else ''}. "
                     f"The range probably includes work merged separately (e.g. a squash-merged earlier PR). "
                     f"Re-run with --base set to the last commit before this PR's own work (for a stacked branch, "
                     f"the tip of the earlier PR's branch). A commit in that list can also be one whose change was "
                     f"undone later in this PR.")
    if pr and pr.get("changedFiles") and pr["changedFiles"] != len(files):
        notes.append(f"GitHub reports {pr['changedFiles']} changed files but the local range has {len(files)}; "
                     f"check the base (use --base).")
    if pr and not (pr.get("body") or "").strip():
        notes.append("PR description is empty.")
    if not pr:
        notes.append("Range mode: no PR description was read. If a PR exists, re-run with --pr N or --pr-json FILE.")

    authors = list(OrderedDict((c["author"], 1) for c in commits))
    meta = {
        "repo": repo_name, "number": pr.get("number") if pr else None,
        "title": (pr or {}).get("title") or args.title or (
            args.head if args.head and not re.fullmatch(r"[0-9a-f]{7,40}|HEAD|FETCH_HEAD", args.head)
            else (commits[-1]["subject"] if commits else head[:9])),
        "url": (pr or {}).get("url"), "base": base[:9], "head": head[:9],
        "baseRef": (pr or {}).get("baseRefName") or args.base, "headRef": (pr or {}).get("headRefName") or args.head,
        "state": (pr or {}).get("state"), "authors": authors,
        "additions": sum(f["add"] for f in files), "deletions": sum(f["del"] for f in files),
        "fileCount": len(files), "commitCount": len(commits),
        "dateRange": [commits[0]["date"], commits[-1]["date"]] if commits else None,
        "summary": "",
    }
    evidence = {"version": 1, "pr": meta, "ticketKeys": ticket_keys, "notes": notes, "alreadyInBase": already,
                "commits": commits, "files": files, "hunks": hunks, "sources": sources,
                "mechanicalGroups": mech_groups, "tests": tests_idx, "undoneInRange": undone, "staleRefs": stale,
                "cleanRemovals": clean_removals}
    with open(os.path.join(out, "evidence.json"), "w") as fh:
        json.dump(evidence, fh, indent=1)

    # ---- skeleton for the agent
    skel_hunks = [{k: h[k] for k in ("id", "file", "header", "add", "del", "commits", "role", "note", "auto")
                   if k in h} for h in hunks]
    skeleton = {
        "version": 1, "pr": meta, "notes": notes,
        "sources": [{k: v for k, v in s.items() if k not in ("outline",)} for s in sources],
        "goals": [], "requirements": {}, "changeSets": [], "decisions": [],
        "files": [{k: f[k] for k in ("id", "path", "name", "group", "kind", "status", "add", "del")} for f in files],
        "hunks": skel_hunks,
        "commits": [{k: c[k] for k in ("sha", "date", "author", "subject")} for c in commits],
    }
    tl = os.path.join(out, "throughline.json")
    if os.path.exists(tl):
        warn("throughline.json exists; wrote skeleton to throughline.skeleton.json instead")
        tl = os.path.join(out, "throughline.skeleton.json")
    with open(tl, "w") as fh:
        json.dump(skeleton, fh, indent=1)

    # ---- digest
    by_kind = defaultdict(lambda: [0, 0, 0])
    for f in files:
        by_kind[f["kind"]][0] += 1; by_kind[f["kind"]][1] += f["add"]; by_kind[f["kind"]][2] += f["del"]
    L = []
    L.append(f"# Evidence: {meta['title']}")
    L.append("")
    L.append(f"- repo: {repo_name}  range: {base[:9]}..{head[:9]}  ({meta['baseRef']} → {meta['headRef']})")
    if meta["url"]:
        L.append(f"- PR: {meta['url']}  state: {meta['state']}")
    L.append(f"- {len(files)} files, +{meta['additions']} −{meta['deletions']}, {len(commits)} commits, {len(hunks)} hunks")
    L.append("- by kind: " + ", ".join(f"{k} {v[0]} files (+{v[1]} −{v[2]})" for k, v in sorted(by_kind.items())))
    L.append(f"- authors: {', '.join(authors)}")
    if ticket_keys:
        L.append(f"- tracker keys found: {', '.join(ticket_keys)}  → fetch each and save to sources/issues/<KEY>.md")
    for n in notes:
        L.append(f"- NOTE: {n}")
    L.append("")
    L.append("## Sources")
    for s in sources:
        L.append(f"- {s['id']} · {s['kind']} · {s['title']} · {s.get('path','')}{'  (PENDING: fetch it)' if s.get('pending') else ''}")
        for o in (s.get("outline") or [])[:25]:
            L.append(f"    {o}")
    L.append("")
    L.append("## Commits (oldest first)")
    for c in commits:
        L.append(f"- {c['sha']} {c['date'][:16]} {c['author']}: {c['subject']}")
    if undone:
        L.append("")
        L.append("## Changed within the range, no net change in the final diff (pass E: undone or reverted work)")
        for r in undone:
            L.append(f"- {r['path']}: {r['note']}")
    auto_n = sum(1 for h in hunks if h.get("auto"))
    L.append("")
    L.append(f"## Auto-noted hunks ({auto_n} of {len(hunks)}; role and note pre-filled, check and override if wrong)")
    imp = [h["id"] for h in hunks if h.get("auto") == "import"]
    if imp:
        L.append(f"- import-only: {', '.join(imp)}")
    for g in mech_groups:
        L.append(f"- {g['id']} · {len(g['hunks'])} hunks in {len(g['files'])} files · at {', '.join(g['signature'][:4])}: "
                 f"{', '.join(g['hunks'])}")
        L.append(f"    e.g. - {g['example'][0]}")
        L.append(f"         + {g['example'][1]}")
    if tests_idx:
        L.append("")
        L.append("## Test functions touched (names usable in changeSets[].tests)")
        by_change = defaultdict(list)
        for t in tests_idx:
            by_change[t["change"].split(" ")[0]].append(t)
        for ch in ("added", "renamed", "edited", "removed"):
            for t in by_change.get(ch, []):
                L.append(f"- {t['hunk']} {t['change']}: {t['name']}")
    if stale or clean_removals:
        L.append("")
        L.append("## Definitions the diff removes (git grep -w at head)")
        if clean_removals:
            L.append(f"- no remaining references: {', '.join(clean_removals)}")
        for r in stale:
            L.append(f"- {r['name']} (removed in {r['removedIn']}): {r['refCount']} refs, e.g. {'; '.join(r['stillReferenced'][:3])}")
    L.append("")
    L.append("## Files and hunks")
    L.append("Read diffs/compact.diff first (changed lines only, auto-noted hunks collapsed); open diffs/<file>.diff for context.")
    for f in files:
        L.append(f"- {f['id']} [{f['kind']}] {f['path']} (+{f['add']} −{f['del']}) {f['status']} → diffs/{f['id']}.diff")
        for hk in f["hunks"]:
            h = hunks_by_id[hk]
            L.append(f"    {hk} +{h['add']} −{h['del']} {h['header'][:90]}  [{', '.join(h['commits'])}]"
                     f"{'  (auto ' + h['auto'] + ')' if h.get('auto') else ''}")
    with open(os.path.join(out, "evidence.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")

    print(json.dumps({"out": out, "files": len(files), "hunks": len(hunks), "commits": len(commits),
                      "sources": len(sources), "ticketKeys": ticket_keys, "notes": notes,
                      "autoNotedHunks": sum(1 for h in hunks if h.get("auto")),
                      "mechanicalGroups": len(mech_groups), "undoneFiles": len(undone),
                      "staleRefs": len(stale)}, indent=1))


if __name__ == "__main__":
    main()
