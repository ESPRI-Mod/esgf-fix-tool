import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.rules import RuleSet

ROOT = Path(__file__).resolve().parents[1]
DATASET = 'CMIP7.OCE.msfty.tavg-ol-ht-sea.mon.glb.g112.IPSL-CM6-ESMCO2.esm-piControl-spinup.r1i1p1f1'


def build_plan(path, vertical):
    message = (f"'msfty' dimensions in netCDF/C order are ['time', 'basin', '{vertical}', 'y', 'x']; "
               "coordinate IDs ['gridlatitude', 'olevel', 'basin', 'time'] require "
               "['time', 'basin', 'lev', 'rlat'] as their required order.")
    finding = Finding(3, '[CMIP7] [COORD002] Data-variable coordinate order', message, DATASET, str(path))
    rules = RuleSet.load(ROOT / 'rules/dimension_order.yaml')
    return FixPlan.from_classification(classify(QAReport(Path('report'), {}, [finding]), rules))


def make(path, vertical, horizontal, nx):
    values = np.arange(2 * 3 * 75 * 332 * nx, dtype='i4').reshape(2, 3, 75, 332, nx)
    with netCDF4.Dataset(path, 'w') as ds:
        for dim, size in [('time', 2), ('basin', 3), (vertical, 75), ('y', 332), ('x', nx), ('bnds', 2)]:
            ds.createDimension(dim, size)
        main = ds.createVariable('msfty', 'i4', ('time', 'basin', vertical, 'y', 'x'),
                                 zlib=True, chunksizes=(1, 1, 5, 166, nx))
        main.set_auto_maskandscale(False)
        main[:] = values
        main.scale_factor = 0.25
        main.coordinates = ' '.join(horizontal)
        level = ds.createVariable(vertical, 'f8', (vertical,))
        level[:] = np.arange(75) + 0.5
        level.units, level.bounds = 'm', 'olevel_bounds'
        bounds = ds.createVariable('olevel_bounds', 'f8', (vertical, 'bnds'))
        bounds[:] = np.column_stack((np.arange(75), np.arange(1, 76)))
        bounds.units = 'm'
        for name in horizontal:
            ds.createVariable(name, 'f8', ('y', 'x'))[:] = np.arange(332 * nx).reshape(332, nx)
    return values


@pytest.mark.parametrize('vertical,horizontal', [
    ('olevel', ('nav_lat', 'nav_lon')),
    ('lev', ('latitude', 'longitude')),
])
@pytest.mark.parametrize('bounds_first', [False, True])
def test_c054_squeezes_x_once_with_original_or_already_renamed_coordinates(tmp_path, vertical, horizontal, bounds_first):
    path = tmp_path / 'msfty.nc'
    values = make(path, vertical, horizontal, 1)
    plan = build_plan(path, vertical)
    steps = plan.files[str(path)]
    assert [s.rule for s in steps] == ['C054']
    bounds_step = Step('C079', 'drop_olevel_bounds_units', {})
    steps.insert(0 if bounds_first else len(steps), bounds_step)

    log = tmp_path / 'log.jsonl'
    assert apply_plan(plan, log)['changed'] == 1
    assert apply_plan(plan, log)['changed'] == 0
    assert [json.loads(l)['rewrite_count'] for l in log.read_text().splitlines()] == [1, 0]
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert 'x' not in ds.dimensions and 'rlat' not in ds.variables
        assert ds['msfty'].dimensions == ('time', 'basin', 'lev', 'y')
        assert ds['msfty'].coordinates == ''
        np.testing.assert_array_equal(ds['msfty'][:], values[..., 0])
        assert ds['msfty'].scale_factor == 0.25 and ds['msfty'].filters()['zlib']
        for name in horizontal:
            assert ds[name].dimensions == ('y',)
            np.testing.assert_array_equal(ds[name][:], np.arange(332))
        assert ds['olevel_bounds'].dimensions == ('lev', 'bnds')
        assert ds['lev'].units == 'm' and 'units' not in ds['olevel_bounds'].ncattrs()


def test_c054_refuses_to_drop_non_singleton_x(tmp_path):
    path = tmp_path / 'msfty.nc'
    make(path, 'olevel', ('nav_lat', 'nav_lon'), 2)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='Cannot remove non-singleton dimension x'):
        apply_plan(build_plan(path, 'olevel'), tmp_path / 'log')
    assert path.read_bytes() == before
