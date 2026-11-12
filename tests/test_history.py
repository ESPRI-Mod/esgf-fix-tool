import shutil
import json
import os
from pathlib import Path

import netCDF4
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.fixes.attributes import drop_var_attr
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "src/esgf_fix_tool/rules/variable_attributes.yaml"


def report(path, variables):
    messages = {
        ('[Appendix A] Attribute "history" should not be present in non-coordinate data (D) '
         f'variable "{var}". This attribute may only appear in global attributes (G).'):
        {"dataset": [str(path)]} for var in variables
    }
    return {"fail": {"3": {"[CF-Conventions] appendix_a": messages}, "1": {
        "[CMIP7] [ATTR004] Variable 'tas' attribute 'comment' registry expected-term check": {
            "No variable 'tas' attribute 'comment' is defined": {"dataset": [str(path)]}}}}}


@pytest.mark.parametrize("global_history", [None, "", "global history\nkeep the complete value"])
def test_history_only_preserves_globals_and_untargeted_variables(tmp_path, global_history):
    source = tmp_path / "source.nc"
    with netCDF4.Dataset(source, "w") as ds:
        ds.createDimension("n", 3)
        if global_history is not None:
            ds.setncattr_string("history", global_history)
        for name in ("tas", "ps", "untargeted"):
            v = ds.createVariable(name, "i2", ("n",), zlib=True, complevel=3, fill_value=-32768)
            v.scale_factor = 0.5
            v.set_auto_maskandscale(False)
            v[:] = [-32768, 10, 20]
            v.history = f"history on {name}"
            v.description = "keep this description too"
    original_bytes = source.read_bytes()
    staged = tmp_path / "staged.nc"
    shutil.copyfile(source, staged)
    qa = tmp_path / "qa_result_test.json"
    qa.write_text(json.dumps(report(staged, ["tas", "ps"])))
    plan_path = tmp_path / "plan.json"
    result = CliRunner().invoke(app, ["plan", str(qa), "-r", str(RULES),
                                     "--only-rule", "C001", "-o", str(plan_path)])
    assert result.exit_code == 0, result.output
    plan = FixPlan.read(plan_path)
    assert [s.params for s in plan.files[str(staged)]] == [
        {"var": "tas", "attr": "history"}, {"var": "ps", "attr": "history"}]
    assert all(s.rule == "C001" and s.fix == "drop_var_attr" for s in plan.files[str(staged)])

    assert apply_plan(plan, tmp_path / "log.jsonl", dry_run=True)["changed"] == 1
    assert staged.read_bytes() == original_bytes
    assert apply_plan(plan, tmp_path / "log.jsonl")["changed"] == 1
    assert source.read_bytes() == original_bytes
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(staged) as dst:
        src.set_auto_maskandscale(False)
        dst.set_auto_maskandscale(False)
        assert dst.ncattrs() == src.ncattrs()
        for attr in src.ncattrs():
            assert dst.getncattr(attr) == src.getncattr(attr)
        for name in src.variables:
            assert dst[name][:].tobytes() == src[name][:].tobytes()
            assert dst[name].filters() == src[name].filters()
            assert dst[name].chunking() == src[name].chunking()
            assert dst[name].description == src[name].description
        assert "history" not in dst["tas"].ncattrs()
        assert "history" not in dst["ps"].ncattrs()
        assert dst["untargeted"].history == "history on untargeted"
    corrected_bytes = staged.read_bytes()
    assert apply_plan(plan, tmp_path / "log.jsonl")["changed"] == 0
    assert staged.read_bytes() == corrected_bytes


def test_global_named_variable_and_missing_attribute_are_unambiguous(tmp_path):
    with netCDF4.Dataset(tmp_path / "test.nc", "w") as ds:
        ds.history = "global attribute"
        var = ds.createVariable("global", "f8")
        var.history = "variable attribute"
        assert drop_var_attr(ds, "global", "history")
        assert ds.history == "global attribute"
        assert not drop_var_attr(ds, "global", "history")
        assert not drop_var_attr(ds, "missing", "history")


def test_only_rule_rejects_unknown_id_without_writing_plan(tmp_path):
    qa = tmp_path / "qa_result_test.json"
    qa.write_text(json.dumps(report(tmp_path / "file.nc", ["tas"])))
    output = tmp_path / "plan.json"
    result = CliRunner().invoke(app, ["plan", str(qa), "-r", str(RULES),
                                     "--only-rule", "C999", "-o", str(output)])
    assert result.exit_code == 2
    assert not output.exists()


def test_only_rule_is_repeatable_and_absence_preserves_existing_plan_behavior(tmp_path):
    qa = tmp_path / "qa_result_test.json"
    qa.write_text(json.dumps(report(tmp_path / "file.nc", ["tas"])))
    output = tmp_path / "plan.json"
    result = CliRunner().invoke(app, ["plan", str(qa), "-r", str(RULES), "--only-rule", "C001",
                                     "--only-rule", "C002", "-o", str(output)])
    assert result.exit_code == 0, result.output
    expected = FixPlan.from_classification(classify(QAReport.load(qa), RuleSet.load(RULES)))
    assert FixPlan.read(output).files == expected.files
