import json
import shutil
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.fixes import REGISTRY, FixFunction
from esgf_fix_tool.fixes.coordinates import (
    add_registry_scalar,
    add_text_coordinate_alias,
    rename_coordinate,
)
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

ROOT = Path(__file__).resolve().parents[1]
VERTICAL = ROOT / 'src/esgf_fix_tool/rules/vertical_coordinate_names.yaml'
REQUIRED = ROOT / 'src/esgf_fix_tool/rules/required_coordinates.yaml'
CHECK = '[CMIP7] [COORD001] Coordinate identity and structure'


def plan(path, fix, params):
    return FixPlan('test', 'test.json', {str(path): [Step('test', fix, params)]})


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('old,new', [('presinter', 'lev'), ('olevel', 'lev'), ('solth', 'depth'),
                                     ('ReffIce', 'effectRadIc'), ('pressure2', 'plev')])
def test_coordinate_rename_preserves_raw_data_after_reopen(tmp_path, fmt, old, new):
    source, staged = tmp_path / 'source.nc', tmp_path / 'staged.nc'
    with netCDF4.Dataset(source, 'w', format=fmt) as ds:
        ds.history = 'global history'
        ds.createDimension('time', None)
        ds.createDimension(old, 3)
        ds.createDimension('bnds', 2)
        v = ds.createVariable(old, 'f8', (old,))
        v[:] = [0.0004887585535, 1.749755620608, 65.562072365568]
        v.setncatts({'name': old, 'standard_name': 'keep this value', 'formula': 'keep this formula',
                    'formula_terms': f'p0: {old} b: b', 'bounds': f'{old}_bounds'})
        b = ds.createVariable(f'{old}_bounds', 'f8', (old, 'bnds'))
        b[:] = np.arange(6).reshape(3, 2)
        data = ds.createVariable('science', 'i2', ('time', old), zlib=fmt.startswith('NETCDF4'),
                                 fill_value=-32768)
        data.scale_factor, data.add_offset = .5, 3.
        data.set_auto_maskandscale(False)
        data[:] = [[-32768, 11, 23], [99, -99, 12]]
        data.coordinates = f'{old} other'
        data.cell_methods = f'{old}: mean'
        data.comment = f'prose mentions {old}; retain it'
    original = source.read_bytes()
    shutil.copyfile(source, staged)
    p = plan(staged, 'rename_coordinate', dict(sources=[old], target=new))
    assert apply_plan(p, tmp_path / 'log', dry_run=True)['changed'] == 1
    assert staged.read_bytes() == original
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    assert source.read_bytes() == original
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(staged) as dst:
        src.set_auto_maskandscale(False)
        dst.set_auto_maskandscale(False)
        assert old not in dst.variables and old not in dst.dimensions
        assert dst[new].dimensions == (new,)
        assert dst['science'].dimensions == ('time', new)
        assert dst.dimensions['time'].isunlimited()
        assert dst.history == src.history
        for name, v in src.variables.items():
            out = dst[new if name == old else name]
            assert out[:].tobytes() == v[:].tobytes()
            assert out.filters() == v.filters()
            assert out.chunking() == v.chunking()
        assert dst[new].getncattr('name') == new
        assert dst[new].standard_name == src[old].standard_name
        assert dst[new].formula == src[old].formula
        assert dst[new].formula_terms == f'p0: {new} b: b'
        assert dst[new].bounds == f'{old}_bounds'
        assert dst['science'].coordinates == f'{new} other'
        assert dst['science'].cell_methods == f'{new}: mean'
        assert dst['science'].comment == src['science'].comment
    corrected = staged.read_bytes()
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0
    assert staged.read_bytes() == corrected
    assert all(json.loads(line)['dry_run'] or json.loads(line)['steps'][0]['data_verified'] for line in (tmp_path / 'log').read_text().splitlines())


@pytest.mark.parametrize('collision', ['variable', 'dimension'])
def test_rename_collision_discards_temp_and_preserves_target(tmp_path, collision):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('olevel', 2)
        ds.createVariable('olevel', 'f8', ('olevel',))[:] = [1., 2.]
        if collision == 'variable':
            ds.createVariable('lev', 'f8').assignValue(42.)
        else:
            ds.createDimension('lev', 3)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='overwrite'):
        apply_plan(plan(path, 'rename_coordinate', dict(sources=['olevel'], target='lev')), tmp_path / 'log')
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.fixtmp'))


def test_data_integrity_guard_rejects_corruption_after_close(tmp_path, monkeypatch):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('n', 2)
        ds.createVariable('science', 'f8', ('n',))[:] = [1., 2.]
    before = path.read_bytes()
    def corrupt(ds):
        ds['science'][0] = 99.
        return True
    monkeypatch.setitem(REGISTRY, 'test_corrupt', FixFunction('test_corrupt', 'metadata', corrupt, True))
    with pytest.raises(ValueError, match='Raw values'):
        apply_plan(plan(path, 'test_corrupt', {}), tmp_path / 'log')
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.fixtmp'))


SCALARS = [
    ('tas_tavg-h2m-hxy-u', 'tas', 'height', 2., 'm', None),
    ('sfcWind_tavg-h10m-hxy-u', 'sfcWind', 'height', 10., 'm', None),
    ('thetao_tavg-d300m-hxy-sea', 'thetao', 'depth', 150., 'm', [0., 300.]),
    ('mrsol_tavg-d10cm-hxy-lnd', 'mrsol', 'depth', 5., 'cm', [0., 10.]),
    ('expc_tavg-d100m-hxy-sea', 'expc', 'depth', 100., 'm', None),
    ('clcalipso_tavg-220hPa-hxy-air', 'clcalipso', 'plev', 22000., 'Pa', [44000., 0.]),
    ('ua_tavg-10hPa-hxy-air', 'ua', 'plev', 1000., 'Pa', None),
    ('od443aer_tavg-u-hxy-u', 'od443aer', 'wavelength', 443., 'nm', None),
    ('abs550aer_tavg-u-hxy-u', 'abs550aer', 'wavelength', 550., 'nm', None),
    ('od865aer_tavg-u-hxy-u', 'od865aer', 'wavelength', 865., 'nm', None),
    ('mlotst_tavg-u-hxy-sea', 'mlotst', 'deltasigt', .03, 'kg m-3', None),
]


@pytest.mark.parametrize('brand,variable,target,value,units,bounds', SCALARS)
def test_scalars_use_registry_values_and_bounds(tmp_path, brand, variable, target, value, units, bounds):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.branded_variable, ds.variable_id, ds.history = brand, variable, 'global history'
        ds.createDimension('n', 2)
        v = ds.createVariable(variable, 'f8', ('n',))
        v[:] = [11., 22.]
        v.coordinates = 'existing_aux'
    p = plan(path, 'add_registry_scalar', dict(target=target))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        assert ds[target].dimensions == ()
        assert ds[target].getValue() == value
        assert ds[target].units == units
        assert ds[variable].coordinates == f'existing_aux {target}'
        assert ds.history == 'global history'
        assert list(ds[variable][:]) == [11., 22.]
        if bounds is not None:
            assert list(ds[ds[target].bounds][:]) == bounds
        else:
            assert 'bounds' not in ds[target].ncattrs()
    before = path.read_bytes()
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0
    assert path.read_bytes() == before


def test_scalar_unknown_brand_and_wrong_identity_are_rejected(tmp_path):
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        ds.branded_variable, ds.variable_id = 'new_unknown_brand', 'tas'
        ds.createVariable('tas', 'f8')
        with pytest.raises(ValueError, match='No reviewed'):
            add_registry_scalar(ds, 'height')
        ds.branded_variable, ds.variable_id = 'tas_tavg-h2m-hxy-u', 'another_variable'
        with pytest.raises(ValueError, match='variable_id'):
            add_registry_scalar(ds, 'height')
        assert 'height' not in ds.variables


@pytest.mark.parametrize('source,dimension', [('line', 'oline'), ('line', 'siline'), ('type', 'vegtype')])
def test_text_alias_copies_existing_labels_and_keeps_registry_coordinate(tmp_path, source, dimension):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.variable_id = 'science'
        ds.createDimension(dimension, 2)
        ds.createDimension('strlen', 5)
        v = ds.createVariable(source, 'S1', (dimension, 'strlen'))
        v[:] = np.array([list(b'abcde'), list(b'fghij')], dtype='u1').view('S1')
        v.standard_name = 'region' if source == 'line' else 'area_type'
        data = ds.createVariable('science', 'f8', (dimension,))
        data[:] = [1., 2.]
        data.coordinates = source
    p = plan(path, 'add_text_coordinate_alias', dict(source=source))
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_chartostring(False)
        assert ds[source][:].tobytes() == ds['sector'][:].tobytes()
        assert ds[source].dimensions == ds['sector'].dimensions
        assert ds['science'].coordinates == f'{source} sector'
        assert list(ds['science'][:]) == [1., 2.]
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0


def test_numeric_basin_is_not_converted_to_text_without_mapping(tmp_path):
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        ds.createDimension('basin', 3)
        ds.createVariable('basin', 'i4', ('basin',))[:] = [1, 2, 3]
        with pytest.raises(ValueError, match='numeric'):
            add_text_coordinate_alias(ds, 'basin')
        assert 'sector' not in ds.variables


def test_scalar_text_rename_does_not_rename_string_length_dimension(tmp_path):
    path = tmp_path / 'test.nc'
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('str_len', 3)
        ds.createVariable('pfttype', 'S1', ('str_len',))[:] = np.array(list('abc'), dtype='S1')
    assert apply_plan(plan(path, 'rename_coordinate', dict(sources=['pfttype'], target='type', rename_dimension=False)),
                      tmp_path / 'log')['changed'] == 1
    with netCDF4.Dataset(path) as ds:
        assert ds['type'][:].tobytes() == b'abc'
        assert ds['type'].dimensions == ('str_len',)


def test_same_depth_message_is_classified_by_dataset_and_can_make_separate_plans(tmp_path):
    message = ("The required one-dimensional coordinate 'depth' (coordinate ID 'sdepth') is absent, "
               'so its configured dependent coordinate checks could not be evaluated.')
    records = {'CMIP7.SRF.tsl.tavg-sl-hxy-lnd.mon': ['thermal.nc'],
               'CMIP7.SRF.mrsol.tavg-sl-hxy-lnd.mon': ['hydrological.nc']}
    report = tmp_path / 'qa_result_test.json'
    report.write_text(json.dumps({'fail': {'3': {CHECK: {message: records}}}}))
    rules = RuleSet.load(REQUIRED)
    c = classify(QAReport.load(report), rules)
    assert [i.outcome for i in c.items] == [Outcome.FIX, Outcome.FIX]
    assert [i.match.rule.id for i in c.items] == ['C021', 'C022']
    output = tmp_path / 'plan.json'
    result = CliRunner().invoke(app, ['plan', str(report), '-r', str(REQUIRED), '--only-rule', 'C021', '-o', str(output)])
    assert result.exit_code == 0, result.output
    assert set(FixPlan.read(output).files) == {'thermal.nc'}


def test_existing_attribute_rules_still_work_after_coordinate_renames(tmp_path):
    rules = RuleSet.load(ROOT / 'src/esgf_fix_tool/rules/units_and_standard_names.yaml')
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        ds.createDimension('solth', 2)
        soil = ds.createVariable('solth', 'f8', ('solth',))
        soil.standard_name, soil.units = 'model_level_number', 'm'
        soil[:] = [.01, .1]
        assert rename_coordinate(ds, ['solth'], 'depth')
        message = 'Units "m" for variable depth must be convertible to canonical units "1"'
        match = rules.match(Finding(3, '[CF-Conventions] §3.1 Units', message, 'dataset', 'file.nc'))
        assert match.rule.id == 'C008'
        assert REGISTRY[match.rule.fix].func(ds, **match.rule.resolve_params(match.groups))
        assert ds['depth'].standard_name == 'depth'
        ds.createDimension('pressure2', 2)
        v = ds.createVariable('pressure2', 'f8', ('pressure2',))
        v.standard_name, v.units = 'pressure', 'mb'
        v[:] = [900., 740.]
        assert rename_coordinate(ds, ['pressure2'], 'plev')
        rule = next(r for r in rules.rules if r.id == 'C016')
        assert REGISTRY[rule.fix].func(ds, **rule.params)
        assert ds['plev'].standard_name == 'air_pressure'
