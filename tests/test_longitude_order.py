"""Rotate every longitude-dependent array once, preserving raw science and storage."""

import json

import netCDF4
import numpy as np
import pytest

import esgf_fix_tool.batch_write as writer
from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.data_integrity import _slices
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.preview import Variable
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


MESSAGE = "'lon' contains -179.75 below required valid_min=0.0. The comparison permits numerical tolerance=0.00035999999999999997."


def make(path, var='rivi', fmt='NETCDF4', reverse_lat=False, aliases=False):
    latitude = np.arange(-89.75, 90., .5)
    if reverse_lat:
        latitude = latitude[::-1]
    lon_name, bounds_name = ('longitude', 'bounds_lon') if aliases else ('lon', 'lon_bnds')
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.variable_id = var
        ds.grid_label = 'g131'
        ds.source_id = 'A_model'
        ds.experiment_id = 'another_experiment'
        ds.history = 'Preserve global history'
        ds.createDimension('time', None)
        for name, size in [('lat', 360), ('lon', 720), ('bnds', 2), ('letters', 2)]:
            ds.createDimension(name, size)
        ds.createVariable('time', 'f8', ('time',))[:] = [1., 2.]
        lat = ds.createVariable('lat', 'f8', ('lat',))
        lat[:] = latitude
        lat.standard_name = 'latitude'; lat.units = 'degrees_north'; lat.bounds = 'lat_bnds'
        ds.createVariable('lat_bnds', 'f8', ('lat', 'bnds'))[:] = np.column_stack((latitude-.25, latitude+.25))
        lon = ds.createVariable(lon_name, 'f8', ('lon',))
        lon[:] = np.arange(-179.75, 180., .5)
        lon.standard_name = 'longitude'; lon.units = 'degrees_east'; lon.bounds = bounds_name
        ds.createVariable(bounds_name, 'f8', ('lon', 'bnds'))[:] = np.column_stack((lon[:]- .25, lon[:] + .25))
        options = {'zlib': True, 'complevel': 2} if fmt.startswith('NETCDF4') else {}
        dimensions = ('lat', 'lon') if var == 'areacellr' else ('time', 'lat', 'lon')
        shape = tuple(len(ds.dimensions[d]) for d in dimensions)
        science = ds.createVariable(var, 'f4', dimensions, fill_value=1e20, **options)
        values = np.arange(np.prod(shape), dtype='f4').reshape(shape)
        values.flat[7] = 1e20
        values.flat[-9] = np.nan
        science[:] = values
        science.long_name = 'Preserve scientific name'; science.history = 'none'
        packed = ds.createVariable('packed_aux', 'i2', ('lon', 'lat'), fill_value=-32767, **options)
        packed.scale_factor = .25; packed.add_offset = 3.
        packed.set_auto_maskandscale(False)
        raw = (np.arange(720*360, dtype='i4') % 30000).astype('i2').reshape(720, 360)
        raw[361, 9] = -32767
        packed[:] = raw
        labels = ds.createVariable('labels', 'S1', ('lon', 'letters'))
        labels[:] = np.tile(np.array([b'A', b'B'], dtype='S1'), (720, 1))
        ds.createVariable('independent', 'i4', ('time',))[:] = [4, 5]
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False); ds.set_auto_chartostring(False)
        return {n: (v.dimensions, v[:].copy(), v.dtype, v.filters(), v.chunking()) for n, v in ds.variables.items()}


def plan(path, reverse_lat=False):
    steps = [Step('C121', 'rotate_reviewed_longitude', {})]
    if reverse_lat:
        steps.append(Step('C118', 'reverse_reviewed_latitude', {}))
    steps.append(Step('C001', 'drop_variable_histories', {}))
    return FixPlan('test', 'qa', {str(path): steps})


@pytest.mark.parametrize('var', ['areacellr', 'rivi', 'rivo'])
@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('reverse_lat', [False, True])
def test_all_arrays_rotate_and_optional_latitude_reversal_shares_one_write(tmp_path, monkeypatch, var, fmt, reverse_lat):
    path = tmp_path / 'river.nc'
    originals = make(path, var, fmt, reverse_lat)
    before = path.read_bytes()
    q = plan(path, reverse_lat)
    writes = []
    original_write = writer.Batch.write

    def record(batch, output):
        assert path.read_bytes() == before, 'Source must survive until verification'
        writes.append(output)
        return original_write(batch, output)

    monkeypatch.setattr(writer.Batch, 'write', record)
    # Several bounded tiles cross the 360-column seam. Coordinates are buffered,
    # science/packed/character arrays are streamed using the very same mapping.
    monkeypatch.setattr(writer, 'data_blocks', lambda v, op: _slices(v.shape, v.dtype.itemsize, budget=40000))
    getitem = Variable.__getitem__

    def no_science_preview(v, key):
        assert v.name not in (var, 'packed_aux', 'labels')
        return getitem(v, key)

    monkeypatch.setattr(Variable, '__getitem__', no_science_preview)
    assert apply_plan(q, tmp_path / 'dry', dry_run=True)['changed'] == 1
    assert path.read_bytes() == before and not writes
    assert apply_plan(q, tmp_path / 'log')['changed'] == 1
    assert len(writes) == 1
    assert {x['rewrite_count'] for x in map(json.loads, (tmp_path / 'log').read_text().splitlines())} == {1}
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False); ds.set_auto_chartostring(False)
        assert np.array_equal(ds['lon'][:], np.arange(.25, 360., .5))
        assert ds['lon_bnds'][0, 0] == 0 and ds['lon_bnds'][-1, 1] == 360
        assert np.array_equal(ds['lon_bnds'][:-1, 1], ds['lon_bnds'][1:, 0])
        for name, (dims, raw, dtype, filters, chunks) in originals.items():
            expected = raw.copy()
            if name in ('lon', 'lon_bnds'):
                offset = np.where(originals['lon'][1] < 0, 360., 0.)
                expected += offset if name == 'lon' else offset[:, None]
            if 'lon' in dims:
                expected = np.roll(expected, 360, axis=dims.index('lon'))
            if reverse_lat and 'lat' in dims:
                expected = np.flip(expected, axis=dims.index('lat'))
            assert ds[name][:].tobytes() == expected.tobytes(), name
            assert ds[name].dtype == dtype and ds[name].filters() == filters and ds[name].chunking() == chunks
        assert ds.history == 'Preserve global history' and ds[var].long_name == 'Preserve scientific name'
        assert 'history' not in ds[var].ncattrs()
    corrected = path.read_bytes()
    assert apply_plan(q, tmp_path / 'again')['changed'] == 0
    assert path.read_bytes() == corrected and len(writes) == 1


@pytest.mark.parametrize('failure', ['grid', 'values', 'bounds', 'packed', 'verification', 'write'])
def test_guards_and_failed_output_leave_source_untouched(tmp_path, monkeypatch, failure):
    path = tmp_path / 'river.nc'; make(path)
    with netCDF4.Dataset(path, 'a') as ds:
        if failure == 'grid': ds.grid_label = 'g243'
        elif failure == 'values': ds['lon'][10] += .01
        elif failure == 'bounds': ds['lon_bnds'][359, 1] = 1.
        elif failure == 'packed': ds['lon'].scale_factor = .5
    if failure in ('verification', 'write'):
        def refuse(*args): raise ValueError('Injected failed '+failure)
        monkeypatch.setattr(writer.Batch, 'verify' if failure == 'verification' else 'write', refuse)
    before = path.read_bytes()
    with pytest.raises(ValueError): apply_plan(plan(path), tmp_path / 'log')
    assert path.read_bytes() == before and not list(tmp_path.glob('*.fixtmp'))


def test_longitude_aliases_and_bounds_rename_compose(tmp_path):
    path = tmp_path / 'river.nc'; make(path, aliases=True)
    q = plan(path)
    q.files[str(path)].append(Step('C067', 'rename_bounds_variable', {'coordinate': 'longitude', 'source': 'bounds_lon', 'target': 'lon_bnds'}))
    apply_plan(q, tmp_path / 'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['longitude'].bounds == 'lon_bnds'
        assert ds['longitude'][0] == .25 and ds['lon_bnds'][-1, 1] == 360


@pytest.mark.parametrize('published', [False, True])
@pytest.mark.parametrize('var', ['areacellr', 'rivi', 'rivo'])
@pytest.mark.parametrize('grid', ['g131', 'g111', 'g243', 'g245'])
def test_rule_is_product_and_grid_scoped_but_model_independent(var, grid, published):
    brand, freq = ('ti-u-hxy-u', 'fx') if var == 'areacellr' else ('tavg-u-hxy-lnd', 'day')
    dataset = (f'CMIP7.CMIP.IPSL.A_model.an_experiment.r1i1p1f1.glb.{freq}.{var}.{brand}.{grid}.v20261119'
               if published else f'CMIP7.SRF.{var}.{brand}.{freq}.glb.{grid}.A_model.an_experiment.r1i1p1f1')
    f = Finding(3, '[CMIP7] [COORD006] Coordinate valid range', MESSAGE, dataset, '/copy/river.nc')
    result = FixPlan.from_classification(classify(QAReport(None, {}, [f]), RuleSet.load(*default_rule_paths())))
    steps = [s for seq in result.files.values() for s in seq if s.rule == 'C121']
    assert bool(steps) == (grid == 'g131')


def test_latitude_first_and_source_revalidated_after_longitude_preparation(tmp_path):
    path = tmp_path / 'river.nc'
    originals = make(path, reverse_lat=True)
    q = plan(path, reverse_lat=True)
    q.files[str(path)].reverse()
    apply_plan(q, tmp_path / 'log')
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        expected = np.roll(np.flip(originals['rivi'][1], axis=1), 360, axis=2)
        assert ds['rivi'][:].tobytes() == expected.tobytes()
        assert ds['lat'][0] == -89.75 and ds['lon'][0] == .25
