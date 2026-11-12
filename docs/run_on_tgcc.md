# Developing a fix and testing it on TGCC

TGCC has no internet: you write the fix on your laptop, push the code to your TGCC home with `rsync`, and run it
**inside the esgf-toolbox image** (it already has python 3.12, netCDF4, pyyaml, typer). The image itself is not
changed: this is the dev copy.

## 1. Laptop: write the fix

```bash
git clone https://github.com/ESPRI-Mod/esgf-fix-tool && cd esgf-fix-tool
git switch -c fix/<what>
uv sync && uv run pytest -q
```
- the **function**: `src/esgf_fix_tool/fixes/`, decorated with `@fix("name")`; it gets an open, writable
  `netCDF4.Dataset` (a temp copy) and the rule's params, must be idempotent and return True if it changed something.
  **A new file in `fixes/` must be imported in `fixes/__init__.py`** (next to `from . import attributes`), otherwise the
  fix is never registered and classifies as `fix_missing`.
- the **rule**: `src/esgf_fix_tool/rules/variable_attributes.yaml` (curated, loaded first), with `fix: name` and `params` (named groups of the
  `check` / `message` regexes are substituted, e.g. `{var}`).
- local check on a real report: `uv run esgf-fix-tool classify <qa_result_….json> -r src/esgf_fix_tool/rules/variable_attributes.yaml -r src/esgf_fix_tool/rules/catalogue_fallbacks.yaml --rule <ID>`

## 2. Laptop → TGCC home (every time the code changes)

```bash
ssh <tgcc> mkdir -p esgf-fix-tool
rsync -av --delete src scripts <tgcc>:esgf-fix-tool/
```

## 3. TGCC: shortcuts

```bash
source ~/esgf-fix-tool/scripts/dev_tgcc.sh      # in every new shell; IMG=<name> before it if your image isn't "et"
eft_help
```
| Shortcut | What |
|---|---|
| `eft …` | the dev esgf-fix-tool in the container (`eft --help`) |
| `eft_report [DIR]` | latest full QA report (default: the reference run) |
| `eft_sev3 [REPORT]` | severity-3 summary: files per check, checker errors |
| `eft_messages CHECK [REPORT] [N]` | the exact messages of a check, with an example file (`eft_messages TIME003`) |
| `eft_classify [REPORT] [--rule ID]` | classify with your rules; `--rule` shows where that rule stands |
| `eft_try RULE_ID [REPORT]` | **test one rule**: copies only the files it touches to `$EFT_WORK/<rule>/files`, applies it, runs esgqa on the copy, prints the rule's findings before / after |

Variables (set them before `source` to change them):
- `EFT_REF`: the **reference run**, only read: by default the pilot on `CM6-ESMCO2-FT-esm-pi-s.01` (the simulation of
  the 2026-10-29 Excel triage) in troussel's scratch. You need read access to it (same group).
- `EFT_WORK`: **your** work area, default `$CCCSCRATCHDIR/eft_dev`. Everything you write goes there.
- `IMG`: your toolbox image (`pcocc-rs image list`), default `et`.

Rules of the game: never `apply` on someone else's staging or on a source directory; `eft_try` only writes under
`$EFT_WORK`, and `apply --only-under` refuses anything else.

## 4. When it works

Push the branch, open a PR: the Rules keeper reviews it. Everybody gets it with `git pull` + `rsync`.
