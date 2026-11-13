# Contributing

Most changes to esgf-fix-tool are one of two things: **a rule** (what to do with a QA message) or **a fix function**
(the code that changes a file). This page covers both, plus the branch and release workflow.

## Setup

```bash
git clone https://github.com/ESPRI-Mod/esgf-fix-tool && cd esgf-fix-tool
uv sync                  # creates .venv with the package installed in editable mode
uv run pytest -q         # must pass before any PR
uv run ruff check        # must be clean before any PR
```

Rules and code are read from `src/`, so edits are picked up immediately by `uv run esgf-fix-tool …`.

## Branches and pull requests

- `main` is always releasable. Nobody pushes to it directly.
- Work on a short-lived branch from `main`, named after what it does: `rule/C115-cell-measures`, `fix/hybrid-bounds`,
  `docs/readme`, `chore/ruff`.
- Open a pull request to `main`. One reviewer approves; **a severity-3 waiver needs a second reviewer**.
- Keep a PR to one topic. Several rules for the same QA family can go together.
- Merge when tests and ruff pass. Delete the branch after merging.

## From a QA finding to a rule

1. **See what is not covered.** On a full esgqa report:

   ```bash
   uv run esgf-fix-tool classify qa_result_XXX.json --show 30
   ```

   Exit code 20 (`new_rule`) lists the unmatched messages and the blocking findings per rule.

2. **Decide the action.** One of:

   | action | When |
   |---|---|
   | `fix` | the correction is known and safe to automate (needs a `fix:` function) |
   | `review` | someone must decide; blocks the loop until then |
   | `waive` | the finding is accepted as is (severity 3: two reviewers) |
   | `defer` | handled at a later pipeline step, checked again at the `--final` gate |
   | `other_step` | fixed by another tool (e.g. repacking) |
   | `upstream_only` | must be fixed by the model or simulation side |

   See [ARCHITECTURE.md](ARCHITECTURE.md#rules) for how each action affects the loop and the final gate.

3. **Write the rule** in the thematic catalogue it belongs to, in `src/esgf_fix_tool/rules/`:

   ```yaml
   - id: C115
     check: '\[CMIP7\] \[ATTR010\] .*'              # regex on the check label, anchored at the start
     message: 'Variable "(?P<var>[^"]+)" .*'        # regex on the message; named groups feed params
     action: fix
     fix: set_var_attr                              # name of a registered fix function
     params: {var: "{var}", attr: cell_measures, value: "area: areacella"}
     note: why this is the right correction, who decided it, and when
   ```

   - **IDs:** reviewed rules are `C###`. Take the next free number (`C115` at the time of writing);
     use a letter suffix (`C074a`) for a variant of an existing rule. `S###` IDs belong to the historical
     fallbacks; don't create new ones.
   - **Order:** the first matching rule wins. Put specific rules before generic ones inside a catalogue.
   - **Scope:** use `dataset:` (a regex on the dataset ID) when a decision was only reviewed for some datasets.
   - **New catalogue file:** add it to the `include:` list of `post_apply.yaml` at the right position. That list
     is the order catalogues are tried in. Never add rules to `catalogue_fallbacks.yaml` or `fallbacks/`.

4. **Record the decision in the rule's `note`.** It travels with the rule and is shown by `classify`.
   Put the longer discussion (evidence, files checked, who approved) in the PR description, so it stays in the
   project history next to the change.

5. **Check it on the real report:**

   ```bash
   uv run esgf-fix-tool classify qa_result_XXX.json --rule C115
   uv run esgf-fix-tool plan qa_result_XXX.json -r C115 --map-path "$src=$staging" -o fixplan.C115.json
   uv run esgf-fix-tool apply fixplan.C115.json --only-under "$staging" --dry-run
   ```

   Then apply on a staging copy for real, run QA again, and check that the finding is gone and nothing new appeared.

## Writing a fix function

Fix functions live in `src/esgf_fix_tool/fixes/`, grouped by theme, and register themselves with `@fix`:

```python
from . import fix


@fix("drop_var_attr")
def drop_var_attr(ds, var: str, attr: str) -> bool:
    """Remove ``attr`` from variable ``var``."""
    v = ds.variables.get(var)
    if v is None or attr not in v.ncattrs():
        return False          # nothing to do: already fixed, or not applicable
    v.delncattr(attr)
    return True
```

The contract:

- **Signature:** `ds` plus the rule's `params` as keyword arguments. `ds` is a Dataset-compatible view, not the
  file itself: the fix runs first in memory (preflight), and the writer decides how to apply the changes.
- **Return** `True` if something changed, `False` otherwise.
- **Idempotent:** running it twice must change nothing the second time. A fixed file is never rewritten.
- **Check preconditions and refuse** (raise) when the file doesn't look like the reviewed case. A wrong fix
  is worse than no fix.
- **Never change science arrays** unless the fix declares it. Structural fixes (`kind="structural"`,
  `layout_change`, `data_targets`) must declare what they change and provide a verifier; `@fix` enforces this.
  Look at the existing fixes in `fixes/` before writing one.
- A new module in `fixes/` must be imported in `fixes/__init__.py` so its fixes register.

## Tests

Every new rule or fix needs a test in `tests/`. Tests build **small synthetic netCDF files and reports**: never
commit real data. A typical test (see `tests/test_history.py`):

1. creates a tiny netCDF file with the problem,
2. writes a minimal `qa_result_*.json` pointing to it,
3. runs `plan` with only the new rule and checks the planned steps,
4. runs `apply` and checks the file is fixed, everything else is unchanged, and a second run changes nothing.

## Releases

Versions follow [semantic versioning](https://semver.org/). Rule changes count: a new or changed rule is at least
a minor version, because it changes what the tool does to files.

1. On a branch, update `version` in `pyproject.toml` and the changelog, then open a PR.
2. After merging, tag `main`: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.

The fixlog records the tool version on every line. Rules ship inside the package, so the version identifies the
exact rules too, and any fixed file can be traced back to a release.
