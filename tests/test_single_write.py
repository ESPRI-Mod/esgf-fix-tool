"""Observable I/O guarantees: zero file copy for metadata, one stream for all structural fixes."""
import json
import os
import shutil

import netCDF4
import numpy as np
import pytest
from test_dimension_order import make_histogram
from test_rectilinear_profiles import make as make_profile

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.plan import FixPlan, Step


def plan(path,steps):return FixPlan('test','report',{str(path):steps})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_attributes_and_coordinate_rename_update_the_same_inode_without_copy(tmp_path,monkeypatch,fmt):
    p=tmp_path/'file.nc'
    with netCDF4.Dataset(p,'w',format=fmt) as ds:
        ds.createDimension('old',3);ds.createDimension('time',None)
        ds.createVariable('old','f8',('old',))[:]=[1.,2.,3.]
        v=ds.createVariable('science','f4',('time','old'));v[:]=np.arange(6).reshape(2,3);v.history='remove'
    inode=p.stat().st_ino
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected whole-file write/copy')
    monkeypatch.setattr(shutil,'copyfile',forbidden);monkeypatch.setattr(Batch,'write',forbidden)
    q=plan(p,[Step('C001','drop_var_attr',dict(var='science',attr='history')),
              Step('rename','rename_coordinate',dict(sources=['old'],target='lev'))])
    assert apply_plan(q,tmp_path/'log')['changed']==1 and p.stat().st_ino==inode
    with netCDF4.Dataset(p) as ds:
        assert 'history' not in ds['science'].ncattrs() and ds['science'].dimensions==('time','lev')
        assert ds['science'][:].tobytes()==np.arange(6,dtype='f4').reshape(2,3).tobytes()
        assert ds['lev'][:].tolist()==[1.,2.,3.]
    log=json.loads((tmp_path/'log').read_text());assert log['write_mode']=='in-place' and log['rewrite_count']==0


def test_attribute_only_preflight_never_reads_the_large_science_field(tmp_path,monkeypatch):
    import esgf_fix_tool.batch_write as writer
    from esgf_fix_tool.preview import Variable
    p=tmp_path/'large-sparse.nc'
    with netCDF4.Dataset(p,'w') as ds:
        for name,n in [('t',2),('z',79),('y',143),('x',144)]:ds.createDimension(name,n)
        ds.createVariable('science','f4',('t','z','y','x'),fill_value=False).history='remove'
    get=Variable.__getitem__
    def no_science(v,key):
        if v.name=='science':raise AssertionError('Unnecessary science-array read')
        return get(v,key)
    monkeypatch.setattr(Variable,'__getitem__',no_science)
    monkeypatch.setattr(writer,'data_blocks',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unnecessary array traversal')))
    inode=p.stat().st_ino
    q=plan(p,[Step('C001','drop_var_attr',dict(var='science',attr='history'))])
    assert apply_plan(q,tmp_path/'log')['changed']==1 and p.stat().st_ino==inode


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_permutation_renames_dimension_merges_and_attributes_share_one_output(tmp_path,monkeypatch,fmt):
    p=tmp_path/'file.nc';make_histogram(p,'science','radius',fmt)
    with netCDF4.Dataset(p,'a') as ds:
        for name in ['axis_nbounds','nvertex','bnds']:ds.createDimension(name,2)
        ds.createVariable('mixed','f8',('axis_nbounds','nvertex'))[:]=[[1.,2.],[3.,4.]]
        ds['science'].history='remove'
    with netCDF4.Dataset(p) as ds:ds.set_auto_maskandscale(False);original=ds['science'][:].copy()
    calls=[];real=Batch.write
    def count(batch,path):calls.append(path);return real(batch,path)
    monkeypatch.setattr(Batch,'write',count)
    monkeypatch.setattr(shutil,'copyfile',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Initial full copy')))
    q=plan(p,[Step('history','drop_var_attr',dict(var='science',attr='history')),
        Step('layout','fix_data_layout',dict(var='science',target_order=['time','tau','level','lat','lon'],aliases={'radius':'level'},backend='python')),
        Step('bounds','normalize_bounds_dimension',{})])
    assert apply_plan(q,tmp_path/'log')['changed']==1 and len(calls)==1
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['science'][:].tobytes()==original.transpose(0,2,1,3,4).tobytes()
        assert ds['mixed'].dimensions==('bnds','bnds') and ds['mixed'][:].tolist()==[[1.,2.],[3.,4.]]
        assert 'axis_nbounds' not in ds.dimensions and 'nvertex' not in ds.dimensions
        assert 'history' not in ds['science'].ncattrs()
    inode=p.stat().st_ino;before=p.read_bytes()
    assert apply_plan(q,tmp_path/'log')['changed']==0 and len(calls)==1
    assert p.stat().st_ino==inode and p.read_bytes()==before
    assert [json.loads(l)['rewrite_count'] for l in (tmp_path/'log').read_text().splitlines()]==[1,0]


def test_profile_and_bound_dimension_merge_are_written_together(tmp_path,monkeypatch):
    p=tmp_path/'profile.nc';make_profile(p)
    with netCDF4.Dataset(p,'a') as ds:
        ds.createDimension('nvertex',2);ds.createVariable('extra','f8',('nvertex',))[:]=[10.,20.]
    calls=[];real=Batch.write
    def count(batch,path):calls.append(path);return real(batch,path)
    monkeypatch.setattr(Batch,'write',count)
    q=plan(p,[Step('C070','fix_basin_profile_latitude',{'var':'hfbasin'}),Step('C068','normalize_bounds_dimension',{})])
    apply_plan(q,tmp_path/'log');assert len(calls)==1
    with netCDF4.Dataset(p) as ds:
        assert ds['hfbasin'].dimensions==('time','basin','lat') and len(ds.dimensions['lat'])==332
        assert ds['lat'][-1]==89.36999 and ds['time_bounds'].dimensions==('time','bnds')
        assert ds['extra'].dimensions==('bnds',)


def test_multiple_variable_permutations_do_not_multiply_file_writes(tmp_path,monkeypatch):
    p=tmp_path/'file.nc';make_histogram(p,'science','radius')
    with netCDF4.Dataset(p,'a') as ds:
        v=ds['science'];v.set_auto_maskandscale(False);raw=v[:].copy()
        ds.createVariable('second','f4',v.dimensions)[:]=raw
    source=tmp_path/'source.nc';os.link(p,source)
    calls=[];real=Batch.write
    def count(batch,path):calls.append(path);return real(batch,path)
    monkeypatch.setattr(Batch,'write',count)
    q=plan(p,[Step('a','fix_data_layout',dict(var='science',target_order=['time','tau','radius','lat','lon'],backend='python')),
              Step('b','fix_data_layout',dict(var='second',target_order=['time','radius','lat','lon','tau'],backend='python'))])
    apply_plan(q,tmp_path/'log');assert len(calls)==1
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['science'][:].tobytes()==raw.transpose(0,2,1,3,4).tobytes()
        assert ds['second'][:].tobytes()==raw.astype('f4').transpose(0,1,3,4,2).tobytes()
    with netCDF4.Dataset(source) as ds:assert ds['science'].dimensions==('time','radius','tau','lat','lon')


def test_dry_run_does_not_create_or_write_a_full_temporary_file(tmp_path,monkeypatch):
    import tempfile
    p=tmp_path/'file.nc';make_histogram(p,'science','radius');before=p.read_bytes()
    monkeypatch.setattr(tempfile,'mkstemp',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Dry-run created an output')))
    q=plan(p,[Step('layout','fix_data_layout',dict(var='science',target_order=['time','tau','radius','lat','lon'],backend='python'))])
    apply_plan(q,tmp_path/'log',dry_run=True);assert p.read_bytes()==before
    log=json.loads((tmp_path/'log').read_text());assert log['rewrite_count']==0 and log['steps'][0]['verification_scope']=='preflight-only'


def test_already_converted_profile_only_updates_the_endpoint_in_place(tmp_path,monkeypatch):
    p=tmp_path/'file.nc';make_profile(p)
    q=plan(p,[Step('C070','fix_basin_profile_latitude',{'var':'hfbasin'})]);apply_plan(q,tmp_path/'first')
    with netCDF4.Dataset(p,'a') as ds:ds['lat'][-1]=ds['lat'][-2]
    inode=p.stat().st_ino
    monkeypatch.setattr(Batch,'write',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unneeded profile rewrite')))
    apply_plan(q,tmp_path/'second');assert p.stat().st_ino==inode
    with netCDF4.Dataset(p) as ds:assert ds['lat'][-1]==89.36999


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_main_variable_rename_does_not_scan_or_backup_its_science_array(tmp_path,monkeypatch,fmt):
    import esgf_fix_tool.batch_write as writer
    from esgf_fix_tool import fixes
    p=tmp_path/'main.nc'
    raw=np.arange(12,dtype='f4').reshape(3,4)
    with netCDF4.Dataset(p,'w',format=fmt) as ds:
        ds.variable_id='science'
        ds.createDimension('y',3);ds.createDimension('x',4)
        ds.createVariable('old','f4',('y','x'))[:]=raw
    def rename(ds):
        if 'old' not in ds.variables:return False
        ds.renameVariable('old','science');return True
    monkeypatch.setitem(fixes.REGISTRY,'rename_main',fixes.FixFunction('rename_main','metadata',rename))
    def forbidden(*args,**kwargs):raise AssertionError('Renaming a main variable must not copy/read its field')
    monkeypatch.setattr(writer,'Snapshot',forbidden)
    monkeypatch.setattr(writer,'data_blocks',forbidden)
    monkeypatch.setattr(Batch,'write',forbidden)
    inode=p.stat().st_ino
    q=plan(p,[Step('rename','rename_main',{})])
    assert apply_plan(q,tmp_path/'log')['changed']==1 and p.stat().st_ino==inode
    with netCDF4.Dataset(p) as ds:assert ds['science'][:].tobytes()==raw.tobytes()
    assert apply_plan(q,tmp_path/'log')['changed']==0
