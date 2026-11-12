import json
import shutil
from pathlib import Path

import netCDF4
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.fixes.attributes import (
    drop_region_units,
    fix_invalid_standard_names,
    fix_soil_depth_metadata,
)
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / 'src/esgf_fix_tool/rules/units_and_standard_names.yaml'
UNITS = '[CF-Conventions] §3.1 Units'
NAMES = '[CF-Conventions] §3.3 Standard Name'


def undefined(name, suggestions=''):
    return f'standard_name {name} is not defined in Standard Name Table v95.{suggestions}'


CASES = [
    ('C007', UNITS, 'Units "1" for variable basin must be convertible to canonical units "None"',
     'basin', 'region', '1', 'region'),
    ('C008', UNITS, 'Units "m" for variable solth must be convertible to canonical units "1"',
     'solth', 'model_level_number', 'm', 'depth'),
    ('C009', NAMES, undefined('ReffIce'), 'ReffIce', 'ReffIce', 'microne', None),
    ('C010', NAMES, undefined('ReffLiq'), 'effectRadLi', 'ReffLiq', 'micron', None),
    ('C011', NAMES, undefined('bounds'), 'bnds', 'bounds', '1', None),
    ('C012', NAMES, undefined('dbze'), 'dbze', 'dbze', 'dBZ', 'equivalent_reflectivity_factor'),
    ('C013', NAMES, undefined('number'), 'klevp1', 'number of layer interfaces', '1', None),
    ('C013', NAMES, undefined('number'), 'klev', 'number of layers', '1', None),
    ('C014', NAMES, undefined('x'), 'lev', 'x', 'Pa', 'atmosphere_hybrid_sigma_pressure_coordinate'),
    ('C015', NAMES, undefined('Pressure', " Possible close match(es): ['air_pressure']"),
     'presinter', 'Pressure at interface of vertical levels', 'Pa', None),
    ('C016', NAMES, undefined('pressure', " Possible close match(es): ['air_pressure', 'sea_water_pressure', 'reference_pressure']"),
     'pressure2', 'pressure', 'mb', 'air_pressure'),
    ('C017', NAMES, undefined('scatratio', " Possible close match(es): ['backscattering_ratio']"),
     'scatratio', 'scatratio', '1', 'backscattering_ratio'),
]


@pytest.mark.parametrize('rule,check,message,var,old,units,new', CASES)
def test_units_and_names_pipeline(tmp_path, rule, check, message, var, old, units, new):
    source, staged = tmp_path / 'source.nc', tmp_path / 'staged.nc'
    with netCDF4.Dataset(source, 'w') as ds:
        ds.history = 'global history to retain'
        ds.createDimension('n', 3)
        v = ds.createVariable(var, 'f8', ('n',))
        v[:] = [0.5, 1, 2]
        v.standard_name, v.units = old, units
        if rule == 'C014':
            for term in ['ap', 'b']:
                ds.createVariable(term, 'f8', ('n',))[:] = [1., 2., 3.]
        if rule == 'C010':
            v.long_name = 'existing descriptive long_name'
        v.comment = 'retain this'
        v.bounds = 'existing_bounds'
        data = ds.createVariable('science', 'i2', ('n',), zlib=True, complevel=3, fill_value=-32768)
        data.scale_factor = 0.5
        data.set_auto_maskandscale(False)
        data[:] = [-32768, 11, 23]
        data.standard_name = 'air_temperature'
    original = source.read_bytes()
    shutil.copyfile(source, staged)
    report = tmp_path / 'qa_result_test.json'
    dataset = 'CMIP7.ATM.ta.tavg-al-hxy-u.mon' if rule == 'C014' else 'dataset'
    report.write_text(json.dumps({'fail': {'3': {check: {message: {dataset: [str(staged)]}}}}}))
    classified = classify(QAReport.load(report), RuleSet.load(RULES, ROOT / 'src/esgf_fix_tool/rules/catalogue_fallbacks.yaml'))
    assert classified.items[0].outcome == Outcome.FIX
    plan = FixPlan.from_classification(classified)
    assert [s.rule for s in plan.files[str(staged)]] == [rule]
    assert apply_plan(plan, tmp_path / 'log.jsonl', dry_run=True)['changed'] == 1
    assert staged.read_bytes() == original
    assert apply_plan(plan, tmp_path / 'log.jsonl')['changed'] == 1
    assert source.read_bytes() == original
    with netCDF4.Dataset(staged) as ds, netCDF4.Dataset(source) as src:
        assert ds.history == src.history
        assert list(ds.dimensions) == list(src.dimensions)
        ds.set_auto_maskandscale(False)
        src.set_auto_maskandscale(False)
        for name in src.variables:
            assert ds[name][:].tobytes() == src[name][:].tobytes()
            assert ds[name].filters() == src[name].filters()
            assert ds[name].chunking() == src[name].chunking()
        assert ds['science'].standard_name == 'air_temperature'
        assert ds[var].comment == 'retain this'
        assert ds[var].bounds == 'existing_bounds'
        if new is None:
            assert 'standard_name' not in ds[var].ncattrs()
            assert ds[var].long_name == ('existing descriptive long_name' if rule == 'C010' else old)
        else:
            assert ds[var].standard_name == new
        if rule == 'C007':
            assert 'units' not in ds[var].ncattrs()
        else:
            assert ds[var].units == units
        if rule == 'C008':
            assert ds[var].axis == 'Z'
            assert ds[var].positive == 'down'
    corrected = staged.read_bytes()
    assert apply_plan(plan, tmp_path / 'log.jsonl')['changed'] == 0
    assert staged.read_bytes() == corrected


def test_hybrid_name_is_scoped_to_atmospheric_datasets_and_unknown_messages_do_not_match():
    rules = RuleSet.load(RULES)
    finding = Finding(3, NAMES, undefined('x'), 'CMIP7.ATM.ta.tavg-al-hxy-u.mon', 'file.nc')
    classified = classify(QAReport(Path('report.json'), {}, [finding]), rules)
    assert classified.items[0].outcome == Outcome.FIX
    assert classified.items[0].match.rule.id == 'C014'
    assert len(FixPlan.from_classification(classified).files) == 1
    for dataset in ['CMIP7.OCE.thetao.tavg-ol-hxy-sea.mon', 'CMIP7.SRF.tsl.tavg-sl-hxy-lnd.mon']:
        assert rules.match(Finding(3, NAMES, undefined('x'), dataset, 'file.nc')) is None
    for message in [undefined('another_name'), undefined('pressure'), undefined('ReffIce') + ' Extra text',
                    undefined('pressure', " Possible close match(es): ['sea_water_pressure']")]:
        assert rules.match(Finding(3, NAMES, message, 'dataset', 'file.nc')) is None


def test_hybrid_name_only_replaces_lev_x_and_preserves_formula_and_units(tmp_path):
    rule = next(r for r in RuleSet.load(RULES).rules if r.id == 'C014')
    from esgf_fix_tool.fixes import REGISTRY
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        lev = ds.createVariable('lev', 'f8')
        lev.standard_name, lev.units = 'x', '1'
        for term in ['ap', 'b']:
            ds.createVariable(term, 'f8').assignValue(1.)
        lev.formula = 'p = ap + b*ps'
        lev.formula_terms = 'ap: ap b: b ps: ps'
        other = ds.createVariable('other', 'f8')
        other.standard_name = 'x'
        fix = REGISTRY[rule.fix].func
        assert fix(ds, **rule.params)
        assert lev.standard_name == 'atmosphere_hybrid_sigma_pressure_coordinate'
        assert lev.units == '1'
        assert lev.formula == 'p = ap + b*ps'
        assert lev.formula_terms == 'ap: ap b: b ps: ps'
        assert other.standard_name == 'x'
        assert not fix(ds, **rule.params)
        lev.standard_name = 'air_pressure'
        assert not fix(ds, **rule.params)
        assert lev.standard_name == 'air_pressure'


def test_current_metadata_guards_and_unrelated_pressure_are_preserved(tmp_path):
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        ds.units = 'global units'
        basin = ds.createVariable('basin', 'f8')
        basin.units, basin.standard_name = '1', 'not_region'
        assert not drop_region_units(ds, 'basin')
        basin.standard_name, basin.units = 'region', 'm'
        assert not drop_region_units(ds, 'basin')
        assert not drop_region_units(ds, 'absent')
        basin.units = '1'
        basin.flag_values = [1., 2., 3.]
        basin.flag_meanings = 'leave these flags alone'
        assert drop_region_units(ds, 'basin')
        assert basin.flag_meanings == 'leave these flags alone'
        assert list(basin.flag_values) == [1., 2., 3.]
        assert ds.units == 'global units'

        soil = ds.createVariable('solth', 'f8')
        soil.standard_name, soil.units = 'model_level_number', '1'
        assert not fix_soil_depth_metadata(ds, 'solth')
        soil.standard_name, soil.units = 'another_name', 'm'
        assert not fix_soil_depth_metadata(ds, 'solth')
        assert not fix_soil_depth_metadata(ds, 'absent')

        for name in ['pressure2', 'ocean_pressure']:
            v = ds.createVariable(name, 'f8')
            v.standard_name, v.units = 'pressure', 'mb'
        target = ds['pressure2']
        target.units = 'm'
        params = dict(expected=['pressure'], replacement='air_pressure', units='mb', var='pressure2')
        assert not fix_invalid_standard_names(ds, **params)
        target.units = 'mb'
        assert fix_invalid_standard_names(ds, **params)
        assert target.standard_name == 'air_pressure'
        assert ds['ocean_pressure'].standard_name == 'pressure'
        target.standard_name = 'sea_water_pressure'
        assert not fix_invalid_standard_names(ds, **params)
        assert target.standard_name == 'sea_water_pressure'


def test_scanning_full_names_tolerates_renames_without_prefix_deletions(tmp_path):
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        for name, value in [('renamed', 'number of layers'), ('other', 'number of layer interfaces'),
                            ('untouched', 'number_of_days_with_air_temperature_above_threshold')]:
            v = ds.createVariable(name, 'f8')
            v.standard_name = value
            v.long_name = 'existing description'
        params = dict(expected=['number of layers', 'number of layer interfaces'])
        assert fix_invalid_standard_names(ds, **params)
        assert not fix_invalid_standard_names(ds, **params)
        for name in ['renamed', 'other']:
            assert 'standard_name' not in ds[name].ncattrs()
            assert ds[name].long_name == 'existing description'
        assert ds['untouched'].standard_name.startswith('number_of_days')


def test_previous_modifier_fix_and_new_family_are_order_independent(tmp_path):
    from esgf_fix_tool.fixes.attributes import drop_descriptive_standard_name
    for reverse in [False, True]:
        with netCDF4.Dataset(tmp_path / f'{reverse}.nc', 'w') as ds:
            v = ds.createVariable('presinter', 'f8')
            v.standard_name = 'Pressure at interface of vertical levels'
            old = lambda: drop_descriptive_standard_name(ds, 'presinter', 'Pressure at interface of vertical levels')
            new = lambda: fix_invalid_standard_names(ds, ['Pressure at interface of vertical levels'])
            first, second = (new, old) if reverse else (old, new)
            assert first()
            assert not second()
            assert 'standard_name' not in v.ncattrs()
            assert v.long_name == 'Pressure at interface of vertical levels'


@pytest.mark.parametrize('present', [[], ['ap'], ['b']])
def test_hybrid_name_requires_both_coefficients_before_changing(present, tmp_path):
    from esgf_fix_tool.fixes import REGISTRY
    rule = next(r for r in RuleSet.load(RULES).rules if r.id == 'C014')
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        lev = ds.createVariable('lev', 'f8')
        lev.standard_name = 'x'
        for name in present:
            ds.createVariable(name, 'f8').assignValue(1.)
        with pytest.raises(ValueError, match='coefficient'):
            REGISTRY[rule.fix].func(ds, **rule.params)
        assert lev.standard_name == 'x'


def test_legacy_hybrid_plan_without_requires_parameter_is_also_guarded(tmp_path):
    with netCDF4.Dataset(tmp_path / 'test.nc', 'w') as ds:
        ds.createVariable('lev', 'f8').standard_name = 'x'
        with pytest.raises(ValueError, match='coefficient'):
            fix_invalid_standard_names(ds, ['x'], replacement='atmosphere_hybrid_sigma_pressure_coordinate', var='lev')
        assert ds['lev'].standard_name == 'x'
