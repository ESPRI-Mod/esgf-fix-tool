import shutil
"""User-approved DR tables; synthetic histogram payloads are never rebinned."""
from pathlib import Path
import os

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.fixes import FixFunction, REGISTRY
from esgf_fix_tool.fixes.bounds import _specs
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.rules import RuleSet

ROOT = Path(__file__).resolve().parents[1]
CASES = {'dbze': ('cfadDbze94', 'C055', 'C062', '[(-47.5, 0.0475), (22.5, 0.0225)]'),
         'scatratio': ('cfadLidarsr532', 'C056', 'C064', '[(0.005, 5e-06), (50040.0, 50.04)]')}


def make(path, coordinate, fmt='NETCDF4', dtype='f8'):
    spec = _specs()['bounds'][coordinate]
    centers = np.asarray(spec['centers']).copy()
    # dbze endpoints are documented in the shared chat; scatratio endpoints here are synthetic.
    centers[0], centers[-1] = (-72.5, 50.) if coordinate == 'dbze' else (0., 100.)
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.variable_id = CASES[coordinate][0]
        ds.history = 'preserved global history'
        for dim, size in [('time', None), ('alt', 2), (coordinate, 15), ('lat', 2), ('lon', 2)]:
            ds.createDimension(dim, size)
        v = ds.createVariable(coordinate, dtype, (coordinate,))
        v[:], v.units, v.long_name = centers, spec['units'], 'preserved coordinate metadata'
        science = ds.createVariable(ds.variable_id, 'f4', ('time', 'alt', coordinate, 'lat', 'lon'),
                                    fill_value=1.e20, zlib=fmt.startswith('NETCDF4'))
        raw = np.arange(240, dtype='f4').reshape(2, 2, 15, 2, 2)
        raw.flat[:3] = [np.nan, -0., 1.e20]
        science[...] = raw
        ds.createVariable('unrelated', 'f8', ('lat', 'lon'))[:] = [[1, 2], [3, 4]]


def plan(path, coordinate, order='both'):
    main, values_id, bounds_id, missing = CASES[coordinate]
    messages = [(values_id, '[CMIP7] [COORD007] Requested coordinate values',
                 f"'{coordinate}' does not contain requested value(s) within their permitted absolute differences: {missing}."),
                (bounds_id, '[CMIP7] [COORD008] Coordinate bounds',
                 f"It is required for '{coordinate}' to have a 'bounds' attribute according to its coordinate definition.")]
    if order == 'values': messages = messages[:1]
    elif order == 'bounds': messages = messages[1:]
    elif order == 'reverse': messages.reverse()
    findings = [Finding(3, check, message, f'CMIP7.ATM.{main}.tavg-h40-hxy-air.mon.glb', str(path))
                for _, check, message in messages]
    c = classify(QAReport(Path('synthetic-report'), {}, findings),
                 RuleSet.load(ROOT / 'rules/coordinate_values_bounds.yaml'))
    p = FixPlan.from_classification(c)
    assert len(p.files[str(path)]) == len(findings)
    assert all(s.fix == 'fix_dr_histogram' for s in p.files[str(path)])
    return p


@pytest.mark.parametrize('coordinate', CASES)
@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('order', ['values', 'bounds', 'both', 'reverse'])
def test_each_diagnostic_independently_applies_dr_centers_and_edges(tmp_path, coordinate, fmt, order):
    original, target = tmp_path / 'source.nc', tmp_path / 'copy.nc'
    make(original, coordinate, fmt)
    source_bytes = original.read_bytes()
    shutil.copyfile(original, target)
    p = plan(target, coordinate, order)
    assert apply_plan(p, tmp_path/'log', dry_run=True)['changed'] == 1
    assert target.read_bytes() == source_bytes
    assert apply_plan(p, tmp_path/'log')['changed'] == 1
    assert original.read_bytes() == source_bytes
    with netCDF4.Dataset(original) as src, netCDF4.Dataset(target) as dst:
        src.set_auto_maskandscale(False); dst.set_auto_maskandscale(False)
        spec = _specs()['bounds'][coordinate]
        np.testing.assert_array_equal(dst[coordinate][:], spec['centers'])
        np.testing.assert_array_equal(dst[coordinate+'_bnds'][:], np.column_stack([spec['edges'][:-1], spec['edges'][1:]]))
        assert dst[coordinate].bounds == coordinate+'_bnds'
        assert dst[coordinate].long_name == src[coordinate].long_name
        assert dst.history == src.history
        for name in [src.variable_id, 'unrelated']:
            assert src[name][:].tobytes() == dst[name][:].tobytes()
            assert src[name].filters() == dst[name].filters()
        assert dst.dimensions['time'].isunlimited()
    corrected = target.read_bytes()
    assert apply_plan(p, tmp_path/'log')['changed'] == 0
    assert target.read_bytes() == corrected


@pytest.mark.parametrize('invalid', ['interior', 'nonmonotonic', 'units', 'nan', 'packing', 'dtype', 'bounds'])
def test_unreviewed_histogram_inputs_preserve_target(tmp_path, invalid):
    path = tmp_path/'test.nc'
    make(path, 'dbze', dtype='f4' if invalid == 'dtype' else 'f8')
    with netCDF4.Dataset(path, 'a') as ds:
        v = ds['dbze']
        if invalid == 'interior': v[3] = -31.
        elif invalid == 'nonmonotonic': v[0] = 0.
        elif invalid == 'units': v.units = '1'
        elif invalid == 'nan': v[0] = np.nan
        elif invalid == 'packing': v.scale_factor = 1.
        elif invalid == 'bounds':
            ds.createDimension('bnds', 2)
            ds.createVariable('dbze_bnds', 'f8', ('dbze', 'bnds'))[:] = np.zeros((15,2))
    original = path.read_bytes()
    with pytest.raises(ValueError): apply_plan(plan(path, 'dbze'), tmp_path/'log')
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('*.fixtmp'))


@pytest.mark.parametrize('corrupt', ['centers', 'bounds'])
def test_histogram_final_verifier_checks_both_arrays_after_other_hooks(tmp_path, monkeypatch, corrupt):
    path = tmp_path/'test.nc'; make(path, 'dbze'); original = path.read_bytes()
    def after(source, output):
        with netCDF4.Dataset(output, 'a') as ds:
            if corrupt == 'centers': ds['dbze'][0] += .0001
            else: ds['dbze_bnds'][0,0] -= 1.
    monkeypatch.setitem(REGISTRY, 'corrupt_histogram', FixFunction('corrupt_histogram', 'metadata', lambda ds: True, True, after))
    p = plan(path, 'dbze')
    p.files[str(path)].append(Step('test', 'corrupt_histogram', {}))
    with pytest.raises(ValueError, match='histogram'): apply_plan(p, tmp_path/'log')
    assert path.read_bytes() == original
