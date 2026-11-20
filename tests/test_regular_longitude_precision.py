"""Correct shared regular-grid edges directly; preserve the stored CM7 centers."""

import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.preview import Variable


def make(path, nlon=270, fmt='NETCDF4', legacy=False):
    edges = np.linspace(0., 360., nlon+1)
    centers = (edges[:-1]+edges[1:])/2
    # A tiny center perturbation is accepted but must not be normalized on g245.
    centers[2] += 1e-11
    old = np.column_stack((edges[:-1], edges[1:]))
    old[0, 0] = -1.11e-16
    old[3, 0] -= 1e-12
    bounds_name = 'bounds_lon' if legacy else 'lon_bnds'
    dim = 'nvertex' if legacy else 'bnds'
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.grid_label = 'g245'; ds.variable_id = 'science'; ds.horizontal_label = 'hxy'; ds.history = 'preserve'
        for name, size in [('time', None), ('lat', 3), ('lon', nlon), (dim, 2)]:
            ds.createDimension(name, size)
        lon = ds.createVariable('lon', 'f8', ('lon',))
        lon[:] = centers; lon.units = 'degrees_east'; lon.bounds = bounds_name; lon.standard_name = 'longitude'
        ds.createVariable(bounds_name, 'f8', ('lon', dim))[:] = old
        options = {'zlib': True, 'complevel': 2} if fmt.startswith('NETCDF4') else {}
        v = ds.createVariable('science', 'i2', ('time', 'lat', 'lon'), fill_value=-32767, **options)
        v.scale_factor = .25; v.add_offset = 10.; v.set_auto_maskandscale(False)
        raw = (np.arange(6*nlon) % 30000).astype('i2').reshape(2, 3, nlon)
        raw[0, 2, 15] = -32767; v[:] = raw
    with netCDF4.Dataset(path) as ds:
        return centers.copy(), edges, raw, ds['science'].filters(), ds['science'].chunking()


def plan(path, legacy=False):
    steps = [Step('C059', 'normalize_regular_longitude', {})]
    if legacy:
        steps += [Step('C067', 'rename_bounds_variable', {'coordinate': 'lon', 'source': 'bounds_lon', 'target': 'lon_bnds'}),
                  Step('C068', 'normalize_bounds_dimension', {})]
    return FixPlan('test', 'qa', {str(path): steps})


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('nlon', [270, 360])
@pytest.mark.parametrize('legacy', [False, True])
def test_shared_edges_are_exact_and_science_is_never_rewritten(tmp_path, monkeypatch, fmt, nlon, legacy):
    path = tmp_path / 'regular.nc'
    centers, edges, raw, filters, chunks = make(path, nlon, fmt, legacy)
    original = path.read_bytes(); inode = path.stat().st_ino

    def forbid(*args, **kwargs):
        raise AssertionError('Small bounds corrections must not rewrite scientific data')

    monkeypatch.setattr(Batch, 'write', forbid)
    getitem = Variable.__getitem__

    def guarded(v, key):
        assert v.name != 'science'
        return getitem(v, key)

    monkeypatch.setattr(Variable, '__getitem__', guarded)
    q = plan(path, legacy)
    assert apply_plan(q, tmp_path / 'dry', dry_run=True)['changed'] == 1
    assert path.read_bytes() == original
    assert apply_plan(q, tmp_path / 'log')['changed'] == 1
    assert path.stat().st_ino == inode
    assert json.loads((tmp_path / 'log').read_text())['rewrite_count'] == 0
    with netCDF4.Dataset(path) as ds:
        assert ds['lon'][:].tobytes() == centers.tobytes()
        actual = ds['lon_bnds'][:]
        assert actual.tobytes() == np.column_stack((edges[:-1], edges[1:])).tobytes()
        assert actual[0, 0] == 0 and actual[-1, 1] == 360
        assert np.array_equal(actual[:-1, 1], actual[1:, 0])
        assert np.all(actual[:, 0] <= centers) and np.all(centers <= actual[:, 1])
        assert ds['lon'].bounds == 'lon_bnds' and ds['lon_bnds'].dimensions == ('lon', 'bnds')
        assert ds['science'].filters() == filters and ds['science'].chunking() == chunks
        ds.set_auto_maskandscale(False)
        assert ds['science'][:].tobytes() == raw.tobytes()
        assert ds.history == 'preserve'
    corrected = path.read_bytes()
    assert apply_plan(q, tmp_path / 'again')['changed'] == 0
    assert path.read_bytes() == corrected


@pytest.mark.parametrize('failure', ['polygon', 'grid', 'centers', 'bounds', 'nonfinite', 'packing'])
def test_nonregular_or_nonrounding_changes_are_refused_before_writing(tmp_path, failure):
    path = tmp_path / 'regular.nc'; make(path)
    with netCDF4.Dataset(path, 'a') as ds:
        if failure == 'polygon':
            ds.renameVariable('lon', 'old_lon')
            ds.createDimension('cell', 270)
            ds.createVariable('lon', 'f8', ('cell',))[:] = ds['old_lon'][:]
            ds['lon'].units = 'degrees_east'; ds['lon'].bounds = 'lon_bnds'
        elif failure == 'grid': ds.grid_label = 'g243'
        elif failure == 'centers': ds['lon'][2] += .01
        elif failure == 'bounds': ds['lon_bnds'][2, 0] += .01
        elif failure == 'nonfinite': ds['lon_bnds'][2, 0] = np.nan
        else: ds['lon_bnds'].scale_factor = .5
    before = path.read_bytes()
    with pytest.raises(ValueError): apply_plan(plan(path), tmp_path / 'log')
    assert path.read_bytes() == before


def test_midpoint_centered_g111_is_detected_without_changing_grid_origin(tmp_path):
    path = tmp_path / 'regular.nc'
    _, edges, _, _, _ = make(path, nlon=720)
    with netCDF4.Dataset(path, 'a') as ds:
        ds.grid_label = 'g111'
    apply_plan(plan(path), tmp_path / 'log')
    with netCDF4.Dataset(path) as ds:
        assert np.array_equal(ds['lon'][:], (edges[:-1]+edges[1:])/2)
        assert ds['lon_bnds'][0, 0] == 0 and ds['lon_bnds'][-1, 1] == 360


@pytest.mark.parametrize('rename_first', [False, True])
def test_longitude_coordinate_rename_composes_with_precision(tmp_path, rename_first):
    path = tmp_path / 'regular.nc'
    centers, _, _, _, _ = make(path)
    normalize = Step('C059', 'normalize_regular_longitude', {})
    rename = Step('C072', 'rename_horizontal_coordinate', {'role': 'longitude'})
    q = FixPlan('test', 'qa', {str(path): [rename, normalize] if rename_first else [normalize, rename]})
    apply_plan(q, tmp_path / 'log')
    with netCDF4.Dataset(path) as ds:
        assert 'lon' not in ds.variables and 'lon' in ds.dimensions
        assert ds['longitude'][:].tobytes() == centers.tobytes()
        assert ds['lon_bnds'][0, 0] == 0 and ds['lon_bnds'][-1, 1] == 360
