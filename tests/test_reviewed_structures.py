"""Approved physical changes compose into one output and preserve data ordering."""

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.fixes.hybrid import _coefficient_reference
from esgf_fix_tool.fixes.reviewed_structures import BASIN_LABELS
from esgf_fix_tool.plan import FixPlan, Step


def apply_checked(path, tmp, steps, monkeypatch, rewrite=True):
    q=FixPlan('test','synthetic',{str(path):steps})
    before=path.read_bytes()
    assert apply_plan(q,tmp/'dry.jsonl',dry_run=True)['changed']==1
    assert before==path.read_bytes()
    calls=[]; original=Batch.write
    def record(batch,output):
        calls.append(output)
        return original(batch,output)
    monkeypatch.setattr(Batch,'write',record)
    assert apply_plan(q,tmp/'apply.jsonl')['changed']==1
    assert len(calls)==int(rewrite)
    after=path.read_bytes();inode=path.stat().st_ino
    assert apply_plan(q,tmp/'replay.jsonl')['changed']==0
    assert path.read_bytes()==after and path.stat().st_ino==inode
    return q


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('name',['time_bounds','time_bnds'])
def test_delete_orphan_point_bounds_with_old_long_name(tmp_path,monkeypatch,fmt,name):
    p=tmp_path/'points.nc'
    with netCDF4.Dataset(p,'w',format=fmt) as ds:
        ds.variable_id='ta';ds.temporal_label='tpt';ds.createDimension('time',None);ds.createDimension('bnds',2)
        ds.createVariable('ta','f4',('time',))[:]=[2,4];ds['ta'].cell_methods='time: point'
        ds.createVariable('time','f8',('time',))[:]=[1,2]
        b=ds.createVariable(name,'f8',('time','bnds'));b[:]=[[1,1],[2,2]];b.long_name='Old orphan'
    apply_checked(p,tmp_path,[Step('C060','detach_instantaneous_time_bounds',{})],monkeypatch)
    with netCDF4.Dataset(p) as ds:
        assert name not in ds.variables
        np.testing.assert_array_equal(ds['ta'][:],[2,4])
        np.testing.assert_array_equal(ds['time'][:],[1,2])


def zostoga_file(p,fmt='NETCDF4',dtype='f4'):
    with netCDF4.Dataset(p,'w',format=fmt) as ds:
        ds.setncatts(dict(source_id='IPSL-CM6-ESMCO2',branded_variable='zostoga_tavg-u-hm-sea',
                         grid_label='g010',variable_id='zostoga'))
        for n,size in [('time',None),('y',2),('x',3),('bnds',2),('nvertex',4)]:ds.createDimension(n,size)
        ds.createVariable('time','f8',('time',))[:]=[.5,1.5,2.5];ds['time'].bounds='time_bnds'
        ds.createVariable('time_bnds','f8',('time','bnds'))[:]=[[0,1],[1,2],[2,3]]
        for n in ('nav_lat','nav_lon'):
            v=ds.createVariable(n,'f8',('y','x'));v[:]=np.arange(6).reshape(2,3);v.bounds='bounds_'+n
            ds.createVariable('bounds_'+n,'f8',('y','x','nvertex'))[:]=1.
        fill=np.int16(-999) if dtype=='i2' else np.float32(1e20)
        v=ds.createVariable('zostoga',dtype,('time','y','x'),fill_value=fill,zlib=fmt.startswith('NETCDF4'))
        v.set_auto_maskandscale(False);v[:]=np.broadcast_to(np.arange(3)[:,None,None]+10,(3,2,3))
        v[:,0,0]=fill;v.coordinates='nav_lat nav_lon';v.units='m'
        if dtype=='i2':v.scale_factor=.25;v.add_offset=100.


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('dtype',['f4','i2'])
def test_zostoga_keeps_one_valid_raw_value_and_real_time_bounds(tmp_path,monkeypatch,fmt,dtype):
    p=tmp_path/'zostoga.nc';zostoga_file(p,fmt,dtype)
    apply_checked(p,tmp_path,[Step('C050','reduce_replicated_zostoga',{}),
                              Step('C111','reduce_replicated_zostoga',{})],monkeypatch)
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        assert set(ds.variables)=={'zostoga','time','time_bnds'}
        assert set(ds.dimensions)=={'time','bnds'} and ds.dimensions['time'].isunlimited()
        assert ds['zostoga'].dimensions==('time',) and ds['zostoga'].dtype==np.dtype(dtype)
        np.testing.assert_array_equal(ds['zostoga'][:],[10,11,12])
        np.testing.assert_array_equal(ds['time_bnds'][:],[[0,1],[1,2],[2,3]])
        assert 'coordinates' not in ds['zostoga'].ncattrs()
        if dtype=='i2':assert ds['zostoga'].scale_factor==.25 and ds['zostoga'].add_offset==100.


@pytest.mark.parametrize('bad',['different','empty'])
def test_zostoga_refuses_a_spatial_difference_or_missing_whole_slice(tmp_path,bad):
    p=tmp_path/'zostoga.nc';zostoga_file(p)
    with netCDF4.Dataset(p,'a') as ds:
        ds['zostoga'][2,:,:]=ds['zostoga']._FillValue if bad=='empty' else 12.
        if bad=='different':ds['zostoga'][2,1,1]=13.
    before=p.read_bytes()
    with pytest.raises(ValueError,match='t=2'):
        apply_plan(FixPlan('t','s',{str(p):[Step('C050','reduce_replicated_zostoga',{})]}),tmp_path/'log')
    assert p.read_bytes()==before


@pytest.mark.parametrize('renamed',[False,True])
def test_soil_nodes_and_main_values_remain_unchanged(tmp_path,monkeypatch,renamed):
    p=tmp_path/'soil.nc';name='depth' if renamed else 'solay'
    nodes=np.r_[0.,np.geomspace(.001,2.,10)]
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='mrsol'
        ds.createDimension(name,11);ds.createDimension('lat',2);ds.createDimension('lon',3)
        v=ds.createVariable(name,'f8',(name,));v[:]=nodes;v.units='m' if renamed else '1'
        v.standard_name='model_level_number'
        ds.createVariable('mrsol','i2',(name,'lat','lon'))[:]=np.arange(66).reshape(11,2,3)
    apply_checked(p,tmp_path,[Step('C022','fix_orchidee_soil_depth',{}),
                              Step('C052','fix_orchidee_soil_depth',{})],monkeypatch,rewrite=False)
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds['depth'][:],nodes)
        assert ds['depth'].units=='m' and ds['depth'].standard_name=='depth'
        expected=np.r_[nodes[0],(nodes[:-1]+nodes[1:])/2,nodes[-1]]
        np.testing.assert_array_equal(ds['depth_bnds'][:],np.column_stack((expected[:-1],expected[1:])))
        np.testing.assert_array_equal(ds['mrsol'][:],np.arange(66).reshape(11,2,3))
        assert ds['mrsol'].dimensions==('depth','lat','lon')


def test_soil_indices_are_not_silently_replaced_by_guessed_depths(tmp_path):
    p=tmp_path/'soil.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='mrsol';ds.createDimension('solay',11)
        ds.createVariable('solay','f8',('solay',))[:]=np.arange(1,12);ds['solay'].units='1'
    before=p.read_bytes()
    with pytest.raises(ValueError,match='0..2'):
        apply_plan(FixPlan('t','s',{str(p):[Step('C022','fix_orchidee_soil_depth',{})]}),tmp_path/'log')
    assert p.read_bytes()==before


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('aliases',[False,True])
def test_msfty_and_sector_compose_without_reordering_science(tmp_path,monkeypatch,reverse,aliases):
    p=tmp_path/'msfty.nc';lat='latitude' if aliases else 'nav_lat';lon='longitude' if aliases else 'nav_lon'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='msfty'
        for n,size in [('time',2),('basin',3),('lev',75),('y',332),('bnds',2)]:ds.createDimension(n,size)
        ds.createVariable(lat,'f8',('y',))[:]=np.linspace(-78,89,332)
        ds.createVariable(lon,'f8',('y',))[:]=0.
        v=ds.createVariable('lev','f8',('lev',));v[:]=np.arange(75);v.units='m';v.positive='down';v.bounds='olevel_bounds'
        ds.createVariable('olevel_bounds','f8',('lev','bnds'))[:]=np.arange(150).reshape(75,2)
        v=ds.createVariable('basin','f8',('basin',));v[:]=[1,2,3]
        v.long_name='Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)';v.standard_name='region'
        science=np.arange(2*3*75*332,dtype='f4').reshape(2,3,75,332)
        ds.createVariable('msfty','f4',('time','basin','lev','y'),zlib=True)[:]=science
        ds['msfty'].coordinates=f'{lat} {lon} basin'
    steps=[Step('C025','fix_msfty_coordinates',{}),Step('C032','fix_basin_sector',{})]
    if reverse:steps.reverse()
    apply_checked(p,tmp_path,steps,monkeypatch)
    with netCDF4.Dataset(p) as ds:
        assert 'y' not in ds.dimensions and lon not in ds.variables
        np.testing.assert_array_equal(ds['basin'][:], [1,2,3])
        assert ds['rlat'].dimensions==('rlat',) and ds['rlat'].standard_name=='grid_latitude'
        assert ds['rlat'].units=='degrees' and ds['rlat'].axis=='Y'
        assert ds['lev'].standard_name=='depth' and ds['lev'].axis=='Z' and ds['lev'].bounds=='lev_bnds'
        assert ds['msfty'].dimensions==('time','basin','lev','rlat') and ds['msfty'].coordinates=='sector'
        np.testing.assert_array_equal(ds['msfty'][:],science)
        assert [row.tobytes().decode().strip() for row in ds['sector'][:]]==list(BASIN_LABELS)


def test_sector_code_order_is_preserved_with_existing_old_labels(tmp_path,monkeypatch):
    p=tmp_path/'sector.nc';codes=[3,1,2]
    old=['indo_pacific_ocean','global_ocean','atlantic_ocean']
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='hfbasinpmadv'
        ds.createDimension('basin',3);ds.createDimension('str_len',255)
        v=ds.createVariable('basin','f8',('basin',));v[:]=codes
        v.long_name='Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)'
        a=np.full((3,255),b'\0',dtype='S1')
        for row,label in zip(a,old):row[:len(label)]=np.frombuffer(label.encode(),dtype='S1')
        ds.createVariable('sector','S1',('basin','str_len'))[:]=a
        ds.createVariable('hfbasinpmadv','f4',('basin',))[:]=[33,11,22]
    apply_checked(p,tmp_path,[Step('C032','fix_basin_sector',{})],monkeypatch)
    with netCDF4.Dataset(p) as ds:
        assert [row.tobytes().decode().strip() for row in ds['sector'][:]]==[BASIN_LABELS[c-1] for c in codes]
        np.testing.assert_array_equal(ds['hfbasinpmadv'][:],[33,11,22])


def test_existing_hybrid_backups_are_removed_after_reference_validation(tmp_path,monkeypatch):
    p=tmp_path/'hybrid.nc';ref=_coefficient_reference()
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.realm='atmos';ds.variable_id='ta'
        ds.createDimension('lev',79);ds.createDimension('klevp1',80);ds.createDimension('bnds',2);ds.createDimension('time',2)
        ds.createVariable('lev','f8',('lev',))[:]=ref[79]['ap']+101325*ref[79]['b'];ds['lev'].units='1'
        ds.createVariable('ta','f4',('time','lev'))[:]=np.arange(158).reshape(2,79)
        ds.createVariable('ps','f4',('time',))[:]=1e5;ds['ps'].units='Pa'
        for n in ('ap','b'):
            ds.createVariable(n,'f8',('lev',))[:]=ref[79][n]
            ds.createVariable(n+'_bnds','f8',('lev','bnds'))[:]=np.column_stack((ref[80][n][:-1],ref[80][n][1:]))
            ds[n].bounds=n+'_bnds'
            ds.createVariable('fix_original_'+n,'f8',('klevp1',))[:]=ref[80][n]
    apply_checked(p,tmp_path,[Step('C074a','fix_hybrid_coefficients',{'coordinate':'alevel'})],monkeypatch)
    with netCDF4.Dataset(p) as ds:
        assert not any(n.startswith('fix_original_') for n in ds.variables)
        np.testing.assert_array_equal(ds['ta'][:],np.arange(158).reshape(2,79))
