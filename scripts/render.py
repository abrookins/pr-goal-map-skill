#!/usr/bin/env python3
"""Render a goal map (throughline.json + evidence.json) into one self-contained HTML file.

Usage: render.py OUT_DIR [--output FILE] [--no-diff-text]

Writes OUT_DIR/goal-map.html by default. Run validate.py first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "viewer.html")
PALETTE = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8", "--c9"]
KIND_TAG = {"test": "test", "doc": "doc", "config": "config", "generated": "gen"}
KIND_ORDER = {"doc": 0, "code": 1, "config": 2, "test": 3, "generated": 4}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--output")
    ap.add_argument("--no-diff-text", action="store_true", help="omit hunk diff text (smaller file)")
    a = ap.parse_args()
    out = a.out if os.path.isdir(a.out) else os.path.dirname(os.path.abspath(a.out))
    tl = json.load(open(os.path.join(out, "throughline.json")))
    ev_path = os.path.join(out, "evidence.json")
    ev = json.load(open(ev_path)) if os.path.exists(ev_path) else {}
    ev_hunks = {h["id"]: h for h in ev.get("hunks", [])}

    files = tl["files"]
    fidx = {f["id"]: i for i, f in enumerate(files)}
    hunks = {h["id"]: h for h in tl["hunks"]}
    commits = tl.get("commits") or ev.get("commits") or []
    corder = {c["sha"]: i for i, c in enumerate(commits)}

    def full_hunk(hid):
        h = dict(hunks.get(hid) or {})
        e = ev_hunks.get(hid, {})
        for k in ("header", "add", "del", "commits", "file"):
            h.setdefault(k, e.get(k))
        h["text"] = [] if a.no_diff_text else e.get("text", [])
        h["truncated"] = e.get("truncated", False)
        return {k: h.get(k) for k in ("id", "file", "header", "add", "del", "role", "note", "commits", "text", "truncated")}

    sets_in = list(tl.get("changeSets", []))
    assigned = {h for cs in sets_in for h in cs.get("hunks", [])}
    leftover = [h for h in hunks if h not in assigned]
    if leftover:
        sets_in.append({"id": "CS-unassigned", "title": f"Unassigned hunks ({len(leftover)})",
                        "short": "Hunks no change set claimed. Review them or assign them.",
                        "why": "The analysis did not place these hunks in any change set.",
                        "hunks": leftover, "serves": [], "tests": [], "unexplained": True})

    sets = []
    ci = 0
    for cs in sets_in:
        hs = [full_hunk(h) for h in cs.get("hunks", []) if h in hunks]
        fw = {}
        for h in hs:
            fw[h["file"]] = fw.get(h["file"], 0) + (h["add"] or 0) + (h["del"] or 0)
        fids = sorted(fw, key=lambda f: fidx.get(f, 1e9))
        cset = sorted({c for h in hs for c in (h["commits"] or [])}, key=lambda c: corder.get(c, 1e9))
        if cs.get("unexplained"):
            color = "--cx"
        else:
            color = PALETTE[ci % len(PALETTE)]
            ci += 1
        code = []
        for ex in cs.get("excerpts", []) or []:
            lines = []
            for k, t in ex.get("lines", []):
                lines.append([{"+": "a", "-": "r", " ": "", "c": "c", "a": "a", "r": "r"}.get(k, ""), t])
            code.append({"h": ex.get("header", ""), "l": lines})
        q = cs.get("quote")
        sets.append({
            "id": cs["id"], "c": color, "title": cs.get("title", cs["id"]), "w": cs.get("short", ""),
            "why": cs.get("why", ""), "quote": q if q else None,
            "serves": [[s["req"], "p" if s.get("strength", "primary") == "primary" else "s"] for s in cs.get("serves", [])],
            "files": fids, "fw": {f: max(1, v) for f, v in fw.items()},
            "tests": [{"kind": t.get("kind", "unit"), "name": t.get("name", ""), "r": t.get("proves", [])} for t in cs.get("tests", [])],
            "commits": cset, "code": code, "unexplained": bool(cs.get("unexplained")), "hunks": hs,
        })

    set_of_hunk = {h["id"]: s["id"] for s in sets for h in s["hunks"]}
    commit_sets = {}
    for h in tl["hunks"]:
        for c in h.get("commits") or []:
            sid = set_of_hunk.get(h["id"])
            if sid:
                commit_sets.setdefault(c, [])
                if sid not in commit_sets[c]:
                    commit_sets[c].append(sid)

    cited = set()
    for r in tl.get("requirements", {}).values():
        for c in (r.get("cites") or []) + (r.get("noteCites") or []):
            cited.add(c.get("source"))
    for cs in sets_in:
        if cs.get("quote"):
            cited.add(cs["quote"].get("source"))
    sources = [{"id": s["id"], "kind": s.get("kind", ""), "title": s.get("title", ""), "sub": s.get("sub", ""),
                "inPR": bool(s.get("inPR")), "url": s.get("url"), "desc": s.get("desc", "")}
               for s in tl.get("sources", []) if s["id"] in cited]

    reqs = {}
    for rid, r in tl.get("requirements", {}).items():
        reqs[rid] = {"t": r.get("text", ""), "st": r.get("status", "missing"), "cites": r.get("cites", []),
                     "note": r.get("note"), "noteCites": r.get("noteCites", [])}

    data = {
        "meta": dict(tl.get("pr", {}), notes=tl.get("notes", []),
                     generatedAt=dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
        "sources": sources,
        "goals": [{"id": g["id"], "title": g.get("title", ""), "sum": g.get("summary", ""),
                   "reqs": g.get("requirements", [])} for g in tl.get("goals", [])],
        "reqs": reqs,
        "files": [{"g": f.get("group", ""), "id": f["id"], "p": f["path"], "n": f.get("name") or os.path.basename(f["path"]),
                   "a": f.get("add", 0), "d": f.get("del", 0), "t": KIND_TAG.get(f.get("kind"))}
                  for f in sorted((f for f in files if any(f["id"] in s["files"] for s in sets)),
                                  key=lambda f: (min(i for i, s in enumerate(sets) if f["id"] in s["files"]),
                                                 KIND_ORDER.get(f.get("kind"), 0), f["path"]))],
        "sets": sets,
        "commits": [[c["sha"], c["date"], c["author"], c["subject"], commit_sets.get(c["sha"], [])] for c in commits],
        "decisions": [{"c": d["commit"].replace("commit:", "")[:9], "t": d.get("title", ""), "x": d.get("text", ""),
                       "affects": d.get("affects", [])} for d in tl.get("decisions", [])],
    }
    # decisions may cite a 7-char sha; normalise to the commit list's 9-char form
    shas = [c["sha"] for c in commits]
    for d in data["decisions"]:
        m = [s for s in shas if s.startswith(d["c"][:7])]
        if m:
            d["c"] = m[0]

    tpl = open(TEMPLATE).read()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("__GOALMAP_DATA__", blob)
    dest = a.output or os.path.join(out, "goal-map.html")
    with open(dest, "w") as fh:
        fh.write(html)
    print(json.dumps({"html": dest, "bytes": len(html), "changeSets": len(sets),
                      "unassignedHunks": len(leftover)}, indent=1))


if __name__ == "__main__":
    main()
