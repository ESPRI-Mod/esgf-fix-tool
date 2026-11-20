"""Whole latitude reversal is streamed, atomic, lossless and idempotent."""
import json

import netCDF4
import numpy as np
import pytest

import esgf_fix_tool.batch_write as writer
from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.data_integrity import _slices
from esgf_fix_tool.plan import FixPlan, Step


def make(path, var, fmt, reversed_endpoints):
    lat=np.arange(89.75,-90.,-.5)
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.variable_id=var;ds.grid_label='g131';ds.history='Original global history'
        for n,size in [('time',2),('lat',360),('lon',3),('bnds',2)]:ds.createDimension(n,size)
        ds.createVariable('lat','f8',('lat',))[:]=lat
        ds['lat'].standard_name='latitude';ds['lat'].units='degrees_north';ds['lat'].bounds='lat_bnds'
        bounds=np.column_stack((lat-.25,lat+.25))
        ds.createVariable('lat_bnds','f8',('lat','bnds'))[:]=bounds[:,::-1] if reversed_endpoints else bounds
        dims=('lat','lon') if var=='areacellr' else ('time','lat','lon')
        shape=tuple(len(ds.dimensions[n]) for n in dims)
        data=np.arange(np.prod(shape),dtype='f4').reshape(shape);data.flat[7]=1e20
        options=dict(zlib=True,complevel=2) if fmt.startswith('NETCDF4') else {}
        v=ds.createVariable(var,'f4',dims,fill_value=1e20,**options);v[:]=data
        v.long_name='Unchanged scientific name';v.history='none'
        # Auxiliaries with lat in another position must receive the same mapping.
        v=ds.createVariable('packed_aux','i2',('lon','lat'),fill_value=-32767,**options)
        v.scale_factor=.25;v.add_offset=3.;v.set_auto_maskandscale(False)
        a=np.arange(1080,dtype='i2').reshape(3,360);a[0,2]=-32767;v[:]=a
        ds.createVariable('independent','f8',('time',))[:]=[1,2]
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return {n:(v.dimensions,v[:].copy(),v.filters(),v.chunking(),v.dtype) for n,v in ds.variables.items()}


def plan(path):
    return FixPlan('test','qa',{str(path):[
        Step('C118','reverse_reviewed_latitude',{}),
        Step('C001','drop_variable_histories',{}),
    ]})


@pytest.mark.parametrize('var',['areacellr','rivi','rivo'])
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('reversed_endpoints',[False,True])
def test_reverses_every_latitude_array_once(tmp_path,monkeypatch,var,fmt,reversed_endpoints):
    path=tmp_path/'reversed.nc';originals=make(path,var,fmt,reversed_endpoints)
    original=path.read_bytes();q=plan(path)
    apply_plan(q,tmp_path/'dry',dry_run=True);assert path.read_bytes()==original
    writes=[];write=writer.Batch.write
    def record(batch,out):
        assert path.read_bytes()==original
        writes.append(out);return write(batch,out)
    monkeypatch.setattr(writer.Batch,'write',record)
    # Force multiple latitude slabs; reversing each tile without relocating it
    # would fail even though a one-tile implementation could appear correct.
    monkeypatch.setattr(writer,'data_blocks',lambda v,operation:_slices(v.shape,v.dtype.itemsize,budget=1024))
    apply_plan(q,tmp_path/'log')
    assert len(writes)==1 and json.loads((tmp_path/'log').read_text())['rewrite_count']==1
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['lat'][0]==-89.75 and ds['lat'][-1]==89.75 and np.all(np.diff(ds['lat'][:])>0)
        for n,(dims,raw,filters,chunks,dtype) in originals.items():
            expected=np.flip(raw,axis=dims.index('lat')) if 'lat' in dims else raw
            if n=='lat_bnds':expected=np.sort(expected,axis=1)
            assert ds[n][:].tobytes()==expected.tobytes(),n
            assert ds[n].dtype==dtype and ds[n].filters()==filters and ds[n].chunking()==chunks
        assert ds.history=='Original global history' and ds[var].long_name=='Unchanged scientific name'
        assert 'history' not in ds[var].ncattrs()
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected and len(writes)==1


@pytest.mark.parametrize('failure',['grid','values','bounds','verification'])
def test_invalid_input_or_failed_verification_keeps_original(tmp_path,monkeypatch,failure):
    path=tmp_path/'reversed.nc';make(path,'rivi','NETCDF4',True)
    with netCDF4.Dataset(path,'a') as ds:
        if failure=='grid':ds.grid_label='g245'
        elif failure=='values':ds['lat'][10]+=0.01
        elif failure=='bounds':ds['lat_bnds'][2,:]=[0,1]
    if failure=='verification':
        def refuse(*args):raise ValueError('Injected failed verification')
        monkeypatch.setattr(writer.Batch,'verify',refuse)
    before=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path),tmp_path/'log')
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('*.fixtmp'))
