# esgf-fix-tool

Fix CMIP7 netCDF files from an **esgqa report** and a set of reviewed **rules**.

esgf-fix-tool reads the full report written by [esgf-qa](https://pypi.org/project/esgf-qa/) (`qa_result_*.json`),
matches each finding against rule catalogues, and applies the fixes the rules allow. It is the FIX half of the
QA ↔ FIX loop of the IPSL CMIP7 publication pipeline: run QA, classify, plan, apply, run QA again.

Design and internals: [ARCHITECTURE.md](ARCHITECTURE.md). Adding rules and fixes: [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

The [esgf-toolbox](https://github.com/ESPRI-Mod/esgf-toolbox) image is the intended way to run it on HPC clusters
(bundling in progress).

Elsewhere, with [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12):

```bash
uv tool install git+https://github.com/ESPRI-Mod/esgf-fix-tool@vX.Y.Z   # a released tag
esgf-fix-tool --version
```

The rule catalogues ship inside the package; nothing else needs to be downloaded.

## Usage

Three commands. Work on an **independent staging copy** of the files, never on the simulation output itself.

```bash
# 1. What does this report need? Prints a summary and exits with the decision code.
esgf-fix-tool classify qa_result_XXX.json

# 2. Which fixes would be applied, per file? Never opens the netCDF files.
esgf-fix-tool plan qa_result_XXX.json -o fixplan.json \
  --map-path "$source_root=$staging_root"      # the report was made on the source, fixes go to staging

# 3. Apply them. Check preconditions in memory first, then for real.
esgf-fix-tool apply fixplan.json --only-under "$staging_root" --dry-run
esgf-fix-tool apply fixplan.json --only-under "$staging_root" --fixlog fixlog.jsonl

# 4. Run QA again on the staging tree and loop.
```

Always use the full report, not `qa_result_*.cluster.json`.

### Decision and exit codes

`classify` looks only at severity-3 findings to decide what the loop does next:

| Exit | Decision | Meaning | Next step |
|---:|---|---|---|
| 0 | `clean` | nothing blocking left | continue the pipeline |
| 10 | `known` | every blocking finding has an implemented fix | `plan` + `apply`, then QA again |
| 20 | `new_rule` | a finding has no rule, a `review` rule, or a rule whose fix is not written yet | write or review a rule ([CONTRIBUTING.md](CONTRIBUTING.md)) |
| 21 | `blocked_upstream` | a finding must be fixed by the model/simulation side | report it upstream |
| 30 | `no_change` | same fixable findings as `--previous`: a fix does not work | tool bug, investigate |
| 40 | `checker_error` | the QA checker failed on some files | fix the QA run first |

`--final` is the last gate before publication: deferred and other-step findings then block too.
`--json` prints a machine-readable summary.

### Choosing rules

By default, `classify` and `plan` load every packaged catalogue, reviewed catalogues first and historical
fallbacks last. To narrow a run:

```bash
esgf-fix-tool plan qa.json -r C001 -r C075           # only these rule IDs
esgf-fix-tool plan qa.json --exclude-rule C103c       # everything except this one (exclusion wins)
esgf-fix-tool plan qa.json -r my_rules.yaml           # explicit catalogue files replace the defaults
esgf-fix-tool classify qa.json --rule C001            # show where one rule stands
```

Unknown rule IDs are rejected. Exclusion filters the planned fixes, not the matching, so an excluded finding never
falls through to a broader rule under another ID.

## What `apply` does to your files

- All the fixes for a file are first checked against an in-memory view of its header. A file that is already
  correct is never written.
- **Metadata changes** (attributes, renames, small auxiliary arrays) update the file in place. They are not
  atomic: an interruption can leave some changes applied. **Hardlinked files are refused** so a staging copy can
  never alias a simulation file.
- **Structural changes** (dimension order, singleton removal, bounds merges) are combined into **one** rewrite per
  file. The output is verified against the source, then atomically replaces it.
- When a fix changes the grid label, the file is renamed to match.
- `--only-under DIR` refuses the whole plan if any file is outside `DIR`.

Each run appends one JSON line per file to the fixlog: tool version, catalogues used, every step applied, write
mode, timings, and any file rename. A detailed `<fixlog>.events.jsonl` is written alongside, flushed as the run goes,
so a long apply can be followed with `tail -f`.

## Rules

Rules live in [`src/esgf_fix_tool/rules/`](src/esgf_fix_tool/rules/), one YAML catalogue per theme.
`post_apply.yaml` includes the thematic catalogues in their reviewed order, which is the order rules are tried:

| Catalogue | Scope |
|---|---|
| `post_apply.yaml` | manifest: includes the catalogues below, in this order |
| `basin_coordinates.yaml` | basin indices and region labels |
| `variable_attributes.yaml` | variable attributes and comments |
| `coordinate_metadata.yaml` | coordinate attributes |
| `coordinate_values_bounds.yaml` | coordinate values and bounds |
| `surface_and_hybrid.yaml` | surface axes and hybrid vertical coefficients |
| `grid_labels.yaml` | region and output-grid labels |
| `qa_metadata.yaml` | CF and CMIP7 recommendations |
| `dimension_order.yaml` | dimension order, renames, singleton removal |
| `standard_name_modifiers.yaml` | descriptive `standard_name` values |
| `units_and_standard_names.yaml` | units and invalid standard names |
| `vertical_coordinate_names.yaml` | missing generic vertical coordinates |
| `required_coordinates.yaml` | missing coordinates |
| `horizontal_coordinates.yaml` | horizontal references and auxiliary names |
| `consistency.yaml` | cross-dataset consistency (review only) |
| `catalogue_fallbacks.yaml` + `fallbacks/` | historical triage, always loaded last |

A rule maps a QA message to an action:

```yaml
- id: C001
  check: '\[CF-Conventions\] appendix_a'
  message: '\[Appendix A\] Attribute "history" should not be present in non-coordinate data \(D\) variable "(?P<var>[^"]+)"'
  action: fix
  fix: drop_var_attr
  params: {var: "{var}", attr: history}
  note: keep history as a global attribute only
```

The first matching rule wins. See [ARCHITECTURE.md](ARCHITECTURE.md#rules) for every field and action.

## Development

```bash
git clone https://github.com/ESPRI-Mod/esgf-fix-tool && cd esgf-fix-tool
uv sync
uv run pytest -q
uv run ruff check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch workflow and how to add a rule or a fix.

## History

This tool was built during the CMIP7 pilot on `CM6-ESMCO2-FT-esm-pi-s.01` (October–November 2026). The per-rule
decision logs, TGCC procedures and test recipes from that pilot are kept at the
[`pilot-2026-11`](https://github.com/ESPRI-Mod/esgf-fix-tool/tree/pilot-2026-11) tag.
