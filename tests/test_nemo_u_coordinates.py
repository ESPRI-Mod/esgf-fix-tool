"""Sfx U-point injection is exact, local, scoped and independent of science values."""

import hashlib
import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.nemo_coordinates import reference
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.preview import Variable
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


def make(path, fmt='NETCDF4', present=(), canonical=False, packed=False):
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.setncatts(dict(variable_id='sfx', branded_variable='sfx_tavg-u-hxy-sea',
                         grid_label='g168', horizontal_label='hxy', realm='ocean',
                         source_id='A_MODEL',experiment_id='AN_EXPERIMENT',history='Preserve global history'))
        for n,size in [('time',None),('y',331),('x',360),('bnds',2)]:ds.createDimension(n,size)
        ds.createVariable('time','f8',('time',))[:]=[15.5]
        ds['time'].bounds='time_bnds'
        ds.createVariable('time_bnds','f8',('time','bnds'))[:]=[[0,31]]
        options=dict(zlib=True,complevel=2,chunksizes=(1,31,60)) if fmt.startswith('NETCDF4') else {}
        dtype,fill=('i2',-32767) if packed else ('f4',1e20)
        v=ds.createVariable('sfx',dtype,('time','y','x'),fill_value=fill,**options)
        values=np.arange(331*360,dtype='i4').reshape(1,331,360)%100
        values[0,::11,::7]=fill if packed else 1  # Install float fill after casting below.
        values=values.astype(dtype)
        values[0,::11,::7]=fill
        if packed:
            v.scale_factor=.25;v.add_offset=2.;v.set_auto_maskandscale(False)
        v[:]=values
        v.standard_name='ocean_salt_x_transport';v.units='kg s-1'
        v.comment='Keep scientific comment';v.cell_methods='area: mean where sea depth: sum where sea time: mean'
        v.coordinates='latitude longitude' if canonical else 'nav_lat nav_lon'
        for key in present:
            data,attrs=reference()[key]
            name={'nav_lat':'latitude','nav_lon':'longitude'}[key] if canonical else key
            out=ds.createVariable(name,'f8',('y','x'),fill_value=False);out[:]=data;out.setncatts(attrs)
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return {n:(v[:].tobytes(),v.dtype,v.filters(),v.chunking(),
                   {a:v.getncattr(a) for a in v.ncattrs()}) for n,v in ds.variables.items()}


def plan(path):
    return FixPlan('test','qa',{str(path):[Step('C119','inject_nemo_u_coordinates',{})]})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('present',[(),('nav_lat',),('nav_lon',),('nav_lat','nav_lon')])
@pytest.mark.parametrize('canonical',[False,True])
def test_direct_injection_preserves_science_and_all_existing_arrays(tmp_path,monkeypatch,fmt,present,canonical):
    path=tmp_path/'sfx.nc';before=make(path,fmt,present,canonical)
    inode=path.stat().st_ino;original=path.read_bytes();q=plan(path)
    def forbid(*args):raise AssertionError('Coordinate injection must not rebuild or snapshot the main field')
    monkeypatch.setattr(Batch,'write',forbid)
    getitem=Variable.__getitem__
    def guarded(v,key):
        assert v.name!='sfx','Do not load the scientific field during preflight'
        return getitem(v,key)
    monkeypatch.setattr(Variable,'__getitem__',guarded)
    result=apply_plan(q,tmp_path/'dry',dry_run=True)
    assert path.read_bytes()==original and result['changed']==(len(present)!=2)
    result=apply_plan(q,tmp_path/'log')
    assert result['changed']==(len(present)!=2) and path.stat().st_ino==inode
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==0
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        for n,(raw,dtype,filters,chunks,attrs) in before.items():
            assert ds[n][:].tobytes()==raw and ds[n].dtype==dtype
            assert ds[n].filters()==filters and ds[n].chunking()==chunks
            assert set(ds[n].ncattrs())==set(attrs)
            for a,value in attrs.items():np.testing.assert_array_equal(ds[n].getncattr(a),value)
        assert ds.history=='Preserve global history'
        for key,(data,attrs) in reference().items():
            name={'nav_lat':'latitude','nav_lon':'longitude'}[key] if canonical else key
            assert ds[name].dimensions==('y','x') and ds[name].dtype==np.dtype('f8')
            assert ds[name][:].tobytes()==data.tobytes()
            for attr,value in attrs.items():assert ds[name].getncattr(attr)==value
            assert 'bounds' not in ds[name].ncattrs() and 'axis' not in ds[name].ncattrs()
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected


def test_packed_science_is_not_unpacked_or_reencoded(tmp_path):
    path=tmp_path/'packed.nc';before=make(path,packed=True)
    apply_plan(plan(path),tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['sfx'][:].tobytes()==before['sfx'][0]
        assert ds['sfx'].scale_factor==.25 and ds['sfx'].filters()==before['sfx'][2]


@pytest.mark.parametrize('failure',['grid','brand','standard_name','dimensions','reference_tokens','wrong_existing','duplicate_alias','packing'])
def test_wrong_grid_or_coordinate_never_changes_source(tmp_path,failure):
    path=tmp_path/'sfx.nc';make(path,present=('nav_lat',))
    with netCDF4.Dataset(path,'a') as ds:
        if failure=='grid':ds.grid_label='g169'
        elif failure=='brand':ds.branded_variable='sfy_tavg-u-hxy-sea'
        elif failure=='standard_name':ds['sfx'].standard_name='ocean_salt_y_transport'
        elif failure=='dimensions':ds.renameDimension('y','other_y')
        elif failure=='reference_tokens':ds['sfx'].coordinates='nav_lat nav_lon unknown'
        elif failure=='wrong_existing':ds['nav_lat'][0,0]+=.01
        elif failure=='packing':ds['nav_lat'].scale_factor=2.
        else:ds.createVariable('latitude','f8',('y','x'))[:]=reference()['nav_lat'][0]
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path),tmp_path/'log')
    assert path.read_bytes()==original


def test_composes_with_existing_canonical_renames_in_one_plan(tmp_path):
    path=tmp_path/'sfx.nc';before=make(path)
    q=FixPlan('test','qa',{str(path):[
        Step('C071','rename_horizontal_coordinate',{'role':'latitude'}),
        Step('C072','rename_horizontal_coordinate',{'role':'longitude'}),
        Step('C119','inject_nemo_u_coordinates',{}),
    ]})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['sfx'].coordinates=='latitude longitude'
        assert ds['sfx'][:].tobytes()==before['sfx'][0]
        assert not {'nav_lat','nav_lon'} & ds.variables.keys()
        np.testing.assert_array_equal(ds['latitude'][:],reference()['nav_lat'][0])
    assert apply_plan(q,tmp_path/'again')['changed']==0


def test_corrupt_reference_fails_before_writing(tmp_path,monkeypatch):
    path=tmp_path/'sfx.nc';make(path);before=path.read_bytes()
    import esgf_fix_tool.fixes.nemo_coordinates as module
    real_load=module.json.loads
    def corrupt(text):
        doc=real_load(text);doc['coordinates']['nav_lat']['values'][0][0]+=.01;return doc
    module.reference.cache_clear()
    monkeypatch.setattr(module.json,'loads',corrupt)
    try:
        with pytest.raises(ValueError,match='Invalid bundled'):apply_plan(plan(path),tmp_path/'log')
        assert path.read_bytes()==before
    finally:module.reference.cache_clear()


def test_bundled_arrays_are_u_points_and_exactly_checksummed():
    ref=reference()
    assert ref['nav_lat'][0].shape==(331,360)
    assert ref['nav_lat'][0][0,0]==-84.21070904403568
    assert ref['nav_lon'][0][0,0]==73.99999999999955
    assert ref['nav_lat'][0][100,100]==-57.6957979726119
    assert ref['nav_lon'][0][165,180]==-106.
    doc=json.loads((Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/nemo_u_coordinates.json').read_text())
    assert set(doc['coordinates'])=={'nav_lat','nav_lon'}
    for n,(a,_) in ref.items():assert hashlib.sha256(a.astype('<f8').tobytes()).hexdigest()==doc['coordinates'][n]['sha256']


def test_six_missing_coordinate_findings_schedule_one_sfx_step_only():
    checks=[('[CF-Conventions] §5 Coordinate Systems',
             "The 'coordinates' attribute of variable 'sfx' references non-existent variable 'nav_lat'."),
            ('[CF-Conventions] §5 Coordinate Systems',
             "The 'coordinates' attribute of variable 'sfx' references non-existent variable 'nav_lon'."),
            ('[CF-Conventions] §5 Coordinate Systems',
             "sfx's auxiliary coordinate specified by the coordinates attribute, nav_lat, is not a variable in this dataset"),
            ('[CF-Conventions] §5 Coordinate Systems',
             "sfx's auxiliary coordinate specified by the coordinates attribute, nav_lon, is not a variable in this dataset"),
            ("[CMIP7] [ATTR004] Variable 'sfx' attribute 'coordinates' as-variable check",
             "Referenced variables not found: ['nav_lat', 'nav_lon']"),
            ('[CMIP7] [COORD011] Horizontal grid coordinates',
             "The required horizontal coordinate(s) are absent: ['longitude', 'latitude']."),]
    dataset='CMIP7.OCE.sfx.tavg-u-hxy-sea.mon.glb.g168.A_MODEL.an_experiment.r1i1p1f1'
    findings=[Finding(3,c,m,dataset,'/tmp/sfx.nc') for c,m in checks]
    rules=RuleSet.load(*default_rule_paths())
    q=FixPlan.from_classification(classify(QAReport(None,{},findings),rules))
    assert q.files['/tmp/sfx.nc']==[Step('C119','inject_nemo_u_coordinates',{})]
    for other in ['sfy','sob','tob']:
        f=Finding(3,checks[0][0],checks[0][1].replace('sfx',other),dataset.replace('.sfx.','.'+other+'.'),'/tmp/a.nc')
        m=rules.match(f)
        assert m is None or m.rule.id!='C119'
