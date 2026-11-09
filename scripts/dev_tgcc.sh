# Shortcuts to develop and test fixes on TGCC with a dev copy of esgf-fix-tool (not the one in the image).
#
#   source ~/esgf-fix-tool/scripts/dev_tgcc.sh        # in every new shell
#   eft_help                                           # what's available
#
# Everything you write goes under $EFT_WORK (your own scratch). The reference run ($EFT_REF) is only READ.
# Override any variable before sourcing, e.g.  IMG=my-toolbox source ~/esgf-fix-tool/scripts/dev_tgcc.sh

: "${IMG:=et}"                                           # your esgf-toolbox image: `pcocc-rs image list`
: "${EFT_HOME:=$HOME/esgf-fix-tool}"                     # where `rsync src rules scripts <tgcc>:esgf-fix-tool/` puts the code
: "${EFT_WORK:=$CCCSCRATCHDIR/eft_dev}"                  # YOUR work area
# reference run: the pilot on CM6-ESMCO2-FT-esm-pi-s.01 (simulation of the 2026-10-29 Excel), read-only
: "${EFT_REF:=/ccc/scratch/cont003/gencmip6/troussel/cmip7_pilot/CM6-ESMCO2-FT-esm-pi-s.01}"
: "${EFT_QA_TESTS:=-t wcrp_cmip7:1.0 -t cf:1.11}"
EFT_RULES="-r $EFT_HOME/rules/cmip7_ipsl.yaml -r $EFT_HOME/rules/seed_20261029.yaml"
export IMG EFT_HOME EFT_WORK EFT_REF EFT_RULES

# esgf-fix-tool (dev copy) inside the toolbox image
eft() { pcocc-rs run "$IMG" -- env PYTHONPATH="$EFT_HOME/src" python -m esgf_fix_tool.cli.main "$@"; }

# latest full report under a directory (default: the reference run)
eft_report() {
    ls -t "${1:-$EFT_REF/runs}"/qa*/qa_result_*.json 2>/dev/null | grep -v '\.cluster\.json$' | head -1
}

# eft_classify [REPORT] [--rule ID …]: classify with your rules (default: latest reference report)
eft_classify() {
    local rep
    if [[ -f "${1:-}" ]]; then rep=$1; shift; else rep=$(eft_report); fi
    eft classify "$rep" $EFT_RULES --show 20 "$@"
}

# eft_try RULE_ID [REPORT]: test ONE rule on a private copy of only the files it touches
#   1. plan of that rule only, paths mapped to $EFT_WORK/<rule>/files
#   2. copy those files there (from the directory the report was made on)
#   3. apply, 4. esgqa on the copy, 5. where the rule's findings stand now
eft_try() {
    local rule=${1:?usage: eft_try RULE_ID [REPORT]} rep=${2:-$(eft_report)}
    [[ -f "$rep" ]] || { echo "no report: $rep"; return 1; }
    local root dir
    root=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["info"]["parent_dir"])' "$rep")
    dir=$EFT_WORK/$rule
    echo "rule $rule | report $rep | files from $root | work dir $dir"
    rm -rf "$dir" && mkdir -p "$dir/files" || return 1
    eft plan "$rep" $EFT_RULES --rule "$rule" --map-path "$root=$dir/files" -o "$dir/plan.json" || return 1
    python3 - "$dir/plan.json" "$root" "$dir/files" <<'EOF' || return 1
import json, shutil, sys
from pathlib import Path
plan, root, dest = json.load(open(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
for p in plan["files"]:
    target = Path(p)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / target.relative_to(dest), target)
print(f"copied {len(plan['files'])} files")
EOF
    eft apply "$dir/plan.json" --only-under "$dir/files" --fixlog "$dir/fixlog.jsonl" -q || return 1
    pcocc-rs run "$IMG" -- esgqa -o "$dir/qa" -P 4 $EFT_QA_TESTS "$dir/files" > "$dir/qa.log" 2>&1 \
        || { echo "esgqa failed, see $dir/qa.log"; return 1; }
    echo "--- before (reference report):"; eft classify "$rep" $EFT_RULES --show 0 --rule "$rule" | grep "^rule"
    echo "--- after  (your copy)       :"; eft classify "$(eft_report "$dir")" $EFT_RULES --show 0 --rule "$rule" | grep "^rule"
}

# eft_sev3 [REPORT]: severity-3 summary (unique files per check) + checker errors
eft_sev3() {
    python3 - "${1:-$(eft_report)}" <<'EOF'
import collections, json, sys
r = json.load(open(sys.argv[1]))
print(sys.argv[1], "| files:", r["info"].get("files"), "| datasets:", r["info"].get("datasets"))
c = collections.Counter()
for chk, msgs in r["fail"].get("3", {}).items():
    c[chk] = len({p for m in msgs.values() for l in m.values() for p in l})
for chk, n in c.most_common():
    print(f"{n:6d}  {chk}")
print("checker errors:", {k: sum(len(l) for m in v.values() for l in m.values()) for k, v in r["error"].items()})
EOF
}

# eft_messages CHECK_FRAGMENT [REPORT] [N]: the exact messages of a check, with one example file each
eft_messages() {
    python3 - "${1:?usage: eft_messages CHECK_FRAGMENT [REPORT] [N]}" "${2:-$(eft_report)}" "${3:-10}" <<'EOF'
import json, sys
frag, rep, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
r = json.load(open(rep))
for sev, checks in r["fail"].items():
    for chk, msgs in checks.items():
        if frag in chk:
            for m, ds in list(msgs.items())[:n]:
                f = next(iter(next(iter(ds.values()))))
                print(f"[{sev}] {chk}\n    {m}\n    e.g. {f}")
EOF
}

eft_help() {
    cat <<EOF
image $IMG | code $EFT_HOME | your work area $EFT_WORK | reference run (read-only) $EFT_REF
  eft …                               esgf-fix-tool (dev copy) in the container, e.g. eft --help
  eft_report [DIR]                    latest full QA report (default: reference run)
  eft_sev3 [REPORT]                   severity-3 summary
  eft_messages CHECK [REPORT] [N]     exact messages of a check, e.g. eft_messages TIME003
  eft_classify [REPORT] [--rule ID]   classify with your rules, e.g. eft_classify --rule C004
  eft_try RULE_ID [REPORT]            test ONE rule on a private copy of the files it touches
EOF
}
