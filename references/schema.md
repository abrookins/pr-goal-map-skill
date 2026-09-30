# throughline.json format

`collect.py` writes a skeleton with `pr`, `notes`, `sources`, `files`, `hunks` and `commits` filled in. You fill in `pr.summary`, `goals`, `requirements`, `changeSets`, `decisions`, and each hunk's `role` and `note`. Leave the collector's fields alone except where noted. Write your parts as small JSON files and merge them with `scripts/merge.py` (keys: `summary`, `title`, `goals`, `requirements`, `changeSets`, `decisions`, `notes`, `sources`); hunk notes go through `scripts/apply_notes.py`.

## Top level

| Field | Who writes it | Meaning |
| --- | --- | --- |
| `pr` | collector (+ you: `summary`) | Title, repo, range, counts. `summary` is 2–4 sentences shown at the top: what the PR achieves and the headline gaps. |
| `notes` | collector (+ you) | Caveats shown under the summary, e.g. "PR description not available". |
| `sources` | collector (+ you) | Every text that states intent. You may add sources (see below). |
| `goals` | you | 3–6 goals, each grouping requirements. |
| `requirements` | you | Map of `R1`… to requirement objects. |
| `changeSets` | you | Ordered list: array order is the reading order. |
| `decisions` | you | Turning points found in the commit history. |
| `files`, `commits` | collector | Don't edit. |
| `hunks` | collector (+ you: `role`, `note`) | Symbol-level chunks of the diff. |

## Source

```json
{"id": "S-RED-216616", "kind": "Tracker issue", "title": "RED-216616", "sub": "Spike: first tool search slice",
 "inPR": false, "url": "https://…", "path": "sources/issues/RED-216616.md", "desc": "…", "pending": false}
```

- `path` is relative to the output directory and must contain the text you quote; the validator checks quotes against it.
- Tracker keys found in the PR get a placeholder with `"pending": true`. After fetching the issue, write its text (title, description, acceptance criteria, relevant comments) to `path` and set `pending` to false (or delete the placeholder if the key was noise).
- To add a source (a Confluence page, a Slack decision, a doc outside the repo), save its text under `sources/` and add an entry. Ids start with `S-`.
- Commits are cited as `"commit:<sha>"` without a source entry.

## Goal

```json
{"id": "G1", "title": "Keep tool schemas out of model context",
 "summary": "An agent finds the right tool without every schema in its context window.",
 "requirements": ["R1", "R2", "R3"]}
```

Every requirement belongs to exactly one goal. Titles are outcomes, not components.

## Requirement

```json
"R4": {
  "text": "Return compact summaries; load the full schema only after a tool is selected",
  "status": "diverged",
  "cites": [{"source": "S-RED-216616", "quote": "Load the complete schema only after a tool is selected.", "anchor": "In scope"}],
  "note": "Search returns complete MCP Tool definitions instead; changed on purpose in 7b07f21.",
  "noteCites": [{"source": "S-f2", "quote": "Compact signatures remain a measured later optimization.", "anchor": "Decision"},
                {"source": "commit:7b07f2109", "quote": "docs: return standard tools from progressive search"}]
}
```

- `status`: `covered` (implemented, ideally proven by a test), `partial`, `deferred` (explicitly left for later by a source), `diverged` (built differently on purpose, with a cited reason), `missing` (asked for, not done, no stated reason; the most important finding).
- `cites`: where the requirement is stated. Quotes are verbatim substrings of the source; use `…` to join two fragments. `anchor` is the section heading or field name.
- `note` is required for every status except `covered`; `noteCites` should back it.

## Change set

```json
{
  "id": "CS4",
  "title": "search_tools: lexical ranking and bounded results",
  "short": "Scores name and description terms, orders deterministically, caps count and bytes.",
  "why": "Two to five sentences: how this set serves its requirements, what to look at, anything surprising.",
  "quote": {"source": "S-f1", "quote": "…", "anchor": "First release"},
  "serves": [{"req": "R2", "strength": "primary"}, {"req": "R14", "strength": "supporting"}],
  "hunks": ["H31", "H32", "H40"],
  "tests": [{"kind": "unit", "name": "TestSearchToolsRanksNameMatchesAboveDescriptionMatches", "proves": ["R2"]}],
  "excerpts": [{"header": "mcp/progressive.go · rankTools", "lines": [["+", "sort.SliceStable(matches, …)"], ["c", "// comment line"]]}],
  "unexplained": false
}
```

- `hunks`: each hunk belongs to one change set. Test hunks go in the change set whose behaviour they test, not in a "tests" bucket; the viewer draws tests as proof of requirements.
- `tests[].name` must appear in the diff; `kind` is `unit`, `integration`, `e2e`, `contract`, `manual` or `other`.
- `excerpts` (optional): the 3–10 lines a reviewer should read first, copied from the diff. Line kinds: `+`, `-`, space, `c` (comment/elided).
- `unexplained: true` marks changes that serve no stated goal. Keep them; they are a finding.

## Hunk (your fields)

- `role`: `mechanism` (the logic that achieves the goal), `wiring` (plumbing that connects it), `contract` (types, schemas, interfaces), `test`, `doc`, `config`, `refactor`, `rename`, `incidental`, `generated`.
- `note`: one line on what the hunk does, in terms of the goal where possible. "Adds decodeGatewayToolExposureMode: empty/full → full, search → search, else invalid" beats "adds a function".

## Decision

```json
{"commit": "7b07f2109", "title": "Search returns standard MCP Tool definitions",
 "text": "Spec and ADR rewritten before the code followed; this is why R4 diverges.",
 "affects": ["R4", "CS4"]}
```

Worth recording: a doc change that redirected later code, a rename that touches many files, a change undone before merge, scope pulled in from another ticket.
