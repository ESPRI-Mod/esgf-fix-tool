import shutil
import json
import os
from pathlib import Path
from types import SimpleNamespace

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.fixes import layouts
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.rules import RuleSet

ROOT = Path(__file__).resolve().parents[1]
CHECK = '[CMIP7] [COORD002] Data-variable coordinate order'


def plan(path, fix, params):
    return FixPlan('test', 'test.json', {str(path): [Step('test', fix, params)]})


def make_histogram(path, var, level, fmt='NETCDF4'):
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.history = 'global history retained'
        for d, size in [('time', None), (level, 2), ('tau', 3), ('lat', 2), ('lon', 2)]:
            ds.createDimension(d, size)
        ds.createVariable(level, 'f8', (level,))[:] = [1.25, 2.75]
        v = ds.createVariable(var, 'i2', ('time', level, 'tau', 'lat', 'lon'),
                              zlib=fmt.startswith('NETCDF4'), fill_value=-32768)
        v.scale_factor, v.add_offset, v.units = .125, 2., '%'
        v.coordinates = level
        v.set_auto_maskandscale(False)
        raw = np.arange(48, dtype='i2').reshape(2, 2, 3, 2, 2)
        raw[0, 0, 0, 0, 0] = -32768
        v[...] = raw
        ds.createVariable('unrelated', 'f8', ('lat', 'lon'))[...] = [[1., 2.], [3., 4.]]


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('var,old,new', [('jpdftaureicemodis', 'ReffIce', 'effectRadIc'),
                                        ('jpdftaureliqmodis', 'effectRadLi', 'effectRadLi'),
                                        ('clisccp', 'pressure2', 'plev')])
def test_histogram_layout_is_exact_and_idempotent(tmp_path, fmt, var, old, new):
    source, staged = tmp_path / 'source.nc', tmp_path / 'staged.nc'
    make_histogram(source, var, old, fmt)
    original = source.read_bytes()
    shutil.copyfile(source, staged)
    target = ['time', 'tau', new, 'lat', 'lon']
    params = dict(var=var, target_order=target, aliases={old: new} if old != new else {}, backend='python')
    p = plan(staged, 'fix_data_layout', params)
    assert apply_plan(p, tmp_path / 'log', dry_run=True)['changed'] == 1
    assert staged.read_bytes() == original
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    assert source.read_bytes() == original
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(staged) as dst:
        src.set_auto_maskandscale(False)
        dst.set_auto_maskandscale(False)
        assert dst[var].dimensions == tuple(target)
        assert dst[var][:].tobytes() == np.transpose(src[var][:], (0, 2, 1, 3, 4)).tobytes()
        assert src[old][:].tobytes() == dst[new][:].tobytes()
        assert src['unrelated'][:].tobytes() == dst['unrelated'][:].tobytes()
        assert dst.history == src.history
        assert dst[var].scale_factor == src[var].scale_factor
        assert dst[var].add_offset == src[var].add_offset
        assert dst[var].filters() == src[var].filters()
        assert dst.dimensions['time'].isunlimited()
    before = staged.read_bytes()
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0
    assert staged.read_bytes() == before
    log = [json.loads(l) for l in (tmp_path / 'log').read_text().splitlines()]
    assert all(r['dry_run'] or r['steps'][0]['layout_verified'] for r in log)


@pytest.mark.parametrize('dtype', ['i2', 'f4'])
def test_singleton_removal_does_not_average_or_unpack(tmp_path, dtype):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('time', None)
        ds.createDimension('y', 1)
        ds.createDimension('x', 1)
        v = ds.createVariable('science', dtype, ('time', 'y', 'x'), zlib=True, fill_value=-32768)
        if dtype == 'i2':
            v.scale_factor, v.add_offset = .25, 3.
        v.set_auto_maskandscale(False)
        raw = np.array([-32768, 3, 7], dtype=dtype).reshape(3, 1, 1)
        if dtype == 'f4':
            raw[:] = np.array([np.nan, -0., np.inf], dtype='f4').reshape(3, 1, 1)
        v[...] = raw
        v.coordinates = 'nav_lat nav_lon'
        ds.createVariable('nav_lat', 'f8', ('y', 'x'))[...] = [[0.]]
        ds.createVariable('nav_lon', 'f8', ('y', 'x'))[...] = [[0.]]
    p = plan(path, 'fix_data_layout', dict(var='science', target_order=['time'], drop=['y', 'x'], backend='python'))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['science'].dimensions == ('time',)
        assert ds['science'][:].tobytes() == raw.ravel().tobytes()
        assert ds['science'].coordinates == ''
        assert ds['nav_lat'].shape == (1, 1)
        if dtype == 'i2':
            assert ds['science'].scale_factor == .25
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0


@pytest.mark.parametrize('old,new', [('ReffIce', 'effectRadIc'), ('pressure2', 'plev')])
@pytest.mark.parametrize('sequence', ['rename_first', 'layout_first', 'same_plan'])
def test_layout_and_previous_coordinate_recipe_are_independent(tmp_path, old, new, sequence):
    path = tmp_path / 'test.nc'
    make_histogram(path, 'science', old)
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        values, coordinate = ds['science'][:].copy(), ds[old][:].copy()
    rename = plan(path, 'rename_coordinate', dict(sources=[old], target=new))
    layout = plan(path, 'fix_data_layout', dict(var='science', target_order=['time', 'tau', new, 'lat', 'lon'],
                                               aliases={old: new}, backend='python'))
    if sequence == 'same_plan':
        rename.files[str(path)].extend(layout.files[str(path)])
        plans = [rename]
    else:
        plans = [rename, layout] if sequence == 'rename_first' else [layout, rename]
    for p in plans:
        apply_plan(p, tmp_path / 'log')
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['science'][:].tobytes() == values.transpose(0, 2, 1, 3, 4).tobytes()
        assert ds[new][:].tobytes() == coordinate.tobytes()
    for p in plans:
        assert apply_plan(p, tmp_path / 'log')['changed'] == 0


def test_non_singleton_reduction_is_refused_without_replacing_file(tmp_path):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        for d, size in [('time', 2), ('y', 2), ('x', 1)]:
            ds.createDimension(d, size)
        ds.createVariable('science', 'f8', ('time', 'y', 'x'))[...] = np.arange(4).reshape(2, 2, 1)
    before = path.read_bytes()
    p = plan(path, 'fix_data_layout', dict(var='science', target_order=['time'], drop=['y', 'x']))
    with pytest.raises(ValueError, match='non-singleton'):
        apply_plan(p, tmp_path / 'log')
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.fixtmp'))


@pytest.mark.parametrize('old,new,count', [('oline', 'line', 16), ('siline', 'line', 4), ('vegtype', 'type', 15)])
def test_auxiliary_dimension_rename_preserves_labels_and_existing_sector(tmp_path, old, new, count):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension(old, count)
        ds.createDimension('strlen', 3)
        for name in [new, 'sector']:
            ds.createVariable(name, 'S1', (old, 'strlen'))[...] = np.full((count, 3), b'x', dtype='S1')
        data = ds.createVariable('science', 'f8', (old,))
        data[:] = np.arange(count)
        data.coordinates = f'{new} sector'
    p = plan(path, 'rename_auxiliary_dimension', dict(var='science', source=old, target=new, coordinate=new))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        assert ds['science'].dimensions == (new,)
        assert ds[new].dimensions == (new, 'strlen')
        assert ds[new][:].tobytes() == b'x' * count * 3
        assert ds['sector'][:].tobytes() == b'x' * count * 3
        assert list(ds['science'][:]) == list(range(count))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0


def test_corrupt_layout_rewrite_is_discarded(tmp_path, monkeypatch):
    path = tmp_path / 'test.nc'
    make_histogram(path, 'science', 'radius')
    before = path.read_bytes()
    from esgf_fix_tool.batch_write import Batch
    real = Batch.write
    def corrupt(batch, dst):
        real(batch, dst)
        with netCDF4.Dataset(dst, 'a') as ds:
            ds['science'].set_auto_maskandscale(False)
            ds['science'][0, 0, 0, 0, 0] = 999
    monkeypatch.setattr(Batch, 'write', corrupt)
    p = plan(path, 'fix_data_layout', dict(var='science', target_order=['time', 'tau', 'radius', 'lat', 'lon'], backend='python'))
    with pytest.raises(ValueError, match='Raw values'):
        apply_plan(p, tmp_path / 'log')
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.fixtmp'))


def test_nco_is_preferred_when_visible_and_output_is_verified(tmp_path, monkeypatch):
    # Test command wiring with a stub; the real ncpdq executable is not installed in this test environment.
    path = tmp_path / 'with spaces.nc'
    make_histogram(path, 'science', 'radius')
    target = ['time', 'tau', 'radius', 'lat', 'lon']
    calls = []
    monkeypatch.setattr(layouts.shutil, 'which', lambda name: '/fake/ncpdq')
    def run(command, **kwargs):
        calls.append(command)
        layouts._python_rewrite(command[-2], command[-1], 'science', target)
        with netCDF4.Dataset(command[-1], 'a') as ds:
            ds.NCO = 'test provenance'
            ds.history = 'simulated NCO history overwrite'
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(layouts.subprocess, 'run', run)
    p = plan(path, 'fix_data_layout', dict(var='science', target_order=target))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    assert calls[0][:5] == ['/fake/ncpdq', '-O', '-h', '-a', ','.join(target)]
    with netCDF4.Dataset(path) as ds:
        assert 'NCO' not in ds.ncattrs()
        assert ds.history == 'global history retained'
    assert json.loads((tmp_path / 'log').read_text())['steps'][0]['backend'] == 'ncpdq'


def test_singleton_and_unresolved_diagnostics_are_classified_separately():
    rules = RuleSet.load(ROOT / 'rules/dimension_order.yaml')
    def finding(var):
        msg = (f"'{var}' dimensions in netCDF/C order are ['time', 'y', 'x']; coordinate IDs ['time'] "
               "require ['time'] as their required order.")
        return Finding(3, CHECK, msg, f'CMIP7.OCE.{var}.tavg-u-hm-sea.mon.glb', 'file.nc')
    c = classify(QAReport(Path('report'), {}, [finding('thetao'), finding('zostoga')]), rules)
    assert [i.outcome for i in c.items] == [Outcome.FIX, Outcome.FIX]
    assert [i.match.rule.id for i in c.items] == ['C049', 'C050']


@pytest.mark.parametrize('id,var,message,old,new,units', [
    ('C036', 'alt', "'alt' axis=''; the required value is 'Z'.", None, 'Z', 'm'),
    ('C037', 'dbze', "'dbze' standard_name='dbze'; the required value is 'equivalent_reflectivity_factor'.",
     'dbze', 'equivalent_reflectivity_factor', 'dBZ'),
    ('C038', 'effectRadLi', "'effectRadLi' defines standard_name='ReffLiq'; it is required for the attribute to be absent when its coordinate-definition value is empty.",
     'ReffLiq', None, 'micron'),
    ('C039', 'scatratio', "'scatratio' standard_name='scatratio'; the required value is 'backscattering_ratio'.",
     'scatratio', 'backscattering_ratio', '1'),
])
def test_coordinate_metadata_rule_pipeline_and_idempotence(tmp_path, id, var, message, old, new, units):
    path = tmp_path / 'test.nc'
    attr = 'axis' if id == 'C036' else 'standard_name'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.history = 'global history'
        ds.createDimension('n', 3)
        v = ds.createVariable(var, 'f8', ('n',))
        v[:] = [1., 2., 3.]
        v.units, v.long_name = units, 'preserved long_name'
        if old is not None:
            v.setncattr(attr, old)
    finding = Finding(3, '[CMIP7] [COORD003] Coordinate metadata', message, 'dataset', str(path))
    c = classify(QAReport(Path('report'), {}, [finding]), RuleSet.load(ROOT / 'rules/coordinate_metadata.yaml'))
    assert c.items[0].outcome == Outcome.FIX and c.items[0].match.rule.id == id
    p = FixPlan.from_classification(c)
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        if new is None:
            assert attr not in ds[var].ncattrs()
        else:
            assert ds[var].getncattr(attr) == new
        assert ds[var].long_name == 'preserved long_name'
        assert ds[var].units == units
        assert list(ds[var][:]) == [1., 2., 3.]
        assert ds.history == 'global history'
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0


def test_rewrite_preserves_nc_string_and_nc_char_attribute_types(tmp_path):
    path = tmp_path / 'test.nc'
    make_histogram(path, 'science', 'radius')
    with netCDF4.Dataset(path, 'a') as ds:
        ds.setncattr_string('history', 'string history')
        ds.title = 'char title'
        ds['science'].setncattr_string('comment', 'string comment')
    p = plan(path, 'fix_data_layout', dict(var='science', target_order=['time', 'tau', 'radius', 'lat', 'lon'], backend='python'))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        assert layouts._attribute_type(ds, 'history') == 12
        assert layouts._attribute_type(ds, 'title') == 2
        assert layouts._attribute_type(ds['science'], 'comment') == 12


@pytest.mark.parametrize('already_correct', [False, True])
@pytest.mark.parametrize('phase', ['open', 'after_close'])
def test_layout_verifies_original_data_after_every_hook(tmp_path, monkeypatch, already_correct, phase):
    from esgf_fix_tool.fixes import REGISTRY, FixFunction
    path = tmp_path / 'test.nc'
    make_histogram(path, 'science', 'radius')
    params = dict(var='science', target_order=['time', 'tau', 'radius', 'lat', 'lon'], backend='python')
    p = plan(path, 'fix_data_layout', params)
    if already_correct:
        assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    before = path.read_bytes()
    def corrupt(ds):
        ds['science'].set_auto_maskandscale(False)
        ds['science'][0, 0, 0, 0, 0] = 999
        return True
    def corrupt_after_close(source, output):
        with netCDF4.Dataset(output, 'a') as ds:
            corrupt(ds)
    monkeypatch.setitem(REGISTRY, 'test_corrupt_layout', FixFunction(
        'test_corrupt_layout', 'metadata', corrupt if phase == 'open' else lambda ds: True,
        True, corrupt_after_close if phase == 'after_close' else None))
    p.files[str(path)].append(Step('corrupt', 'test_corrupt_layout', {}))
    with pytest.raises(ValueError, match='Raw values'):
        apply_plan(p, tmp_path / 'log')
    assert path.read_bytes() == before
