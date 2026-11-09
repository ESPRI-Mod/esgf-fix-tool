# Hybrid dependencies and catalogue cleanup

## Failure reproduced

The reported plan already ran C074a before C102/C103b. Its late failure came from
the normalization verifier comparing output coefficients against the original file.
C074a had legitimately rebound/replaced those arrays using the approved reference,
so different original dimensions and values caused a false failure after the rewrite.
The original file was not installed over: verification precedes atomic replacement.

## Preparation and verification boundaries

Both newly generated plans and application of older plans enforce this dependency:

1. Run selected `fix_hybrid_coefficients` steps (C074/C074a).
2. Validate normalization inputs and capture their small coefficient arrays, including
   dtype and dimensions, after the approved reference binding.
3. Run `normalize_hybrid_coordinate` (C102/C103b).
4. Verify reference coefficients independently against the supplied reference, and
   verify normalization preserves the coefficients captured at its own input boundary.

This does not weaken data preservation checks. The main scientific field and ps
remain protected by the combined writer. Coefficient values, types and dimensions
must match their approved baseline. All transformations share one verified output.

Ordering is stable for otherwise independent steps. A prerequisite excluded by a
rule selector is never silently added. If coefficients are incompatible and C074/a
was not selected, preparation fails before any rewrite with an actionable message.
Already aligned files need no reference replacement. Replays remain read-only when
everything is already correct.

Regression tests cover all six permutations of C074a/C102/C103b, 79/80 source
coefficients on a separate dimension, transposed coefficient bounds, three NetCDF
formats, one grouped rewrite, raw science preservation and no-write replay.

## Original catalogues removed

The former cmip7_ipsl.yaml contained five reviewed rules. All are kept:

| Rules | New location |
| --- | --- |
| C001, C002, C002a, C112 | rules/variable_attributes.yaml |
| C003 | rules/coordinate_metadata.yaml |

The former seed_20261029.yaml contained 92 historical diagnostic patterns: 74
`fix` decisions without an implemented method, 9 review, 6 waive, 1 other_step,
1 defer and 1 upstream_only. Removing an entire unused-looking pattern would
discard coverage of potential future or older reports, so their original IDs,
patterns, notes and decisions are retained under rules/fallbacks/, split by theme.
The new catalogue_fallbacks.yaml manifest loads them after reviewed catalogues.
No speculative correction or new equivalence is introduced.

On the supplied current full report, only these historical rules remain selected:

| Rule | Findings | Purpose |
| --- | ---: | --- |
| S029 | 581 | Consolidated internal metadata: separate repacking step |
| S030 | 584 | DRS paths: deferred publication step |
| S032 | 46 | Dimension-order waiver |

Counts are entries from the full report, not distinct cluster messages. The other
89 patterns are historical coverage rather than active corrections for this run.
The exact migration map is in catalogue-migration.json. All 218 rule records are
preserved. The 5,586 classifications and 743 planned steps for 292 files remain
unchanged by this move, apart from dependency ordering within a file.

The two original YAML files are deleted from the repository. Default discovery
also ignores leftover copies of those names when scp overlays an older TGCC tree,
so they cannot cause duplicate IDs. Explicit commands naming the old files must
be updated; the short default command is recommended.

```bash
fix_tool plan "$report" -o fixplan.all.json
fix_tool apply fixplan.all.json --only-under "$copie" --fixlog fixlog.all.jsonl
```

Transfer the complete updated tree, including rules/fallbacks/ and the Python
changes. Old fix plans remain executable with the dependency-aware application,
but regenerate them to record the current catalogue names.
