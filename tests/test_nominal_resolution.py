"""The reviewed resolution correction changes one global attribute only."""

import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.attributes import set_global_attr_if_matches
from esgf_fix_tool.fixes.layouts import _attribute_type
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.preview import Variable
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


CHECK = "[CMIP7] [ATTR004] Global attribute 'nominal_resolution' vocabulary check"
MESSAGE = "'150 km' not found in collection 'nominal_resolution' for NetCDF attribute 'nominal_resolution'"


def plan(path):
    # No model/experiment/filename restriction: the reviewed diagnostic is enough.
    finding = Finding(3, CHECK, MESSAGE, 'CMIP7.ANY.any_product.another_model.another_experiment', str(path))
    result = FixPlan.from_classification(classify(
        QAReport(None, {}, [finding]), RuleSet.load(*default_rule_paths())
    ))
    steps = result.files[str(path)]
    assert len(steps) == 1 and steps[0].rule == 'C120'
    assert steps[0].params == {'attr': 'nominal_resolution', 'expected': '150 km', 'value': '100 km'}
    return result


@pytest.mark.parametrize('fmt,string_type', [
    ('NETCDF4', False), ('NETCDF4', True),
    ('NETCDF4_CLASSIC', False), ('NETCDF3_64BIT_OFFSET', False),
])
def test_resolution_is_direct_idempotent_metadata_edit(tmp_path, monkeypatch, fmt, string_type):
    path = tmp_path / 'science.nc'
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        if string_type:
            ds.setncattr_string('nominal_resolution', '150 km')
        else:
            ds.nominal_resolution = '150 km'
        ds.grid_label = 'g243'
        ds.history = 'preserve global history'
        ds.createDimension('cell', 4)
        options = {'zlib': True, 'complevel': 2} if fmt.startswith('NETCDF4') else {}
        var = ds.createVariable('science', 'f4', ('cell',), fill_value=1e20, **options)
        var[:] = np.ma.array([1., 2., 3., 4.], mask=[False, True, False, False])
        var.long_name = 'preserve scientific attributes'
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        raw, filters, chunks = ds['science'][:].tobytes(), ds['science'].filters(), ds['science'].chunking()
        xtype = _attribute_type(ds, 'nominal_resolution')
    original = path.read_bytes()
    inode = path.stat().st_ino
    q = plan(path)

    def forbid_rewrite(*args, **kwargs):
        raise AssertionError('A global attribute must not cause a grouped rewrite')

    monkeypatch.setattr(Batch, 'write', forbid_rewrite)
    getitem = Variable.__getitem__

    def forbid_science_read(var, key):
        assert var.name != 'science'
        return getitem(var, key)

    monkeypatch.setattr(Variable, '__getitem__', forbid_science_read)
    assert apply_plan(q, tmp_path / 'dry.jsonl', dry_run=True)['changed'] == 1
    assert path.read_bytes() == original
    assert apply_plan(q, tmp_path / 'apply.jsonl')['changed'] == 1
    assert path.stat().st_ino == inode
    assert json.loads((tmp_path / 'apply.jsonl').read_text())['rewrite_count'] == 0
    with netCDF4.Dataset(path) as ds:
        assert ds.nominal_resolution == '100 km'
        assert _attribute_type(ds, 'nominal_resolution') == xtype
        assert ds.grid_label == 'g243' and ds.history == 'preserve global history'
        ds.set_auto_maskandscale(False)
        assert ds['science'][:].tobytes() == raw
        assert ds['science'].filters() == filters and ds['science'].chunking() == chunks
        assert ds['science'].long_name == 'preserve scientific attributes'
    corrected = path.read_bytes()
    assert apply_plan(q, tmp_path / 'again.jsonl')['changed'] == 0
    assert path.read_bytes() == corrected


@pytest.mark.parametrize('value', [None, '200 km', 150])
def test_unreviewed_global_values_are_preserved(tmp_path, value):
    path = tmp_path / 'other.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        if value is not None:
            ds.nominal_resolution = value
        with pytest.raises(ValueError):
            set_global_attr_if_matches(ds, 'nominal_resolution', '150 km', '100 km')
        if value is None:
            assert 'nominal_resolution' not in ds.ncattrs()
        else:
            assert ds.nominal_resolution == value
