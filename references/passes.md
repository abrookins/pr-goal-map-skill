# Pass guidance

Contents: §A goals and requirements · §B hunk notes · §C change sets · §D trace and verify · §E decisions

## §A Goals and requirements

**Where intent lives, in order of precision:** specs and ADRs in the PR, then the tracker issue's acceptance criteria, then the PR description, then commit messages, then prior docs. The issue is usually the *ask*; the spec is usually the *answer*. When they disagree, both are requirements' sources, and the disagreement is a finding (diverged, partial or missing).

**Writing requirements**
- One checkable statement each: "Unknown exposure mode values fail closed with HTTP 503" rather than "Handle configuration".
- Merge the same requirement from several sources into one, with one cite per source. That's what lets the map show the issue, spec and ADR agreeing.
- Include what the issue asked for even when the PR doesn't do it. That's how *missing* and *deferred* surface. Don't add requirements nobody stated just to make the map look complete.
- Include documentation deliverables when a source asks for them ("ADR for the decision").
- 8–25 requirements is typical. Past 30, you are probably listing implementation details; fold them into their parent requirement.

**Goals** are 3–6 outcomes that group requirements, named as outcomes ("Existing Gateways keep working"), not components ("Repository changes").

**Quotes** must be copied character for character from the saved source text. Use `…` to join two fragments of one sentence. Keep them short (one sentence). `anchor` names the section or field ("Acceptance criteria", "Scenario 4") so a reader can find it.

## §B Hunk notes

- Start with `diffs/compact.diff`: changed lines only, one `## H12 · label` line per hunk with its commits. Open `diffs/<file>.diff` when you need the surrounding context.
- Hunks marked `[auto import]` or `[auto mechN]` already have a role and note. Import-only hunks need nothing. For a mechanical group, `evidence.md` shows one example; check that it really is the same edit everywhere (skim the group's hunks in the per-file diffs if the example doesn't make it obvious), and override any hunk that also does something else by giving it a note in `notes.json`. A group usually becomes its own change set (§C), unless its hunks are test edits that belong with the behaviour they test.
- Write notes only for hunks without one. `apply_notes.py` overwrites pre-filled notes you include, so leave auto-noted hunks out unless you're correcting them.
- The note says what changed *in terms of behaviour*: "Adds withInvocationMeta: deletes any Upstream-supplied key under the reserved prefix, then writes trace/span ids and the target tool". Not "adds a function".
- Roles: `mechanism`, `wiring`, `contract`, `test`, `doc`, `config`, `refactor`, `rename`, `incidental`, `generated`.
- For test hunks, the note names what is asserted. That makes pass D fast.
- For doc hunks (each heading section of a changed doc is its own hunk), the note says what the section decides or specifies.
- `reqs`: candidate requirement ids when obvious. Leave empty when unsure; pass C decides.

**Batching for big PRs:** group whole files so related code stays together (a file and its test in the same batch). Give each subagent: the requirement list (ids + text), its batch of `diffs/*.diff` paths, the role list, and the output format `{"H12": {"role": "...", "note": "...", "reqs": ["R3"]}}` written to `notes-<n>.json`.

## §C Change sets

A change set is what a reviewer would want to read in one sitting: one idea, its tests and its docs. Split when two groups serve different requirements or need a different reviewer mindset (a data model change vs. an API surface change).

**Reading order** (array order): intent documents first (spec/ADR sections, if substantial), then contracts and data model, then the core mechanism, then wiring and call sites, then edge cases and error handling, then end-to-end harness and fixtures, then operator docs, then unexplained changes last.

**Per set**
- `title`: what it does, in reviewer words.
- `short`: one line under the title.
- `why`: 2–5 sentences: how this set serves its requirements, what to scrutinise, anything surprising (a divergence, a subtle ordering, a rename that inflates the diff).
- `quote`: the single most relevant line from a source.
- `serves`: `primary` for requirements this set implements; `supporting` for ones it documents or enables.
- `tests`: the test functions in this set's hunks with the requirements each one proves. Use real names from the diff; the validator checks them.
- `excerpts`: for the 2–4 core sets, the 3–10 lines worth reading first.

A rename or mechanical refactor touching many files gets its own set so it doesn't blur the others. Generated files go in one set with a one-line why.

**Unexplained changes**: anything that serves no requirement, such as a drive-by fix, a stray config change or work from another PR riding along. Put them in a set with `unexplained: true` and say plainly why no goal fits. Don't stretch a goal to cover them.

## §D Trace and verify

For each requirement, decide the status from evidence:

| Status | Evidence required |
| --- | --- |
| covered | A primary change set implements it; for behaviour, a named test asserts it. For a docs deliverable, the doc hunk is enough. |
| partial | Part is implemented; the note says which part is not, citing a source when one scopes it. |
| deferred | Not implemented, and a source says it's for later (spec "Deferred work", issue "out of scope for this slice"). Cite it in `noteCites`. |
| diverged | Implemented differently from how a source asked, and another source or commit records the change of direction. Cite both. |
| missing | Asked for, not implemented, and nothing defers it. Say what you searched for. |

Verify rather than trust notes: grep the head revision for the behaviour; open the test and confirm the assertion matches the requirement, not just the name. If a test only exercises the happy path of a requirement about failure handling, the requirement is not proven; say so in the note.

## §E Decisions

Check `evidence.md` "Changed within the range, no net change in the final diff" first: those files were changed and then undone before the head, so the diff can't show them. `git show <sha> -- <path>` shows what was tried.

Read `sources/commits.md` in order, looking for:
- a doc or spec commit that is followed by code commits in a new direction (often explains a divergence);
- a rename touching many files (warn reviewers that the diff is inflated);
- a change later undone, e.g. a file changed by one commit and absent from the final diff (check with `git show --stat <sha>`);
- scope pulled in from another ticket or spec;
- fixes that respond to review ("fix: …" after a long gap).

Each decision cites its commit sha, says what changed and why it matters to the reviewer, and lists the requirements or change sets it affects.
