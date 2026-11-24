"""Calendar labels change only when the existing numeric interpretation is preserved."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


def plan(path):
    finding=Finding(2,'[CMIP7] [TIME003a] Calendar for time coordinate',
        "Variable 'time' has a calendar attribute with the value 'standard'. It is recommended to use 'proleptic_gregorian' instead.",
        'CMIP7.CHM.ch4.tavg-al-hxy-u.mon.glb.g111.OtherModel.historical.r1i1p1f1',str(path))
    result=classify(QAReport(Path('qa.json'),{},[finding]),RuleSet.load(*default_rule_paths()))
    assert result.items[0].match.rule.id=='C003'
    return FixPlan.from_classification(result)


def make(path, fmt='NETCDF4', bounds='bounds'):
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.createDimension('time',2);ds.createDimension('bnds',2)
        t=ds.createVariable('time','f8',('time',));t[:]=[15.5,45]
        t.standard_name='time';t.units='days since 1850-01-01 00:00:00';t.calendar='standard'
        t.setncattr(bounds,'time_bnds')
        b=ds.createVariable('time_bnds','f8',('time','bnds'));b[:]=[[0,31],[31,59]]
        b.calendar='standard'
        v=ds.createVariable('science','f4',('time',),zlib=True,complevel=2);v[:]=[7,9]


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('bounds',['bounds','climatology'])
def test_calendar_and_explicit_bounds_calendar_change_in_place(tmp_path, monkeypatch, fmt, bounds):
    path=tmp_path/'time.nc';make(path,fmt,bounds)
    with netCDF4.Dataset(path) as ds:
        raw={n:(v[:].tobytes(),v.filters(),v.chunking()) for n,v in ds.variables.items()}
        dates=netCDF4.num2date(ds['time'][:],ds['time'].units,calendar='standard')

    def refuse(*args,**kwargs):
        raise AssertionError('A calendar label must not rewrite or copy scientific data')

    monkeypatch.setattr(Batch,'write',refuse)
    p=plan(path);original=path.read_bytes();inode=path.stat().st_ino
    assert apply_plan(p,tmp_path/'dry',dry_run=True)['changed']==1
    assert path.read_bytes()==original
    assert apply_plan(p,tmp_path/'apply')['changed']==1
    assert path.stat().st_ino==inode
    with netCDF4.Dataset(path) as ds:
        assert ds['time'].calendar==ds['time_bnds'].calendar=='proleptic_gregorian'
        result=netCDF4.num2date(ds['time'][:],ds['time'].units,calendar='proleptic_gregorian')
        assert [d.isoformat() for d in dates]==[d.isoformat() for d in result]
        for name,(values,filters,chunks) in raw.items():
            assert ds[name][:].tobytes()==values
            assert ds[name].filters()==filters and ds[name].chunking()==chunks
    fixed=path.read_bytes()
    assert apply_plan(p,tmp_path/'again')['changed']==0 and path.read_bytes()==fixed


@pytest.mark.parametrize('failure',['old_epoch','old_bound','bad_calendar','nan','masked'])
def test_unsafe_interpretations_fail_before_writing(tmp_path, failure):
    path=tmp_path/'bad.nc';make(path)
    with netCDF4.Dataset(path,'a') as ds:
        if failure=='old_epoch':ds['time'].units='days since 1500-01-01';ds['time'][:]=[128000,128031]
        elif failure=='old_bound':ds['time_bnds'][0,0]=-200000
        elif failure=='bad_calendar':ds['time_bnds'].calendar='360_day'
        elif failure=='nan':ds['time'][0]=np.nan
        else:ds['time'][0]=netCDF4.default_fillvals['f8']
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path),tmp_path/'log')
    assert path.read_bytes()==original
