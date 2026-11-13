# esgf-fix-tool: architecture

Fix CMIP7 netCDF files from an **esgqa report**, with **rules** that say what to do with each QA message.
It is the FIX half of the QA ↔ FIX loop of the publication pipeline. Usage: [README.md](README.md).

```
esgqa full report (qa_result_*.json)
        │
        ▼
  report.py ── QAReport: file → [Finding(severity, check, message)] + checker errors
        │
        ▼
  ruleset.py ─ RuleSet (src/esgf_fix_tool/rules/*.yaml): (check regex, message regex with named groups) → action
        │
        ▼
  classify.py ─ every finding gets an Outcome; one Decision for the whole run
        │        clean · known · new_rule · blocked_upstream · no_change · checker_error
        ▼
  plan.py ──── FixPlan: file → ordered [(fix name, params)]   (only for Outcome.FIX)
        │
        ▼
  apply.py ─── preflight → direct patch OR one combined rewrite (+ filename sync) + fix log
        │
        └── fixes/ ── registry of fix functions (@fix("drop_var_attr", kind="metadata"))
```

## Inputs and outputs

| | What | Notes |
|---|---|---|
| in | full esgqa report `qa_result_*.json` | `fail[severity][check][message][dataset] → [files]`, `error{}`, `info{}`. Not the `.cluster.json` |
| in | `src/esgf_fix_tool/rules/*.yaml` | shipped in the package; the catalogues used are recorded in every output |
| in | previous report (optional) | to detect "no change" between two loop iterations |
| out | classification (JSON + text summary, exit code) | drives the loop: the pipeline reads the exit code |
| out | fix plan (JSON) | reviewable before applying |
| out | fixed files + `fixlog.jsonl` + `fixlog.events.jsonl` | fixlog = one line per file: tool version, steps, write mode, renames |

## Rules

```yaml
- id: R001
  check: '\[CF-Conventions\] appendix_a'          # regex on the check label
  message: 'Attribute "history" should not be present in non-coordinate data \(D\) variable "(?P<var>[^"]+)"'
  action: fix                                     # fix | waive | defer | other_step | upstream_only | review
  fix: drop_var_attr                              # name in the fixes registry
  params: {var: "{var}", attr: history}           # named groups are substituted
  upstream_status: reported                       # not_reported | reported | fixed_upstream
  note: keep history as a global attribute
```

- `check` and `message` are regexes, matched with `re.search` anchored at the start (`^` added automatically).
- Optional `dataset` regex restricts a rule to an investigated dataset subtype (e.g. thermal soil);
  absent this key, matching is unchanged.
- **First matching rule wins**; order the file from specific to generic.
- Catalogue order: `post_apply.yaml` `include`s the thematic catalogues in their reviewed order, and a file is
  loaded only once, so that list is the order catalogues are tried. Catalogues it does not include are loaded
  next (`PRIORITY` in `cli/catalogues.py`, then by name), and `catalogue_fallbacks.yaml` always last.
- Actions → outcomes:

| action | Outcome | Blocks the loop? | Blocks the final gate? |
|---|---|---|---|
| `fix` (+ `fix:` present in the registry) | `FIX` | no: the fix is applied | yes, if still there |
| `fix` without an implemented function | `FIX_MISSING` | **yes** (→ fix-tool developer) | yes |
| `waive` | `WAIVED` | no | no (sev-3 waiver = 2 people, in the PR) |
| `defer` | `DEFERRED` (PATH001–003) | no | **checked for real** at the gate |
| `other_step` | `OTHER_STEP` (FILE004*: repack) | no | yes, if still there after repack |
| `upstream_only` | `UPSTREAM` | **yes** (→ simulator) | yes |
| `review` | `REVIEW` (Excel "Vérifier", "À qualifier") | **yes** (→ Rules keeper) | yes |
| no rule matched | `UNKNOWN` | **yes** (→ new rule) | yes |

Only **severity 3** decides; severity 2 and 1 are fixed when a rule exists, never blocking.

## Decision of one QA iteration (`classify`)

Checked in this order:
1. checker `error` entries → `checker_error` (files not fully checked)
2. any blocking sev-3 outcome `UNKNOWN` / `REVIEW` / `FIX_MISSING` → `new_rule`
3. any sev-3 `UPSTREAM` → `blocked_upstream`
4. any sev-3 `FIX`, and the same set of findings as the previous report → `no_change` (a rule doesn't work: tool bug)
5. any sev-3 `FIX` → `known` (run `plan` + `apply`, then QA again)
6. otherwise → `clean`

With `--final` (the gate on the DRS draft tree), `DEFERRED` and `OTHER_STEP` are no longer accepted.

Exit codes: `0` clean · `10` known · `20` new_rule · `21` blocked_upstream · `30` no_change · `40` checker_error.

## Writing files (`apply`)

1. `preview.py` exposes a Dataset-compatible, diskless header view with lazy source reads.
   Recipes keep their `ds, **params` signatures. Only small auxiliary edits are buffered;
   undeclared edits to existing numeric arrays are rejected before writing.
2. `batch_write.py` combines final names, attributes, explicit axis permutations/squeezes and
   size-two bounds-dimension mappings into one schema. Dimension identities are tracked through
   renames, so a layout change and a bounds merge do not trigger successive rewrites.
3. `execute.py` chooses the least expensive physical operation:
   - no change: read-only, no disk write;
   - metadata or small auxiliary edits: update the target with netCDF4 `a`, preserving its inode;
   - structural changes: stream the unchanged source into **one** final-schema output, check all
     mapped raw arrays, format, attributes/types, packing, compression and chunks, then replace it.
     There is no preliminary source copy. A standalone compatible permutation can use local
     `ncpdq`; compound transformations use one bounded Python/netCDF4 pass.

Direct updates validate final headers and affected auxiliary arrays, without reading unrelated
science arrays. Coordinate snapshots protect against the reproduced NetCDF-4 rename data-loss
bug; only differing arrays are restored. These updates are not atomic, and a failed/interrupted
operation can already have changed the target. Hardlinked targets are refused before direct writes
because they could alias a simulator file, unless `--break-hardlinks` is given: the file is then copied
next to itself and renamed onto its path just before the first write (after preflight, so an unchanged
file is never copied), and its other links keep the previous content. Structural replacement safely leaves
other links alone. Both record `hardlinks_broken` (the number of other links) in the fixlog.
Classic-format header growth can cause internal data movement within the NetCDF library.

Built-in `after_close` operations are compiled into the batch instead of executing their separate
legacy rewrites. A custom finalizer requires a verified output; it must not perform another structural
rewrite. Explicit reviewed `data_targets` and domain verifiers remain enforced. The unified mapping
proof handles combinations such as profile squeezing plus bounds-dimension merging.

A failing file does not stop `apply`: the exception and traceback go to the fixlog (`status: failed`) and the next
file is processed; the exit code is 1 if any file failed (`--fail-fast` aborts instead). Cancellation and global plan
validation always abort.

`--dry-run` performs only preflight in the diskless view: it does not write or physically verify an
output. Real application records `verification_scope`, `write_mode` and `rewrite_count` (0 or 1).
Per-file completion means every step/check succeeded; recipes remain idempotent. A grid-label fix can
rename the file in its own directory (hardlink then unlink, never overwriting an existing file); the fixlog
records `drs_rename` and `final_file`, and a replayed plan follows the new name.

## Historical fallbacks

`rules/catalogue_fallbacks.yaml` and `rules/fallbacks/` were generated once from the 2026-10-29 Excel triage
(the one-off `seed-rules` importer has since been removed; see git history). They are always loaded after the
reviewed thematic catalogues. Rules with `fix: null` classify as `FIX_MISSING`.

## Layout

```
src/esgf_fix_tool/
  report.py      read the esgqa report
  ruleset.py     Rule, RuleSet, matching
  classify.py    Outcome, Decision, summary
  plan.py        FixPlan from classified findings
  apply.py       plan iteration and fix log
  preview.py     diskless recipe preflight, lazy arrays
  batch_write.py compose final schema and one source-to-output mapping
  execute.py     direct update / one rewrite / no-op execution
  deduplicate.py ATM/SRF overlap removal (the `deduplicate` command), independent of reports and rules
  fixes/         registry + fix functions (attributes.py, …)
  cli/           Typer app: main.py (entry point) + one file per command
                 (classify.py, plan.py, apply.py, deduplicate.py; common.py = shared options)
  rules/         rule files (versioned, shipped in the package; fallbacks/ included by catalogue_fallbacks.yaml)
tests/           small synthetic reports and netCDF files
```

## Implementation notes

Horizontal recipes live in fixes/horizontal.py. Coordinate-reference removal is scoped to the main
variable and 21 bundled DR policies. Numeric 2-D auxiliary renames preserve y/x and reuse the existing
after-close coordinate restoration. Bounds renames resolve both original and canonical horizontal
names, so successive QA reports remain matchable after an earlier coordinate rename.

Hybrid fixes in fixes/hybrid.py bind the bundled IPSL ap/b coefficients to the
existing vertical axis. The reference centers and interfaces are checked against
each other and the full lev axis. Small variables that require new dimension
identities are recreated in place, with displaced arrays kept under
fix_original_* names. C068 uses the same approach when a bnds coordinate already
exists and the only affected arrays are known small bounds. Structural rewrites
remain available for other layouts.

Basin-profile conversion in fixes/rectilinear.py declares its mapped arrays and a final verifier.
The temporary rewrite renames y to lat, squeezes only x=1 and changes only the last latitude to
89.36999 under the model-engineer decision relayed by the user. Every scientific array is compared
against the source by the exact squeeze mapping after all hooks; all 332 rows are retained.
Globals, format and compression are preserved; chunks lose only their singleton x component.
No latitude bounds are synthesized. Other spatial inventories or endpoint anomalies are refused.

Progress is centralized in progress.py and rendered by cli/progress.py. Context-local callbacks
attach file/rule/parameter/phase data without changing any recipe signature. Long block loops emit
real counters, throttled by the renderer. There is no periodic worker or heartbeat. A recipe is reported Done only after final verification and file commit.
Detailed events are flushed to a sidecar JSONL log independently of the existing per-file fixlog.

data_integrity._slices uses bounded contiguous C-order slabs rather than repeatedly splitting the
largest axis. This limits compressed-chunk cache thrashing and gives a canonical hash traversal.
Python rewrite and full comparison loops share this iterator. Direct metadata updates avoid
whole-science-array fingerprints; structural outputs prove the declared mapping for every array.
