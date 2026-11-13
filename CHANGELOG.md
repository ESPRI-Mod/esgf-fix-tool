# Changelog

All notable changes to esgf-fix-tool are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/).
A new or changed rule is at least a minor version: it changes what the tool does to files.

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
