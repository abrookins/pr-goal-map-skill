#!/usr/bin/env python3
"""Merge hunk notes into throughline.json.

Usage: apply_notes.py OUT_DIR NOTES.json [NOTES2.json ...]

Each NOTES file maps hunk ids to {"role": ..., "note": ..., "reqs": [...]}:
  {"H12": {"role": "mechanism", "note": "Adds decodeGatewayToolExposureMode …", "reqs": ["R16"]}}
"reqs" (candidate requirements) is kept on the hunk as "candidateReqs" to help clustering.
Several files can be merged at once, e.g. one per subagent batch.
"""
import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out = sys.argv[1]
    path = os.path.join(out, "throughline.json")
    tl = json.load(open(path))
    by_id = {h["id"]: h for h in tl["hunks"]}
    applied, unknown = 0, []
    for nf in sys.argv[2:]:
        notes = json.load(open(nf))
        for hid, v in notes.items():
            h = by_id.get(hid)
            if not h:
                unknown.append(hid)
                continue
            if isinstance(v, str):
                v = {"note": v}
            for k in ("role", "note"):
                if v.get(k):
                    h[k] = v[k]
            if v.get("reqs"):
                h["candidateReqs"] = v["reqs"]
            applied += 1
    json.dump(tl, open(path, "w"), indent=1)
    missing = [h["id"] for h in tl["hunks"] if not h.get("note")]
    print(json.dumps({"applied": applied, "unknownIds": unknown, "hunksWithoutNote": missing[:50],
                      "hunksWithoutNoteCount": len(missing)}, indent=1))


if __name__ == "__main__":
    main()
