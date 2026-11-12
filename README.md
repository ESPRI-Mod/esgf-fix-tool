# esgf-fix-tool

Latest maintenance: [hybrid dependencies and catalogue cleanup](docs/hybrid-dependencies-and-catalogues.md).

Monthly tclm series now use [the per-file numerical climatology reconstruction](docs/monthly-climatology.md)
(C106, shared with C065/C066). Regenerate plans before application.

Latest maintenance: [shared basin recipe, text-label deletion, tossq and thematic catalogues](docs/fix-interactions.md).
`src/esgf_fix_tool/rules/post_apply.yaml` is now a compatibility manifest. Rules themselves live in
the thematic YAML files; IDs and the `plan`/`apply` commands are preserved.

Latest approved corrections: [normalized lev/bounds, four ice passages, micron radii,
thermal/plev7c bounds and registry comments](docs/normalized-coordinates-20261109.md).
Regenerate plans. Latitude profile bounds remain in review.

C101 converts only the 2-D `basin_ti-u-hxy-u` mask to int32 with the approved
0..10 flags. The nine 1-D basin axes retain their numeric indices, with the
separately approved region labels in sector.
Regenerate plans. See [the latest reviewed structures and grouped deletions](docs/reviewed-structures-20261109.md).

Follow-up to the 2026-11-06 QA: load `src/esgf_fix_tool/rules/post_apply.yaml` first and regenerate
the plan. See [changes, review cases and the complete TGCC command](docs/post-apply-20261106.md).

Fix CMIP7 netCDF files from an **esgqa report** (`qa_result_*.json`) and a set of **rules**.
Part of the IPSL CMIP7 publication pipeline (QA ↔ FIX loop). Design: [ARCHITECTURE.md](ARCHITECTURE.md).

```bash
uv sync
# what does this report need? (exit code = decision: 0 clean, 10 known, 20 new_rule, 21 blocked_upstream, 30 no_change, 40 checker_error)
uv run esgf-fix-tool classify qa_result_XXX.json -r src/esgf_fix_tool/rules/post_apply.yaml -r src/esgf_fix_tool/rules/variable_attributes.yaml -r src/esgf_fix_tool/rules/standard_name_modifiers.yaml -r src/esgf_fix_tool/rules/units_and_standard_names.yaml -r src/esgf_fix_tool/rules/vertical_coordinate_names.yaml -r src/esgf_fix_tool/rules/required_coordinates.yaml -r src/esgf_fix_tool/rules/coordinate_metadata.yaml -r src/esgf_fix_tool/rules/dimension_order.yaml -r src/esgf_fix_tool/rules/coordinate_values_bounds.yaml -r src/esgf_fix_tool/rules/horizontal_coordinates.yaml -r src/esgf_fix_tool/rules/surface_and_hybrid.yaml -r src/esgf_fix_tool/rules/qa_metadata.yaml -r src/esgf_fix_tool/rules/grid_labels.yaml -r src/esgf_fix_tool/rules/catalogue_fallbacks.yaml
# the fixes it would apply, per file
uv run esgf-fix-tool plan qa_result_XXX.json -o fixplan.json
# apply them: direct metadata updates, or one combined structural rewrite per file
uv run esgf-fix-tool apply fixplan.json --fixlog fixlog.jsonl [--dry-run]
```
`plan` loads every YAML catalogue in the project's `src/esgf_fix_tool/rules/` by default, with
`post_apply` first and historical fallbacks last. Only rules whose action is `fix` and
whose method is implemented produce steps; `review` and ignored findings stay out.
Use the full report, not `.cluster.json`. Planning never opens the NetCDF files.

```bash
# Include only these rule IDs (repeat -r to select more).
uv run esgf-fix-tool plan qa_result_XXX.json -r C095 -r C103c -o fixplan.pressure.json
# All rules except the selected IDs (repeat --exclude-rule to exclude more).
uv run esgf-fix-tool plan qa_result_XXX.json --exclude-rule C103c -o fixplan.other.json
```

`--only-rule ID` remains supported. Exclusions take precedence over inclusions;
unknown IDs are rejected. Existing `-r src/esgf_fix_tool/rules/catalogue.yaml` commands still work:
explicit catalogue paths replace the defaults and retain their supplied order.
If paths and IDs are combined, the IDs select within those catalogues.

Rules:
- `src/esgf_fix_tool/rules/basin_coordinates.yaml`: C032/C078/C096/C101a share `fix_basin_sector`;
  keep basin values, move region semantics to sector and describe basin as units=1 indices.
  C101 is the independent 2-D mask correction.
- `src/esgf_fix_tool/rules/variable_attributes.yaml`: C001/C002/C002a/C112, variable attributes and comments.
  C003 (calendar) is in coordinate_metadata.yaml. The default manifest sets their priority.
- `src/esgf_fix_tool/rules/standard_name_modifiers.yaml`: C004–C006, the three investigated descriptive `standard_name` values.
  Load before the fallbacks, or alone to apply only this correction (example below).
- `src/esgf_fix_tool/rules/units_and_standard_names.yaml`: C007–C017, the reviewed units and invalid-name cases.
  C014 now fixes atmospheric lev:standard_name=x using the mapping confirmed by the user on 2026-11-05.
- `src/esgf_fix_tool/rules/vertical_coordinate_names.yaml`: C018–C020, the three missing-generic-lev messages.
- `src/esgf_fix_tool/rules/required_coordinates.yaml`: C021–C035, missing coordinates with explicit fix/review decisions.
- `src/esgf_fix_tool/rules/coordinate_metadata.yaml`: C036–C039, the four reviewed COORD003 attribute messages.
- `src/esgf_fix_tool/rules/dimension_order.yaml`: C040–C054, reviewed COORD002 permutations, renames and singleton removal;
  C051 remains in review. C050/C052/C053 use the newly approved definitions; C054 removes singleton x from msfty.
- `src/esgf_fix_tool/rules/coordinate_values_bounds.yaml`: C055–C068, reviewed COORD007/008/009 values and bounds;
  DR histogram centers/edges and the five IPSL ice thickness classes are approved fixes.
- `src/esgf_fix_tool/rules/horizontal_coordinates.yaml`: C069–C072, unnecessary horizontal references and curvilinear
  auxiliary names; C070 now converts the eight reviewed rectilinear profiles and applies the approved terminal latitude.
- `src/esgf_fix_tool/rules/surface_and_hybrid.yaml`: C073 removes only categorical `seasurface:axis="Z"` per the user's
  updated DR decision; load before the older fallback waivers. C074 records the two hybrid COORD012
  variants and uses the bundled IPSL reference to align ap/b with lev. Science arrays stay unchanged.
- `src/esgf_fix_tool/rules/qa_metadata.yaml`: C075–C088, reviewed CF and CMIP7 recommendations.
  C076 now adds the user-approved temperature:on_scale for tossq.
  It also holds C099/C100/C105/C106/C108a for text coordinates and related checks.
  See [the report variants and TGCC commands](docs/qa-metadata.md).
- `src/esgf_fix_tool/rules/grid_labels.yaml`: C089–C094, reviewed region and output-grid recommendations.
  See [variants, DRS rename suggestions, and TGCC commands](docs/grid-labels.md).
- `src/esgf_fix_tool/rules/catalogue_fallbacks.yaml`: manifest for the historical triage, split by theme under
  `src/esgf_fix_tool/rules/fallbacks/` and loaded last. These retain the original S IDs and decisions;
  `action: fix` with `fix: null` means `fix_missing`, not an implemented recipe.

Adding a fix: write a function in `src/esgf_fix_tool/fixes/`, decorate it with `@fix("name")`,
then add its reviewed rule to the appropriate thematic catalogue, ahead of the historical fallbacks.

## Follow apply while a large file is being processed

All recipes for a file are first evaluated against an in-memory metadata view. No science
array is copied into that view. The resulting operations are combined before writing:

- Attributes, variable/dimension renames and small auxiliary edits update the opened target
  directly. Its inode is preserved; the writer neither copies nor streams its science fields.
- Permutations, singleton removal and bounds-dimension merges share **one output file**.
  Metadata changes are included in that output; there is no preliminary full-file copy.
- An already-correct file stays read-only. `--dry-run` performs preflight only, without writing
  a temporary NetCDF or testing the physical update. Run QA after the actual application.

Direct updates require an independent staging copy: **hardlinked files are refused** before
mutation. Direct updates are not atomic; an interruption can leave some changes applied.
Structural outputs are verified against the source and atomically replace the target only after
success. The NetCDF library may internally move data when a classic-format header grows;
there is no application-level full rewrite for metadata changes.

The current file is announced before processing. Each recipe displays its rule ID, title,
parameters and status. `Préparé` means its change has passed preflight; `Done` means all
operations and checks for that file succeeded. `Done (simulation)` means preflight only.

```text
[2/584] En cours - .../airmass_....nc
  Fix: C001 - Suppression d'un attribut de variable - {"attr": "history", "var": "airmass"} - En cours
  Fix: C001 - Suppression d'un attribut de variable - {"attr": "history", "var": "airmass"} - Préparé — vérifications en attente
  Phase - Application directe des noms et attributs - En cours
  Fix: C001 - Suppression d'un attribut de variable - {"attr": "history", "var": "airmass"} - Done
```

Interactive terminals update the active line; redirected/container output uses flushed lines.
There is **no periodic message or background timer**. Actual long array loops emit block counters.
`--fixlog fixlog.all.jsonl` also creates **fixlog.all.events.jsonl**, flushed throughout processing.
The per-file fixlog records timings, `write_mode` (`in-place`, `rewrite-once`, `none`) and
`rewrite_count` (0 or 1). `verification_scope` distinguishes metadata/edited auxiliaries from
full mapped-array verification and preflight. `--quiet` hides console progress only.
See [the TGCC procedure and integrity checks](docs/apply-progress.md).

Array copying/comparison uses bounded contiguous C-order slabs, avoiding repeated decompression
of the same time chunks. Direct metadata updates validate headers and affected auxiliaries without
fingerprinting science fields. Renamed coordinates are protected against the reproduced NetCDF-4
rename data-loss bug. A second application does not rewrite already-correct files.

Snapshot before adding standard_name_modifiers.yaml (2026-11-04, report of CM6-ESMCO2-FT-esm-pi-s.01, 584 files): every severity-3 finding has a rule;
665 are fixable today (`history` on variables), 1,044 `fix_missing`, 446 `review`; plus PATH003 (deferred to the DRS
draft) and FILE004a (repack step).

## Test only the standard_name modifier correction

Use the **full** `qa_result_*.json`, not its `.cluster.json`. The full 2026-11-03 report gives
100 findings in 81 files for these rules. They preserve an existing `long_name`, fill it with the
description if absent/empty, and remove `standard_name` only if its current value exactly matches
the expected text. They do not infer `air_pressure`, change units, or modify coordinate values.

```bash
report="/path/to/qa_result_20261103-1557_89a5a6651ffe774d3afdcb2b44b22dde.json"
source_root="/ccc/work/cont003/gencmip7/p86caub/IGCM_OUT/ESMCO2/PROD/esm-piControl-spinup/CM6-ESMCO2-FT-esm-pi-s.01"
staging_root="/path/to/COPY/CM6-ESMCO2-FT-esm-pi-s.01"

uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/standard_name_modifiers.yaml \
  --map-path "$source_root=$staging_root" -o fixplan.standard-name.json
uv run esgf-fix-tool apply fixplan.standard-name.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.standard-name.dry-run.jsonl
uv run esgf-fix-tool apply fixplan.standard-name.json --only-under "$staging_root" \
  --fixlog fixlog.standard-name.jsonl
```

The staging copy contains `CMIP7/ATM/`. `--only-under` refuses the whole plan if any original path
was left unmapped. Direct metadata updates require an independent staging copy;
a hardlinked staging file is refused before mutation. Applying the same plan again changes nothing for already-corrected attributes.
Rerun QA on staging afterwards; other diagnostic families are outside this test.

At TGCC, use the prepared Python environment (no network is needed at runtime). If Python, netCDF4,
PyYAML and Typer are already available in `esgf-toolbox`, the equivalent entry point from this repository is:

```bash
esgf-toolbox env PYTHONPATH="$PWD/src" python -m esgf_fix_tool.cli.main --help
```

Use that prefix instead of `uv run esgf-fix-tool` for `plan` and `apply`. For the complete rule set,
add `-r src/esgf_fix_tool/rules/standard_name_modifiers.yaml` before `-r src/esgf_fix_tool/rules/catalogue_fallbacks.yaml`.

## Test only variable history removal

This fix is already implemented by **C001** in `src/esgf_fix_tool/rules/variable_attributes.yaml`, calling
`drop_var_attr(ds, var, attr="history")`. It only addresses `ds.variables[var]`, never global attributes.
In the full 2026-11-03 report, the 351 distinct messages differ only in variable name: they represent
665 findings in 584 files and all need the same correction. Each finding also occurs in a failed
`cf / appendix_a` control in its individual file report.

Use `plan --only-rule C001` to exclude other fixes from this test. With `report`, `source_root` and
`staging_root` defined as above:

```bash
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/variable_attributes.yaml --only-rule C001 \
  --map-path "$source_root=$staging_root" -o fixplan.history.json
uv run esgf-fix-tool apply fixplan.history.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.history.dry-run.jsonl
uv run esgf-fix-tool apply fixplan.history.json --only-under "$staging_root" \
  --fixlog fixlog.history.jsonl
```

The expected plan is **665 fixes for 584 files**. Already-removed attributes cause no change on replay.
`--only-rule` may be repeated to select several rules; omitting it keeps the previous behavior.
An unknown rule ID is rejected before writing the plan. In esgf-toolbox, use the Python entry point
shown above instead of `uv run esgf-fix-tool`.

QA consistency snapshots in this scan do not export `history`, so they cannot establish whether a
global history exists. Tests explicitly preserve it when present (including empty and NC_STRING
values), leave other variables' history alone, and check raw data, compression and hardlink rejection.

## Test units and invalid standard names

Load `src/esgf_fix_tool/rules/units_and_standard_names.yaml` alone to test these three diagnostic families.
The full 2026-11-03 report contains 11 exact messages / 260 fixable findings / 98 files. C014 now
replaces atmospheric `lev:standard_name="x"` with `atmosphere_hybrid_sigma_pressure_coordinate`
following the user confirmation of 2026-11-05. Suggestions printed by the checker are never applied automatically.

| Rule | Observed case | Metadata correction | Findings |
| --- | --- | --- | ---: |
| C007 | basin, units=1, standard_name=region | remove units; keep codes and flags | 10 |
| C008 | solth, units=m, standard_name=model_level_number | set depth, axis=Z, positive=down; keep meters | 2 |
| C009–C010 | ReffIce / ReffLiq | remove exact invalid standard_name; preserve long_name | 1 + 1 |
| C011 | bounds | remove exact invalid standard_name | 81 |
| C012 | dbze | equivalent_reflectivity_factor, guarded by units=dBZ | 1 |
| C013 | number | same descriptive attributes as C004/C005 | 81 |
| C014 | x on atmospheric lev | atmosphere_hybrid_sigma_pressure_coordinate; keep units, values and formulas | 62 |
| C015 | Pressure | same descriptive attribute as C006; no air_pressure inference | 19 |
| C016 | pressure on pressure2 | air_pressure per plev7c; units and values need separate review | 1 |
| C017 | scatratio | backscattering_ratio per registry (valid CF v95 alias) | 1 |

All occurrences were reconciled with failed individual CF controls and their metadata snapshots.
The replacements come from WCRP Universe coordinates sdepth, dbze, plev7c and scatratio, checked
against CF standard-name table v95; effectradic/effectradli define no standard_name.
The Python methods are in `src/esgf_fix_tool/fixes/attributes.py`: `drop_region_units`,
`fix_soil_depth_metadata`, and `fix_invalid_standard_names` (also reusing `drop_descriptive_standard_name`).
They check current attributes before changing them, preserve global attributes and data, and are idempotent.
Replacement rules restrict variable names to the reviewed original/canonical names; destructive prefix matches
such as dropping all standard_names starting with `number` or `Pressure` are not used.

With `report`, `source_root` and `staging_root` defined as above:

```bash
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/units_and_standard_names.yaml \
  --map-path "$source_root=$staging_root" -o fixplan.units-standard-names.json
uv run esgf-fix-tool apply fixplan.units-standard-names.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.units-standard-names.dry-run.jsonl
uv run esgf-fix-tool apply fixplan.units-standard-names.json --only-under "$staging_root" \
  --fixlog fixlog.units-standard-names.jsonl
```

Expected: **260 fixes for 98 files**, including the 100 attributes already covered by C004–C006 if
those were not applied earlier. Replay skips already-corrected attributes. C014 changes only the name:
hybrid formula/formula_terms and unit requirements remain separate QA investigations. This plan does
not resolve the full QA report. Coordinate renames, bins, bounds,
flags, radius conversions and pressure conversion to Pa remain separate recipes; C008 only fixes soil
metadata, and C016 leaves units untouched. In UDUNITS, `mb` means millibarn (area), not millibar: confirm the
actual pressure values before relabeling as mbar/hPa or converting to Pa. The following QA may therefore
report a units error for pressure2. Use the existing esgf-toolbox Python prefix at TGCC.

References checked for these rules: [WCRP Universe data coordinates](https://github.com/WCRP-CMIP/WCRP-universe/tree/main/data_coordinate),
[CF standard-name table v95](https://cfconventions.org/Data/cf-standard-names/95/build/cf-standard-name-table.html),
and [Unidata on mb versus mbar](https://support.unidata.ucar.edu/archives/udunits/msg00721.html).

## Test COORD001 families separately

The full 2026-11-03 report has 61 missing-generic-lev findings and 108 required-coordinate findings
(17 distinct messages, 167 files). The initial rules made **152 steps for 152 files** and left
17 findings for review. The November 9 definitions now cover the seven hydrological soil axes,
msfty grid latitude and nine basin sector coordinates; see the current decisions above.

| Rule | Case | Findings | Action |
| --- | --- | ---: | --- |
| C018 / C019 | alevel / alevhalf: presinter → lev | 2 / 17 | rename variable and dimension |
| C020 | olevel → lev | 42 | rename variable and dimension |
| C021 / C022 | sdepth: solth / solay | 2 / 7 | rename thermal depth / preserve hydrological depths and add approved midpoint bounds |
| C023 / C024 | ReffIce / pressure2 | 1 / 1 | rename to effectRadIc / plev |
| C025 | rlat on msfty | 1 | use the approved existing meridional positions and complete lev metadata |
| C026 | deltasigt | 4 | add registry scalar 0.03 kg m-3 |
| C027 | depth scalar | 10 | explicit registry values, including layer bounds |
| C028 | height scalar | 34 | registry height 2 or 10 m |
| C029 | plev scalar | 14 | registry pressures in Pa, including layer bounds where specified |
| C030 | pfttype | 2 | rename scalar text variable to type |
| C031 | wavelength | 16 | registry 443 / 550 / 865 nm |
| C032 | sector from numeric basin | 9 | approved three-label sector, preserving scientific slice order |
| C033–C035 | sector from type / line text | 1 / 3 / 3 | add exact text alias, retain original coordinate |

The b3 checker asks for `sector` while the consulted registry still uses `basin`, `type` or `line`.
The text alias copies current labels and order; numeric basin codes are not relabeled by guessing.

C099 now deletes redundant line/type (including legacy line_label/type_label)
after comparing their ordered labels with sector, instead of renaming them.
The registered function name remains `rename_redundant_text_label` for old plans.
For scalar creation, `fixes/coordinate_specs.json` bundles 20 explicit coordinate definitions and
59 branded-variable mappings at Universe commit e8711372fddb0295916afff1e90c6e5469d8537f.
There is no filename-number inference or runtime network access. For example, a 0–300 m ocean layer
uses a depth of 150 m with bounds [0, 300], and top 10 cm of soil uses 5 cm with bounds [0, 10].

With the same `report`, `source_root`, and `staging_root` variables as above:

```bash
# Only the first diagnostic family: 61 steps / 61 files.
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/vertical_coordinate_names.yaml \
  --map-path "$source_root=$staging_root" -o fixplan.lev.json
# Only the second family: 91 steps / 91 files; the review cases stay out of the plan.
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/required_coordinates.yaml \
  --map-path "$source_root=$staging_root" -o fixplan.required.json
# Only one variant: height scalars, 34 steps / 34 files.
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/required_coordinates.yaml --only-rule C028 \
  --map-path "$source_root=$staging_root" -o fixplan.height.json
uv run esgf-fix-tool apply fixplan.lev.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.lev.dry-run.jsonl
```

Choose the desired plan for `apply`; remove `--dry-run` to keep changes, then rerun QA. `--only-rule`
is repeatable for combining variants. The optional YAML `dataset` regex distinguishes identical
missing-depth messages for thermal soil (tsl) and hydrological soil; old rules without it are unchanged.
At TGCC, use the previously shown esgf-toolbox Python prefix instead of uv.

Coordinate methods are in `fixes/coordinates.py`. Renames update existing CF references and the
coordinate's `name` attribute but do not infer standard_name, physical formula, or new formula_terms.
The existing soil and pressure metadata rules also accept their reviewed canonical names after rename.
Direct metadata updates refuse hardlinked targets. Because NetCDF-4 coordinate renames can lose
values, run `rename_coordinate` through this writer: it backs up affected coordinates and restores
only arrays that need it on a fresh handle. Direct updates check headers and affected auxiliaries;
they do not scan unrelated science arrays. Structural outputs are compared against all mapped
source arrays before replacement. No coordinate order, bin values or science data are inferred.
The tests include NETCDF4, NETCDF4_CLASSIC and NETCDF3, idempotence, collisions and injected corruption.

## Test only the confirmed atmospheric hybrid standard_name (C014)

C014 matches only the observed CF v95 `standard_name x` diagnostic for `CMIP7.ATM.*` datasets.
At application it requires `lev:standard_name="x"`; any different current value is retained.
It also requires both coefficient variables **ap and b** in the current file. A missing coefficient
aborts that file before replacement, including when replaying a plan prepared before this guard existed.
Both variables appear in all 62 individual metadata snapshots; this is a presence check, not validation
of their values, dimensions, or the physical formula.
Units are preserved without being a prerequisite, so a separate units fix does not block this change.
No coefficient, formula, formula_terms, array value or variable name is added or changed.

```bash
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/units_and_standard_names.yaml --only-rule C014 \
  --map-path "$source_root=$staging_root" -o fixplan.C014.json
uv run esgf-fix-tool apply fixplan.C014.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.C014.dry-run.jsonl
```

Expected for the 2026-11-03 report: **62 fixes for 62 files**. Remove `--dry-run` to keep the change,
then rerun QA. At TGCC use the existing esgf-toolbox Python prefix instead of uv.

## Test COORD002 order and COORD003 metadata separately

Every finding below was checked against its failed individual control and metadata snapshot
in QA_results_v3. The 2026-11-03 report has **101 COORD002 findings / 89 exact messages**, plus
**8 findings / 4 exact COORD003 messages** requested here. Combined: 109 findings in 106 files,
96 fix steps in 93 files and 13 review findings. Counts are findings, not distinct message templates.

| Rule | Reviewed action | Findings |
| --- | --- | ---: |
| C036 | alt: absent/empty axis → Z | 5 |
| C037 | dbze: dbze → equivalent_reflectivity_factor, units=dBZ | 1 |
| C038 | effectRadLi: remove exact standard_name ReffLiq | 1 |
| C039 | scatratio: scatratio → backscattering_ratio, units=1 | 1 |
| C040 / C041 | MODIS ice / liquid: time,tau,radius,lat,lon; normalize radius name if needed | 1 / 1 |
| C042 | clisccp: pressure2 → plev, then time,tau,plev,lat,lon | 1 |
| C043 / C044 / C045 | olevel → lev / presinter → lev / solth → depth; same existing recipes | 41 / 19 / 2 |
| C046 / C047 / C048 | index dimension oline/siline → line, vegtype → type; keep existing text labels | 3 / 3 / 1 |
| C049 | remove x=y=1 from the main variable only; no averaging | 15 |
| C050 | zostoga: verify exact replication, then keep one valid point per date | 2 fix |
| C051 | hfx/hfy: 75 levels absent from required order | 4 review |
| C052 / C053 | hydrological solay: use existing 0..2 m depths and approved midpoint edges | 6 / 1 fix |
| C054 | msfty: remove x=1, rename olevel to lev, preserve y=332; C025 still tracks rlat | 1 fix |

C054 squeezes x from the main field and its auxiliaries in one grouped rewrite.
It accepts the old or renamed vertical axis. Auxiliary coordinate references are
checked by their dimensions, so prior renames such as nav_lat → latitude work too.
The resulting main dimensions are `time, basin, lev, y`. C025 now exposes the approved
existing meridional coordinate as `rlat`, completing the names and attributes.

Load the two catalogues before the fallbacks. Alone they select only these diagnostics. For example,
with `report`, `source_root` and `staging_root` defined as above:

```bash
# One real permutation only: 1 file.
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/dimension_order.yaml --only-rule C040 \
  --map-path "$source_root=$staging_root" -o fixplan.C040.json
# All fixable order cases: 88 steps / 88 files; metadata alone gives 8 steps / 6 files.
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/dimension_order.yaml \
  --map-path "$source_root=$staging_root" -o fixplan.order.json
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/coordinate_metadata.yaml \
  --map-path "$source_root=$staging_root" -o fixplan.metadata.json
uv run esgf-fix-tool apply fixplan.C040.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.C040.dry-run.jsonl
```

Remove `--dry-run` to keep changes and rerun QA. The previous recipes and these recipes can be
applied in either order: an existing canonical coordinate name is accepted, and corrected metadata
is skipped. Review cases stay out of the plans; radius units/values, pressure units, formulas and
scientific reductions remain separate investigations.

`fixes/layouts.py` implements `fix_data_layout`. For permutations it prefers **ncpdq**, if visible
in the environment running Python, using `ncpdq -O -h -a ...`. If that executable is unavailable,
it rewrites with netCDF4 Python. Singleton removal uses Python and requires length exactly 1 for
every removed axis. No installation or Internet access is needed in the prepared environment.
The Python rewrite supports flat files, standard types, and uncompressed/zlib data; an unsupported
group, custom type or filter aborts without replacing the target. Only the main variable's layout
changes; other arrays and unused dimensions are retained. The rewrite checks every raw array,
the declared index permutation, packing, compression, chunk sizes, format and attributes, including
NC_CHAR/NC_STRING attribute types. Global provenance is preserved; backend details go to fixlog.
Reading and rewriting large files adds I/O and requires temporary disk space.

Run these functions through `apply`: its combined writer handles coordinate restoration and layout
verification. Independent staging copies are required for direct updates; structural replacements
leave any hardlinked source untouched. A second pass changes nothing. Tests cover three NetCDF formats, corruption rejection,
missing coefficients and idempotence. The NCO command is tested with a stub here; the real ncpdq
executable was not available locally. Its actual output must pass the same verification at TGCC.
See the [NCO manual](https://nco.sourceforge.net/nco.html) for dimension permutation with `-a`.

## Test coordinate values and bounds (COORD007/008/009)

Load `src/esgf_fix_tool/rules/coordinate_values_bounds.yaml` before the fallbacks, or alone to test this batch.
All **3,430 findings / 28 exact messages / 584 files** were reconciled with failed individual
controls (`value[0] < value[1]`, priority 3) in QA_results_v3. The plan has **2,676 steps / 584 files**
after deduplicating operations on shared dimensions and the two climatology diagnostics.

| Rule | Reviewed action | Findings |
| --- | --- | ---: |
| C055 / C056 | apply user-approved DR centers and edges for dbze / scatratio | 1 / 1 |
| C057 | seasurface: osurf → ocean_surface_layer; retain the NetCDF variable name | 5 |
| C058 | six exact type labels → registry values; no embedded NUL | 6 |
| C059 | normalize lon and its bounds on regular g111, 144 points, deviation ≤1e-10 degree | 374 |
| C060 | tpt, time: point and every pair exactly [time,time]: physically remove degenerate bounds | 36 |
| C061 / C065 | add explicit registry edges for alt40 / tau after checking centers and units | 5 / 3 |
| C062 / C064 | same complete DR histogram recipe as C055 / C056 | 1 / 1 |
| C063 | publish confirmed iceband upper limits and contiguous bounds | 3 |
| C066 | existing climatological intervals: bounds → climatology, variable climatology_bnds | 10 in 5 files |
| C067 | six reviewed bounds name/reference combinations | 1,656 |
| C068 | size-two nvertex / axis_nbounds → bnds; shared operation per file | 1,328 in 579 files |

With the report and path variables defined above, for one rule:

```bash
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/coordinate_values_bounds.yaml --only-rule C059 \
  --map-path "$source_root=$staging_root" -o fixplan.C059.json
uv run esgf-fix-tool apply fixplan.C059.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.C059.dry-run.jsonl
```

Use the existing esgf-toolbox Python prefix at TGCC; no uv, network or additional dependency is
needed in that prepared environment. Remove `--only-rule` for the full batch. After the dry run,
remove `--dry-run` to keep changes, then rerun QA. Plans contain conditional steps: C067 skips
time bounds already detached by C060 or replaced by C066. Naming, dimension normalization and
longitude normalization accept their already-canonical forms and can be tested separately.

The methods are in `fixes/bounds.py`, with seven label mappings and four explicit center/edge tables
bundled in `fixes/bounds_specs.json` at the recorded Universe commit. The six type mappings are
typebare→bare_ground, typecrop→crops, typenatgr→natural_grasses, typeresidual→residual,
typetree→trees and typeveg→vegetation. Character arrays retain their length and use space padding;
scalar NC_STRING labels retain that representation. Metadata is preserved.

C059 implements the correction confirmed in the shared chat (messages 247–254). It checks both
original lon and bounds against the exact regular grid before changing only rounding differences.
C060 implements the minimal test proposed in messages 255–258: every pair is checked at runtime,
no interval is fabricated, and the unreferenced bounds array is now physically deleted. The registry's time1
definition does not require bounds. C066 validates temporal labels, cell_methods and existing finite,
increasing intervals; it changes references and names without recomputing climatological statistics.
For alt40 the explicit edges are 0…19200 m in 480 m steps; for tau they are
[0, 0.3, 1.3, 3.6, 9.4, 23, 60, 100000]. No midpoint inference is used. Conflicting existing bounds
or an unreviewed center/unit combination abort that file.

User confirmation on 2026-11-05 selects the DR tables for dbze/scatratio. `fix_dr_histogram`
writes the explicit 15 coordinate_values and 16 coordinate_bounds together, preserving histogram
payloads and class order. C055/C062 and C056/C064 each share this recipe; either diagnostic alone
works and the second step becomes a no-op. It requires unpacked double coordinates, matching units,
15 increasing classes, and agreement of all 13 interior centers in their existing order within the
checker requested-value tolerances. Conflicting existing bounds are rejected. Only the declared
coordinate array may change; a final verifier checks exact DR centers and edges after all hooks.
### Test the five ice thickness classes (C063)

The consistency checksums for `siitdconc`, `siitdthick` and `siitdsnthick` match the double
values `[1, 2, 3, 4, 5]`. The user confirmed the model's upper bounds
`[0.45, 1.12, 2.14, 3.67, 99]` m and the first lower bound of `0` m.
C063 uses the upper limits as the coordinate values and stores the five consecutive pairs in
`iceband_bnds(iceband, bnds)`. It preserves the science slices and their order.
It reuses an existing size-two `bnds`, or renames the sole compatible legacy dimension in
place to avoid a subsequent merge. The size-four ocean vertex dimension stays intact.

```bash
fix_tool plan "$report" -r src/esgf_fix_tool/rules/coordinate_values_bounds.yaml --only-rule C063 \
  --map-path "$source_root=$copie" -o fixplan.iceband.json
fix_tool apply fixplan.iceband.json --only-under "$copie" --fixlog fixlog.iceband.jsonl
```


C067 renames only the bounds variables, updates CF references, and rejects collisions. C068
renames the dimension directly when there is one source and no bnds collision. For a NetCDF-4
file with an existing bnds coordinate and only reviewed small bounds on the old dimensions, it
creates canonical bounds in place and keeps the displaced arrays as fix_original_*. Other merges
use the verified chunked rewrite. Size-four ocean nvertex is retained;
the 328 vertex-dimension recommendations are a separate diagnostic, outside this batch.

Use these fixes through `apply`. Direct updates check affected auxiliaries and headers; structural
outputs check every mapped array. Label and longitude edits also have explicit final verifiers.
Climatology attribute removal is checked after reopening because a NetCDF-4 rename can resurrect
time:bounds. Structural validation failure discards the output; direct changes are not atomic.
Tests cover three formats, hardlink rejection, bnds collisions, composition and idempotence.
Large merges require a full rewrite and temporary disk space. No real simulation file was modified
and no new real QA was run locally.


## Test horizontal associations and grid-coordinate names

The requested COORD010/011 messages represent **388 failed findings / 38 exact messages / 190 files**
in QA_results_v3, checked individually using scores and priority 3. They give **380 steps / 190 files**. C070 combines two messages per profile into one operation.

| Rule | Action | Findings / files |
| --- | --- | ---: |
| C069 | remove only unrequested tokens from the main variable coordinates attribute | 44 / 26 |
| C070 | nav_lat(y,1) -> lat(lat), squeeze x=1, approved final latitude | 16 / 8 |
| C071 | nav_lat → latitude, preserving curvilinear y,x arrays | 164 / 164 |
| C072 | nav_lon → longitude, preserving curvilinear y,x arrays | 164 / 164 |

C069 uses 21 explicit DR branded-variable definitions in fixes/horizontal_specs.json. Its variants are
17 global outputs with neither latitude nor longitude required (34 findings), 8 hyb profiles with only
longitude unrequested, and msfty needing gridlatitude instead of the two true horizontal coordinates
(2 findings). It retains the coordinate arrays and all other listed tokens; the main data are untouched.
For example hfbasinpmdiff keeps nav_lat and loses only nav_lon from its coordinates attribute.
A different brand or a required role is rejected, and canonical names are accepted after prior renames.

C071/C072 are variable-only renames on reviewed hxy grids g112/g168/g169/g186. They check standard_name,
units and 2-D y,x structure, update CF references, and reject name collisions. They use the existing
post-close restoration and raw-array verification, preserving y/x and the main variable layout.
The bounds recipe C067 also accepts latitude/longitude, including messages produced by a fresh QA
after these renames, so either order of bounds/name corrections works.

The eight C070 profiles are hfbasin, hfbasinpmadv, hfbasinpmdiff, htovgyre, htovovrt, sltbasin,
sltovgyre and sltovovrt. The user relayed model-engineer approval for changing only the last latitude
from the repeated 89.36998 (stored as 89.36997985839844) to 89.36999. The recipe exposes lat(lat),
renames y to lat and removes singleton x without changing any of the 332 scientific data rows.
It requires every earlier latitude to increase strictly. The unreferenced nav_lon array is retained
and squeezed losslessly. No latitude bounds, grid_label change or regridding is introduced.
See [profile guards and testing](docs/basin-profile.md). Use --only-rule C070 to test this error alone.

```bash
uv run esgf-fix-tool plan "$report" -r src/esgf_fix_tool/rules/horizontal_coordinates.yaml --only-rule C071 \
  --map-path "$source_root=$staging_root" -o fixplan.C071.json
uv run esgf-fix-tool apply fixplan.C071.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.C071.dry-run.jsonl
```

Use the previously documented esgf-toolbox Python prefix at TGCC. Load this catalogue before the fallbacks,
or alone for this batch. Remove --dry-run to keep changes, then rerun QA. No network or new dependency
is needed in the prepared environment. Run these functions through apply for coordinate protection
and verification. Direct renames check affected auxiliaries without scanning science fields and
require independent staging copies; structural outputs verify all mapped data.

## Test categorical osurf axis removal (C073)

The two CF vertical-coordinate messages occur on the same five files (10 findings). C073 merges
them into one operation per file and calls `drop_osurf_axis(ds)` in `fixes/surface.py`. It removes
only `axis="Z"` from the reviewed string coordinate `seasurface`; it accepts `osurf` and
`ocean_surface_layer`, so it works before or after C057's separate label correction.
Arrays, global attributes and other variable attributes are preserved. No units, positive value
or numeric depth are introduced. The user's DR clarification supersedes the older fallback waivers;
the currently published DR still contains axis=Z, so an older DR/checker can continue requesting it.

```bash
fix_tool() {
  esgf-toolbox env PYTHONPATH="$PWD/src" python -m esgf_fix_tool.cli.main "$@"
}
fix_tool plan "$report" -r src/esgf_fix_tool/rules/surface_and_hybrid.yaml --only-rule C073 \
  --map-path "$source_root=$staging_root" -o fixplan.C073.json
fix_tool apply fixplan.C073.json --only-under "$staging_root" \
  --dry-run --fixlog fixlog.C073.dry-run.jsonl
```

Remove `--dry-run` to keep the change, then rerun QA with the updated DR when available.
Load `surface_and_hybrid.yaml` before `catalogue_fallbacks.yaml` in a combined run.

## Test hybrid metadata and reference coefficients (C074)

C074 uses the supplied IPSL reference to put `ap` and `b` on `lev`, attach the
reference interface pairs to their bounds for `alevel`, and update formula metadata.
Values of `lev` and existing `lev` bounds stay unchanged. Regenerate any old C074
plan to select the new recipe. See [the reference, scope and command](docs/hybrid-coordinate.md).
