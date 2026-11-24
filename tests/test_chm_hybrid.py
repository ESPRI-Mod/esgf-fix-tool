"""CHM uses the reviewed coefficient repair before normalization, including mixed realms."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.hybrid import STANDARD_NAME, _coefficient_reference
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


def selected(path, variable='c2h4', brand='tavg-al-hxy-u'):
    finding=Finding(3,'[CF-Conventions] §3.3 Standard Name',
                    'standard_name x is not defined in Standard Name Table v95.',
                    f'CMIP7.CHM.{variable}.{brand}.mon.glb.g111.AnyModel.historical.r1i1p1f1',str(path))
    return classify(QAReport(Path('qa.json'),{},[finding]),RuleSet.load(*default_rule_paths()))


@pytest.mark.parametrize('realm',['aerosol','atmosChem','atmosChem aerosol','aerosol atmosChem'])
@pytest.mark.parametrize('size,count',[(79,80),(79,79),(80,79),(95,96),(96,95)])
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_chemical_axis_is_bound_and_normalized_without_changing_science(tmp_path, realm, size, count, fmt):
    path=tmp_path/'chem.nc';refs=_coefficient_reference()
    label='al' if size in (79,95) else 'alh'
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.setncatts(dict(variable_id='c2h4',branded_variable=f'c2h4_tavg-{label}-hxy-u',
                         vertical_label=label,realm=realm,source_id='AnotherModel'))
        for n,s in [('time',2),('lev',size),('coeff',count),('bnds',2),('cell',3)]:
            ds.createDimension(n,s)
        lev=ds.createVariable('lev','f8',('lev',));lev[:]=refs[size]['ap']+101325*refs[size]['b']
        lev.standard_name='x';lev.units='Pa'
        for name in ('ap','b'):
            ds.createVariable(name,'f8',('coeff',))[:]=refs[count][name]
            ds.createVariable(name+'_bnds','f8',('bnds','coeff'))[:]=np.tile(refs[count][name],(2,1))
        ps=ds.createVariable('ps','f4',('time','cell'));ps[:]=100000;ps.units='Pa'
        v=ds.createVariable('c2h4','f4',('time','lev','cell'),zlib=True,complevel=2,fill_value=1e20)
        values=np.ma.array(np.arange(2*size*3,dtype='f4').reshape(2,size,3),mask=False)
        values.mask[0,0,0]=True;v[:]=values;v.comment='Keep scientific metadata'
        ds.set_auto_maskandscale(False)
        before={n:(ds[n][:].tobytes(),ds[n].filters(),ds[n].chunking()) for n in ('c2h4','ps')}
    result=selected(path,brand=f'tavg-{label}-hxy-u')
    assert result.items[0].match.rule.id=='C014a'
    plan=FixPlan.from_classification(result)
    original=path.read_bytes()
    assert apply_plan(plan,tmp_path/'dry',dry_run=True)['changed']==1
    assert path.read_bytes()==original
    assert apply_plan(plan,tmp_path/'log')['changed']==1
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['lev'].standard_name==STANDARD_NAME
        assert ds['lev'].units=='1' and ds['lev'].computed_standard_name=='air_pressure'
        np.testing.assert_array_equal(ds['lev'][:],refs[size]['ap']/101325+refs[size]['b'])
        for name in ('ap','b'):
            assert ds[name].dimensions==('lev',)
            np.testing.assert_array_equal(ds[name][:],refs[size][name])
            if label=='al':
                assert ds[name+'_bnds'].dimensions==('lev','bnds')
        if label=='al':
            assert ds['lev'].bounds=='lev_bnds'
            assert ds['lev_bnds'].formula_terms=='ap: ap_bnds b: b_bnds ps: ps'
        else:
            assert 'bounds' not in ds['lev'].ncattrs()
        for name,(raw,filters,chunks) in before.items():
            assert ds[name][:].tobytes()==raw
            assert ds[name].filters()==filters and ds[name].chunking()==chunks
        assert ds['c2h4'].comment=='Keep scientific metadata'
    fixed=path.read_bytes()
    assert apply_plan(plan,tmp_path/'again')['changed']==0 and path.read_bytes()==fixed


@pytest.mark.parametrize('variable',['ch4','n2o'])
def test_retired_global_products_belong_to_the_cleanup_step(tmp_path, variable):
    result=selected(tmp_path/'global.nc',variable,'tclm-u-hm-air')
    assert result.items[0].outcome.value=='other_step'
    assert result.items[0].match.rule.id=='C014b'
    assert not FixPlan.from_classification(result).files
