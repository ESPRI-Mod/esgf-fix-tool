"""Known checker crashes (a checker bug, not the file) do not stop the loop; packing goes to the repack step;
classify leaves a summary next to the report."""

import json

import pytest
from typer.testing import CliRunner

from esgf_fix_tool.classify import Decision, Outcome, classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet

ISSUE_1374 = ("Exception: sequence item 0: expected str instance, numpy.ndarray found at /app/.venv/lib/python3.12/"
              "site-packages/compliance_checker/cf/cf_1_6.py:2682 in function/method 'check_geographic_region'.")
REGION = "[CF-Conventions] check_geographic_region"


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load(*default_rule_paths())


def report(tmp_path, fail=None, errors=None, name="qa_result_x.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"fail": fail or {}, "error": errors or {}, "info": {"parent_dir": str(tmp_path)}}))
    return p


def test_the_known_crash_does_not_stop_the_loop_but_any_other_does(tmp_path, rules):
    known = {REGION: {ISSUE_1374: {"ds1": ["/sim/basin_a.nc", "/sim/basin_b.nc"]}}}
    c = classify(QAReport.load(report(tmp_path, errors=known)), rules)
    assert c.decision() is Decision.CLEAN and [m.rule.id for _, m in c.known_errors] == ["E001", "E001"]
    other = dict(known, **{"[CF-Conventions] check_units": {"Exception: KeyError 'x'": {"ds1": ["/sim/c.nc"]}}})
    c = classify(QAReport.load(report(tmp_path, errors=other)), rules)
    assert c.decision() is Decision.CHECKER_ERROR and len(c.unknown_errors) == 1 and len(c.known_errors) == 2
    assert classify(QAReport.load(report(tmp_path, errors=known)), rules, final=True).decision() is Decision.CLEAN


def test_a_checker_error_rule_never_matches_an_ordinary_finding(tmp_path, rules):
    fail = {"3": {REGION: {ISSUE_1374: {"ds1": ["/sim/a.nc"]}}}}
    (item,) = classify(QAReport.load(report(tmp_path, fail=fail)), rules).items
    assert item.match is None or not item.match.rule.for_checker_errors


@pytest.mark.parametrize("check,message", [
    ("[CMIP7] [FILE004b] Internal packing : Time coordinate chunking",
     "Time coordinate variable 'time' has 1200 chunks (expected 1 chunk or contiguous). It is required to repack this variable"),
    ("[CMIP7] [FILE004c] Internal packing : Time bounds chunking ('time_bnds')",
     "Time bounds variable 'time_bnds' has 864 chunks (expected 1 chunk or contiguous). It is required to repack this variable"),
    ("[CMIP7] [FILE004d] Internal packing : Data variable chunking ('tas')",
     ("Data variable 'tas': uncompressed chunk size 82368 B (expected at least 4111936 B, or 1 chunk, or contiguous). "
     "It is required to repack this variable")),
])
def test_time_chunking_goes_to_the_repack_step(tmp_path, rules, check, message):
    (item,) = classify(QAReport.load(report(tmp_path, fail={"3": {check: {message: {"ds1": ["/sim/a.nc"]}}}})),
                       rules).items
    assert item.outcome is Outcome.OTHER_STEP and item.match.rule.id in ("P001", "P002", "P003")


def test_classify_leaves_a_summary_next_to_the_report(tmp_path):
    fail = {"3": {"[CMIP7] [ZZZ999] Something new": {"boom": {"ds1": ["/sim/a.nc", "/sim/b.nc"]}},
                  "[CMIP7] [FILE004b] Internal packing : Time coordinate chunking": {"x": {"ds1": ["/sim/a.nc"]}}}}
    errors = {REGION: {ISSUE_1374: {"ds1": ["/sim/basin.nc"]}}}
    path = report(tmp_path, fail=fail, errors=errors, name="qa_result_20261119-1203_abc.json")
    result = CliRunner().invoke(app, ["classify", str(path)])
    assert result.exit_code == int(Decision.NEW_RULE), result.output
    assert "known checker errors (a checker bug, not the file): 1" in result.output
    summary = json.loads((tmp_path / "qa_result_20261119-1203_abc.classify.json").read_text())
    assert summary["decision"] == "new_rule" and summary["exit_code"] == 20 and summary["mode"] == "loop"
    assert summary["blocking"] == {"findings": 2, "files": 2, "unknown": 2}
    assert summary["known_checker_errors"] == {"E001": 1} and summary["unknown_checker_errors"] == 0
    assert summary["counts"]["3"] == {"unknown": 2, "other_step": 1}
    as_json = json.loads(CliRunner().invoke(app, ["classify", str(path), "--json"]).output)
    assert as_json["blocking"] == summary["blocking"]
