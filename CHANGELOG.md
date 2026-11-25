# Changelog

All notable changes to esgf-fix-tool are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/).
A new or changed rule is at least a minor version: it changes what the tool does to files.

## [0.13.0] - 2026-11-25

### Added

- `fill_missing_time_steps` (C122, C122b, C122c): the missing 3-hourly steps of cltcalipso (IPSL-CM6-ESMCO2
  historical) are inserted with their exact time and a fully missing field (`_FillValue`), as the model team decided
  on 2026-11-25 while they look for the cause. The fix finds the missing steps itself, against the axis of the file
  name's time range; it refuses anything else (values off that axis, duplicates, time bounds, other variables on the
  time dimension, no `_FillValue`, more than 1% of the steps missing, other hard links without `--break-hardlinks`).
  The existing values are copied unchanged and checked after writing; the fixlog (`filled_steps`) and the global
  `history` list the filled steps (`comment` belongs to the CV, a variable `history` is forbidden). The insertion
  changes the length of the time dimension, which the grouped rewrite refuses by design: `apply` runs it on its own,
  after the file's other fixes. It resolves the TIME001 axis shift, the TIME003 end of range and the 6-hour gaps
  between files.

### Fixed

- A final QA on the DRS names datasets by their directories (`CMIP7.<activity>.<institution>.<source>....<grid>.v<date>`),
  with no realm: no rule scoped to datasets matched (W001-W003, C107a, ...), so findings decided in the loop blocked
  the final gate. The report now gives them their WORK form back
  (`CMIP7.<realm>.<variable>.<branding>.<frequency>.<region>.<grid>.<source>.<experiment>.<variant>`), the realm
  found from the file names in the WORK directory: `--work`, or by default the directory the latest `../qa_<N>`
  report checked. A realm not found keeps the DRS id: the scoped rules do not apply, the finding still blocks.

## [0.12.0] - 2026-11-24

### Changed

- Reuse the cleanup command for explicitly retired CHM ch4/n2o_tclm-u-hm-air
  products, without requiring a duplicate keeper. This applies to every simulation with a `CMIP7/CHM` tree (any
  source, any experiment): `deduplicate` and the clean-up stage of an ordinary `apply` delete them. Check identity and deletion
  boundaries, preserve dry-run behavior, and log separate obsolete-product counts.
  Skip recipes on these products during ordinary apply and handle stale-plan replay.
- C066/C106 reconstruction preserves area:mean where air and accepts the reviewed
  ch4/n2o/o3 p19 climatologies; retain complete-year and numerical checks.
- C014a reuses hybrid normalization for CHM al/alh products, including coefficient
  dimension repair before normalization. Shared recipes accept compound atmospheric
  realms (atmosChem, aerosol, atmos) while retaining full reference/vector checks.
- C003 now changes standard to proleptic_gregorian for the observed TIME003a
  recommendation. Validate epochs, times and temporal bounds before editing only
  calendar attributes; remove the obsolete gregorian-to-standard conversion.
- Extend C112's existing exact registry-comment replacement to the 35 branded
  products (24 grouped diagnostics) audited in the 2026-11-24 INCA report.

## [0.11.0] - 2026-11-24

### Added

- `rules/waivers.yaml`: scoped waivers, false positives decided for one context only (at least the experiment, via the
  dataset id), with their scope, decision and reason. W001: 3hr series of `historical` starting in 1950 (the Data
  Request asks for 3hr from 1950 only). W002: climatology dates (TIME001 midpoints) of `historical` `tclm`. W003: the
  continuity "gap" between two climatologies of `historical` `tclm`. Gaps of ordinary series stay S031. Decided at
  the meeting of 2026-11-24 on the pilot simulation (IPSL-CM6-ESMCO2 historical).

## [0.10.0] - 2026-11-23

### Fixed

- C075 says which check refused a variable: an unreviewed standard_name/units pair (with the reviewed one), or an
  unreviewed `cell_methods` (shown). The former message only showed the standard_name and units, which were fine.
- C011a: set cell_fraction:standard_name to
  area_fraction when absent, empty or still cell_fraction. The invalid-name and
  missing-name diagnostics now select the same guarded attribute recipe, instead
  of deleting the attribute. Preserve other values and all numerical arrays.


- Plan now prepares current NetCDF files read-only using apply's dry-run pipeline,
  after path mapping. Count confirmed changed steps/files; remove fully conforming
  files while retaining selected no-op prerequisites in changed files. Record
  preparation in plan JSON; retain and label failures/inaccessible files instead
  of counting them as conforming. Add --report-only for explicit offline candidate
  planning and --break-hardlinks to match apply's preparation policy.

- C086 repairs the former NEMO U/V/T long_name captions on the matching injected
  coordinate grids. C119 now assigns lowercase latitude/longitude captions when
  injecting coordinates, preserving all coordinate and scientific values.

- Extend C083 to rsus land products on g243 and g245 as well as g111, using
  the same exact guarded cell_methods replacement without changing arrays.

- Make the plan summary explicit: selected automatic fixes and affected files,
  distinct ignored QA diagnostics, pending/external-work diagnostics with status
  counts, and automatic steps omitted by filters. Keep waived/review-only files
  out of the executable count and retain read-only, report-based planning.

- C101/C101w accept both reviewed g112 basin-mask sizes: CM6 (332,362) and
  CM7 (331,360). Preserve each native grid, codes and missing-point mask;
  keep one-dimensional basin coordinates outside this conversion. Report the
  observed dimensions and metadata when preflight rejects a mask.

- C033 also recognizes the vegtype coordinate ID; C048 accepts both vegtype and
  modelvegtype and runs the existing category-dimension rename before sector
  alias creation. Keep gppVgt/laiVgt fields reported as (time,lat,lon) in review:
  missing vegetation-category data cannot be repaired by a permutation.
- C075 accepts instantaneous tslsi surface temperatures after validating their
  standard_name and units, without depending on a sampled cell_methods list.
- C002a includes the exact reviewed fVegLitter comment and normalizes only its
  escaped underscores, preserving the complete scientific text.

## [0.9.0] - 2026-11-20

### Added

- P003 (`rules/packing.yaml`): FILE004d (data variable chunking, chunks under ~4 MB) goes to the repack step
  (`other_step`), like FILE004a-c: 2489 of the 2509 unmatched severity-3 findings of a CMIP7 simulation's third
  QA iteration.

- Add C119v to reuse existing XIOS cell vertices for sfx/sfy/sob/tob from
  hfx/hfy/areacello on their respective U/V/T grids. Bundle exact arrays and
  checksums in a 5 MiB JSON gzip reference; append vertices/bounds links directly
  without changing centers or scientific data. Restrict the sfy exception to
  14 exact northern cells masked across all target records; all 120 hfy donor
  records were checked during extraction. No donor access during parallel apply.
- Add a reproducible read-only donor extraction script for the vertex reference.

- C119 adds the explicitly sourced eORCA U-point gphiu/glamu arrays as missing
  sfx nav_lat/nav_lon on g168 (331x360). Store exact double values and checksums
  in a 3.6 MiB JSON reference; the large domain NetCDF is not required at runtime.
  Append directly without streaming the science matrix, retain existing matching
  coordinate aliases, and reuse one step for related CF/CMIP7 diagnostics.
- Add an offline extraction script to reproduce the U coordinate reference.


### Changed

- The packing rules released in 0.8.0 as C117 / C118 are renumbered P001 / P002, freeing C117 / C118 for the
  reviewed lev precision promotion and g131 latitude reversal below. They only route findings to the repack step,
  so no fixlog refers to them.

- Correct C070's 331-point basin transports to true latitude lat(lat),
  latitude/degrees_north metadata and lat_bnds, matching the existing 332-point
  convention. Recover former rlat/rlat_bnds outputs directly without reading or
  rewriting science; retain all 331 positions, centers, bounds, basin/sector and
  storage settings. Select the missing-latitude diagnostic on the eight products.
  Keep msfty on rlat and preserve the separate 332-point terminal convention.
- Extend C119 with exact V-point coordinates for sfy and T-point coordinates for
  sob/tob from the supplied eORCA domain. Retain the original U arrays unchanged
  in the same portable JSON; publish bottom coordinates as latitude/longitude
  and update their association directly without copying scientific data.

- Add C121 for the reviewed 720-column g131 longitude rotation on areacellr,
  rivi and rivo. Map all longitude-dependent arrays in bounded tiles, including
  packed data/masks, and combine with C118 in a single verified atomic rewrite.
  Preserve the terminal 360-degree bound and make conforming replays a no-op.
- Extend C059 to the reviewed regular g245 grid and derive shared bounds from
  nlon. Validate centers/existing edges within 1e-8 degree before a direct
  bounds-only edit; retain the separate CM6 g111 geometry and rounding behavior.
  Keep polygon grids excluded and preserve all scientific arrays/storage.

- Add C120 for the reviewed nominal_resolution=150 km to 100 km replacement.
  Update only the exact global text, preserve its NetCDF string type, and apply
  directly without copying scientific arrays or restricting model/experiment.

- C035 reuses the existing ice-passage label recipe when line=4 exists without
  a label variable. Validate the four numbered passages in the scientific comment,
  then append sector(line,strlen), region metadata and the main coordinates link.
  Keep the dimension and all scientific values/masks/compression unchanged.

- Extend C025 to the exact 331-row msfty profile with 32 zero placeholders.
  Validate all retained latitudes against the supplied T-column 97 reference and
  require the corresponding science rows to be masked across all times/basins/depths.
  Restore only those coordinates, preserve all rows/raw data, and reuse C103 for
  midpoint bounds with ordinary adjacent half-steps at both ends. Retain the
  separate legacy 332-row endpoint/bounds convention.

- Extend existing comment normalization to fNleach, mrsow and rivi; include lev
  in lossless double promotion and order it before hybrid processing.
- Reuse edt/evu centering for both 80-to-79 and 96-to-95 references, including
  exact real-precision CM7 inputs, then restore center bounds and formula metadata.
- Accept the existing 331-row CM7 msfty grid in the shared coordinate recipe.
  Waive the inverse seasurface axis=Z recommendation pending the DR change.
- Add scoped C118 for the three reviewed g131 latitude reversals, streaming all
  latitude-dependent arrays into the same atomic output and verifying their values.

- Extend C074/C074a and C102 to the supplied CM7 95-center / 96-interface
  references. Repair paired coefficient-size mismatches before normalization,
  preserve already matching coefficient values within decimal dump precision,
  and retain scientific data and compression. Store explicit coefficient bounds
  and dump provenance; keep al/alh bounds semantics and numerical safeguards.

- Continue mode isolates failed recipe preflights, discards their private changes,
  blocks dependants and applies the validated remainder once. Partial results
  remain errors in logs and exit status; fail-fast behavior remains available.
- Add reviewed CM7 float32-to-float64 coordinate promotions, cell histogram
  permutations, category-index aliases and cell-indexed edt/evu support.
  Preserve coefficient reference validation and scientific arrays outside the
  already approved transforms. Distinguish rectilinear g245 from cell meshes.

- C059 selects g111 only in legacy ATM/SRF and published DRS identifiers; g243/g245 polygon longitudes are never scheduled for regular-grid normalization. Runtime numeric safeguards remain unchanged.

- C086 sets lowercase latitude/longitude long_name on reviewed cell-indexed g243/g245 grids, before or after coordinate renames, while preserving rectilinear and ocean conventions.

- C067/C071/C072/C088 support cell-indexed g243/g245 icosahedral coordinates and six-vertex bounds. Preserve cell, polygon values, padding and scientific arrays; normalize names directly without a full rewrite.

- C107 now adds `lev:computed_standard_name = "air_pressure"` for the CF §4.3.3 diagnostic,
  without model or experiment restrictions. Shared hybrid recipes also set and retain it;
  the former CMIP7 recommendation no longer triggers attribute deletion.

## [0.8.0] - 2026-11-20

### Added

- Known checker errors: `rules/checker_errors.yaml` waives checker crashes known to be a checker bug
  (`applies_to: checker_error`; these rules only match the report's error section, never findings). `classify`
  lists them apart and only stops at `checker_error` (40) for the other crashes. E001: compliance-checker #1374,
  `check_geographic_region` on flag-encoded region variables (`basin`), which stopped every QA iteration at 40.
- `classify` writes its summary next to the report, `<report>.classify.json`: decision, counts, blocking findings
  and files per outcome, known and unknown checker errors (also printed by `--json`).
- C117 / C118 (`rules/packing.yaml`): FILE004b / FILE004c (time coordinate / bounds chunking) go to the repack
  step (`other_step`), like FILE004a: most of the unmatched findings of a CMIP7 simulation's second iteration.

### Changed

- `classify` lists the unknown checker errors by check and message.

## [0.7.1] - 2026-11-19

### Fixed

- `apply --resume` expands a quoted pattern itself (`--resume 'run/fixlog*.jsonl'`); a pattern that matches
  nothing is refused. In 0.7.0, the documented `--resume run/fixlog*.jsonl` failed as soon as the shell
  expanded it to several files ("unexpected extra argument").

## [0.7.0] - 2026-11-19

### Added

- `apply --resume FIXLOG...`: skip the files that earlier fixlogs of the plan record as done (the latest record
  of a file wins); failed files are retried unless `--skip-failed`. For a plan cut by a job time limit.
- `apply --shard k/K`: only share k of K of the plan's files (every K-th in path order, computed on the whole
  plan), to split one plan over K jobs with their own fixlogs. Refused if plan entries share a file or a rename
  target. No automatic ATM/SRF deduplication with `--shard`. Each fixlog line records the shard.
- Before starting, `apply` removes the leftover temporary files (`.<name>.*.fixtmp`, `.linkbreak`) of
  interrupted rewrites of the files it is about to fix; a dry run only counts them.

## [0.6.0] - 2026-11-18

### Added

- `apply` deduplicates ATM/SRF overlaps automatically at the end, after the workers have closed every file: the
  plan keeps the report scope (`deduplicate_root`, from `info.parent_dir`), limited to one ATM/SRF tree inside
  `--only-under`. A pair whose planned fix failed keeps both files; a copy already removed is skipped on replay.
  Logged in `<fixlog-stem>.deduplicate.jsonl`. The separate `deduplicate` command is unchanged.
- A readable text journal next to the events log: `--fixlog fixlog.jsonl` also writes `fixlog.events.log` (file,
  phase and fix messages, parameters, sampled I/O progress, tracebacks), also with `--quiet`.
- Tracebacks of failed files are recorded in the events log.

### Changed

- The events log samples array I/O at 25% steps (at most five entries per large variable and operation) instead of
  every block; phases, fix results and errors are all kept. Console messages name the file, not its full path.
- `deduplicate` no longer requires `source_id = IPSL-CM6-ESMCO2`: any model with the same ATM/SRF layout.
- Rules and fixes: C107 adds `lev:computed_standard_name = "air_pressure"` (shared hybrid recipes keep it) instead
  of removing it; the basin region mask is converted to int32 (`convert_basin_region_mask`) instead of waived;
  scalar thetao/mrsol/clcalipso bounds from the bundled DR values; eight registry label mappings, NUL-padded labels
  accepted; a grid-label correction renames the file in the same operation; 80-interface `alh` coefficients
  (C074/C074a); fixes in about fifteen fix modules. Rules and recipes no longer filter by model or experiment.

## [0.5.1] - 2026-11-17

### Fixed

- `deduplicate` no longer refuses ATM/SRF pairs whose time axes only differ by an equivalent calendar name:
  `gregorian`/`standard` (or no calendar), `365_day`/`noleap`, `366_day`/`all_leap`, and `standard`/
  `proleptic_gregorian` when the reference date and every time value fall on or after 15 October 1582. Seen on a
  CM6-ESMCO2 historical simulation, where all 20 pairs failed (SRF `gregorian`, ATM `proleptic_gregorian`). Other
  calendar differences still keep both files, now with the error `Calendars differ (a / b)`.

## [0.5.0] - 2026-11-16

### Added

- `apply --workers N` (`-j N`): process up to N independent files at once, in separate spawned processes. Each file
  keeps its ordered fixes and single combined rewrite; the parent writes both JSONL logs in completion order and
  records each `worker_pid`. Duplicate paths, hardlink aliases and conflicting rename targets are refused before
  anything is applied. Works with `--dry-run`, `--fail-fast` and `--break-hardlinks`.

### Changed

- In an interactive terminal, `apply` shows one live view (totals and one current-stage line per active file)
  instead of the detailed messages, also without `--workers`. Batch or redirected output keeps the detailed
  messages, with one summary per grouped rewrite instead of intermediate percentages. The events log is unchanged.
- C073 (`drop_osurf_axis`) no longer checks the `seasurface` label value, only that it is a scalar string or a
  one-dimensional character label: unexpected or non-ASCII label bytes no longer block removing `axis`.

## [0.4.0] - 2026-11-13

### Added

- `apply --break-hardlinks`: a hard-linked file (e.g. a WORK file already linked into the DRS, when preparing a new
  version) is copied before a metadata update, so its other links keep the previous content. Without it, such files
  are still refused. The fixlog records `hardlinks_broken` for metadata updates and structural rewrites alike.

## [0.3.0] - 2026-11-13

### Added

- `etb_run_id` in every fixlog line and every `deduplicate` log entry: the `ETB_RUN_ID` set by `etb run` in the
  esgf-toolbox image, linking the line to the run that wrote it (`null` outside `etb run`). The `deduplicate` log
  also records the tool version, and failed-file fixlog records now include the rules digest.

## [0.2.0] - 2026-11-13

### Added

- `deduplicate`: delete the secondary copy of approved ATM/SRF overlaps (`areacella` and `evspsbl` hxy-u kept from
  ATM, `snc` hxy-lnd kept from SRF) after checking paths, netCDF identity and time coordinate; `--dry-run`, JSONL log.
  Deleting requires `--only-under DIR`, which must contain the simulation directory.
- Reviewed fixes from the QA v4/v5 iterations of the pilot, among them: conventional lat/rlat bounds for the NEMO
  transport profiles and msfty, edt/evu interpolation from 80 interfaces to 79 centers, ta/zg lev normalization and
  `axis_nbounds` rename, vertical hfx/hfy sums, retired variables and scoped bounds removal, severity-2 metadata
  recommendations and DR cell_methods order. Now 233 rules (141 reviewed `C` rules) and 62 fix functions.

### Changed

- `apply` no longer stops at the first failing file: each error is printed with its traceback and recorded in the
  fixlog, the other files are processed, and the exit code is 1 if any file failed. `--fail-fast` restores the
  previous behaviour.
- C001 removes `history` from every variable of a selected file, including unreported auxiliaries, with a final
  sweep after all recipes. Global `history` is kept.

## [0.1.0] - 2026-11-12

First release, from the CMIP7 pilot on `CM6-ESMCO2-FT-esm-pi-s.01`
(pilot notes at the [`pilot-2026-11`](https://github.com/ESPRI-Mod/esgf-fix-tool/tree/pilot-2026-11) tag).

### Added

- `classify`: classify every finding of a full esgqa report and exit with the loop decision
  (0 clean, 10 known, 20 new_rule, 21 blocked_upstream, 30 no_change, 40 checker_error); `--final` gate, `--json`.
- `plan`: write the per-file fix plan without opening netCDF files; select rules by ID or catalogue,
  `--exclude-rule`, `--map-path` from source to staging.
- `apply`: in-memory preflight, then direct metadata updates or one verified structural rewrite per file;
  hardlinked targets refused, `--only-under`, `--dry-run`, filename sync for grid-label fixes.
- Fixlog (one JSON line per file) and a flushed `.events.jsonl` sidecar for following long runs.
- 218 rules shipped inside the package: 126 reviewed `C` rules in 14 thematic catalogues and 92 historical
  `S` fallbacks, plus 53 fix functions.
- Rules digest: a sha256 of the loaded rule content, recorded in plans, fixlogs and `classify --json`.
