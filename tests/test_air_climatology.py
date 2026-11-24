"""C066 reconstructs the three INCA p19 climatologies while retaining where air."""

import json
from pathlib import Path

import cftime
import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.climatology import _ordered_methods
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

METHODS='area: mean where air time: mean within years time: mean over years'


@pytest.mark.parametrize('var',['ch4','n2o','o3'])
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('calendar',['standard','proleptic_gregorian'])
def test_sixty_months_become_twelve_means_in_one_rewrite(tmp_path,var,fmt,calendar):
    path=tmp_path/(var+'.nc');units='days since 1850-01-01'
    dates=[cftime.datetime(1850+i//12,i%12+1,1,calendar=calendar) for i in range(61)]
    edges=netCDF4.date2num(dates,units,calendar)
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.setncatts(dict(variable_id=var,temporal_label='tclm',frequency='mon',
                         branded_variable=var+'_tclm-p19-hxy-air'))
        for n,s in [('time',None),('plev',19),('lat',1),('lon',2),('bnds',2)]:ds.createDimension(n,s)
        t=ds.createVariable('time','f8',('time',));t[:]=(edges[:-1]+edges[1:])/2
        t.units=units;t.calendar=calendar;t.bounds='time_bounds';t.standard_name='time'
        ds.createVariable('time_bounds','f8',('time','bnds'))[:]=np.column_stack((edges[:-1],edges[1:]))
        ds.createVariable('plev','f8',('plev',))[:]=np.arange(19)+100
        v=ds.createVariable(var,'f4',('time','plev','lat','lon'),fill_value=1e20,zlib=True,complevel=2)
        a=np.arange(60)//12+10*(np.arange(60)%12)
        values=np.ma.array(np.broadcast_to(a[:,None,None,None],(60,19,1,2)).astype('f4'),mask=False)
        values.mask[0,0,0,1]=True;values.mask[:,1,0,1]=True;v[:]=values
        v.cell_methods=METHODS;v.comment='Preserve scientific comment'
        filters=v.filters()
    dataset=f'CMIP7.CHM.{var}.tclm-p19-hxy-air.mon.glb.g111.AnyModel.historical.r1i1p1f1'
    findings=[Finding(3,'[CMIP7] [COORD008] Coordinate bounds',
        "It is required for climatological time coordinate 'time' to have a climatology attribute naming its climatology bounds variable.",dataset,str(path))]
    if calendar=='standard':
        findings.insert(0,Finding(2,'[CMIP7] [TIME003a] Calendar for time coordinate',
            "Variable 'time' has a calendar attribute with the value 'standard'. It is recommended to use 'proleptic_gregorian' instead.",dataset,str(path)))
    result=classify(QAReport(Path('qa.json'),{},findings),RuleSet.load(*default_rule_paths()))
    p=FixPlan.from_classification(result)
    assert 'C066' in [s.rule for s in p.files[str(path)]]
    original=path.read_bytes()
    assert apply_plan(p,tmp_path/'dry',dry_run=True)['changed']==1 and path.read_bytes()==original
    assert apply_plan(p,tmp_path/'apply')['changed']==1
    with netCDF4.Dataset(path) as ds:
        assert len(ds.dimensions['time'])==12
        assert ds[var].dimensions==('time','plev','lat','lon')
        assert ds[var].cell_methods==METHODS and ds[var].comment=='Preserve scientific comment'
        assert ds[var].filters()==filters
        np.testing.assert_array_equal(ds[var][:,0,0,0],10*np.arange(12)+2)
        assert ds[var][0,0,0,1]==2.5
        assert np.ma.getmaskarray(ds[var][:,1,0,1]).all()
        np.testing.assert_array_equal(ds['plev'][:],np.arange(19)+100)
        assert ds['time'].climatology=='climatology_bnds' and 'bounds' not in ds['time'].ncattrs()
        np.testing.assert_array_equal(ds['climatology_bnds'][:],np.column_stack((edges[:12],edges[49:61])))
    assert json.loads((tmp_path/'apply').read_text())['rewrite_count']==1
    fixed=path.read_bytes()
    assert apply_plan(p,tmp_path/'again')['changed']==0 and path.read_bytes()==fixed


def test_air_clause_can_move_without_being_lost_or_broadened():
    assert _ordered_methods(METHODS,'tclm')==METHODS
    assert _ordered_methods('time: mean within years time: mean over years area: mean where air','tclm')==METHODS
    for text in (METHODS.replace('mean where air','where air'),METHODS.replace('where air','where land')):
        with pytest.raises(ValueError):_ordered_methods(text,'tclm')
