#!/usr/bin/env python3
"""Validate a goal map (throughline.json) against its evidence bundle.

Usage: validate.py OUT_DIR [--strict]

Checks structure and cross-references, then checks the claims:
  * every quoted citation must appear in the source text it names (fragments split on "…")
  * every test name must appear in the PR's diff
  * statuses must be consistent with what implements them
Exit code 1 when there are errors (or warnings with --strict).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

STATUSES = {"covered", "partial", "deferred", "diverged", "missing"}
ROLES = {"mechanism", "wiring", "contract", "test", "doc", "config", "refactor", "rename", "incidental", "generated", ""}
TEST_KINDS = {"unit", "integration", "e2e", "contract", "manual", "other"}


def norm(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-").replace("→", "->")
    s = re.sub(r"[`*_>|]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    out = a.out if os.path.isdir(a.out) else os.path.dirname(os.path.abspath(a.out))
    tl_path = a.out if a.out.endswith(".json") else os.path.join(out, "throughline.json")
    tl = json.load(open(tl_path))
    ev_path = os.path.join(out, "evidence.json")
    ev = json.load(open(ev_path)) if os.path.exists(ev_path) else {"hunks": [], "commits": []}

    errors, warnings = [], []
    E = errors.append
    W = warnings.append

    sources = {s["id"]: s for s in tl.get("sources", [])}
    commits = {c["sha"]: c for c in (ev.get("commits") or tl.get("commits") or [])}
    commit_prefix = {}
    for sha in commits:
        commit_prefix[sha[:7]] = sha
    hunks = {h["id"]: h for h in tl.get("hunks", [])}
    ev_hunks = {h["id"]: h for h in ev.get("hunks", [])}
    reqs = tl.get("requirements", {})
    goals = tl.get("goals", [])
    sets = tl.get("changeSets", [])

    def commit_of(ref):
        sha = ref.split(":", 1)[1] if ref.startswith("commit:") else ref
        for full in commits:
            if full.startswith(sha[:7]) or sha.startswith(full[:7]):
                return commits[full]
        return None

    text_cache = {}

    def source_text(src_id):
        if src_id in text_cache:
            return text_cache[src_id]
        t = None
        if src_id.startswith("commit:"):
            c = commit_of(src_id)
            t = (c["subject"] + "\n" + c.get("body", "")) if c else None
        else:
            s = sources.get(src_id)
            if s and s.get("path"):
                p = os.path.join(out, s["path"])
                if os.path.exists(p):
                    t = open(p, errors="replace").read()
        text_cache[src_id] = norm(t) if t is not None else None
        return text_cache[src_id]

    def check_cite(where, c):
        src = c.get("source", "")
        if src.startswith("commit:"):
            if not commit_of(src):
                E(f"{where}: cites unknown commit {src}")
                return
        elif src not in sources:
            E(f"{where}: cites unknown source {src!r}")
            return
        q = c.get("quote", "")
        if not q.strip():
            E(f"{where}: empty quote from {src}")
            return
        text = source_text(src)
        if text is None:
            s = sources.get(src, {})
            if s.get("pending"):
                E(f"{where}: cites {src}, which was never fetched (save it to {s.get('path')})")
            else:
                W(f"{where}: cannot verify quote, no local text for {src}")
            return
        frags = [f for f in re.split(r"\s*(?:…|\.\.\.)\s*", q) if len(norm(f)) >= 8]
        for f in frags or [q]:
            if norm(f).rstrip(".") not in text:
                W(f"{where}: quote not found verbatim in {src}: \"{f[:80]}\"")
                break

    # ---- goals / requirements
    if not goals:
        E("no goals")
    seen_req = {}
    gids = set()
    for g in goals:
        if g["id"] in gids:
            E(f"duplicate goal id {g['id']}")
        gids.add(g["id"])
        if not g.get("title"):
            E(f"{g['id']}: missing title")
        for r in g.get("requirements", []):
            if r not in reqs:
                E(f"{g['id']}: unknown requirement {r}")
            elif r in seen_req:
                E(f"{r} is listed under both {seen_req[r]} and {g['id']}")
            seen_req[r] = g["id"]
    for rid, r in reqs.items():
        if rid not in seen_req:
            E(f"{rid} belongs to no goal")
        st = r.get("status")
        if st not in STATUSES:
            E(f"{rid}: status {st!r} not one of {sorted(STATUSES)}")
        if not r.get("text"):
            E(f"{rid}: missing text")
        if not r.get("cites"):
            E(f"{rid}: no citations (every requirement needs a source)")
        for i, c in enumerate(r.get("cites", [])):
            check_cite(f"{rid} cite {i + 1}", c)
        if st in ("partial", "deferred", "diverged", "missing") and not r.get("note"):
            E(f"{rid}: status {st} needs a note explaining it")
        for i, c in enumerate(r.get("noteCites", []) or []):
            check_cite(f"{rid} note cite {i + 1}", c)
        if st in ("deferred", "diverged") and not r.get("noteCites"):
            W(f"{rid}: {st} without noteCites; cite the doc or commit that says so")

    # ---- change sets
    set_ids = set()
    assigned = {}
    primary = {}
    proven = {}
    for cs in sets:
        sid = cs.get("id")
        if sid in set_ids:
            E(f"duplicate change set id {sid}")
        set_ids.add(sid)
        for k in ("title", "why"):
            if not cs.get(k):
                E(f"{sid}: missing {k}")
        if not cs.get("hunks"):
            E(f"{sid}: no hunks")
        for h in cs.get("hunks", []):
            if h not in hunks:
                E(f"{sid}: unknown hunk {h}")
            elif h in assigned:
                W(f"{h} is in both {assigned[h]} and {sid}; pick one (split the hunk's meaning in the note instead)")
            assigned.setdefault(h, sid)
        if not cs.get("unexplained") and not cs.get("serves"):
            E(f"{sid}: serves no requirement; mark it unexplained: true if that is the finding")
        for s in cs.get("serves", []):
            r = s.get("req")
            if r not in reqs:
                E(f"{sid}: serves unknown requirement {r}")
                continue
            if s.get("strength", "primary") not in ("primary", "supporting"):
                E(f"{sid}: strength must be primary or supporting")
            if s.get("strength", "primary") == "primary":
                primary.setdefault(r, []).append(sid)
        if cs.get("quote"):
            check_cite(f"{sid} quote", cs["quote"])
        for t in cs.get("tests", []):
            if t.get("kind") not in TEST_KINDS:
                W(f"{sid}: test kind {t.get('kind')!r} not in {sorted(TEST_KINDS)}")
            for r in t.get("proves", []):
                if r not in reqs:
                    E(f"{sid}: test {t.get('name')} proves unknown requirement {r}")
                proven.setdefault(r, []).append(t.get("name"))
            name = t.get("name", "")
            tok = max(re.findall(r"[A-Za-z_][\w]{5,}", name) or [""], key=len)
            # a test edited (not added) by the PR may appear only in a hunk's header
            if tok and ev_hunks and not any(tok in "\n".join(h.get("text", [])) or tok in (h.get("header") or "")
                                            for h in ev_hunks.values()):
                W(f"{sid}: test {name!r} not found in the diff")
            if not t.get("proves"):
                W(f"{sid}: test {name!r} proves no requirement")

    unassigned = [h for h in hunks if h not in assigned]
    if unassigned:
        W(f"{len(unassigned)} hunks are in no change set: {', '.join(unassigned[:20])}{' …' if len(unassigned) > 20 else ''}")
    no_note = [h for h, v in hunks.items() if not v.get("note")]
    if no_note and len(no_note) > len(hunks) * 0.2:
        W(f"{len(no_note)} of {len(hunks)} hunks have no note (pass B incomplete?)")
    bad_roles = [h for h, v in hunks.items() if v.get("role") not in ROLES]
    if bad_roles:
        W(f"unknown hunk roles on {', '.join(bad_roles[:10])}; use one of {sorted(r for r in ROLES if r)}")

    for rid, r in reqs.items():
        st = r.get("status")
        if st in ("covered", "partial", "diverged") and rid not in primary:
            E(f"{rid} is {st} but no change set serves it as primary")
        if st in ("deferred", "missing") and rid in primary:
            W(f"{rid} is {st} but {', '.join(primary[rid])} serves it as primary")
        if st == "covered" and rid not in proven:
            doc_only = all(all(hunks.get(h, {}).get("role") == "doc" for h in cs.get("hunks", []))
                           for cs in sets if cs.get("id") in primary.get(rid, []))
            if not doc_only:
                W(f"{rid} is covered but no test proves it; name the test, or explain in the note why none applies")

    for d in tl.get("decisions", []):
        if not commit_of(d.get("commit", "")):
            E(f"decision {d.get('title')!r}: unknown commit {d.get('commit')}")
        for x in d.get("affects", []):
            if x not in reqs and x not in set_ids:
                E(f"decision {d.get('title')!r}: affects unknown id {x}")

    if not (tl.get("pr") or {}).get("summary"):
        W("pr.summary is empty; write the two-to-four sentence verdict shown at the top")

    counts = {s: sum(1 for r in reqs.values() if r.get("status") == s) for s in STATUSES}
    print(json.dumps({
        "goals": len(goals), "requirements": len(reqs), "status": counts,
        "changeSets": len(sets), "hunks": len(hunks), "unassignedHunks": len(unassigned),
        "tests": sum(len(cs.get("tests", [])) for cs in sets), "decisions": len(tl.get("decisions", [])),
        "errors": len(errors), "warnings": len(warnings)}, indent=1))
    for e in errors:
        print("ERROR  " + e)
    for w in warnings:
        print("warn   " + w)
    sys.exit(1 if errors or (a.strict and warnings) else 0)


if __name__ == "__main__":
    main()
