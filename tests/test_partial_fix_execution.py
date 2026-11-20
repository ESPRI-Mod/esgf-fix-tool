"""Failed preparation never leaks, while independently valid fixes still commit."""
import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool import fixes
from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.fixes import FixFunction
from esgf_fix_tool.plan import FixPlan, Step


def fixture(path):
    with netCDF4.Dataset(path, 'w') as ds:
        ds.variable_id = 'field'
        ds.grid_label = 'g243'
        ds.createDimension('x', 3)
        ds.createVariable('field', 'f4', ('x',), zlib=True, complevel=2)[:] = [1, 2, 3]
        ds['field'].history = 'remove'
        ds['field'].description = 'Keep scientific description.'


@pytest.mark.parametrize('workers', [1, 2])
@pytest.mark.parametrize('dry_run', [False, True])
def test_independent_metadata_survives_a_rejected_longitude_fix(tmp_path, workers, dry_run):
    p = tmp_path / 'field.nc'
    fixture(p)
    q = FixPlan('t', 'qa', {str(p): [
        Step('C001', 'drop_variable_histories', {}),
        Step('C059', 'normalize_regular_longitude', {}),
        Step('C002', 'rename_var_attr', dict(var='field', old='description', new='comment')),
    ]})
    original, inode = p.read_bytes(), p.stat().st_ino
    result = apply_plan(q, tmp_path/'log', workers=workers, dry_run=dry_run, continue_on_error=True)
    assert result['changed'] == result['failed'] == result['partial'] == 1
    row = json.loads((tmp_path/'log').read_text())
    assert row['status'] == 'partial' and row['rewrite_count'] == 0
    assert row['steps'][1]['status'] == 'failed'
    assert 'g111' in row['steps'][1]['traceback']
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds['field'][:], [1, 2, 3])
        assert ('history' in ds['field'].ncattrs()) == dry_run
        assert ('comment' in ds['field'].ncattrs()) != dry_run
        assert ds['field'].filters()['complevel'] == 2
    assert p.stat().st_ino == inode
    if dry_run:
        assert p.read_bytes() == original


def test_failed_private_mutations_are_discarded_before_retry(tmp_path, monkeypatch):
    p = tmp_path/'field.nc'
    fixture(p)
    def broken(ds):
        ds.renameDimension('x', 'bad_dimension')
        ds['field'].comment = 'must never persist'
        raise ValueError('failure after private mutation')
    monkeypatch.setitem(fixes.REGISTRY, 'broken_preview', FixFunction('broken_preview', 'metadata', broken))
    q = FixPlan('t', 'qa', {str(p): [Step('bad', 'broken_preview', {}), Step('C001', 'drop_variable_histories', {})]})
    apply_plan(q, tmp_path/'log', continue_on_error=True)
    with netCDF4.Dataset(p) as ds:
        assert ds['field'].dimensions == ('x',)
        assert 'comment' not in ds['field'].ncattrs()
        assert 'history' not in ds['field'].ncattrs()


def test_failed_hybrid_prerequisite_blocks_its_metadata_but_not_history(tmp_path, monkeypatch):
    p = tmp_path/'field.nc'
    fixture(p)
    def broken(ds, **params):
        raise ValueError('Unapproved coefficients')
    monkeypatch.setitem(fixes.REGISTRY, 'fix_hybrid_coefficients', FixFunction('fix_hybrid_coefficients', 'metadata', broken))
    q = FixPlan('t', 'qa', {str(p): [
        Step('C074', 'fix_hybrid_coefficients', {}),
        Step('C102', 'normalize_hybrid_coordinate', {}),
        Step('C001', 'drop_variable_histories', {}),
    ]})
    apply_plan(q, tmp_path/'log', continue_on_error=True)
    row = json.loads((tmp_path/'log').read_text())
    assert [e.get('status', 'done') for e in row['steps']] == ['failed', 'blocked', 'done']
    with netCDF4.Dataset(p) as ds:
        assert 'history' not in ds['field'].ncattrs()


def test_fail_fast_preserves_original_file(tmp_path):
    p = tmp_path/'field.nc'
    fixture(p)
    before = p.read_bytes()
    q = FixPlan('t', 'qa', {str(p): [Step('C001', 'drop_variable_histories', {}), Step('C059', 'normalize_regular_longitude', {})]})
    with pytest.raises(ValueError, match='g111'):
        apply_plan(q, tmp_path/'log')
    assert p.read_bytes() == before


def test_failed_precision_of_one_axis_does_not_block_another_axes_bounds(tmp_path,monkeypatch):
    p=tmp_path/'axes.nc'
    fixture(p)
    with netCDF4.Dataset(p,'a') as ds:
        ds.createVariable('depth','f4',('x',))[:]=[1,2,3]
        ds['depth'].scale_factor=1.0  # Packed coordinates are deliberately refused.
        ds.createVariable('plev','f8')[:]=50000.
    def bounds(ds,coordinate):
        ds[coordinate].long_name='Independent pressure bounds preparation completed'
        return True
    monkeypatch.setitem(fixes.REGISTRY,'add_reviewed_coordinate_bounds',FixFunction('add_reviewed_coordinate_bounds','metadata',bounds))
    q=FixPlan('t','qa',{str(p):[
        Step('C117','promote_coordinate_to_double',dict(var='depth')),
        Step('bounds','add_reviewed_coordinate_bounds',dict(coordinate='plev')),
    ]})
    apply_plan(q,tmp_path/'log',continue_on_error=True)
    with netCDF4.Dataset(p) as ds:
        assert ds['plev'].long_name=='Independent pressure bounds preparation completed'
        assert ds['depth'].dtype==np.dtype('f4')
