"""CM7 real/double conversions and category-index variants preserve values."""
import json
import netCDF4
import numpy as np
import pytest
from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.report import QAReport, Finding
from esgf_fix_tool.ruleset import RuleSet


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
def test_all_coordinate_promotions_share_one_rewrite(tmp_path, fmt):
    p = tmp_path/'data.nc'
    with netCDF4.Dataset(p, 'w', format=fmt) as ds:
        ds.variable_id = 'science'
        for n,size in [('depth', 3), ('lat', 2), ('lon', 4), ('bnds', 2)]:ds.createDimension(n,size)
        for n,dims in [('depth',('depth',)), ('lat',('lat',)), ('lon',('lon',)), ('lat_bnds',('lat','bnds')), ('lon_bnds',('lon','bnds'))]:
            v=ds.createVariable(n,'f4',dims,fill_value=np.float32(1e20));v[:]=np.arange(v.size).reshape(v.shape)/7
        v=ds.createVariable('science','f4',('depth','lat','lon'),zlib=fmt.startswith('NETCDF4'),complevel=2);v[:]=np.arange(v.size).reshape(v.shape)
    with netCDF4.Dataset(p) as ds:
        raw={n:np.asarray(v[:]).astype('f8') for n,v in ds.variables.items() if n!='science'}
        science=ds['science'][:].tobytes();filters=ds['science'].filters()
    q=FixPlan('t','qa',{str(p):[Step('C117','promote_coordinate_to_double',dict(var=n)) for n in raw]})
    apply_plan(q,tmp_path/'log')
    row=json.loads((tmp_path/'log').read_text());assert row['rewrite_count']==1
    with netCDF4.Dataset(p) as ds:
        for n,a in raw.items():
            assert ds[n].dtype==np.dtype('f8');np.testing.assert_array_equal(ds[n][:],a)
        assert ds['science'][:].tobytes()==science and ds['science'].filters()==filters
    assert apply_plan(q,tmp_path/'again')['changed']==0


@pytest.mark.parametrize('source,target,var,size', [('vegtype','type','landCoverFrac',15),('section_ice','line','siareaacrossline',4)])
def test_category_index_rename_preserves_text_and_numeric_arrays(tmp_path,source,target,var,size):
    p=tmp_path/'category.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.variable_id=var
        ds.createDimension('time',2);ds.createDimension(source,size);ds.createDimension('strlen',8)
        ds.createVariable(var,'f4',('time',source))[:]=np.arange(2*size).reshape(2,size)
        ds.createVariable('sector','S1',(source,'strlen'))[:]=np.full((size,8),b'a')
    q=FixPlan('t','qa',{str(p):[Step('rename','rename_category_dimension',dict(source=source,target=target))]})
    inode=p.stat().st_ino
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert ds[var].dimensions==('time',target) and ds['sector'].dimensions==(target,'strlen')
        np.testing.assert_array_equal(ds[var][:],np.arange(2*size).reshape(2,size))
    assert p.stat().st_ino==inode and apply_plan(q,tmp_path/'again')['changed']==0


def test_cm7_histogram_cell_variant_selects_existing_layout_recipe():
    f=Finding(3,'[CMIP7] [COORD002] Data-variable coordinate order',"'clisccp' dimensions in netCDF/C order are ['time', 'plev', 'tau', 'cell']; coordinate IDs ['longitude', 'latitude', 'plev7c', 'tau', 'time'] require ['time', 'tau', 'plev', 'cell'] as their required order.",'CMIP7.ATM.clisccp.tavg-p7c-hxy-air.mon.glb.g243.IPSL-CM7A.piControl-spinup.r1i1p1f1','/tmp/clisccp.nc')
    q=FixPlan.from_classification(classify(QAReport(None,{},[f]),RuleSet.load(*default_rule_paths())))
    assert q.files[f.path][0].fix=='fix_data_layout' and q.files[f.path][0].params['target_order']==['time','tau','plev','cell']


@pytest.mark.parametrize('var,axis', [('clisccp','plev'),('jpdftaureicemodis','effectRadIc'),('jpdftaureliqmodis','effectRadLi')])
def test_cell_histogram_transpose_preserves_every_value(tmp_path,var,axis):
    p=tmp_path/'histogram.nc'
    values=np.arange(2*3*4*5,dtype='f4').reshape(2,3,4,5)
    with netCDF4.Dataset(p,'w') as ds:
        ds.variable_id=var
        for n,size in [('time',2),(axis,3),('tau',4),('cell',5)]:ds.createDimension(n,size)
        ds.createVariable(var,'f4',('time',axis,'tau','cell'),zlib=True,complevel=2)[:]=values
    q=FixPlan('t','qa',{str(p):[Step('layout','fix_data_layout',dict(var=var,target_order=['time','tau',axis,'cell'],backend='python'))]})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds[var][:],values.transpose(0,2,1,3))
        assert ds[var].filters()['complevel']==2
    assert apply_plan(q,tmp_path/'again')['changed']==0


@pytest.mark.parametrize('var',['edt','evu'])
def test_approved_hybrid_centering_streams_cell_fields(tmp_path,var):
    from esgf_fix_tool.fixes.hybrid import _coefficient_reference
    p=tmp_path/'cell-hybrid.nc';ref=_coefficient_reference()[80]
    values=np.ma.array(np.arange(2*80*5,dtype='f4').reshape(2,80,5),mask=False)
    values.mask[0,20,2]=True
    with netCDF4.Dataset(p,'w') as ds:
        ds.variable_id=var;ds.realm='atmos';ds.vertical_label='al';ds.branded_variable=var+'_tavg-al-hxy-u'
        for n,size in [('time',2),('lev',80),('cell',5),('bnds',2)]:ds.createDimension(n,size)
        ds.createVariable('lev','f8',('lev',))[:]=ref['ap']/101325+ref['b']
        for n in ['ap','b']:ds.createVariable(n,'f8',('lev',))[:]=ref[n]
        ds['ap'].units='Pa'
        ds.createVariable('ps','f4',('time','cell'))[:]=100000.;ds['ps'].units='Pa'
        ds.createVariable(var,'f4',('time','lev','cell'),fill_value=1e20,zlib=True,complevel=2)[:]=values
    q=FixPlan('t','qa',{str(p):[Step('C104','center_hybrid_interfaces',{})]})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        expected=(values[:,:-1,:]+values[:,1:,:])/2
        np.testing.assert_array_equal(np.ma.getmaskarray(ds[var][:]),np.ma.getmaskarray(expected))
        np.testing.assert_array_equal(ds[var][:].compressed(),expected.compressed())
        assert ds[var].shape==(2,79,5) and ds[var].filters()['complevel']==2
        assert ds['lev_bnds'].shape==(79,2)
    assert apply_plan(q,tmp_path/'again')['changed']==0


@pytest.mark.parametrize('names_first',[False,True])
def test_rectilinear_g245_keeps_two_endpoint_bounds_with_geographic_aliases(tmp_path,names_first):
    p=tmp_path/'regular-g245.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.variable_id='science';ds.grid_label='g245';ds.horizontal_label='hxy'
        for n,size in [('lat',2),('lon',3),('bnds',2)]:ds.createDimension(n,size)
        for n,role,unit in [('lat','latitude','degrees_north'),('lon','longitude','degrees_east')]:
            v=ds.createVariable(n,'f4',(n,));v[:]=np.arange(v.size);v.standard_name=role;v.units=unit;v.bounds='bounds_'+n;v.long_name=role.title()
            ds.createVariable('bounds_'+n,'f4',(n,'bnds'))[:]=np.arange(v.size*2).reshape(v.size,2)
        ds.createVariable('science','f4',('lat','lon'))[:]=np.arange(6).reshape(2,3)
    names=[Step('name','rename_horizontal_coordinate',dict(role=role)) for role in ['latitude','longitude']]
    bounds=[Step('bounds','rename_bounds_variable',dict(coordinate=n,source='bounds_'+n,target=n+'_bnds')) for n in ['lat','lon']]
    titles=[Step('title','set_reviewed_coordinate_long_name',dict(coordinate=n,value=role)) for n,role in [('lat','latitude'),('lon','longitude')]]
    q=FixPlan('t','qa',{str(p):names+bounds+titles if names_first else bounds+titles+names})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert ds['latitude'].dimensions==('lat',) and ds['latitude'].bounds=='lat_bnds'
        assert ds['longitude'].dimensions==('lon',) and ds['longitude'].bounds=='lon_bnds'
        assert ds['lat_bnds'].shape==(2,2) and ds['lon_bnds'].shape==(3,2)
        assert ds['latitude'].long_name=='latitude'
        np.testing.assert_array_equal(ds['science'][:],np.arange(6).reshape(2,3))
    assert apply_plan(q,tmp_path/'again')['changed']==0


@pytest.mark.parametrize('var,coordinate,expected',[('rld','alevhalf','C074'),('edt','alevel','C104'),('evu','alevel','C104')])
def test_descriptive_hybrid_names_reuse_guarded_recipes(var,coordinate,expected):
    label='alh' if coordinate=='alevhalf' else 'al'
    message=f"Coordinate '{coordinate}' could not be verified. Expected is a generic vertical coordinate with out_name 'lev'. No model_level_coordinate marked for generic_level_name='{coordinate}' has standard_name='Pressure at interface of vertical levels'; available standard names are ['atmosphere_hybrid_height_coordinate', 'atmosphere_hybrid_sigma_ln_pressure_coordinate', 'atmosphere_hybrid_sigma_pressure_coordinate', 'atmosphere_sigma_coordinate', 'model_level_number']."
    finding=Finding(3,'[CMIP7] [COORD012] Parametric vertical coordinate',message,f'CMIP7.ATM.{var}.tavg-{label}-hxy-u.mon.glb.g245.IPSL-CM7A.piControl-spinup.r1i1p1f1','/tmp/file.nc')
    match=RuleSet.load(*default_rule_paths()).match(finding)
    assert match and match.rule.id==expected
