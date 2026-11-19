# Changelog

All notable changes to esgf-fix-tool are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/).
A new or changed rule is at least a minor version: it changes what the tool does to files.

## [Unreleased]

### Added

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

- `apply --allow-hardlinks`: explicitly permit direct edits shared by every physical alias.
  Group planned hardlink aliases into one job, merging their selected steps and logging the aliases.
  Shared structural rewrites and filename changes are refused before writing in this mode.

### Changed

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
