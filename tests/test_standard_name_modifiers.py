import shutil
import json
import os
from pathlib import Path

import netCDF4
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.fixes.attributes import drop_descriptive_standard_name
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.rules import RuleSet

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("C004", "klevp1", "of layer interfaces", "number of layer interfaces"),
    ("C005", "klev", "of layers", "number of layers"),
    ("C006", "presinter", "at interface of vertical levels", "Pressure at interface of vertical levels"),
]


@pytest.mark.parametrize("rule_id,var,modifier,expected", CASES)
def test_modifier_rule_pipeline_and_idempotence(tmp_path, rule_id, var, modifier, expected):
    source = tmp_path / "source.nc"
    with netCDF4.Dataset(source, "w") as ds:
        ds.history = "global history"
        ds.createDimension("n", 3)
        v = ds.createVariable(var, "f8", ("n",))
        v[:] = [1, 2, 3]
        v.standard_name = expected
        v.units = "Pa" if var == "presinter" else "1"
        if var != "presinter":
            v.long_name = "existing description"
        data = ds.createVariable("data", "i2", ("n",), zlib=True, complevel=3, fill_value=-32768)
        data.scale_factor = 0.5
        data.set_auto_maskandscale(False)
        data[:] = [-32768, 11, 23]
    original_bytes = source.read_bytes()
    staged = tmp_path / "staged.nc"
    shutil.copyfile(source, staged)
    message = (f'Standard name modifier "{modifier}" for variable {var} '
               "is not a valid modifier according to CF Appendix C")
    report = tmp_path / "qa_result_test.json"
    report.write_text(json.dumps({"fail": {"3": {"[CF-Conventions] §3.3 Standard Name": {
        message: {"dataset": [str(staged)]}}}}}))
    rules = RuleSet.load(ROOT / "rules/standard_name_modifiers.yaml", ROOT / "rules/catalogue_fallbacks.yaml")
    classified = classify(QAReport.load(report), rules)
    assert classified.items[0].outcome is Outcome.FIX
    plan = FixPlan.from_classification(classified)
    step = plan.files[str(staged)][0]
    assert (step.rule, step.fix, step.params) == (
        rule_id, "drop_descriptive_standard_name", {"var": var, "expected": expected})

    # A dry run exercises the real fix on a discarded copy, including on hardlinks.
    assert apply_plan(plan, tmp_path / "log.jsonl", dry_run=True)["changed"] == 1
    assert staged.read_bytes() == original_bytes
    assert apply_plan(plan, tmp_path / "log.jsonl")["changed"] == 1
    assert source.read_bytes() == original_bytes
    with netCDF4.Dataset(staged) as ds, netCDF4.Dataset(source) as src:
        assert "standard_name" not in ds[var].ncattrs()
        assert ds[var].long_name == (expected if var == "presinter" else "existing description")
        assert ds[var].units == src[var].units
        assert ds.history == src.history
        ds.set_auto_maskandscale(False)
        src.set_auto_maskandscale(False)
        for name in src.variables:
            assert ds[name][:].tobytes() == src[name][:].tobytes()
            assert ds[name].filters() == src[name].filters()
            assert ds[name].chunking() == src[name].chunking()
    corrected_bytes = staged.read_bytes()
    assert apply_plan(plan, tmp_path / "log.jsonl")["changed"] == 0
    assert staged.read_bytes() == corrected_bytes


def test_changed_or_missing_standard_name_is_left_alone(tmp_path):
    with netCDF4.Dataset(tmp_path / "test.nc", "w") as ds:
        ds.createDimension("n", 1)
        var = ds.createVariable("presinter", "f8", ("n",))
        var.standard_name = "air_pressure"
        expected = "Pressure at interface of vertical levels"
        assert not drop_descriptive_standard_name(ds, "presinter", expected)
        assert var.standard_name == "air_pressure"
        assert not drop_descriptive_standard_name(ds, "missing", expected)
        var.delncattr("standard_name")
        assert not drop_descriptive_standard_name(ds, "presinter", expected)


def test_current_report_variable_name_is_used(tmp_path):
    # No dependency on a previous/next coordinate rename or its original name.
    rules = RuleSet.load(ROOT / "rules/standard_name_modifiers.yaml")
    from esgf_fix_tool.report import Finding

    finding = Finding(3, "[CF-Conventions] §3.3 Standard Name",
                      'Standard name modifier "of layers" for variable renamed '
                      'is not a valid modifier according to CF Appendix C', "dataset", "file.nc")
    match = rules.match(finding)
    with netCDF4.Dataset(tmp_path / "test.nc", "w") as ds:
        var = ds.createVariable("renamed", "f8")
        var.standard_name = "number of layers"
        assert drop_descriptive_standard_name(ds, **match.rule.resolve_params(match.groups))


def test_other_modifiers_do_not_acquire_an_unverified_fix():
    from esgf_fix_tool.report import Finding

    rules = RuleSet.load(ROOT / "rules/standard_name_modifiers.yaml", ROOT / "rules/catalogue_fallbacks.yaml")
    finding = Finding(3, "[CF-Conventions] §3.3 Standard Name",
                      'Standard name modifier "uninvestigated" for variable other '
                      'is not a valid modifier according to CF Appendix C', "dataset", "file.nc")
    match = rules.match(finding)
    assert match.rule.id == "S003"
    assert match.rule.fix is None
