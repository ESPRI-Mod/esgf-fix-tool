"""Known QA variants select existing recipes and compose in one file transaction."""

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.bounds import _specs
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

FORMATS = ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET']


def selected(path, name, brand, findings):
    realm = {'mrsol': 'SRF', 'clcalipso': 'ATM'}.get(name, 'OCE')
    dataset = f'CMIP7.{realm}.{name}.{brand}.mon.glb.g112.OtherModel.historical.r2i1p1f1'
    report = QAReport(Path('qa.json'), {}, [
        Finding(3, check, message, dataset, str(path)) for check, message in findings
    ])
    result = classify(report, RuleSet.load(*default_rule_paths()))
    assert all(item.outcome.value == 'fix' for item in result.items)
    return FixPlan.from_classification(result)


def apply_replay(path, tmp, plan):
    before = path.read_bytes()
    assert apply_plan(plan, tmp/'dry', dry_run=True)['changed'] == 1
    assert path.read_bytes() == before
    assert apply_plan(plan, tmp/'apply')['changed'] == 1
    after = path.read_bytes()
    assert apply_plan(plan, tmp/'replay')['changed'] == 0
    assert path.read_bytes() == after


@pytest.mark.parametrize('fmt', FORMATS)
@pytest.mark.parametrize('var,old,value', [
    (var, old, new) for var, pairs in _specs()['labels'].items() for old, new in pairs.items()
])
def test_nul_terminated_labels_with_non_ascii_garbage(tmp_path, fmt, var, old, value):
    path = tmp_path/'label.nc'
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.createDimension('strlen', 64)
        v = ds.createVariable(var, 'S1', ('strlen',))
        raw = (old.encode() + b'\0\xff\xfe\x80' + b'garbage').ljust(64, b'\x01')
        v[:] = np.frombuffer(raw, dtype='S1')
        ds.createDimension('n', 2)
        ds.createVariable('science', 'f4', ('n',))[:] = [1, 2]
    # The checker uses either quoting style when the damaged padding has quotes.
    quote = '"' if var == 'seasurface' else "'"
    message = (f"Scalar coordinate '{var}'={quote}{old}\\x00�'�\\x7f{quote}; "
               f"the required value is '{value}'.")
    plan = selected(path, 'chl', 'tavg-u-hxy-sea', [
        ('[CMIP7] [COORD007] Requested coordinate values', message)
    ])
    apply_replay(path, tmp_path, plan)
    with netCDF4.Dataset(path) as ds:
        ds[var].set_auto_chartostring(False)
        assert ds[var][:].tobytes() == value.encode().ljust(64, b' ')
        np.testing.assert_array_equal(ds['science'][:], [1, 2])


@pytest.mark.parametrize('bad', [b'typebareBAD\0\xff', b'other\0typebare'])
def test_label_repair_refuses_a_different_prefix(tmp_path, bad):
    path = tmp_path/'label.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('strlen', 64)
        ds.createVariable('type', 'S1', ('strlen',))[:] = np.frombuffer(bad.ljust(64, b' '), dtype='S1')
    before = path.read_bytes()
    plan = FixPlan('test', 'test', {str(path): [Step('C058', 'fix_scalar_label', {
        'var': 'type', 'expected': 'typebare', 'value': 'bare_ground'})]})
    with pytest.raises(ValueError, match='Unexpected current label'):
        apply_plan(plan, tmp_path/'log')
    assert path.read_bytes() == before


SCALARS = [
    ('thetao_tavg-d300m-hxy-sea', 'depth', 150., 'm', [0., 300.]),
    ('thetao_tavg-d700m-hxy-sea', 'depth', 350., 'm', [0., 700.]),
    ('thetao_tavg-d2000m-hxy-sea', 'depth', 1000., 'm', [0., 2000.]),
    ('mrsol_tavg-d10cm-hxy-lnd', 'depth', 5., 'cm', [0., 10.]),
    ('mrsol_tpt-d10cm-hxy-lnd', 'depth', .05, 'm', [0., .1]),
    ('clcalipso_tavg-220hPa-hxy-air', 'plev', 22000., 'Pa', [44000., 0.]),
    ('clcalipso_tavg-560hPa-hxy-air', 'plev', 560., 'hPa', [680., 440.]),
    ('clcalipso_tavg-840hPa-hxy-air', 'plev', 84000., 'Pa', [100000., 68000.]),
]


@pytest.mark.parametrize('fmt', FORMATS)
@pytest.mark.parametrize('brand,coord,center,units,edges', SCALARS)
def test_existing_scalars_receive_explicit_registry_bounds_only(tmp_path, fmt, brand, coord, center, units, edges):
    path = tmp_path/'scalar.nc'
    name, branded = brand.split('_', 1)
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.source_id = 'OtherModel'
        ds.branded_variable = brand
        ds.variable_id = name
        ds.createDimension('time', 2)
        ds.createVariable(name, 'f4', ('time',))[:] = [3, 4]
        v = ds.createVariable(coord, 'f8')
        v.assignValue(center)
        v.units = units
    plan = selected(path, name, branded, [('[CMIP7] [COORD008] Coordinate bounds',
        f"It is required for '{coord}' to have a 'bounds' attribute according to its coordinate definition.")])
    assert str(path) in plan.files
    inode = path.stat().st_ino
    apply_replay(path, tmp_path, plan)
    assert path.stat().st_ino == inode  # small coordinates do not require a science-file rewrite
    with netCDF4.Dataset(path) as ds:
        assert ds[coord][...].item() == center and ds[coord].units == units
        assert ds[coord].bounds == coord + '_bnds'
        assert ds[coord + '_bnds'].dimensions == ('bnds',)
        np.testing.assert_allclose(ds[coord + '_bnds'][:], edges, rtol=0, atol=1e-12)
        np.testing.assert_array_equal(ds[name][:], [3, 4])


@pytest.mark.parametrize('bad', ['center', 'bounds', 'units'])
def test_scalar_conflicts_abort_before_writing(tmp_path, bad):
    path = tmp_path/'scalar.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.source_id = 'OtherModel'
        ds.variable_id = 'thetao'
        ds.branded_variable = 'thetao_tavg-d300m-hxy-sea'
        ds.createVariable('thetao', 'f4').assignValue(4.)
        v = ds.createVariable('depth', 'f8')
        v.assignValue(100. if bad == 'center' else 150.)
        v.units = 's' if bad == 'units' else 'm'
        if bad == 'bounds':
            ds.createDimension('bnds', 2)
            ds.createVariable('depth_bnds', 'f8', ('bnds',))[:] = [0., 700.]
    before = path.read_bytes()
    plan = FixPlan('test', 'test', {str(path): [Step('C103a', 'add_reviewed_coordinate_bounds', {'coordinate': 'depth'})]})
    with pytest.raises(ValueError):
        apply_plan(plan, tmp_path/'log')
    assert path.read_bytes() == before


@pytest.mark.parametrize('reverse', [False, True])
def test_msfty_known_findings_compose_in_one_pass(tmp_path, reverse):
    from test_msfty_layout import make
    path = tmp_path/'msfty.nc'
    science = make(path, 'olevel', ('nav_lat', 'nav_lon'), 1)
    with netCDF4.Dataset(path, 'a') as ds:
        ds.source_id = 'OtherModel'
        ds.experiment_id = 'historical'
        ds.variant_label = 'r2i1p1f1'
        ds.variable_id = 'msfty'
        ds.vertical_label = 'ol'
        ds.branded_variable = 'msfty_tavg-ol-ht-sea'
        ds['msfty'].history = 'remove'
        ds.history = 'preserve global history'
        ds['nav_lat'][:] = np.linspace(-84., 89., 332).reshape(332, 1)
        ds['nav_lat'].standard_name = 'latitude'
        ds['nav_lon'][:] = 0.
        ds['nav_lon'].standard_name = 'longitude'
        v = ds.createVariable('basin', 'f8', ('basin',))
        v[:] = [1, 2, 3]
        v.standard_name = 'region'
        v.units = '1'
        v.long_name = 'Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)'
    from test_msfty_layout import build_plan
    layout = build_plan(path, 'olevel').files[str(path)][0]
    steps = [Step('C001', 'drop_variable_histories', {}),
        Step('C101a', 'fix_basin_sector', {}),
        Step('C020', 'fix_ocean_vertical_metadata', {}),
        Step('C025', 'fix_msfty_coordinates', {}), layout,
        Step('C079', 'drop_olevel_bounds_units', {}),
        Step('C069', 'drop_unrequested_horizontal_reference', dict(var='msfty', role='latitude', coordinate='nav_lat', branded_variable='msfty_tavg-ol-ht-sea'))]
    plan = FixPlan('test', 'test', {str(path): list(reversed(steps)) if reverse else steps})
    apply_replay(path, tmp_path, plan)
    assert json.loads((tmp_path/'apply').read_text().splitlines()[0])['rewrite_count'] == 1
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['msfty'].dimensions == ('time', 'basin', 'lev', 'rlat')
        np.testing.assert_array_equal(ds['msfty'][:], science[..., 0])
        assert ds['msfty'].filters()['zlib'] and ds['msfty'].scale_factor == .25
        assert ds['msfty'].coordinates == 'sector'
        assert 'nav_lon' not in ds.variables and 'x' not in ds.dimensions
        assert ds['basin'].units == '1' and 'standard_name' not in ds['basin'].ncattrs()
        assert 'history' not in ds['msfty'].ncattrs() and ds.history == 'preserve global history'
        assert ds['rlat'].long_name == 'Grid Latitude'


def test_grid_filename_sync_accepts_other_model_experiment_member(tmp_path):
    path = tmp_path/'zostoga_tavg-u-hm-sea_mon_glb_g112_OtherModel_historical_r2i1p1f1_185001-185912.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.setncatts(dict(source_id='OtherModel', experiment_id='historical',
            variant_label='r2i1p1f1', frequency='mon', variable_id='zostoga',
            branded_variable='zostoga_tavg-u-hm-sea', horizontal_label='hm', region='glb', grid_label='g010'))
        ds.createDimension('time', 2)
        ds.createVariable('zostoga', 'f4', ('time',))[:] = [1., 2.]
    plan = selected(path, 'zostoga', 'tavg-u-hm-sea', [
        ('[CMIP7] [ATTR005] Consistency: Filename vs Global Attributes',
         "Filename component 'grid_label' ('g112') does not match global attribute ('g010').")])
    target = path.with_name(path.name.replace('_g112_', '_g010_'))
    assert apply_plan(plan, tmp_path/'dry', dry_run=True)['changed'] == 1
    assert path.exists() and not target.exists()
    assert apply_plan(plan, tmp_path/'apply')['changed'] == 1
    assert not path.exists() and target.exists()
    assert apply_plan(plan, tmp_path/'replay')['changed'] == 0
    with netCDF4.Dataset(target) as ds:
        np.testing.assert_array_equal(ds['zostoga'][:], [1., 2.])


def test_iceband_recipe_accepts_other_model_experiment_member(tmp_path):
    path = tmp_path/'iceband.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.setncatts(dict(source_id='OtherModel', experiment_id='historical',
            variant_label='r2i1p1f1', grid_label='g112', variable_id='siitdconc'))
        ds.createDimension('iceband', 5)
        v = ds.createVariable('iceband', 'f8', ('iceband',))
        v.units, v.standard_name = 'm', 'sea_ice_thickness'
        v[:] = [1, 2, 3, 4, 5]
        ds.createVariable('siitdconc', 'f4', ('iceband',))[:] = np.arange(5)
    plan = selected(path, 'siitdconc', 'tavg-u-hxy-si', [
        ('[CMIP7] [COORD008] Coordinate bounds',
         "It is required for 'iceband' to have a 'bounds' attribute according to its coordinate definition.")])
    apply_replay(path, tmp_path, plan)
    with netCDF4.Dataset(path) as ds:
        np.testing.assert_array_equal(ds['iceband_bnds'][:],
            [[0., .45], [.45, 1.12], [1.12, 2.14], [2.14, 3.67], [3.67, 99.]])
        np.testing.assert_array_equal(ds['siitdconc'][:], np.arange(5))


def test_hybrid_reference_remains_validated_without_model_experiment_filter(tmp_path):
    from test_interface_coefficient_dimensions import make, snapshot, steps
    path = tmp_path/'hybrid.nc'
    make(path)
    with netCDF4.Dataset(path, 'a') as ds:
        ds.source_id = 'OtherModel'
        ds.experiment_id = 'historical'
    science = snapshot(path)
    plan = FixPlan('test', 'test', {str(path): steps('both')})
    apply_replay(path, tmp_path, plan)
    assert snapshot(path) == science
    with netCDF4.Dataset(path) as ds:
        assert ds['ap'].shape == ds['b'].shape == ds['lev'].shape == (80,)
        assert np.max(ds['lev'][:]) <= 1.
