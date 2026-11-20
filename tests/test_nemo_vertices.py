"""Existing XIOS vertices are copied without changing centers or science."""

import gzip
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
from esgf_fix_tool.fixes.nemo_coordinates import PRODUCTS, reference as center_reference
from esgf_fix_tool.fixes.nemo_vertices import NAMES, reference
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.preview import Variable
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


MESSAGE = "It is required for horizontal auxiliary coordinate '{name}' to have a bounds attribute naming its vertex variable; dependent vertex checks could not be evaluated."


def make(path,var,fmt='NETCDF4',canonical=True,present=False):
    grid,label,standard=PRODUCTS[var]
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.setncatts(dict(variable_id=var,branded_variable=var+'_tavg-u-hxy-sea',grid_label=label,
                         horizontal_label='hxy',source_id='AnyModel',experiment_id='AnyExperiment',history='Preserve global history'))
        ds.createDimension('time',None);ds.createDimension('y',331);ds.createDimension('x',360)
        ds.createVariable('time','f8',('time',))[:]=[15.5,45.]
        options=dict(zlib=True,complevel=2,chunksizes=(1,31,60)) if fmt.startswith('NETCDF4') else {}
        main=ds.createVariable(var,'f4',('time','y','x'),fill_value=1e20,**options)
        values=np.arange(2*331*360,dtype='f4').reshape(2,331,360)
        values[:,::11,::7]=1e20
        if var=='sfy':values[:,330,196:210]=1e20
        main[:]=values;main.standard_name=standard;main.comment='Preserve scientific comment'
        main.long_name='Preserve scientific title';main.cell_methods='time: mean'
        main.coordinates='latitude longitude' if canonical else 'nav_lat nav_lon'
        for key,(data,attrs) in center_reference(grid).items():
            name={'nav_lat':'latitude','nav_lon':'longitude'}[key] if canonical else key
            v=ds.createVariable(name,'f8',('y','x'));v[:]=data;v.setncatts(attrs)
        if present:
            ds.createDimension('vertices',4)
            for name,data in reference()[grid][1].items():
                ds.createVariable(name,'f8',('y','x','vertices'),fill_value=False)[:]=data
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return {name:(v[:].copy(),v.filters(),v.chunking(),v.dtype,
                      {a:v.getncattr(a) for a in v.ncattrs()}) for name,v in ds.variables.items()}


def plan(path,var,canonical=True):
    grid,label,_=PRODUCTS[var]
    dataset=f'CMIP7.OCE.{var}.tavg-u-hxy-sea.mon.glb.{label}.AnotherModel.an_experiment.r1i1p1f1'
    names=('latitude','longitude') if canonical else ('nav_lat','nav_lon')
    findings=[Finding(3,'[CMIP7] [COORD011] Horizontal grid coordinates',MESSAGE.format(name=name),dataset,str(path)) for name in names]
    q=FixPlan.from_classification(classify(QAReport(None,{},findings),RuleSet.load(*default_rule_paths())))
    assert q.files[str(path)]==[Step('C119v','inject_nemo_vertices',{})]
    return q


@pytest.mark.parametrize('var',PRODUCTS)
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('canonical',[False,True])
def test_vertices_are_appended_directly_and_every_original_array_survives(tmp_path,monkeypatch,var,fmt,canonical):
    path=tmp_path/'ocean.nc';original=make(path,var,fmt,canonical)
    before=path.read_bytes();inode=path.stat().st_ino;q=plan(path,var,canonical)
    def forbid(*args):raise AssertionError('Appending vertices must not rewrite the scientific field')
    monkeypatch.setattr(Batch,'write',forbid)
    getitem=Variable.__getitem__
    def guarded(v,key):
        assert v.name!=var,'Only the validated 14-cell sfy mask may be read from the source'
        return getitem(v,key)
    monkeypatch.setattr(Variable,'__getitem__',guarded)
    apply_plan(q,tmp_path/'dry',dry_run=True);assert path.read_bytes()==before
    assert apply_plan(q,tmp_path/'log')['changed']==1 and path.stat().st_ino==inode
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==0
    grid=PRODUCTS[var][0]
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert len(ds.dimensions['vertices'])==4
        for name,(data,filters,chunks,dtype,attrs) in original.items():
            assert ds[name][:].tobytes()==data.tobytes(),name
            assert ds[name].dtype==dtype and ds[name].filters()==filters and ds[name].chunking()==chunks
            for a,value in attrs.items():np.testing.assert_array_equal(ds[name].getncattr(a),value)
        for name,data in reference()[grid][1].items():
            assert ds[name].dimensions==('y','x','vertices') and ds[name].dtype==np.dtype('f8')
            assert ds[name][:].tobytes()==data.tobytes()
            assert not ds[name].ncattrs()
        lat,lon=('latitude','longitude') if canonical else ('nav_lat','nav_lon')
        assert ds[lat].bounds=='vertices_latitude' and ds[lon].bounds=='vertices_longitude'
        assert ds.history=='Preserve global history'
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected


@pytest.mark.parametrize('failure',['valid_second_time','unreviewed_exception_value','other_row','different_grid','wrong_vertices'])
def test_exception_is_limited_to_the_reviewed_masked_cells(tmp_path,failure):
    path=tmp_path/'ocean.nc';make(path,'sfy',present=failure=='wrong_vertices')
    with netCDF4.Dataset(path,'a') as ds:
        if failure=='valid_second_time':ds['sfy'][1,330,200]=0.
        elif failure=='unreviewed_exception_value':ds['latitude'][330,200]+=.01
        elif failure=='other_row':ds['latitude'][329,200]+=.01
        elif failure=='different_grid':ds.grid_label='g168'
        else:ds['vertices_latitude'][100,100,0]+=.01
    before=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,'sfy'),tmp_path/'log')
    assert path.read_bytes()==before


def test_existing_exact_vertices_need_only_bounds_attributes(tmp_path,monkeypatch):
    path=tmp_path/'ocean.nc';make(path,'tob',present=True)
    def forbid(*args):raise AssertionError('Existing vertices must be reused in place')
    monkeypatch.setattr(Batch,'write',forbid)
    apply_plan(plan(path,'tob'),tmp_path/'log')
    with netCDF4.Dataset(path) as ds:assert ds['latitude'].bounds=='vertices_latitude'
    assert apply_plan(plan(path,'tob'),tmp_path/'again')['changed']==0


def test_composes_with_coordinate_injection_and_canonical_renames(tmp_path):
    path=tmp_path/'ocean.nc';make(path,'sfx',canonical=False)
    q=plan(path,'sfx',False)
    q.files[str(path)][:0]=[Step('C071','rename_horizontal_coordinate',{'role':'latitude'}),
                           Step('C072','rename_horizontal_coordinate',{'role':'longitude'}),
                           Step('C119','inject_nemo_u_coordinates',{})]
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['sfx'].coordinates=='latitude longitude'
        assert ds['latitude'].bounds=='vertices_latitude' and ds['longitude'].bounds=='vertices_longitude'
    assert apply_plan(q,tmp_path/'again')['changed']==0


def test_corrupt_reference_is_refused_before_any_target_write(tmp_path,monkeypatch):
    import esgf_fix_tool.fixes.nemo_vertices as module
    path=tmp_path/'ocean.nc';make(path,'sob');before=path.read_bytes()
    real_load=module.json.load
    def corrupt(stream):
        data=real_load(stream);data['profiles']['T']['vertices']['vertices_latitude']['values'][0][0][0]+=.1
        return data
    reference.cache_clear();monkeypatch.setattr(module.json,'load',corrupt)
    try:
        with pytest.raises(ValueError,match='Invalid bundled'):apply_plan(plan(path,'sob'),tmp_path/'log')
        assert path.read_bytes()==before
    finally:reference.cache_clear()


def test_bundled_reference_has_exact_arrays_and_all_reviewed_exception_cells():
    path=Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/nemo_vertices_reference.json.gz'
    with gzip.open(path,'rt') as stream:document=json.load(stream)
    for grid,(_,vertices,valid) in reference().items():
        assert int(valid.sum())==(90074 if grid=='U' else 90075)
        for name,values in vertices.items():
            assert hashlib.sha256(values.tobytes()).hexdigest()==document['profiles'][grid]['vertices'][name]['sha256']
    assert document['profiles']['V']['masked_source_exception_cells']==[[330,x] for x in range(196,210)]


def test_extraction_preserves_vertices_and_checks_all_donor_records(tmp_path):
    from esgf_fix_tool.fixes.nemo_vertices import DONORS, extract_reference

    sources={}
    for grid,(main,label) in DONORS.items():
        source=tmp_path/(main+'.nc');sources[grid]=source
        centers,vertices,_=reference()[grid]
        with netCDF4.Dataset(source,'w') as ds:
            ds.variable_id=main;ds.grid_label=label;ds.horizontal_label='hxy'
            ds.branded_variable=main+('_ti-u-hxy-u' if grid=='T' else '_tavg-u-hxy-sea')
            for name,size in [('time',2),('y',331),('x',360),('vertices',4)]:ds.createDimension(name,size)
            for key,name in [('nav_lat','latitude'),('nav_lon','longitude')]:
                ds.createVariable(name,'f8',('y','x'))[:]=centers[key]
            for name,data in vertices.items():ds.createVariable(name,'f8',('y','x','vertices'),fill_value=False)[:]=data
            dims=('y','x') if grid=='T' else ('time','y','x')
            ds.createVariable(main,'f4',dims,fill_value=1e20)
    output=tmp_path/'vertices.json.gz'
    extract_reference(sources,output)
    saved=output.read_bytes()
    with gzip.open(output,'rt') as stream:doc=json.load(stream)
    for grid,(_,vertices,_) in reference().items():
        for name,data in vertices.items():
            spec=doc['profiles'][grid]['vertices'][name]
            assert np.asarray(spec['values'],dtype='<f8').tobytes()==data.tobytes()
    with pytest.raises(FileExistsError):extract_reference(sources,output)
    assert output.read_bytes()==saved
    # The first record remains masked; one valid value in the second record
    # invalidates the exception and prevents a new reference from being written.
    with netCDF4.Dataset(sources['V'],'a') as ds:ds['hfy'][1,330,200]=0.
    rejected=tmp_path/'rejected.json.gz'
    with pytest.raises(ValueError,match='time index 1'):extract_reference(sources,rejected)
    assert not rejected.exists()
