# esgf-fix-tool: architecture

Fix CMIP7 netCDF files from an **esgqa report**, with **rules** that say what to do with each QA message.
It is step 3–4 of the publication pipeline (QA ↔ FIX loop), see `publish_tgcc/fix.md`.

```
esgqa full report (qa_result_*.json)
        │
        ▼
  report.py ── QAReport: file → [Finding(severity, check, message)] + checker errors
        │
        ▼
  rules.py ─── RuleSet (rules/*.yaml): (check regex, message regex with named groups) → action
        │
        ▼
  classify.py ─ every finding gets an Outcome; one Decision for the whole run
        │        clean · known · new_rule · blocked_upstream · no_change · checker_error
        ▼
  plan.py ──── FixPlan: file → ordered [(fix name, params)]   (only for Outcome.FIX)
        │
        ▼
  apply.py ─── safe writer: copy → fix the copy → rename over (never in place) + fix log + renames.tsv
        │
        └── fixes/ ── registry of fix functions (@fix("drop_var_attr", kind="metadata"))
```

## Inputs and outputs

| | What | Notes |
|---|---|---|
| in | full esgqa report `qa_result_*.json` | `fail[severity][check][message][dataset] → [files]`, `error{}`, `info{}`. Not the `.cluster.json` |
| in | `rules/*.yaml` | versioned in git; the rules version is recorded in every output |
| in | previous report (optional) | to detect "no change" between two loop iterations |
| out | classification (JSON + text summary, exit code) | drives the loop: `jobs/qa.sh` reads the exit code |
| out | fix plan (JSON) | reviewable before applying |
| out | fixed files + `fixlog.jsonl` + `renames.tsv` | fixlog = rules applied per file; renames for the relink step |

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
- **First matching rule wins**; order the file from specific to generic.
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

## Writing files safely (`apply`)

- **Never** open the target file in append mode. Copy it to a temp file in the same directory, fix the copy, then
  `os.replace` it over the target: new inode, atomic. That matters because staging files start as **hardlinks** to the
  source (`cp -al`): an in-place write would modify the simulator's file.
- Metadata fixes (attributes, renames) are applied on the copy with netCDF4 in `a` mode. Structural fixes (dimension order,
  coordinate rewrites) write a new file. Both end with the same `os.replace`.
- Every applied fix is appended to `fixlog.jsonl` (file, rule id, fix, params, tool version, rules version).
  File renames (DRS-changing fixes) go to `renames.tsv`.
- Fixes must be **idempotent**: applying them to an already-fixed file changes nothing.

## Seeding the rules from the 2026-10-29 Excel triage (`seed-rules`)

One-off import, so the Excel is not needed at runtime:
- template from the cluster report → regex (`{A}` → named group, the "(N occurrences, e.g. …)" suffix removed),
- quoted names in check labels generalised (`Variable 'abs550aer' attribute 'comment'…` → one rule for all variables),
- Excel action → rule action: Corriger → fix, Déjà corrigé → fix (`upstream_status: fixed_upstream`),
  Ignorer → waive, Vérifier / À qualifier → review,
- overrides: PATH001–003 → defer, FILE004* → other_step (repack), `[Continuity]` → upstream_only.

Generated rules have `fix: null` until someone writes the function: they classify as `FIX_MISSING`, which shows how
much of the report the tool can fix today.

## Layout

```
src/esgf_fix_tool/
  report.py      read the esgqa report
  rules.py       Rule, RuleSet, matching
  classify.py    Outcome, Decision, summary
  plan.py        FixPlan from classified findings
  apply.py       safe writer, fix log
  fixes/         registry + fix functions (attributes.py, …)
  seed.py        Excel + cluster report → rules YAML (optional dependency: openpyxl)
  cli/           Typer app: main.py (entry point) + one file per command
                 (classify.py, plan.py, apply.py, seed_rules.py; common.py = shared options)
rules/           rule files (versioned)
tests/           small synthetic reports and netCDF files
```
