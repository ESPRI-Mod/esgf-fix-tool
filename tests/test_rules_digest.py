"""The rules digest identifies the exact rule content behind a plan and every fixlog line."""
import json

import netCDF4
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.ruleset import RuleSet

RULE = "- id: R1\n  check: x\n  message: y\n  action: review\n"


def catalogues(tmp_path, included=RULE):
    (tmp_path / "part.yaml").write_text(included)
    main = tmp_path / "main.yaml"
    main.write_text("include:\n  - part.yaml\n")
    return main


def test_digest_is_stable_for_the_same_content(tmp_path):
    main = catalogues(tmp_path)
    assert RuleSet.load(main).digest == RuleSet.load(main).digest
    assert len(RuleSet.load(*default_rule_paths()).digest) == 64


def test_digest_changes_when_an_included_catalogue_changes(tmp_path):
    before = RuleSet.load(catalogues(tmp_path)).digest
    after = RuleSet.load(catalogues(tmp_path, RULE.replace("review", "waive"))).digest
    assert before != after


def test_plan_and_fixlog_carry_the_digest(tmp_path):
    nc = tmp_path / "f.nc"
    with netCDF4.Dataset(nc, "w") as ds:
        ds.createDimension("n", 1)
        ds.createVariable("tas", "f4", ("n",)).history = "drop me"
    report = tmp_path / "qa_result_test.json"
    report.write_text(json.dumps({"fail": {"3": {"[CF-Conventions] appendix_a": {
        '[Appendix A] Attribute "history" should not be present in non-coordinate data (D) variable "tas". '
        "This attribute may only appear in global attributes (G).": {"dataset": [str(nc)]}}}}}))
    plan_path = tmp_path / "plan.json"
    result = CliRunner().invoke(app, ["plan", str(report), "-r", "C001", "-o", str(plan_path)])
    assert result.exit_code == 0, result.output

    expected = RuleSet.load(*default_rule_paths()).digest
    plan = FixPlan.read(plan_path)
    assert plan.rules_digest == expected

    log = tmp_path / "fixlog.jsonl"
    apply_plan(plan, log, dry_run=True)
    assert json.loads(log.read_text())["rules_digest"] == expected


def test_plans_written_before_the_digest_still_load(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"rules_version": "a.yaml", "report": "r",
                               "files": {"f.nc": [{"rule": "C001", "fix": "drop_var_attr", "params": {}}]}}))
    plan = FixPlan.read(old)
    assert plan.rules_digest == "" and plan.files["f.nc"] == [Step("C001", "drop_var_attr", {})]
