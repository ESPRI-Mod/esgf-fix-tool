import shutil
from pathlib import Path
import os
import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding,QAReport
from esgf_fix_tool.rules import RuleSet
from esgf_fix_tool.classify import classify,Outcome

ROOT=Path(__file__).resolve().parents[1]
VARS=['hfbasin','hfbasinpmadv','hfbasinpmdiff','htovgyre','htovovrt','sltbasin','sltovgyre','sltovovrt']
MESSAGES=["It is required for 'lat' on the configured rectilinear grid to be a 1-D coordinate variable; found 'nav_lat' with dimensions ['y', 'x']. Dependent horizontal-grid checks could not be evaluated.",
          "The required coordinate for the configured rectilinear grid is 'lat(lat)' for latitude; standard_name='latitude' identified 'nav_lat'."]


def make(path,fmt='NETCDF4',var='hfbasin',corrected=False):
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.variable_id=var;ds.branded_variable=var+'_tavg-u-hyb-sea';ds.horizontal_label='hyb';ds.grid_label='g112'
        ds.history='original global history'
        if fmt=='NETCDF4':ds.setncattr_string('comment','preserve NC_STRING')
        for d,n in [('time',None),('basin',3),('y',332),('x',1),('axis_nbounds',2)]:ds.createDimension(d,n)
        lat=np.linspace(-84.21752166748047,89.36997985839844,331).tolist()+[89.36999 if corrected else 89.36997985839844]
        c=ds.createVariable('nav_lat','f8',('y','x'));c[:]=np.asarray(lat).reshape(332,1)
        c.standard_name='latitude';c.units='degrees_north';c.setncattr('name','nav_lat')
        c=ds.createVariable('nav_lon','f8',('y','x'));c[:]=0.;c.standard_name='longitude';c.units='degrees_east'
        ds.createVariable('basin','f8',('basin',))[:]=[1.,2.,3.]
        v=ds.createVariable(var,'i2',('time','basin','y','x'),zlib=fmt.startswith('NETCDF4'),fill_value=-32768)
        v.scale_factor=.25;v.add_offset=10.;v.coordinates='nav_lat nav_lon';v.set_auto_maskandscale(False)
        v[:]=np.arange(2*3*332,dtype='i2').reshape(2,3,332,1)
        ds.createVariable('time','f8',('time',))[:]=[1.,2.]
        ds.createVariable('time_bounds','f8',('time','axis_nbounds'))[:]=[[0.,1.],[1.,2.]]


def step(var):return Step('C070','fix_basin_profile_latitude',{'var':var})
def plan(path,steps):return FixPlan('test','report',{str(path):steps})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('order',['longitude_first','latitude_first','same_plan'])
def test_profile_conversion_preserves_every_data_row_and_composes_with_reference_removal(tmp_path,fmt,order):
    source,target=tmp_path/'source.nc',tmp_path/'copy.nc';make(source,fmt)
    original=source.read_bytes();shutil.copyfile(source,target)
    longitude=Step('C069','drop_unrequested_horizontal_reference',dict(var='hfbasin',role='longitude',coordinate='nav_lon',branded_variable='hfbasin_tavg-u-hyb-sea'))
    latitude=step('hfbasin');steps=[latitude,longitude] if order=='latitude_first' else [longitude,latitude]
    plans=[plan(target,steps)] if order=='same_plan' else [plan(target,[s]) for s in steps]
    for p in plans:
        apply_plan(p,tmp_path/'log',dry_run=True)
        apply_plan(p,tmp_path/'log')
    assert source.read_bytes()==original
    with netCDF4.Dataset(source) as src,netCDF4.Dataset(target) as dst:
        for ds in [src,dst]:ds.set_auto_maskandscale(False)
        assert set(dst.variables)==(set(src.variables)-{'nav_lat'})|{'lat'}
        assert 'x' not in dst.dimensions and 'y' not in dst.dimensions and len(dst.dimensions['lat'])==332
        assert dst['hfbasin'].dimensions==('time','basin','lat')
        assert dst['hfbasin'][:].tobytes()==src['hfbasin'][:].squeeze(-1).tobytes()
        assert dst['hfbasin'][:,:,-1].tobytes()==src['hfbasin'][:,:,-1,0].tobytes()
        assert dst['lat'][:-1].tobytes()==src['nav_lat'][:-1,0].tobytes()
        assert dst['lat'][-1]==89.36999 and (np.diff(dst['lat'][:])>0).all()
        assert dst['nav_lon'][:].tobytes()==src['nav_lon'][:,0].tobytes()
        assert dst['lat'].axis=='Y' and dst['lat'].getncattr('name')=='lat'
        assert dst['hfbasin'].coordinates=='lat'
        assert 'bounds' not in dst['lat'].ncattrs()
        assert dst['hfbasin'].filters()==src['hfbasin'].filters()
        assert all(dst.getncattr(a)==src.getncattr(a) for a in src.ncattrs())
    corrected=target.read_bytes()
    for p in plans:assert apply_plan(p,tmp_path/'log')['changed']==0
    assert target.read_bytes()==corrected


@pytest.mark.parametrize('var',VARS)
@pytest.mark.parametrize('corrected',[False,True])
def test_all_eight_profiles_accept_only_the_reviewed_endpoint(tmp_path,var,corrected):
    p=tmp_path/'test.nc';make(p,var=var,corrected=corrected)
    assert apply_plan(plan(p,[step(var)]),tmp_path/'log')['changed']==1
    assert apply_plan(plan(p,[step(var)]),tmp_path/'log')['changed']==0


@pytest.mark.parametrize('invalid',['earlier_duplicate','unknown_last','unknown_penultimate','wrong_brand','collision','extra_spatial','unexpected_bounds','packed_latitude'])
def test_unreviewed_profile_changes_are_refused_atomically(tmp_path,invalid):
    p=tmp_path/'test.nc';make(p)
    with netCDF4.Dataset(p,'a') as ds:
        if invalid=='earlier_duplicate':ds['nav_lat'][30,0]=ds['nav_lat'][29,0]
        elif invalid=='unknown_last':ds['nav_lat'][-1,0]=89.5
        elif invalid=='unknown_penultimate':ds['nav_lat'][-2:,0]=89.3
        elif invalid=='wrong_brand':ds.branded_variable='msfty_tavg-ol-ht-sea'
        elif invalid=='collision':ds.createDimension('lat',332)
        elif invalid=='extra_spatial':ds.createVariable('extra','f8',('y','x'))[:]=0.
        elif invalid=='unexpected_bounds':ds['nav_lat'].bounds='unknown'
        else:ds['nav_lat'].scale_factor=.01
    before=p.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(p,[step('hfbasin')]),tmp_path/'log')
    assert p.read_bytes()==before


@pytest.mark.parametrize('corrupt',['latitude','main_data'])
def test_later_hook_cannot_change_other_latitudes_or_scientific_data(tmp_path,monkeypatch,corrupt):
    from esgf_fix_tool.fixes import REGISTRY,FixFunction
    p=tmp_path/'test.nc';make(p)
    def damage(source,output):
        with netCDF4.Dataset(output,'a') as ds:
            v=ds['lat' if corrupt=='latitude' else 'hfbasin'];v.set_auto_maskandscale(False)
            index=(0,)*v.ndim;v[index]=v[index]+1
    monkeypatch.setitem(REGISTRY,'damage_profile',FixFunction('damage_profile','metadata',lambda ds:True,after_close=damage))
    before=p.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(p,[step('hfbasin'),Step('test','damage_profile',{})]),tmp_path/'log')
    assert p.read_bytes()==before


def test_two_individual_diagnostics_deduplicate_to_one_profile_recipe():
    findings=[Finding(3,'[CMIP7] [COORD011] Horizontal grid coordinates',m,
                      'CMIP7.OCE.hfbasin.tavg-u-hyb-sea.mon.glb.g112.IPSL-CM6-ESMCO2.esm-piControl-spinup.r1i1p1f1','file.nc') for m in MESSAGES]
    rules=RuleSet.load(ROOT/'rules/horizontal_coordinates.yaml',ROOT/'rules/seed_20261029.yaml')
    c=classify(QAReport(Path('report'),{},findings),rules)
    assert all(i.outcome==Outcome.FIX and i.match.rule.id=='C070' for i in c.items)
    assert FixPlan.from_classification(c).files['file.nc']==[step('hfbasin')]
