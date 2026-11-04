# esgf-fix-tool

Fix CMIP7 netCDF files from an **esgqa report** (`qa_result_*.json`) and a set of **rules**.
Part of the IPSL CMIP7 publication pipeline (QA ↔ FIX loop). Design: [ARCHITECTURE.md](ARCHITECTURE.md).

```bash
uv sync
# what does this report need? (exit code = decision: 0 clean, 10 known, 20 new_rule, 21 blocked_upstream, 30 no_change, 40 checker_error)
uv run esgf-fix-tool classify qa_result_XXX.json -r rules/cmip7_ipsl.yaml -r rules/seed_20261029.yaml
# the fixes it would apply, per file
uv run esgf-fix-tool plan qa_result_XXX.json -r rules/cmip7_ipsl.yaml -r rules/seed_20261029.yaml -o fixplan.json
# apply them: copy → fix the copy → rename over (never in place, safe on hardlinks)
uv run esgf-fix-tool apply fixplan.json --fixlog fixlog.jsonl [--dry-run]
```

Rules:
- `rules/cmip7_ipsl.yaml`: curated rules with an implemented fix. Load it **first** (first match wins).
- `rules/seed_20261029.yaml`: generated from the 2026-10-29 Excel triage (`seed-rules`, needs `--extra seed`).
  Every rule has a decision; most still have `fix: null` (classified `fix_missing`).

Adding a fix: write a function in `src/esgf_fix_tool/fixes/`, decorate it with `@fix("name")`, then move the matching
seed rule into `rules/cmip7_ipsl.yaml` with `fix: name` and its `params`.

Status (2026-11-04, report of CM6-ESMCO2-FT-esm-pi-s.01, 584 files): every severity-3 finding has a rule;
665 are fixable today (`history` on variables), 1,044 `fix_missing`, 446 `review`; plus PATH003 (deferred to the DRS
draft) and FILE004a (repack step).
