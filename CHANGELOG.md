# Changelog

All notable changes to esgf-fix-tool are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/).
A new or changed rule is at least a minor version: it changes what the tool does to files.

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
