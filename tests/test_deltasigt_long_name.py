"""C084 installs the approved deltasigt title even when QA hides old-text formatting."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


TITLE = "sigma_t criterion that determines layer thickness"
OLD = TITLE + ". The method is documented in Griffies et al. 2016 ( https://doi.org/10.5194/gmd-9-3231-2016 )."


def snapshot(path):
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return (
            {a: ds.getncattr(a) for a in ds.ncattrs()},
            {n: (v[:].tobytes(), v.filters(), v.chunking(),
                 {a: v.getncattr(a) for a in v.ncattrs()
                  if (n, a) != ('deltasigt', 'long_name')})
             for n, v in ds.variables.items()},
        )


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('current', [
    OLD, OLD + ' ', OLD + '\n', OLD.replace('. The method', '.\nThe method'),
    OLD.replace('https:', 'https\\:'), TITLE,
])
def test_c084_replaces_only_the_title_without_rewriting_science(tmp_path, monkeypatch, fmt, current):
    path = tmp_path/'mlotst.nc'
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.createDimension('time', 2)
        ds.createDimension('lat', 2)
        coord = ds.createVariable('deltasigt', 'f8')
        coord.assignValue(.03)
        coord.standard_name = 'sea_water_sigma_t_difference'
        coord.units = 'kg m-3'
        coord.long_name = current
        coord.comment = 'Keep this independent scientific description.'
        options = dict(zlib=True, complevel=2) if fmt.startswith('NETCDF4') else {}
        main = ds.createVariable('mlotst', 'f4', ('time', 'lat'), fill_value=np.float32(1e20), **options)
        main[:] = [[1., 2.], [3., 4.]]
        main[1, 0] = np.ma.masked
        main.coordinates = 'deltasigt'
        ds.history = 'Keep global history.'

    # Reproduce the old diagnostic text even if the stored attribute has extra
    # whitespace, escapes, or has already been corrected since the QA run.
    finding = Finding(2, '[CMIP7] [COORD004] Coordinate metadata recommendations',
        f"'deltasigt' long_name='{OLD}'; the recommended value is '{TITLE}'.",
        'CMIP7.OCE.mlotst.tavg-u-hxy-sea.mon.glb.g112.OtherModel.historical.r2i1p1f1', str(path))
    report = QAReport(Path('qa.json'), {}, [finding])
    plan = FixPlan.from_classification(classify(report, RuleSet.load(*default_rule_paths())))
    assert [s.rule for s in plan.files[str(path)]] == ['C084']

    def refuse_rewrite(*args):
        raise AssertionError('Changing long_name must not rewrite the science field')

    monkeypatch.setattr('esgf_fix_tool.batch_write.Batch.write', refuse_rewrite)
    before, original, inode = snapshot(path), path.read_bytes(), path.stat().st_ino
    expected_changed = int(current != TITLE)
    assert apply_plan(plan, tmp_path/'dry', dry_run=True)['changed'] == expected_changed
    assert path.read_bytes() == original
    assert apply_plan(plan, tmp_path/'log')['changed'] == expected_changed
    with netCDF4.Dataset(path) as ds:
        assert ds['deltasigt'].long_name == TITLE
    assert snapshot(path) == before and path.stat().st_ino == inode
    corrected = path.read_bytes()
    assert apply_plan(plan, tmp_path/'replay')['changed'] == 0
    assert path.read_bytes() == corrected


@pytest.mark.parametrize('var,attr,value', [
    ('deltasigt', 'long_name', 'An unapproved new title'),
    ('deltasigt', 'comment', TITLE),
    ('height', 'long_name', TITLE),
])
def test_other_attribute_changes_still_require_the_observed_old_text(tmp_path, var, attr, value):
    path = tmp_path/'coordinate.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        v = ds.createVariable(var, 'f8')
        v.assignValue(.03)
        v.setncattr(attr, 'Different current content')
    original = path.read_bytes()
    plan = FixPlan('test', 'qa', {str(path): [Step('C084', 'set_var_attr_if_matches', dict(
        var=var, attr=attr, expected=OLD, value=value))]})
    assert apply_plan(plan, tmp_path/'log')['changed'] == 0
    assert path.read_bytes() == original


def test_latest_report_selects_the_four_deltasigt_replacements():
    source = Path('/Users/glipsl/Desktop/QA_results_v5/qa_result_20261117-1023_2d517d8032b8ff5b6eb96fc1c434e144.json')
    if not source.is_file():
        pytest.skip('The exact 17 November QA fixture is unavailable')
    rules = RuleSet.load(*default_rule_paths())
    report = QAReport.load(source)
    findings = [f for f in report.findings if f.message.startswith("'deltasigt' long_name=")]
    assert len({f.path for f in findings}) == 4
    for finding in findings:
        match = rules.match(finding)
        assert match.rule.id == 'C084'
        assert match.rule.resolve_params(match.groups)['value'] == TITLE
