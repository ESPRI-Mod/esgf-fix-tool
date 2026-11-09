"""Exercise shared recipes in every relevant order and refuse lossy deletions."""
from itertools import permutations
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.rules import RuleSet


def apply(path, tmp, steps):
    plan = FixPlan('test', 'synthetic', {str(path): steps})
    before = path.read_bytes()
    apply_plan(plan, tmp/'dry.jsonl', dry_run=True)
    assert path.read_bytes() == before
    apply_plan(plan, tmp/'apply.jsonl')
    after = path.read_bytes()
    assert apply_plan(plan, tmp/'replay.jsonl')['changed'] == 0
    assert path.read_bytes() == after


@pytest.mark.parametrize('ids', list(permutations(['C032', 'C078', 'C096', 'C101a'])))
def test_four_basin_diagnostics_share_one_idempotent_recipe(tmp_path, ids):
    path = tmp_path/'basin.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.source_id = 'IPSL-CM6-ESMCO2'
        ds.variable_id = 'hfbasin'
        ds.createDimension('basin', 3)
        basin = ds.createVariable('basin', 'f8', ('basin',))
        basin[:] = [3, 1, 2]
        basin.standard_name = 'region'
        basin.long_name = 'Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)'
        basin.flag_values = [1., 2., 3.]
        basin.flag_meanings = 'global_ocean atlantic_ocean indo_pacific_ocean'
        v = ds.createVariable('hfbasin', 'f4', ('basin',))
        v[:] = [33, 11, 22]
        v.coordinates = 'basin'
        v.cell_methods = 'depth: longitude: sum where sea (along a zig-zag grid path spanning a basin) where sea time: mean'
    steps = [Step(id, 'fix_basin_sector', {}) for id in ids]
    apply(path, tmp_path, steps)
    with netCDF4.Dataset(path) as ds:
        np.testing.assert_array_equal(ds['basin'][:], [3, 1, 2])
        np.testing.assert_array_equal(ds['hfbasin'][:], [33, 11, 22])
        assert ds['basin'].units == '1' and 'standard_name' not in ds['basin'].ncattrs()
        assert 'flag_values' not in ds['basin'].ncattrs()
        assert ds['sector'].standard_name == 'region'
        assert [r.tobytes().decode().strip() for r in ds['sector'][:]] == ['indian_pacific_ocean','global_ocean','atlantic_arctic_ocean']


def text_file(path, name, alias=False, different=False, sector=True):
    with netCDF4.Dataset(path, 'w') as ds:
        ds.variable_id = 'mfo' if name == 'line' else 'landCoverFrac'
        ds.createDimension(name, 2)
        ds.createDimension('str_len', 255)
        labels = np.full((2, 255), b'\0', dtype='S1')
        for row, label in zip(labels, ['atlantic_ocean', 'pacific_ocean']):
            row[:len(label)] = np.frombuffer(label.encode(), dtype='S1')
        source = name + '_label' if alias else name
        ds.createVariable(source, 'S1', (name, 'str_len'))[:] = labels
        if sector:
            if different:
                labels = labels[::-1]
            ds.createVariable('sector', 'S1', (name, 'str_len'))[:] = labels
        main = ds.createVariable(ds.variable_id, 'f4', (name,))
        main[:] = [11, 22]
        main.coordinates = 'sector ' + source if sector else source


@pytest.mark.parametrize('name', ['line', 'type'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('order', list(permutations(['C099', 'C100', 'C087'])))
def test_label_deletion_padding_and_string_dimension_in_any_order(tmp_path, name, alias, order):
    path = tmp_path/'labels.nc'
    text_file(path, name, alias)
    recipes = {'C099': Step('C099','rename_redundant_text_label',{'var':name}),
               'C100': Step('C100','normalize_text_padding',{}),
               'C087': Step('C087','rename_scalar_strlen',{'coordinate':'sector'})}
    apply(path, tmp_path, [recipes[id] for id in order])
    with netCDF4.Dataset(path) as ds:
        assert name in ds.dimensions
        assert name not in ds.variables and name + '_label' not in ds.variables
        assert ds['sector'].dimensions == (name, 'strlen')
        assert ds[ds.variable_id].coordinates == 'sector'
        np.testing.assert_array_equal(ds[ds.variable_id][:], [11, 22])


@pytest.mark.parametrize('name', ['line', 'type'])
def test_different_sector_information_refuses_deletion_before_write(tmp_path, name):
    path = tmp_path/'different.nc'
    text_file(path, name, different=True)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='same ordered labels'):
        apply_plan(FixPlan('t','s',{str(path):[Step('C099','rename_redundant_text_label',{'var':name})]}), tmp_path/'log')
    assert path.read_bytes() == before


@pytest.mark.parametrize('name', ['line', 'type'])
@pytest.mark.parametrize('alias', [False, True])
def test_missing_sector_is_copied_from_valid_labels_before_removal(tmp_path, name, alias):
    path = tmp_path/'missing.nc'
    text_file(path, name, alias=alias, sector=False)
    apply(path, tmp_path, [Step('C099','rename_redundant_text_label',{'var':name})])
    with netCDF4.Dataset(path) as ds:
        assert name not in ds.variables and name + '_label' not in ds.variables
        assert [r.tobytes().decode().strip() for r in ds['sector'][:]] == ['atlantic_ocean', 'pacific_ocean']


def test_tossq_uses_the_approved_temperature_scale_without_changing_values(tmp_path):
    path = tmp_path/'tossq.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('time',2)
        v = ds.createVariable('tossq','f4',('time',))
        v[:] = [3,8]
        v.standard_name = 'square_of_sea_surface_temperature'
        v.units = 'degC2'
        v.cell_methods = 'area: mean where sea time: mean'
    apply(path, tmp_path, [Step('C076','add_temperature_units_metadata',{'var':'tossq'})])
    with netCDF4.Dataset(path) as ds:
        assert ds['tossq'].units_metadata == 'temperature: on_scale'
        np.testing.assert_array_equal(ds['tossq'][:], [3,8])


@pytest.mark.parametrize('order', [False, True])
def test_type_strlen_rule_uses_sector_after_duplicate_type_is_deleted(tmp_path, order):
    path = tmp_path/'type.nc'
    text_file(path, 'type')
    steps = [Step('C099','rename_redundant_text_label',{'var':'type'}),
             Step('C087','rename_scalar_strlen',{'coordinate':'type'})]
    if order:
        steps.reverse()
    apply(path, tmp_path, steps)
    with netCDF4.Dataset(path) as ds:
        assert ds['sector'].dimensions == ('type', 'strlen')
        assert 'type' in ds.dimensions and 'type' not in ds.variables


def test_manifest_includes_relative_files_once_and_keeps_duplicate_id_guard(tmp_path):
    rule = '- id: X\n  check: x\n  message: x\n  action: review\n'
    (tmp_path/'leaf.yaml').write_text(rule)
    (tmp_path/'all.yaml').write_text('include: [leaf.yaml, leaf.yaml]\n')
    assert [r.id for r in RuleSet.load(tmp_path/'all.yaml', tmp_path/'leaf.yaml').rules] == ['X']
    (tmp_path/'other.yaml').write_text(rule)
    with pytest.raises(ValueError, match='duplicate rule ids'):
        RuleSet.load(tmp_path/'all.yaml',tmp_path/'other.yaml')


def test_recursive_manifest_is_rejected(tmp_path):
    (tmp_path/'a.yaml').write_text('include: [b.yaml]\n')
    (tmp_path/'b.yaml').write_text('include: [a.yaml]\n')
    with pytest.raises(ValueError, match='Circular catalogue include'):
        RuleSet.load(tmp_path/'a.yaml')


def test_basin_trigger_rules_are_together_and_call_one_method():
    root = Path(__file__).resolve().parents[1]
    rules = RuleSet.load(root/'rules/basin_coordinates.yaml')
    shared = [r for r in rules.rules if r.id in ('C032','C078','C096','C101a')]
    assert len(shared) == 4
    assert {r.fix for r in shared} == {'fix_basin_sector'}
