#!/usr/bin/env python3
"""Merge a partial analysis into throughline.json without touching collector fields.

Usage: merge.py OUT_DIR PART.json [PART2.json ...]

A part may contain any of:
  "summary": str                   -> pr.summary
  "title": str                     -> pr.title (set it in range mode, where the collector guesses)
  "goals": [...]                   -> replaces goals
  "requirements": {id: {...}}      -> merged by id (null deletes)
  "changeSets": [...]              -> replaces changeSets (array order = reading order)
  "decisions": [...]               -> replaces decisions
  "notes": [str]                   -> appended to notes
  "sources": [{id, ...}]           -> upserted by id (null fields ignored); {"id": ..., "remove": true} deletes
Writing parts per pass keeps each edit small and leaves hunks, files and commits intact.
"""
import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    path = os.path.join(sys.argv[1], "throughline.json")
    tl = json.load(open(path))
    changed = []
    for part_path in sys.argv[2:]:
        part = json.load(open(part_path))
        for k in ("summary", "title"):
            if k in part:
                tl.setdefault("pr", {})[k] = part[k]; changed.append(k)
        for k in ("goals", "changeSets", "decisions"):
            if k in part:
                tl[k] = part[k]; changed.append(k)
        if "requirements" in part:
            reqs = tl.setdefault("requirements", {})
            for rid, v in part["requirements"].items():
                if v is None:
                    reqs.pop(rid, None)
                else:
                    reqs[rid] = v
            changed.append(f"requirements({len(part['requirements'])})")
        if "notes" in part:
            tl.setdefault("notes", []).extend(n for n in part["notes"] if n not in tl["notes"]); changed.append("notes")
        if "sources" in part:
            by_id = {s["id"]: s for s in tl.setdefault("sources", [])}
            for s in part["sources"]:
                if s.get("remove"):
                    tl["sources"] = [x for x in tl["sources"] if x["id"] != s["id"]]
                    by_id.pop(s["id"], None)
                    continue
                if s["id"] in by_id:
                    by_id[s["id"]].update({k: v for k, v in s.items() if v is not None})
                else:
                    tl["sources"].append(s)
            changed.append("sources")
    json.dump(tl, open(path, "w"), indent=1)
    print(json.dumps({"updated": changed}, indent=1))


if __name__ == "__main__":
    main()
