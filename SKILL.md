---
name: pr-goal-map
description: Builds an interactive, goal-first review map of a pull request. It traces the goals stated in the linked issue, PR description, specs, ADRs and commit history down to the change sets, tests and files that implement them, and flags requirements that are missing, deferred or diverged, plus changes that serve no goal. Use when the user wants to understand, review or get oriented in a large or unfamiliar PR or branch, asks what a PR is for, how its changes relate to the issue or spec, whether it does what the ticket asked, or wants a PR visualized as a goal map (also "PR map", "throughline", "walk me through this PR"). Not for line-by-line bug hunting or style review.
metadata:
  author: Andrew Brookins
  version: 0.2.0
  category: code-review
---

# PR goal map

Turn a PR into a single HTML page that reads left to right: where intent is stated (issue, PR, specs, ADRs) → goals and requirements → change sets in reading order → files. Every claim links back to a quote, a hunk or a commit, so the reviewer can check it.

Scripts do the deterministic work (collecting evidence, splitting the diff into symbol-level hunks, mapping hunks to commits with blame, checking quotes, rendering). You do the judgment passes in between. The result is only as useful as its honesty: a requirement marked *missing* or a change marked *unexplained* is often the most valuable thing on the page, so don't smooth them over.

## Bundle files

- `scripts/collect.py`: PR or range → evidence bundle and a `throughline.json` skeleton. It also runs the deterministic pre-passes in `scripts/autonotes.py`: pre-filled notes for import-only and mechanical hunks, a compact diff, the test functions the diff touches, work undone within the range, and removed definitions that are still referenced.
- `scripts/apply_notes.py`: merges hunk notes (pass B) into `throughline.json`.
- `scripts/merge.py`: merges your analysis (goals, requirements, change sets, decisions, summary, title, extra sources) into `throughline.json` without touching the collector's fields. Write each pass as a small JSON part file and merge it, rather than hand-editing the big file.
- `scripts/find_quote.py`: finds a phrase in the sources and prints the exact text and its heading, ready to cite.
- `scripts/validate.py`: checks references, verifies every quote against its source text and every test name against the diff.
- `scripts/render.py` + `assets/viewer.html`: writes `goal-map.html`.
- `references/schema.md`: the `throughline.json` format with examples. Read it before pass A.
- `references/passes.md`: detailed guidance and pitfalls for passes A–E. Read the section for each pass as you reach it.

Requirements: `python3` (3.9+, stdlib only), `git`. Optional: `gh` (GitHub PR metadata and issues), a tracker tool such as a Jira or Linear MCP server for ticket text.

## Locate the scripts

Set `SKILL_DIR` to this skill's folder on disk (for example `~/.claude/skills/pr-goal-map` or the Upskill store path). If the skill was loaded through a tool rather than from disk, write `scripts/*.py` and `assets/viewer.html` from the bundle into a temporary folder that keeps the same layout, and use that as `SKILL_DIR`.

## Instructions

Work through these steps in order. Tell the user briefly which step you are on for long PRs; a large PR takes a while.

### 1. Scope

Identify the repo (a local checkout) and either a PR number or a `base..head` range. If the user gave a PR URL, use its number and run from the matching checkout. Ask only if you can't tell which repo or PR.

### 2. Collect

```bash
python3 "$SKILL_DIR/scripts/collect.py" --repo /path/to/repo --pr 1534
# where gh can't run (e.g. a sandbox): save the PR metadata elsewhere, then pass the file
python3 "$SKILL_DIR/scripts/collect.py" --repo /path/to/repo --pr-json pr.json
# for a local branch with no PR:
python3 "$SKILL_DIR/scripts/collect.py" --repo /path/to/repo --base origin/main --head my-branch
```

It prints the output folder (default `~/pr-goal-maps/<repo>-pr-<n>/`; pass `--out` if you can't write there). If `--pr` fails because gh can't reach GitHub, don't fall back to range mode silently: the PR description is a primary source. The error prints the `gh pr view … --json … > pr.json` command; ask the user to run it where gh works (in Claude Code, `! <command>` runs it outside the sandbox), then use `--pr-json`. Without `headRefOid` in the file, the collector looks the head up with `git ls-remote`.

Read `evidence.md` first: counts, sources with their outlines, commits, files and hunk ids, plus the pre-pass sections (auto-noted hunks, test functions touched, changes undone within the range, removed definitions).

Check the NOTES lines before going further. A file-count mismatch with GitHub, or commits "patch-equivalent to commits already on the base", mean the range picked up another PR's work (common with stacked branches and squash merges). Re-run with `--base <sha>` set to where this PR's own work starts, then continue.

### 3. Fetch tracker issues

`evidence.md` lists tracker keys found in the PR title, body, branch and commits, each with a pending source file under `sources/issues/`. For each key that is this PR's ticket (or its parent epic when the ticket is thin), fetch it with whatever tool is available (Jira MCP `getJiraIssue`, Linear, `gh issue view`) and save its title, description, acceptance criteria and any decision-bearing comments as markdown to that path. Then mark it fetched by merging a part like `{"sources": [{"id": "S-RED-216616", "pending": false, "sub": "<issue title>", "url": "<link>"}]}`. Remove placeholders for keys that are just mentions of unrelated tickets by merging `{"sources": [{"id": "S-KEY", "remove": true}]}`. If no tool can reach the tracker, leave it pending and add a note to `notes` saying so.

### 4. Pass A: goals and requirements

Read `references/schema.md`, then the sources: the issue(s) and PR body in full, specs and ADRs in the PR in full, other docs by outline, prior docs as needed, and the commit subjects. Write `goals` and `requirements` to a part file (e.g. `pass-a.json`) and merge it with `python3 "$SKILL_DIR/scripts/merge.py" "$OUT" pass-a.json`. Guidance is in `references/passes.md` §A. In short: requirements are checkable statements, each cites where it is stated with a verbatim quote, and the same requirement stated in several places is one requirement with several cites. To get a quote's exact text and anchor, run `python3 "$SKILL_DIR/scripts/find_quote.py" "$OUT" "phrase"` rather than re-reading the source.

### 5. Pass B: hunk notes

Read `diffs/compact.diff` (changed lines only), opening `diffs/<file>.diff` where you need context. Hunks marked `auto` already have a role and note: check the mechanical groups listed in `evidence.md` and override any that are wrong, but don't rewrite them. For every other hunk write a `role` and a one-line `note` into a JSON file keyed by hunk id, optionally with candidate requirement ids, then merge it:

```bash
python3 "$SKILL_DIR/scripts/apply_notes.py" "$OUT" notes.json
```

For big PRs (more than ~80 hunks or ~3,000 changed lines), split the diff files into batches and, if you can run subagents, give each one a batch plus the requirement list and have it write its own notes file; merge them all with one `apply_notes.py` call. See §B.

### 6. Pass C: change sets

Group hunks into 4–10 change sets and order them the way a reviewer should read them. Test hunks go with the behaviour they prove. Write `why` for each set, add the tests with the requirements they prove, and add a short excerpt for the core sets. Anything that serves no requirement goes in a set marked `unexplained: true`. Merge them as a part file (`changeSets`). See §C.

### 7. Pass D: trace and verify

Go requirement by requirement and set each status by checking the code, not by trusting the notes: grep for the behaviour, open the test and confirm it asserts the requirement. `evidence.md` lists the test functions the diff adds, renames, edits or removes; edited existing tests are valid proof too. For a requirement that something is removed, the "Definitions the diff removes" section already shows whether any references remain at head. `covered` needs a primary change set and, for behaviour, a test. `deferred` and `diverged` need a cited reason. A requirement the sources ask for with nothing implementing it and no stated deferral is `missing`. See §D.

### 8. Pass E: decisions

Read the commits in order (`sources/commits.md`), starting from the "Changed within the range, no net change" list in `evidence.md`, and record 2–6 turning points in `decisions`: a doc change that redirected the code, a rename across many files, a change undone before merge, scope pulled in from another ticket. See §E.

### 9. Summary

Write `summary` (2–4 sentences on what the PR achieves and its headline gaps, with counts) and merge it. In range mode also set `title`, since the collector can only guess one from the branch or last commit.

### 10. Validate, then render

```bash
python3 "$SKILL_DIR/scripts/validate.py" "$OUT"
python3 "$SKILL_DIR/scripts/render.py" "$OUT"
```

Fix every ERROR. Treat "quote not found" warnings as errors: re-copy the exact words from the source, since a paraphrased quote is exactly the kind of claim a reviewer can't check. Other warnings are judgment calls; resolve them or be ready to explain them. Re-run until clean, then render and open `goal-map.html` (`open` on macOS, `xdg-open` on Linux), or hand the file over if you're remote.

### Checklist before you report

Go through this list and say which items you checked. An unchecked item means the map isn't done.

- [ ] NOTES from collect were addressed (range correct, or a note says why not)
- [ ] every tracker key for this PR was fetched or noted as unreachable
- [ ] every requirement cites a verbatim quote; validate reports no quote warnings
- [ ] every hunk has a role and note, and belongs to exactly one change set
- [ ] each covered behavioural requirement names a test that proves it
- [ ] missing, partial, deferred and diverged requirements each have a note with a cited reason (or, for missing, the absence of one)
- [ ] unexplained changes are in their own set, not folded into a goal
- [ ] validate exits 0; render produced goal-map.html

### 11. Report

In chat, give the path to `goal-map.html` and 3–5 lines: what the PR achieves, the requirements that are missing, diverged or partial, any unexplained changes, and the change set to read first. Don't restate the whole map.

## Examples

**"Help me review #1534, I don't know the context gateway code."** Collect with `--pr 1534`; evidence shows `RED-216616` → fetch it from Jira; the spec and ADR in the PR carry most of the intent; pass A finds 19 requirements across 5 goals; pass D finds one requirement diverged on purpose (the issue asked for compact results; a spec commit switched to full definitions) and three deferred by the spec; one small route fix belongs to another PR and lands in an unexplained set. Report those four findings plus the path.

**"Map what's on my branch before I open the PR."** No PR yet: `--base origin/main --head HEAD`. Sources are the commits and any docs changed on the branch plus the ticket in the branch name. The map doubles as a self-review: unexplained changes are candidates to split out.

## Troubleshooting

- *`--pr needs the GitHub CLI`* or *gh could not read PR*: install/authenticate `gh`, or save the metadata where gh works and use `--pr-json`. In a sandbox, gh often fails on TLS verification or keychain access even when it's logged in.
- *Head commit not fetchable* (read-only checkout): pass `--head <sha>` for a commit that's already local; `git cat-file -t <sha>` tells you.
- *Head commit not in the local repo* (branch deleted after merge): collect fetches `pull/<n>/head`; if that fails, `git fetch origin pull/<n>/head` yourself and pass `--head FETCH_HEAD`.
- *Range includes another PR's commits*: see step 2; pass `--base` explicitly.
- *A 5,000-line generated file dominates*: hunks from `generated` files need no notes beyond "generated"; put them in one set.
- *Quote not found but the words are there*: the source likely has formatting inside the phrase (links, code ticks across a line break). Quote a shorter fragment, or join two fragments with `…`.
- *Privacy*: `goal-map.html` embeds code and ticket text. Treat it like the repo itself; don't publish it outside the people who can see the code.
