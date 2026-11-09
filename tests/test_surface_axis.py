import shutil
from pathlib import Path
import os

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify, Outcome
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.rules import RuleSet
from test_horizontal_coordinates import make as make_horizontal

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = ["seasurface: vertical coordinates not defining pressure must include a positive attribute that is either 'up' or 'down'",
            "§4.3.1 seasurface's units must be defined for vertical coordinates, there is no default"]


def make(path, fmt='NETCDF4', label='osurf'):
    make_horizontal(path, fmt, var='chl', brand='chl_tavg-ols-hxy-sea')
    with netCDF4.Dataset(path, 'a') as ds:
        ds.createDimension('str_len', 255)
        v = ds.createVariable('seasurface', 'S1', ('str_len',))
        data = label.encode() + b'\0' + b' '*(254-len(label))
        v[:] = np.frombuffer(data, dtype='S1')
        v.axis, v.long_name = 'Z', 'Ocean surface coordinate'
        ds['chl'].coordinates = 'seasurface nav_lat nav_lon'


@pytest.mark.parametrize('fmt', ['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('label', ['osurf','ocean_surface_layer'])
@pytest.mark.parametrize('sequence', ['axis_first','names_first','same_plan'])
def test_surface_axis_removal_preserves_values_and_composes_with_horizontal_renames(tmp_path, fmt, label, sequence):
    original, target = tmp_path/'source.nc', tmp_path/'copy.nc'
    make(original, fmt, label); source_bytes = original.read_bytes(); shutil.copyfile(original,target)
    axis = Step('C073','drop_osurf_axis',{})
    names = [Step('test','rename_horizontal_coordinate',dict(role=role)) for role in ['latitude','longitude']]
    steps = [axis]+names if sequence != 'names_first' else names+[axis]
    plans = [FixPlan('test','report',{str(target):steps})] if sequence == 'same_plan' else [FixPlan('test','report',{str(target):[s]}) for s in steps]
    for p in plans:
        assert apply_plan(p,tmp_path/'log',dry_run=True)['changed']==1
        assert apply_plan(p,tmp_path/'log')['changed']==1
    assert original.read_bytes()==source_bytes
    with netCDF4.Dataset(original) as src,netCDF4.Dataset(target) as dst:
        src.set_auto_maskandscale(False);dst.set_auto_maskandscale(False)
        src.set_auto_chartostring(False);dst.set_auto_chartostring(False)
        assert 'axis' not in dst['seasurface'].ncattrs()
        assert 'units' not in dst['seasurface'].ncattrs() and 'positive' not in dst['seasurface'].ncattrs()
        assert dst['seasurface'].long_name==src['seasurface'].long_name
        for name,v in src.variables.items():
            out=dst[{'nav_lat':'latitude','nav_lon':'longitude'}.get(name,name)]
            assert v[:].tobytes()==out[:].tobytes()
            assert v.filters()==out.filters()
        assert dst.history==src.history
    corrected=target.read_bytes()
    for p in plans:assert apply_plan(p,tmp_path/'log')['changed']==0
    assert target.read_bytes()==corrected


@pytest.mark.parametrize('order',['axis_first','label_first','same_plan'])
def test_surface_axis_and_label_corrections_are_independent(tmp_path,order):
    path=tmp_path/'test.nc';make(path)
    axis=Step('C073','drop_osurf_axis',{})
    label=Step('C057','fix_scalar_label',dict(var='seasurface',expected='osurf',value='ocean_surface_layer'))
    steps=[axis,label] if order!='label_first' else [label,axis]
    plans=[FixPlan('test','report',{str(path):steps})] if order=='same_plan' else [FixPlan('test','report',{str(path):[s]}) for s in steps]
    for p in plans:assert apply_plan(p,tmp_path/'log')['changed']==1
    with netCDF4.Dataset(path) as ds:
        assert 'axis' not in ds['seasurface'].ncattrs()
        assert ds['seasurface'][:].tobytes().rstrip(b' ') == b'ocean_surface_layer'
    for p in plans:assert apply_plan(p,tmp_path/'log')['changed']==0


def test_two_surface_diagnostics_deduplicate_and_override_old_seed_waivers():
    findings=[Finding(3,'[CF-Conventions] §4.3 Vertical Coordinate',m,'dataset','file.nc') for m in MESSAGES]
    rules=RuleSet.load(ROOT/'rules/surface_and_hybrid.yaml',ROOT/'rules/catalogue_fallbacks.yaml')
    c=classify(QAReport(Path('report'),{},findings),rules)
    assert [i.outcome for i in c.items]==[Outcome.FIX,Outcome.FIX]
    p=FixPlan.from_classification(c)
    assert len(p.files['file.nc'])==1 and p.files['file.nc'][0].rule=='C073'


@pytest.mark.parametrize('coordinate', ['alevel', 'alevhalf'])
def test_hybrid_coordinate_family_selects_reference_coefficients(coordinate):
    message = (f"Coordinate '{coordinate}' could not be verified. Expected is a generic vertical "
               f"coordinate with out_name 'lev'. No model_level_coordinate marked for "
               f"generic_level_name='{coordinate}' has standard_name='x'; available standard names "
               "are ['atmosphere_hybrid_height_coordinate', 'atmosphere_hybrid_sigma_ln_pressure_coordinate', "
               "'atmosphere_hybrid_sigma_pressure_coordinate', 'atmosphere_sigma_coordinate', 'model_level_number'].")
    finding = Finding(3, '[CMIP7] [COORD012] Parametric vertical coordinate', message, 'CMIP7.ATM.test', 'file.nc')
    rules = RuleSet.load(ROOT/'rules/surface_and_hybrid.yaml', ROOT/'rules/catalogue_fallbacks.yaml')
    match = rules.match(finding)
    assert match.rule.id == 'C074' and match.groups == {'coordinate': coordinate}
    c = classify(QAReport(Path('report'), {}, [finding]), rules)
    assert c.items[0].outcome == Outcome.FIX
    step=FixPlan.from_classification(c).files['file.nc'][0]
    assert step.fix=='fix_hybrid_coefficients' and step.params=={'coordinate':coordinate}


@pytest.mark.parametrize('invalid',['numeric','unknown_label','wrong_axis'])
def test_surface_refuses_non_osurf_representations(tmp_path,invalid):
    path=tmp_path/'test.nc'
    if invalid=='numeric':
        with netCDF4.Dataset(path,'w') as ds:
            v=ds.createVariable('seasurface','f8',());v.assignValue(0.);v.axis='Z'
    else:
        make(path,label='not_osurf' if invalid=='unknown_label' else 'osurf')
        if invalid=='wrong_axis':
            with netCDF4.Dataset(path,'a') as ds:ds['seasurface'].axis='Y'
    original=path.read_bytes()
    p=FixPlan('test','report',{str(path):[Step('C073','drop_osurf_axis',{})]})
    with pytest.raises(ValueError):apply_plan(p,tmp_path/'log')
    assert path.read_bytes()==original


def test_hybrid_dimension_findings_share_one_reference_fix():
    findings = [
        Finding(3, '[CMIP7] [COORD012] Parametric vertical coordinate',
                f"Formula term '{name}' has dimensions ['klevp1']; its coordinate definition "
                "describes ['alevel']; the required netCDF/C dimensions are ['lev'].",
                'CMIP7.ATM.zg', 'file.nc')
        for name in ('ap', 'b')
    ]
    rules = RuleSet.load(ROOT / 'rules/surface_and_hybrid.yaml')
    p = FixPlan.from_classification(classify(QAReport(Path('report'), {}, findings), rules))
    assert len(p.files['file.nc']) == 1
    assert p.files['file.nc'][0].fix == 'fix_hybrid_coefficients'
