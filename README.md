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

Three commands for the QA ↔ FIX loop. `apply` also handles the approved ATM/SRF
overlaps automatically; [`deduplicate`](#deduplicate-atmsrf-overlaps) remains available separately.
Work on an **independent staging copy** of the files, never on the simulation output itself.

```bash
# 1. What does this report need? Prints a summary and exits with the decision code.
esgf-fix-tool classify qa_result_XXX.json

# 2. Check current NetCDFs in read-only mode and plan only needed changes.
esgf-fix-tool plan qa_result_XXX.json -o fixplan.json \
  --map-path "$source_root=$staging_root"      # the report was made on the source, fixes go to staging

# 3. Apply them. Check preconditions in memory first, then for real.
esgf-fix-tool apply fixplan.json --only-under "$staging_root" --dry-run
esgf-fix-tool apply fixplan.json --only-under "$staging_root" --fixlog fixlog.jsonl

# 4. Run QA again on the staging tree and loop.
```

Always use the full report, not `qa_result_*.cluster.json`.

The `plan` summary separates automatic corrections from non-automatic QA findings:
`N fixes à appliquer sur M fichiers` counts only distinct selected executable
steps and their file paths. Waived-only and review-only files are excluded.
A separate line counts distinct ignored (`waive`) diagnostics and pending or
external-work diagnostics across the **full QA report**, with a status breakdown
(`review`, `deferred`, `other_step`, `upstream`, `fix_missing`, `unknown`, checker
errors). A repeated check/message/path is counted once, including dataset aliases.
A file can occur in several groups, so their file totals must not be added.
Rule filters are reported separately as unselected automatic steps.

By default, planning opens candidate NetCDFs read-only, **after** path mapping,
using the same preparation pipeline as `apply --dry-run`. It removes fully
conforming files from the plan and counts changed steps/files rather than stale
QA candidates. This may read scientific arrays for recipes that need numerical
validation, but it never updates a NetCDF, renames a file, breaks hardlinks or
runs the output rewrite/verification phase. Pass `--break-hardlinks` at plan time
if it will be used during apply; planning itself still never breaks links.

The plan JSON records this preparation in `preflight`. Failed/inaccessible files
remain in the plan as explicitly unverified candidates, outside the confirmed
change count. Within changed files, selected no-op prerequisite steps are kept
for composition and revalidation; the displayed change count excludes them.
Apply always validates the retained steps again. Files can change after planning,
and final output checks run only during apply, so preparation is not a guarantee
of a successful write.

For offline use, `plan ... --report-only` keeps QA candidates without opening
NetCDFs and labels them `fixes candidats ... (non vérifiés)`. Regenerate QA after
applying fixes.
A zero automatic count with pending findings does not mean all remaining findings
are accepted false positives. An accepted false positive must be classified `waive`.

### Processing several files concurrently

`apply` defaults to one file at a time. Use `--workers N` (or `-j N`) to process
up to N independent files in separate spawned processes:

```bash
esgf-fix-tool apply fixplan.json --only-under "$staging_root" --workers 4 --fixlog fixlog.jsonl
```

The option also works with `--dry-run`. Each file retains its ordered fixes,
in-place metadata edits and single combined rewrite when required. NetCDF/HDF5
handles are never shared between processes. The parent writes both JSONL logs;
completed files are logged in completion order. In an interactive terminal,
one compact live view shows totals and a single current-stage line per active
file, with I/O percentages. If the terminal is too short, remaining active files
are counted below the visible rows. In batch or redirected output, the existing
detailed messages are retained, with rewrite progress sampled at observed 25%
milestones and a final summary per grouped rewrite. Human-readable prefixes use
only the filename; full file paths remain in the JSON journals and tracebacks.
The events JSONL retains every
phase, fix result and error. Array I/O is sampled at observed 25% milestones
(at most five entries per large variable and operation, including start/end);
small auxiliary arrays record completion only. The interactive view still uses
live counters independently of this journal sampling. A human-readable text
journal is also appended automatically: `--fixlog fixlog.jsonl` creates
`fixlog.events.log` alongside `fixlog.events.jsonl`. It contains the file, phase
and fix messages, readable parameters, sampled I/O progress and tracebacks,
including with `--quiet`. All journals are written by the parent process;
no separate per-file logs are created. Error tracebacks remain available, and the CLI continues other files by
default. With `--fail-fast`, no new files are dispatched once a failure is
observed; already dispatched files finish and are logged before the error is
returned. Interruption stops further dispatch and waits for active workers to
close their files.

Parallel mode rejects duplicate resolved paths, hardlink aliases and conflicting
rename targets before applying fixes. More workers increase aggregate memory,
temporary disk space and filesystem traffic; speedup depends on available I/O
bandwidth and the fixes selected. No change to the fix plan is needed.

### Resuming a plan, or splitting it over several jobs

A batch job cut by its time limit (SIGKILL) leaves the files it had finished in the fixlog, and up to
`--workers` files half-way. To finish the plan without redoing the rest:

```bash
esgf-fix-tool apply fixplan.json --only-under "$simulation_dir" --workers 16 \
    --resume 'run/fixlog*.jsonl' --fixlog run/fixlog.resume.jsonl
```

`--resume` skips the files that the given fixlogs record as done (the latest record of a file wins) and
retries the failed ones, unless `--skip-failed`. Lines that are not fixlog records (events journals,
deduplication logs) and dry-run lines are ignored, so a glob over the run directory is fine. Quote it, so the
tool expands it (`--resume a.jsonl --resume b.jsonl` works too; `--resume a.jsonl b.jsonl` does not).

A file interrupted during a structural rewrite is intact: the rewrite goes to a temporary
`.<name>.*.fixtmp` that only replaces the file once verified. Before starting, `apply` removes such leftovers
of the files it is about to fix (a dry run only counts them). A file interrupted during a direct metadata
update may be partly updated: running its fixes again completes it.

To go faster than one node allows, split one plan over K jobs, each with its own fixlog:

```bash
esgf-fix-tool apply fixplan.json --only-under "$simulation_dir" --workers 16 --shard 2/4 --fixlog run/fixlog.shard-2.jsonl
```

`--shard k/K` keeps every K-th file of the plan in path order, from the k-th. The shares are computed on the
whole plan, never on what is left, so jobs started at different times, or resumed with `--resume`, never
overlap; the plan is refused if two entries share a file or a rename target. The automatic ATM/SRF
deduplication does not run with `--shard`, since shards run concurrently: run `deduplicate` once they are
all done. `--resume` and `--shard` can be combined. The statistics and each fixlog line record the shard.

### Hybrid coefficient dimensions

Rules and recipes do not filter by model, experiment or realization. They retain
their product, structure, unit and numerical-reference checks; removing a model
filter does not authorize extrapolating physical coefficients or bounds.

For atmospheric `alh` files with `lev=80` but both `ap` and `b` on 79 entries,
the existing C074/C074a recipe binds the supplied 80-interface coefficient
reference before C102 normalization. It first validates the complete existing
`lev(80)` against that reference; the old 79-entry values are not interpolated
or required to match the 79-center reference. The main field and `ps` retain
their values and compression. Already 80-entry coefficients with different
values, mixed 79/80 coefficient pairs, and unknown vertical references remain
rejected. This does not change the separate handling of 79-level outputs.

The bundled `hybrid_coefficients.json` also contains the user-supplied CM7
95-center and 96-interface coefficient dumps, including both sets of explicit
coefficient bounds and source-dump hashes. The donor `airmass` file's `lev=95`
does not determine its reference family: its coefficients have 96 entries.

The existing C074/C074a and C102 recipes select the reference from the target
`lev` length. For CM7, a paired 95/96 coefficient-size mismatch is repaired from
the corresponding supplied reference after validating the complete `lev` vector.
Already correctly sized coefficients must agree with that reference; their binary
values are preserved, allowing decimal dump rounding (`rtol=5e-14`,
`atol=1e-14`) or the exact float32 rounding of the complete reference vector.
Real-precision CM7 coordinates/coefficients are widened losslessly before their
normalization; unrelated values still fail the reference checks. Mixed coefficient
lengths and unrelated vertical grids are refused.

For `al` with 95 levels, explicit coefficient bounds are aligned to `(lev,bnds)`;
C102 computes `lev=ap/101325+b` and `lev_bnds=ap_bnds/101325+b_bnds`, validates
containment and supplies the formula attributes. For `alh`, optional vertical
bounds are removed when unreferenced, independently of the level count. A 96-level
product labelled `al` remains rejected by ordinary normalization: the supplied
96-entry coefficients describe interfaces. The existing edt/evu-specific C104/C103e
recipe handles the approved exception, selecting 80-to-79 or 96-to-95 adjacent
interface means from the supplied references. It propagates a missing value if
either interface is missing, rebuilds the corresponding center coordinate and
bounds, preserves science metadata and compression, and never averages a replay
already on 79/95 centers.
Coefficient binding/normalization preserve the main field and `ps`, including
their masks and compression. The edt/evu-specific interpolation is the explicit
scientific-data transformation described above. All structural changes for a file
share one atomic rewrite; replaying a conforming file does not
rewrite it.

### Reviewed g131 latitude reversal and longitude rotation

C118 selects only `areacellr` fixed fields and daily `rivi`/`rivo` on g131.
It validates the complete 360-point half-degree axis, then makes latitude increase
from -89.75 to 89.75 degrees by reversing every array using the `lat` dimension.
Existing latitude bounds are reordered by rows and by endpoint columns; no edges
are reconstructed. The grouped writer streams this transformation once, preserves
raw missing/packed values and compression, verifies every output tile, and only
then replaces the original file. A conforming replay does not rewrite the file.

C121 rotates the same three river products from signed longitudes
(-179.75..179.75) to 0.25..359.75 degrees. Validate all 720 centers and their
existing half-degree bounds first, then translate and rotate every array using
`lon`, including packed/masked auxiliaries. Translate both bounds by the cell's
longitude offset: the last upper edge is 360, not zero. Combine the rotation with
C118 in one bounded-memory rewrite; validate the output before replacing the
source. Replaying the plan is a no-op.

C059 remains a separate precision correction for regular g111/g245 grids. Detect
`nlon` from `lon(lon)`, verify existing two-endpoint bounds, and build a shared
`linspace(0,360,nlon+1)` interface vector for midpoint-centered CM7 cells. Retain
g245 longitude centers exactly and reject deviations above 1e-8 degree. Keep the
established CM6 g111 zero-centered convention (bounds offset by half a cell,
1e-10 tolerance). Polygon `cell/nvertex` grids are excluded. This correction
writes only the small coordinate arrays directly; no scientific field is copied.

C002a also covers the audited `fNleach`, `mrsow` and `rivi` comment variants.
C117 includes `lev` precision promotion. The shared msfty coordinate recipe accepts
331 or 332 existing meridional positions without changing their number.
The DR recommendation to add `seasurface:axis=Z` is explicitly waived under C073b;
C073 continues removing that marker when the CF vertical-coordinate diagnostic
selects it.

### Basin-transport latitude at 331 or 332 positions

C070 handles only the eight g112/hyb transport products hfbasin, hfbasinpmadv,
hfbasinpmdiff, htovgyre, htovovrt, sltbasin, sltovgyre and sltovovrt. Their target
is `lat(lat)`, `lat_bnds(lat,bnds)` and science `(time,basin,lat)`, with
`lat:standard_name="latitude"`, `lat:units="degrees_north"`, `lat:axis="Y"` and
`lat:long_name="Latitude"`. Only msfty retains `rlat`/`grid_latitude`/`degrees`.
Keep the native size: 331 for the reviewed CM7 profile, 332 for the legacy profile.

For the original 331-row y/x representation, validate all 299 retained latitude
values against the supplied native T-column 97 reference. Restore the 32 initial
zero placeholders only when every science value on those rows is masked across
all times/basins. Retain every row and use conventional midpoint bounds with
adjacent half-step outer limits, without extending to the poles. Squeeze only
x=1 in one verified atomic rewrite. Keep the 332-point endpoint convention.

Files produced by the former 331-point rlat correction are also accepted. Select
C070 on the exact missing-latitude diagnostic; rename rlat/rlat_bnds to lat/lat_bnds
and correct CF attributes in place when x is already absent. Preserve the existing
native centers, conventional bounds, zero-longitude auxiliary, science, basin/sector,
packing, compression and chunking. No science read or full-file rewrite is needed
for this metadata-only recovery. Replaying C070/C103 on a conforming result is a no-op.

### Missing horizontal coordinates on reviewed NEMO grids

C119 uses one offline JSON extracted from the supplied eORCA1.4.2 domain,
with independent exact arrays and checksums for each grid:

| Product | grid_label | NEMO points | Latitude source | Longitude source |
|---|---|---|---|---|
| sfx_tavg-u-hxy-sea | g168 | U | gphiu[0] | glamu[0] |
| sfy_tavg-u-hxy-sea | g169 | V | gphiv[0] | glamv[0] |
| sob_tavg-u-hxy-sea, tob_tavg-u-hxy-sea | g112 | T | gphit[0] | glamt[0] |

Require `time,y,x` with `y=331,x=360`, matching scientific identity and a
latitude/longitude reference in the field's `coordinates` attribute. For sob/tob,
create canonical `latitude(y,x)`/`longitude(y,x)` directly and set
`coordinates="latitude longitude"`. Existing matching nav_lat/nav_lon aliases
are renamed; conflicting arrays are refused before writing. Sfx/sfy retain the
requested nav_* or canonical aliases. U, V and T coordinates are never interchanged.

The shared writer appends/renames the small coordinate arrays in place, preserving
science values, masks, packing, compression and chunking without reading/copying
the main matrix. The JSON is about 11 MiB; the 502 MiB domain NetCDF is not needed
at runtime. The extraction script retains its original filename and CLI:
`scripts/extract_nemo_u_reference.py SOURCE.nc NEW_OUTPUT.json`. C119 retains the
legacy internal fix name `inject_nemo_u_coordinates` so existing plans still load.

### Missing XIOS vertices on sfx, sfy, sob and tob

C119v copies the existing XIOS vertex arrays from the reviewed local donor files:
`sfx <- hfx` (U/g168), `sfy <- hfy` (V/g169), and `sob/tob <- areacello` (T/g112).
It adds `vertices_latitude(y,x,vertices)` and `vertices_longitude(y,x,vertices)`,
with fixed `vertices=4`, then sets latitude:bounds="vertices_latitude" and
longitude:bounds="vertices_longitude" on the existing geographic coordinates.
Both nav_* and canonical coordinate names are accepted; selected horizontal
renames run first. Central coordinates, scientific values/masks, attributes and
compression/chunking are preserved. New vertex arrays are appended directly;
conforming replays do not write anything.

`fixes/nemo_vertices_reference.json.gz` is a portable JSON reference (~5 MiB),
containing exact donor centers/vertices, per-array checksums and provenance.
Donor NetCDF files are not opened during parallel application. The extractor
`scripts/extract_nemo_vertices_reference.py --hfx HFX.nc --hfy HFY.nc
--areacello AREACELLO.nc --output NEW_REFERENCE.json.gz` reproduces it without
copying science fields. It reads only the 14 exceptional hfy cells at every time.

Before copying vertices, compare centers on every donor cell with usable nonzero
coordinates (90074 U cells; 90075 V/T cells), using a 1e-4-degree absolute tolerance
and periodic longitude differences. Reject other mismatches. The sole exception
is sfy at y=330,x=196..209: require the exact native V centers on these cells and
missing scientific data at every target time. Extraction already verified all
120 donor hfy records there. No general masked-cell exception is allowed.

The source vertex arrays contain 29085 zero quadruplets per grid. They are copied
verbatim, without altering centers or constructing missing polygons. Adding the
bounds links does not establish complete geometric CF/CMIP7 conformity; subsequent
QA can still flag center/bounds inconsistencies inherited from this representation.

### The 331-row msfty profile with empty southern rows

C025 now handles the reviewed CM7 profile whose first 32 `nav_lat` values are
zero placeholders. It retains all 331 rows and every scientific value/mask.
The small `fixes/msfty_latitude_reference_331.json` stores the full supplied
`gphit[0,:,97]` profile, with provenance and a SHA-256 checksum. This is a separate
reference from the older 332-row profile.

Before restoring any latitude, the recipe requires the remaining 299 coordinates
to match that reference exactly and checks that rows 0–31 are missing for every
time, basin and depth. It reads only those rows, one time record at a time, and
refuses the correction if any valid value is found. Only the 32 placeholders are
replaced. The genuine equatorial zero and all other coordinates are preserved.
The resulting `rlat(rlat)` has `standard_name="grid_latitude"`, `units="degrees"`,
`axis="Y"` and `long_name="Grid Latitude"`; its last value remains
`88.94849723000922`, without the legacy 89.36999 terminal adjustment.

C025 reuses C103's bounds method to add `rlat_bnds(rlat,bnds)`: internal midpoints
and the adjacent half-step at each end, without forcing pole values. These are
conventional one-dimensional bands, not native NEMO transport-section boundaries.
C103 also accepts this exact 331-row reference if only the bounds are missing in
a later iteration. Other 331-point grids are not inferred or extrapolated.
The previous 332-point branch and its approved terminal convention remain intact.

### Missing categorical coordinates for four ice passages

C035 reuses the existing C105 `fix_ice_passage_sector` method for the three
reviewed `siareaacrossline`, `simassacrossline` and `sisnmassacrossline` products.
When only the `line=4` dimension exists, there is no text variable to copy.
The method now creates `char sector(line,strlen)` with `strlen=21`,
`sector:standard_name="region"`, `sector:long_name="ocean passage"`, and sets
the main variable's `coordinates="sector"`.

The supplied order is retained: `fram_strait`, `canadian_archipelego`,
`barents_opening`, `bering_strait`. Creation requires the scientific comment to
confirm those four numbered passages in that order. `canadian_archipelego`
retains the spelling requested by the CMIP7 vocabulary. All raw scientific values,
masks, attributes other than `coordinates`, and compression remain unchanged.
The labels and string dimension are appended directly; the `line=4` dimension
is neither renamed nor resized. Replaying is a no-op.

The current WCRP checker requests `sector` for `siline`, although the coordinate
table's `out_name` is `line`. Using the auxiliary `sector` avoids recreating
`char line(line,strlen)` and its CF shared-name diagnostic, and remains consistent
with the existing duplicate-label cleanup. No latitude coordinate is invented for
these categorical passages. Reference: CMIP7_coordinate.json `siline` at
https://github.com/WCRP-CMIP/cmip7-cmor-tables/blob/main/tables/CMIP7_coordinate.json
and CF 1.11 section 5 at
https://cfconventions.org/Data/cf-documents/requirements-recommendations/conformance-1.11.html .

C120 corrects the exact vocabulary diagnostic for global
`nominal_resolution="150 km"` by setting `nominal_resolution="100 km"`.
This is a direct metadata edit; grid labels, filenames, coordinates and scientific
arrays are preserved. A conforming replay is a no-op, while a different or missing
current attribute is refused rather than silently overwritten.

### Decision and exit codes

`classify` looks only at severity-3 findings to decide what the loop does next:

| Exit | Decision | Meaning | Next step |
|---:|---|---|---|
| 0 | `clean` | nothing blocking left | continue the pipeline |
| 10 | `known` | every blocking finding has an implemented fix | `plan` + `apply`, then QA again |
| 20 | `new_rule` | a finding has no rule, a `review` rule, or a rule whose fix is not written yet | write or review a rule ([CONTRIBUTING.md](CONTRIBUTING.md)) |
| 21 | `blocked_upstream` | a finding must be fixed by the model/simulation side | report it upstream |
| 30 | `no_change` | same fixable findings as `--previous`: a fix does not work | tool bug, investigate |
| 40 | `checker_error` | the QA checker crashed on some files, for a reason not known to be a checker bug | fix the QA run first |

`--final` is the last gate before publication: deferred and other-step findings then block too.
`--json` prints a machine-readable summary. `classify` also writes it next to the report, as
`<report>.classify.json` (decision, counts per severity and outcome, blocking findings and files per outcome,
checker errors), so tools following the loop (e.g. `etb todo`) can show it without classifying again.

Checker crashes known to come from a bug in the checker are listed apart and do not give 40: they are waived by
rules in `rules/checker_errors.yaml` (`applies_to: checker_error`), which only ever match crashes. Today: E001,
compliance-checker [#1374](https://github.com/ioos/compliance-checker/issues/1374), `check_geographic_region` on
flag-encoded region variables (`basin`); the other checks of those files did run. Any other crash still gives 40.

### Scoped waivers

`rules/waivers.yaml` holds findings decided to be false positives **in a given context**: each rule restricts the
dataset id (`CMIP7.<realm>.<variable>.<branding>.<frequency>.<region>.<grid>.<source>.<experiment>.<variant>`) at
least to the experiment the decision was taken for, and records the scope, the decision and the reason. The same
finding in another experiment is not waived: it shows up again, and someone decides again (widening the scope if it
holds). Today: W001 (3hr series of `historical` starting in 1950), W002/W003 (climatology dates and the "gap"
between two climatologies of `historical`).

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
  never alias a simulation file. For a file that is meant to be hard-linked (a WORK file already linked into the
  DRS, when preparing a new version), `--break-hardlinks` copies it first: WORK gets the fix, and the other links
  (the published version) keep the previous content. The fixlog records `hardlinks_broken`.
- **Structural changes** (dimension order, singleton removal, bounds merges) are combined into **one** rewrite per
  file. The output is verified against the source, then atomically replaces it.
- When a fix changes the grid label, the file is renamed to match in the same
  application, without waiting for another QA report to select C097. Existing
  destination files are never overwritten.
- `--only-under DIR` refuses the whole plan if any file is outside `DIR`.
- **A file that fails does not stop the run**: its error and traceback are printed and recorded in the fixlog
  (`"status": "failed"`), and the other files are processed. The final summary counts `failed` files and `apply`
  exits with 1 if there is any. `--fail-fast` stops at the first failing file instead.

Each run appends one JSON line per file to the fixlog: tool version, catalogues used, rules digest, every step
applied, write mode, timings, and any file rename. Run with `etb run` in the esgf-toolbox image, each line also
carries `etb_run_id`, which points to that run in the image's run log (`null` otherwise). A detailed `<fixlog>.events.jsonl` is written alongside, flushed as the run goes,
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
| `consistency.yaml` | cross-dataset consistency |
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

## Basin region mask

The fixed 2-D `basin_ti-u-hxy-u` mask is handled by the shared C101/C101w recipe:
int32 codes 0..10, typed `flag_values`, the approved `flag_meanings`, and `units=1`.
The CF canonical-units diagnostic selects this conversion too; it is not waived
before inspecting the encoding. An already-correct mask is verified without
another write. Numeric basin axes in transport products use a separate recipe.

## Deduplicate ATM/SRF overlaps

Some products are written by both the atmosphere (ATM) and surface (SRF) components.
At the end of `apply`, the tool automatically keeps the preferred component and
**deletes the secondary file**, only when its matching preferred file exists and
passes the identity/coverage checks. This runs independently of QA diagnostics
and rule selections, including when there are no QA fixes in the plan:

| Product | Kept | Deleted when both exist |
|---|---|---|
| `areacella`, hxy-u | ATM | SRF |
| `evspsbl`, hxy-u | ATM | SRF |
| `snc`, hxy-lnd | SRF | ATM |

New plans retain `deduplicate_root` from the report's `info.parent_dir`;
`--map-path` updates that root along with file paths. `--only-under` also checks
this scope before any write. Older plans recover a single `CMIP7/ATM|SRF` tree
from their paths, or use `--only-under` when it directly names a simulation tree.
The tool never scans an arbitrary parent containing other simulations as a
fallback. A replay skips already removed secondary copies only after checking
the existing preferred file; its planned fixes are never redirected to the keeper.

The phase runs sequentially after all file workers finish. If a planned fix
failed on either file of a pair, both are retained. `--dry-run` previews deletions
without changing files. Full paths and decisions are written to
`<fixlog-stem>.deduplicate.jsonl`; the progress journals also record the phase.
The final counters include `duplicates_deleted`, `duplicates_would_delete`,
`duplicates_missing_keeper`, `duplicates_failed` and `duplicates_already_removed`.

To run this operation separately, without a plan:

```bash
esgf-fix-tool deduplicate "$simulation_dir" --dry-run --fixlog deduplicate.preview.jsonl   # what would be deleted
esgf-fix-tool deduplicate "$simulation_dir" --only-under "$simulation_dir" --fixlog deduplicate.jsonl
```

**Deleting requires `--only-under DIR`**, naming the directory deletions are allowed in (the simulation directory or
a parent). Without it, or if the simulation directory is not inside `DIR`, nothing is deleted. `--dry-run` does not
need it.

A pair is only deleted when both paths are identical apart from the ATM/SRF folder, and the netCDF identity
(`source_id`, `experiment_id`, `variant_label`, `branded_variable`, `frequency`, `region`, `grid_label`) and time
coordinate match. A mismatch, an unreadable file or a symlink marks the pair `failed` and deletes nothing; the exit
code is then 1. A separate run can be used before QA and planning. Normal `apply`
already includes this operation and needs no additional command. Model,
experiment and realization must agree within a pair but are not fixed to one simulation.

## Development

```bash
git clone https://github.com/ESPRI-Mod/esgf-fix-tool && cd esgf-fix-tool
uv sync
uv run pytest -q
uv run ruff check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch workflow and how to add a rule or a fix.
Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat(apply): …`, `fix(surface): …`, `chore: …`).

## History

This tool was built during the CMIP7 pilot on `CM6-ESMCO2-FT-esm-pi-s.01` (October–November 2026). The per-rule
decision logs, TGCC procedures and test recipes from that pilot are kept at the
[`pilot-2026-11`](https://github.com/ESPRI-Mod/esgf-fix-tool/tree/pilot-2026-11) tag; the later decision logs
(2026-11-09 to 2026-11-12) at commit
[`3efff8e`](https://github.com/ESPRI-Mod/esgf-fix-tool/tree/3efff8e/docs).

### Continuing after a recipe preparation error

The CLI's normal continuation mode now also preserves independent corrections
within a file. If a recipe fails in the read-only preparation phase, its entire
private preview is discarded. The remaining fixes are prepared again from the
original file; declared dependants and coupled hybrid corrections are blocked.
Only the validated remainder is committed, with at most one grouped rewrite.

The fixlog marks the file `partial`, lists failed/blocked steps and their
tracebacks, and the command exits with an error status so unresolved work stays
visible. Failures during writing or final verification are never retried this
way. `--fail-fast` retains the original whole-file refusal. The Python API uses
this behavior when `continue_on_error=True` is requested.


### Retired global chemistry climatologies

The cleanup stage used by `deduplicate` and ordinary `apply` also removes exactly
`ch4_tclm-u-hm-air` and `n2o_tclm-u-hm-air` in the simulation's CHM tree. These are
explicitly retired products, not duplicate copies: no keeper is required. Validate
their filename and NetCDF identity first, reject symbolic links or mismatches,
and log them separately with `operation="unused-product-removal"`. Deletions obey
the same `--only-under` boundary and `--dry-run` policy as component cleanup.
Other CH4/N2O products, including `tclm-p19-hxy-air`, remain untouched by this policy.

`plan` reserves these products for cleanup without modifying them. Ordinary
`apply` skips their scientific recipes and removes them during the cleanup stage;
replaying a stale plan recognizes already-removed retired products. Sharded apply
keeps cleanup disabled, as for ATM/SRF duplicates: run `deduplicate` separately.
The cleanup summary distinguishes retired products from ATM/SRF duplicates.

Monthly climatology reconstruction accepts the reviewed spatial clause
`area: mean where air` and keeps it alongside `time: mean within years time:
mean over years`. Complete monthly years and the existing missing-value checks
remain required; the statistical operation is unchanged.
