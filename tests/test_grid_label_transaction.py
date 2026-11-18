"""Grid metadata and basename remain consistent within one apply transaction."""

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet

CASES = [
    ('co2mass', 'u', 'g111', 'hm', 'glb', 'g010'),
    ('tos', 'sea', 'g112', 'hm', 'glb', 'g010'),
    ('siarea', 'u', 'g112', 'hm', 'nh', 'g011'),
    ('siarea', 'u', 'g112', 'hm', 'sh', 'g012'),
    ('hfacrossline', 'sea', 'g112', 'ht', 'glb', 'g014'),
]


def make(path, name, area, source, horizontal, region, target):
    with netCDF4.Dataset(path, 'w') as ds:
        ds.setncatts(dict(source_id='OtherModel', experiment_id='historical',
            variant_label='r2i1p1f1', variable_id=name, frequency='mon',
            grid_label=source, region=region, horizontal_label=horizontal,
            branded_variable=f'{name}_tavg-u-{horizontal}-{area}'))
        ds.createDimension('time', 2)
        dims = ('time',)
        if horizontal == 'ht':
            ds.createDimension('line', 16)
            ds.createDimension('strlen', 255)
            sector = ds.createVariable('sector', 'S1', ('line', 'strlen'))
            sector[:] = np.full((16, 255), b' ', dtype='S1')
            sector.standard_name = 'region'
            dims += ('line',)
        v = ds.createVariable(name, 'f4', dims, zlib=True, complevel=2)
        v[:] = np.arange(v.size).reshape(v.shape)
        ds.history = 'Original global history'
    return Step('grid', 'set_reduced_grid_label', dict(
        source=source, horizontal=horizontal, region=region, target=target))


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('workers', [1, 2])
def test_grid_change_alone_renames_and_is_replayable(tmp_path, case, explicit, workers):
    name, area, source, horizontal, region, target_grid = case
    path = tmp_path/f'{name}_tavg-u-{horizontal}-{area}_mon_{region}_{source}_OtherModel_historical_r2i1p1f1_185001-185912.nc'
    grid = make(path, *case)
    target = path.with_name(path.name.replace(f'_{source}_', f'_{target_grid}_'))
    with netCDF4.Dataset(path) as ds:
        science = ds[name][:].tobytes()
        filters, chunks = ds[name].filters(), ds[name].chunking()
    steps = [grid]
    if explicit:
        # Even a reversed manual plan must change the attribute before C097's
        # content validation, and perform only one final basename rename.
        steps.insert(0, Step('C097', 'sync_filename_grid_label', dict(source=source, target=target_grid)))
    plan = FixPlan('test', 'synthetic', {str(path): steps})
    before, inode = path.read_bytes(), path.stat().st_ino
    assert apply_plan(plan, tmp_path/'dry', dry_run=True, workers=workers)['changed'] == 1
    assert path.read_bytes() == before and not target.exists()
    assert apply_plan(plan, tmp_path/'apply', workers=workers)['changed'] == 1
    assert not path.exists() and target.stat().st_ino == inode
    record = json.loads((tmp_path/'apply').read_text())
    assert record['filename_renamed'] and record['final_file'] == str(target)
    assert record['rewrite_count'] == 0
    with netCDF4.Dataset(target) as ds:
        assert ds.grid_label == target_grid and ds.region == region
        assert ds[name][:].tobytes() == science
        assert ds[name].filters() == filters and ds[name].chunking() == chunks
        assert ds.history == 'Original global history'
    after = target.read_bytes()
    assert apply_plan(plan, tmp_path/'replay', workers=workers)['changed'] == 0
    assert target.read_bytes() == after


def test_implicit_grid_rename_collision_aborts_before_any_other_write(tmp_path):
    case = CASES[2]
    path = tmp_path/'siarea_tavg-u-hm-u_mon_nh_g112_OtherModel_historical_r2i1p1f1_185001-185912.nc'
    step = make(path, *case)
    target = path.with_name(path.name.replace('_g112_', '_g011_'))
    target.write_bytes(b'existing output')
    first = tmp_path/'first.nc'
    with netCDF4.Dataset(first, 'w') as ds:
        ds.createVariable('v', 'i4').history = 'keep'
    plan = FixPlan('test', 'synthetic', {
        str(first): [Step('history', 'drop_variable_histories', {})], str(path): [step]})
    before = first.read_bytes(), path.read_bytes()
    with pytest.raises(FileExistsError):
        apply_plan(plan, tmp_path/'log')
    assert (first.read_bytes(), path.read_bytes()) == before
    assert target.read_bytes() == b'existing output'


def test_latest_report_selects_all_requested_existing_recipes():
    path = Path('/Users/glipsl/Desktop/QA_results_v4/qa_result_20261117-0953_0b256d124639a9830278005130a1f60c.json')
    if not path.exists():
        pytest.skip('The exact 17 November QA fixture is unavailable')
    result = classify(QAReport.load(path), RuleSet.load(*default_rule_paths()))
    plan = FixPlan.from_classification(result)
    for rule, count in [('C097', 22), ('C102', 17), ('C103', 1), ('C084', 4)]:
        items = [i for i in result.items if i.match and i.match.rule.id == rule]
        assert len({i.finding.path for i in items}) == count, rule
        assert all(i.outcome.value == 'fix' for i in items)
        assert all(any(s.rule == rule for s in plan.files[i.finding.path]) for i in items)
    mask = [i for i in result.items if i.match and i.match.rule.id == 'C101w']
    assert len(mask) == 1 and mask[0].outcome.value == 'fix'
    assert any(s.fix == 'convert_basin_region_mask' for s in plan.files[mask[0].finding.path])
