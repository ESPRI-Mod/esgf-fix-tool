import shutil
from pathlib import Path
import os

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify, Outcome
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.rules import RuleSet

ROOT=Path(__file__).resolve().parents[1]


def make(path, fmt='NETCDF4', var='thetao', brand='thetao_tavg-ol-hxy-sea', horizontal='hxy'):
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.variable_id,ds.branded_variable,ds.horizontal_label,ds.grid_label=var,brand,horizontal,'g112'
        ds.history='global history preserved'
        for name,size in [('time',None),('y',2),('x',3),('nvertex',4),('axis_nbounds',2)]:ds.createDimension(name,size)
        for name,role,units,values in [
            ('nav_lat','latitude','degrees_north',[[-40.,-39.,-38.],[60.,61.,62.]]),
            ('nav_lon','longitude','degrees_east',[[5.,15.,25.],[4.,14.,24.]])]:
            c=ds.createVariable(name,'f8',('y','x'));c[:]=values
            c.standard_name,c.units,c.bounds=role,units,'bounds_'+name
            c.setncattr('name',name)
            ds.createVariable('bounds_'+name,'f8',('y','x','nvertex'))[:]=np.arange(24,dtype='f8').reshape(2,3,4)
        v=ds.createVariable(var,'i2',('time','y','x'),zlib=fmt.startswith('NETCDF4'),fill_value=-32768)
        v.scale_factor,v.add_offset=.25,4.
        v.coordinates='nav_lat nav_lon'
        v.set_auto_maskandscale(False);v[:]=np.arange(12,dtype='i2').reshape(2,2,3)
        ds.createVariable('time_bounds','f8',('time','axis_nbounds'))[:]=[[0.,1.],[1.,2.]]


def plan(path,*steps):
    return FixPlan('test','synthetic-report',{str(path):[Step('test',fix,params) for fix,params in steps]})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('sequence',['names_first','bounds_first','same_plan'])
def test_curvilinear_names_compose_with_previous_bounds_plans_and_keep_every_array(tmp_path,fmt,sequence):
    source,target=tmp_path/'source.nc',tmp_path/'copy.nc';make(source,fmt);original=source.read_bytes();shutil.copyfile(source,target)
    names=[('rename_horizontal_coordinate',dict(role=role)) for role in ['latitude','longitude']]
    bounds=[('rename_bounds_variable',dict(coordinate=n,source='bounds_'+n,target='vertices_'+role))
            for n,role in [('nav_lat','latitude'),('nav_lon','longitude')]]
    bounds.append(('normalize_bounds_dimension',{}))
    steps=names+bounds if sequence!='bounds_first' else bounds+names
    plans=[plan(target,*steps)] if sequence=='same_plan' else [plan(target,*[s]) for s in steps]
    for p in plans:
        assert apply_plan(p,tmp_path/'log',dry_run=True)['changed']==1
        assert apply_plan(p,tmp_path/'log')['changed']==1
    assert source.read_bytes()==original
    mapping={'nav_lat':'latitude','nav_lon':'longitude','bounds_nav_lat':'vertices_latitude','bounds_nav_lon':'vertices_longitude'}
    with netCDF4.Dataset(source) as a,netCDF4.Dataset(target) as b:
        a.set_auto_maskandscale(False);b.set_auto_maskandscale(False)
        for n,v in a.variables.items():
            out=b[mapping.get(n,n)]
            assert v[:].tobytes()==out[:].tobytes()
            assert v.filters()==out.filters()
        assert b['thetao'].dimensions==('time','y','x')
        assert b['latitude'].dimensions==('y','x') and b['longitude'].dimensions==('y','x')
        assert b['thetao'].coordinates=='latitude longitude'
        assert b['latitude'].bounds=='vertices_latitude'
        assert b['latitude'].getncattr('name')=='latitude'
        assert len(b.dimensions['nvertex'])==4 and len(b.dimensions['bnds'])==2
        assert b.history==a.history
    corrected=target.read_bytes()
    for p in plans:assert apply_plan(p,tmp_path/'log')['changed']==0
    assert target.read_bytes()==corrected


@pytest.mark.parametrize('canonical',[False,True])
@pytest.mark.parametrize('var,brand,roles,left',[
    ('bigthetao','bigthetao_tavg-u-hm-sea',['latitude','longitude'],[]),
    ('hfbasinpmdiff','hfbasinpmdiff_tavg-u-hyb-sea',['longitude'],['nav_lat']),
    ('msfty','msfty_tavg-ol-ht-sea',['latitude','longitude'],[]),
])
def test_only_unrequested_tokens_are_removed_in_both_name_forms(tmp_path,canonical,var,brand,roles,left):
    p=tmp_path/'test.nc';make(p,var=var,brand=brand,horizontal='hm')
    with netCDF4.Dataset(p,'a') as ds:
        ds[var].coordinates='time nav_lat nav_lon nav_lon_extra type'
        if canonical:
            ds.renameVariable('nav_lat','latitude');ds.renameVariable('nav_lon','longitude')
            ds[var].coordinates='time latitude longitude nav_lon_extra type'
    before={}
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False);before={n:v[:].tobytes() for n,v in ds.variables.items()}
    steps=[('drop_unrequested_horizontal_reference',dict(var=var,role=role,coordinate='nav_lat' if role=='latitude' else 'nav_lon',branded_variable=brand)) for role in roles]
    assert apply_plan(plan(p,*steps),tmp_path/'log')['changed']==1
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        assert ds[var].coordinates==' '.join(['time']+[('latitude' if canonical else 'nav_lat') for _ in left]+['nav_lon_extra','type'])
        for n,raw in before.items():assert ds[n][:].tobytes()==raw
    assert apply_plan(plan(p,*steps),tmp_path/'log')['changed']==0


@pytest.mark.parametrize('invalid',['required_latitude','brand_mismatch','unknown_brand','role_mismatch','collision','rectilinear_profile','bad_standard_name'])
def test_unreviewed_horizontal_changes_do_not_replace_target(tmp_path,invalid):
    path=tmp_path/'test.nc';brand='hfbasinpmdiff_tavg-u-hyb-sea'
    make(path,var='hfbasinpmdiff',brand=brand)
    params=dict(var='hfbasinpmdiff',role='longitude',coordinate='nav_lon',branded_variable=brand)
    method='drop_unrequested_horizontal_reference'
    if invalid=='required_latitude':params.update(role='latitude',coordinate='nav_lat')
    elif invalid=='brand_mismatch':params['branded_variable']='hfbasin_tavg-u-hyb-sea'
    elif invalid=='unknown_brand':params['branded_variable']='unknown'
    elif invalid=='role_mismatch':params['coordinate']='nav_lat'
    else:
        method='rename_horizontal_coordinate';params=dict(role='latitude')
        with netCDF4.Dataset(path,'a') as ds:
            if invalid=='collision':ds.createVariable('latitude','f8',('y','x'))[:]=1.
            elif invalid=='rectilinear_profile':ds.horizontal_label='hyb'
            else:ds['nav_lat'].standard_name='depth'
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,(method,params)),tmp_path/'log')
    assert path.read_bytes()==original and not list(tmp_path.glob('*.fixtmp'))


@pytest.mark.parametrize('role',['latitude','longitude'])
def test_next_qa_with_canonical_names_still_matches_bounds_and_association_rules(tmp_path,role):
    target=role;source='bounds_nav_lat' if role=='latitude' else 'bounds_nav_lon'
    bounds=Finding(3,'[CMIP7] [COORD009] Coordinate bounds naming',
        f"'{target}' bounds='{source}'; the required name is 'vertices_{role}'.",'dataset','file.nc')
    association=Finding(3,'[CMIP7] [COORD010] Auxiliary-coordinate associations',
        f"It is required for 'bigthetao' coordinates attribute not to include horizontal {role} coordinate '{target}', because {role} is not required by branded variable 'bigthetao_tavg-u-hm-sea'.",'dataset','file.nc')
    rules=RuleSet.load(ROOT/'rules/coordinate_values_bounds.yaml',ROOT/'rules/horizontal_coordinates.yaml')
    c=classify(QAReport(Path('synthetic'),{},[bounds,association]),rules)
    assert [i.outcome for i in c.items]==[Outcome.FIX,Outcome.FIX]
    assert [i.match.rule.id for i in c.items]==['C067','C069']


@pytest.mark.parametrize('new_first', [False, True])
def test_vertex_and_long_name_fixes_compose_with_existing_horizontal_recipes(tmp_path, new_first):
    path = tmp_path / 'ocean.nc'
    make(path)
    with netCDF4.Dataset(path, 'a') as ds:
        ds['nav_lat'].long_name = 'Latitude'
        ds['nav_lon'].long_name = 'Longitude'
        ds.set_auto_maskandscale(False)
        expected = {name: v[:].tobytes() for name, v in ds.variables.items()}

    new = [('rename_ocean_vertices_dimension', {})]
    old = [('normalize_bounds_dimension', {})]
    aliases = {}
    for original, canonical in [('nav_lat', 'latitude'), ('nav_lon', 'longitude')]:
        new.append(('set_reviewed_coordinate_long_name', dict(coordinate=original, value=canonical)))
        old.extend([
            ('rename_horizontal_coordinate', dict(role=canonical)),
            ('rename_bounds_variable', dict(coordinate=original, source='bounds_' + original,
                                            target='vertices_' + canonical)),
        ])
        aliases.update({original: canonical, 'bounds_' + original: 'vertices_' + canonical})
    p = plan(path, *(new + old if new_first else old + new))
    inode = path.stat().st_ino
    assert apply_plan(p, tmp_path / 'log')['changed'] == 1
    assert path.stat().st_ino == inode
    assert apply_plan(p, tmp_path / 'log')['changed'] == 0
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        for name, data in expected.items():
            assert ds[aliases.get(name, name)][:].tobytes() == data
        assert ds['latitude'].long_name == 'latitude'
        assert ds['longitude'].long_name == 'longitude'
        assert ds['vertices_latitude'].dimensions == ('y', 'x', 'vertices')
        assert ds['vertices_longitude'].dimensions == ('y', 'x', 'vertices')
        assert ds['time_bounds'].dimensions == ('time', 'bnds')
