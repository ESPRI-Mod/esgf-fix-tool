"""Reported Pa-valued plev7c axes were incorrectly labelled mb/hPa."""
import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan,Step


CENTERS=np.array([90000.,74000.,62000.,50000.,37500.,24500.,9000.])
PAIRS=np.array([[100000.,80000.],[80000.,68000.],[68000.,56000.],[56000.,44000.],
                [44000.,31000.],[31000.,18000.],[18000.,0.]])


@pytest.mark.parametrize('units',['mb','hPa','Pa'])
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_literal_pa_centers_are_kept_and_dr_pairs_are_added(tmp_path,units,fmt):
    p=tmp_path/'clisccp.nc'
    with netCDF4.Dataset(p,'w',format=fmt) as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.branded_variable='clisccp_tavg-p7c-hxy-air'
        ds.createDimension('plev',7);ds.createDimension('time',2)
        ds.createVariable('plev','f8',('plev',))[:]=CENTERS;ds['plev'].units=units
        ds.createVariable('clisccp','i2',('time','plev'))[:]=np.arange(14).reshape(2,7)
    q=FixPlan('t','s',{str(p):[
        Step('C095','add_reviewed_coordinate_bounds',{'coordinate':'plev','add_bounds':False}),
        Step('C103c','add_reviewed_coordinate_bounds',{'coordinate':'plev'})]})
    before=p.read_bytes();inode=p.stat().st_ino
    assert apply_plan(q,tmp_path/'dry.jsonl',dry_run=True)['changed']==1
    assert p.read_bytes()==before
    assert apply_plan(q,tmp_path/'log')['changed']==1
    with netCDF4.Dataset(p) as ds:
        assert ds['plev'].units=='Pa' and ds['plev'].bounds=='plev_bnds'
        np.testing.assert_array_equal(ds['plev'][:],CENTERS)
        np.testing.assert_array_equal(ds['plev_bnds'][:],PAIRS)
        np.testing.assert_array_equal(ds['clisccp'][:],np.arange(14).reshape(2,7))
    assert p.stat().st_ino==inode
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==0
    after=p.read_bytes()
    assert apply_plan(q,tmp_path/'replay')['changed']==0 and p.read_bytes()==after


def test_c095_metadata_only_does_not_add_excluded_bounds(tmp_path):
    p=tmp_path/'metadata.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.branded_variable='clisccp_tavg-p7c-hxy-air'
        ds.createDimension('plev',7);ds.createVariable('plev','f8',('plev',))[:]=CENTERS
        ds['plev'].units='hPa'
    q=FixPlan('t','s',{str(p):[Step('C095','add_reviewed_coordinate_bounds',{'coordinate':'plev','add_bounds':False})]})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert ds['plev'].units=='Pa' and 'plev_bnds' not in ds.variables and 'bounds' not in ds['plev'].ncattrs()
