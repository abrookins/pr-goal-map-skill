#!/usr/bin/env python3
"""Find a phrase in the collected sources and print a citation you can paste.

Usage: find_quote.py OUT_DIR "phrase" [--source S-id]

Matching ignores case, runs of whitespace and markdown emphasis/code marks, so a phrase copied
from a rendered page still finds the raw text. Output is one JSON object per match with the
source id, the exact text to quote (copied from the file), and the nearest heading as the anchor.
"""
import json
import os
import re
import sys


def strip_marks(s):
    return re.sub(r"[*_`]", "", s)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--source")]
    only = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--source=")), None)
    if "--source" in sys.argv:
        i = sys.argv.index("--source")
        only = sys.argv[i + 1]
        args = [a for a in args if a != only]
    if len(args) < 2:
        raise SystemExit(__doc__)
    out, phrase = args[0], args[1]
    tl = json.load(open(os.path.join(out, "throughline.json")))
    want = re.sub(r"\s+", " ", strip_marks(phrase)).strip().lower()
    def norm(text):
        return re.sub(r"\s+", " ", strip_marks(text)).strip().lower()

    hits = 0
    for s in tl.get("sources", []):
        if only and s["id"] != only or not s.get("path"):
            continue
        p = os.path.join(out, s["path"])
        if not os.path.exists(p):
            continue
        lines = open(p, errors="replace").read().split("\n")
        headings, heading = [], ""
        for line in lines:
            if re.match(r"^#{1,6} ", line):
                heading = line.lstrip("#").strip()
            headings.append(heading)
        found = [(i, lines[i].strip()) for i in range(len(lines)) if want in norm(lines[i])]
        if not found:
            # a phrase wrapped across a line break: report the pair of lines
            found = [(i, lines[i].strip() + " " + lines[i + 1].strip()) for i in range(len(lines) - 1)
                     if want in norm(lines[i] + " " + lines[i + 1])]
        for i, text in found:
            print(json.dumps({"source": s["id"], "line": i + 1, "anchor": headings[i], "text": text[:400],
                              "note": "quote a verbatim substring of text"}))
            hits += 1
    if not hits:
        print(json.dumps({"match": None, "hint": "try a shorter fragment; the source may have links or code marks inside the phrase"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
