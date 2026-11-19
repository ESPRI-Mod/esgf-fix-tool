# Changelog

All notable changes to esgf-fix-tool are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/).
A new or changed rule is at least a minor version: it changes what the tool does to files.

## [Unreleased]

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
