# PR Goal Map

PR Goal Map helps a reviewer understand what a pull request is meant to do and where each requirement is implemented. It produces a standalone, interactive HTML page that connects source documents and issues to goals, requirements, change sets, tests, and files. It also shows requirements that are missing, partial, deferred, or changed from the original plan, and changes that have no clear goal.

The scripts collect and check evidence. A coding agent reads that evidence, groups the changes, and verifies each claim against the code and tests. The map is a guide for review, not an automated verdict on code quality.

## Requirements

- Python 3.9 or newer (standard library only)
- Git and a local checkout of the repository you want to review
- Optional: an authenticated [GitHub CLI](https://cli.github.com/) for PR metadata, plus access to any issue tracker that holds the requirements

## Get started

Clone this repository and give your coding agent its [`SKILL.md`](SKILL.md). Ask it to map a PR in a local checkout, for example: “Map PR #1534 in `/path/to/repo`.” The skill guides the agent through evidence collection, issue retrieval, analysis, validation, and rendering.

## Trying the steps yourself (optional)
Optionally, you can test out the steps from the skill yourself, starting with data collection:

```bash
git clone https://github.com/abrookins/pr-goal-map-skill.git
python3 pr-goal-map-skill/scripts/collect.py --repo /path/to/repo --pr 1534
```

For a local branch without a PR, give the base and head revisions:

```bash
python3 pr-goal-map-skill/scripts/collect.py \
  --repo /path/to/repo --base origin/main --head my-branch
```

The collector prints its output directory. By default, it uses `~/pr-goal-maps/<repo>-pr-<number>/` for a PR; use `--out DIR` to choose another location. Read `evidence.md` first and check that the selected commit range contains only the work you intend to map. Collection creates a skeleton, not a finished map. Follow [`SKILL.md`](SKILL.md) and the [pass guidance](references/passes.md) to add goals, requirements, hunk notes, change sets, verification results, and decisions.

When the analysis is complete, validate it and render the page:

```bash
python3 pr-goal-map-skill/scripts/validate.py OUT_DIR
python3 pr-goal-map-skill/scripts/render.py OUT_DIR
```

Open `OUT_DIR/goal-map.html` in a browser. Resolve validation errors and incorrect source quotes before treating the map as complete.

## Output files

| Path | Contents |
| --- | --- |
| `evidence.md` | A readable digest of sources, commits, files, hunks, and collection notes. |
| `evidence.json` | The machine-readable evidence bundle. |
| `throughline.json` | The map skeleton that the analysis passes fill in. |
| `diffs/` | Per-file diffs and a compact diff for review. |
| `sources/` | Local copies of source text that the map can quote. |
| `goal-map.html` | The final, self-contained viewer, created by `render.py`. |

The viewer presents a goal map, requirement coverage, and a commit timeline. Select a node to inspect its sources, related changes, tests, and diff hunks.

## Repository layout

- [`SKILL.md`](SKILL.md) contains the end-to-end agent workflow and reporting checklist.
- [`references/schema.md`](references/schema.md) defines the `throughline.json` format.
- [`references/passes.md`](references/passes.md) explains the analysis passes.
- [`scripts/`](scripts/) contains the collector, merge helpers, quote finder, validator, and renderer.
- [`assets/viewer.html`](assets/viewer.html) is the HTML viewer template.

## License

MIT. See [`LICENSE`](LICENSE).
