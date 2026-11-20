"""Restore only the reviewed 32 masked rows using the 331-point native profile."""

import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.fixes.msfty_latitude import reference_331
from esgf_fix_tool.plan import FixPlan, Step


def make(path, fmt, alias='nav_lat', pending_x=False, packed=False):
    profile=reference_331().copy();profile[:32]=0
    dims=('time','basin','lev','y')+(('x',) if pending_x else ())
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.setncatts(dict(variable_id='msfty',branded_variable='msfty_tavg-ol-ht-sea',
                         grid_label='g112',horizontal_label='ht',source_id='A_MODEL',history='Keep global history'))
        for n,size in [('time',None),('basin',3),('lev',75),('y',331),('bnds',2)]:ds.createDimension(n,size)
        if pending_x:ds.createDimension('x',1)
        shape=(3,3,75,331)+((1,) if pending_x else ())
        a=np.ma.array(np.arange(np.prod(shape),dtype='f4').reshape(shape)/8,mask=False)
        a.mask[:,:,:,:32]=True
        dtype,fill=('i2',-32767) if packed else ('f4',1e20)
        options=dict(zlib=True,complevel=2) if fmt.startswith('NETCDF4') else {}
        v=ds.createVariable('msfty',dtype,dims,fill_value=fill,**options)
        if packed:v.scale_factor=2.;v.add_offset=1.
        v[:]=a;v.coordinates=alias+' nav_lon';v.units='kg s-1';v.comment='Original scientific comment'
        coord_dims=('y','x') if pending_x else ('y',)
        ds.createVariable(alias,'f8',coord_dims)[:]=profile.reshape(331,-1) if pending_x else profile
        ds[alias].standard_name='latitude';ds[alias].units='degrees_north'
        ds.createVariable('nav_lon','f8',coord_dims)[:]=0
        ds.createVariable('lev','f8',('lev',))[:]=np.arange(75)+.5;ds['lev'].units='m'
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return ds['msfty'][:].copy(),ds['msfty'].filters(),ds['msfty'].dtype


def plan(path, pending_x=False):
    steps=[Step('C025','fix_msfty_coordinates',{})]
    if pending_x:
        steps.append(Step('C054','fix_data_layout',dict(var='msfty',target_order=['time','basin','lev','y'],
                                                      drop=['x'],drop_shared=True,backend='python')))
    return FixPlan('test','qa',{str(path):steps})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('alias',['nav_lat','latitude'])
@pytest.mark.parametrize('pending_x',[False,True])
def test_restore_reference_without_losing_any_row_or_science_value(tmp_path,monkeypatch,fmt,alias,pending_x):
    path=tmp_path/'msfty.nc';raw,filters,dtype=make(path,fmt,alias,pending_x)
    original=path.read_bytes();q=plan(path,pending_x)
    apply_plan(q,tmp_path/'dry',dry_run=True);assert path.read_bytes()==original
    writes=[];write=Batch.write
    def record(batch,out):writes.append(out);return write(batch,out)
    monkeypatch.setattr(Batch,'write',record)
    apply_plan(q,tmp_path/'log')
    assert len(writes)==1 and json.loads((tmp_path/'log').read_text())['rewrite_count']==1
    with netCDF4.Dataset(path) as ds:
        assert ds['msfty'].dimensions==('time','basin','lev','rlat') and ds['msfty'].shape==(3,3,75,331)
        assert ds['rlat'].standard_name=='grid_latitude' and ds['rlat'].units=='degrees' and ds['rlat'].axis=='Y'
        assert ds['rlat'].long_name=='Grid Latitude'
        np.testing.assert_array_equal(ds['rlat'][:],reference_331())
        assert ds['rlat'][-1]==88.94849723000922 and ds['rlat'][32]==-79.52471556192377
        assert np.all(np.diff(ds['rlat'][:])>0)
        assert np.count_nonzero(ds['rlat'][:]==0)==1  # Keep the genuine equator.
        from esgf_fix_tool.fixes.basin_latitude_bounds import conventional_bounds
        assert ds['rlat'].bounds == 'rlat_bnds'
        assert ds['rlat_bnds'].dimensions == ('rlat', 'bnds')
        np.testing.assert_array_equal(ds['rlat_bnds'][:], conventional_bounds(reference_331()))
        assert np.all(ds['rlat_bnds'][:,0] <= ds['rlat'][:])
        assert np.all(ds['rlat'][:] <= ds['rlat_bnds'][:,1])
        assert ds['rlat_bnds'][-1,1] == reference_331()[-1]+.5*(reference_331()[-1]-reference_331()[-2])
        assert np.ma.getmaskarray(ds['msfty'][:,:,:,:32]).all()
        ds.set_auto_maskandscale(False)
        expected=raw[...,0] if pending_x else raw
        assert ds['msfty'][:].tobytes()==expected.tobytes()
        assert ds['msfty'].filters()==filters and ds['msfty'].dtype==dtype
        assert ds['msfty'].comment=='Original scientific comment' and ds.history=='Keep global history'
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected and len(writes)==1


@pytest.mark.parametrize('failure',['valid_second_month','wrong_tail','wrong_grid','missing_times','31_zeros'])
def test_guard_every_time_and_reference_value_before_writing(tmp_path,failure):
    path=tmp_path/'msfty.nc';make(path,'NETCDF4')
    with netCDF4.Dataset(path,'a') as ds:
        if failure=='valid_second_month':ds['msfty'][1,2,74,31]=0.0
        elif failure=='wrong_tail':ds['nav_lat'][100]+=.001
        elif failure=='wrong_grid':ds.grid_label='g168'
        elif failure=='missing_times':ds.renameVariable('msfty','other')
        else:ds['nav_lat'][31]=reference_331()[31]
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path),tmp_path/'log')
    assert path.read_bytes()==original


def test_packed_msfty_missing_mask_and_raw_values_are_preserved(tmp_path):
    path=tmp_path/'packed.nc';raw,filters,dtype=make(path,'NETCDF4',packed=True)
    apply_plan(plan(path),tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert np.ma.getmaskarray(ds['msfty'][:,:,:,:32]).all()
        ds.set_auto_maskandscale(False)
        assert ds['msfty'][:].tobytes()==raw.tobytes() and ds['msfty'].dtype==dtype
        assert ds['msfty'].filters()==filters and ds['msfty'].scale_factor==2.


def test_existing_rlat_prefix_and_c103_compose_in_one_rewrite(tmp_path):
    path=tmp_path/'msfty.nc';raw,_filters,_dtype=make(path,'NETCDF4')
    with netCDF4.Dataset(path,'a') as ds:
        ds.renameVariable('nav_lat','rlat');ds.renameDimension('y','rlat')
        ds['msfty'].coordinates='rlat nav_lon'
    # NetCDF-4 identifier changes require reopening before coordinate values
    # are restored; create a valid already-renamed input for this scenario.
    with netCDF4.Dataset(path,'a') as ds:
        values=reference_331().copy();values[:32]=0;ds['rlat'][:]=values
    q=plan(path)
    q.files[str(path)].insert(0,Step('C103','add_basin_latitude_bounds',{}))
    apply_plan(q,tmp_path/'log')
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==1
    with netCDF4.Dataset(path) as ds:
        np.testing.assert_array_equal(ds['rlat'][:],reference_331())
        assert ds['rlat_bnds'].shape==(331,2)
        ds.set_auto_maskandscale(False)
        assert ds['msfty'][:].tobytes()==raw.tobytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0


def test_c103_can_complete_bounds_after_coordinate_was_already_repaired(tmp_path):
    path=tmp_path/'msfty.nc';raw,_,_=make(path,'NETCDF4')
    with netCDF4.Dataset(path,'a') as ds:
        ds.renameVariable('nav_lat','rlat');ds.renameDimension('y','rlat')
        ds['rlat'][:]=reference_331();ds['rlat'].standard_name='grid_latitude';ds['rlat'].units='degrees'
        ds['rlat'].axis='Y';ds['rlat'].long_name='Grid Latitude'
        ds['msfty'].coordinates='rlat'
    with netCDF4.Dataset(path,'a') as ds:ds['rlat'][:]=reference_331()
    q=FixPlan('test','qa',{str(path):[Step('C103','add_basin_latitude_bounds',{})]})
    inode=path.stat().st_ino
    apply_plan(q,tmp_path/'log')
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==0 and path.stat().st_ino==inode
    with netCDF4.Dataset(path) as ds:
        assert ds['rlat'].bounds=='rlat_bnds'
        assert ds['rlat_bnds'][-1,1] < 90 and ds['rlat_bnds'][0,0] > -90
        ds.set_auto_maskandscale(False)
        assert ds['msfty'][:].tobytes()==raw.tobytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0
