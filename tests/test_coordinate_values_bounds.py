import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.fixes import FixFunction, REGISTRY
from esgf_fix_tool.fixes import bounds as fixes
from esgf_fix_tool.plan import FixPlan, Step


def make(path, fmt='NETCDF4', label='tavg'):
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.variable_id, ds.temporal_label, ds.grid_label = 'science', label, 'g111'
        ds.history = 'global history'
        for name, size in [('time', None), ('lon', 144), ('lat', 2), ('axis_nbounds', 2), ('nvertex', 2)]:
            ds.createDimension(name, size)
        t = ds.createVariable('time', 'f8', ('time',))
        t[:], t.bounds, t.units, t.calendar = [1., 2.], 'time_bounds', 'days since 1850-01-01', 'proleptic_gregorian'
        tb = ds.createVariable('time_bounds', 'f8', ('time', 'axis_nbounds'))
        tb[:] = [[1., 1.], [2., 2.]] if label == 'tpt' else [[0., 1.5], [1.5, 3.]]
        v = ds.createVariable('science', 'i2', ('time', 'lat', 'lon'),
                               zlib=fmt.startswith('NETCDF4'), fill_value=-32768)
        v.scale_factor, v.add_offset = .25, 2.
        v.cell_methods = 'area: mean time: point' if label == 'tpt' else 'area: mean time: mean'
        v.set_auto_maskandscale(False)
        v[:] = np.arange(576, dtype='i2').reshape(2, 2, 144)
        lon = ds.createVariable('lon', 'f8', ('lon',))
        lon[:], lon.units, lon.bounds = np.arange(144)*2.5 + 1.e-13, 'degrees_east', 'bounds_lon'
        b = ds.createVariable('bounds_lon', 'f8', ('lon', 'nvertex'))
        b[:] = np.column_stack([np.arange(144)*2.5-1.25, np.arange(144)*2.5+1.25]) + 1.e-13


def plan(path, *steps):
    return FixPlan('test', 'test.json', {str(path): [Step('test', name, params) for name, params in steps]})


def apply(path, tmp_path, *steps):
    p = plan(path, *steps)
    assert apply_plan(p, tmp_path/'log')['changed'] == 1
    corrected = path.read_bytes()
    assert apply_plan(p, tmp_path/'log')['changed'] == 0
    assert path.read_bytes() == corrected


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('var,old,new', [('seasurface','osurf','ocean_surface_layer'),
    ('type','typebare','bare_ground'), ('type','typecrop','crops'), ('type','typenatgr','natural_grasses'),
    ('type','typeresidual','residual'), ('type','typetree','trees'), ('type','typeveg','vegetation')])
def test_seven_label_mappings_preserve_other_arrays_and_remove_nuls(tmp_path, fmt, var, old, new):
    path=tmp_path/'file.nc';make(path,fmt)
    with netCDF4.Dataset(path,'a') as ds:
        ds.createDimension('str_len',255)
        v=ds.createVariable(var,'S1',('str_len',));v.long_name='unchanged'
        raw=old.encode()+b'\0'+b' '*(254-len(old));v[:]=np.frombuffer(raw,dtype='S1')
    apply(path,tmp_path,('fix_scalar_label',dict(var=var,expected=old,value=new)))
    with netCDF4.Dataset(path) as ds:
        v=ds[var];v.set_auto_maskandscale(False)
        assert v[:].tobytes()==new.encode()+b' '*(255-len(new))
        assert v.long_name=='unchanged' and ds.history=='global history'


def test_scalar_nc_string_label(tmp_path):
    path=tmp_path/'file.nc';make(path)
    with netCDF4.Dataset(path,'a') as ds:
        ds.createVariable('type',str,())[()] = 'typebare'
    apply(path,tmp_path,('fix_scalar_label',dict(var='type',expected='typebare',value='bare_ground')))


@pytest.mark.parametrize('fmt', ['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('existing', [False,True])
def test_bounds_dimension_merge_handles_existing_bnds_and_preserves_every_raw_array(tmp_path,fmt,existing):
    path=tmp_path/'file.nc';make(path,fmt)
    if existing:
        with netCDF4.Dataset(path,'a') as ds:ds.createDimension('bnds',2)
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False);before={n:v[:].tobytes() for n,v in ds.variables.items()}
    apply(path,tmp_path,('normalize_bounds_dimension',{}))
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert 'nvertex' not in ds.dimensions and 'axis_nbounds' not in ds.dimensions
        assert len(ds.dimensions['bnds'])==2 and ds.dimensions['time'].isunlimited()
        for n,raw in before.items():assert ds[n][:].tobytes()==raw
        assert ds['bounds_lon'].dimensions==('lon','bnds')
        assert ds['time_bounds'].dimensions==('time','bnds')


def test_ocean_vertices_size_four_are_not_merged(tmp_path):
    path=tmp_path/'file.nc'
    with netCDF4.Dataset(path,'w') as ds:
        ds.createDimension('axis_nbounds',2);ds.createDimension('nvertex',4)
        ds.createVariable('vertices_longitude','f8',('nvertex',))[:]=[1,2,3,4]
        ds.createVariable('time_bounds','f8',('axis_nbounds',))[:]=[0,1]
    apply(path,tmp_path,('normalize_bounds_dimension',{}))
    with netCDF4.Dataset(path) as ds:assert len(ds.dimensions['nvertex'])==4 and ds['vertices_longitude'].dimensions==('nvertex',)


def test_iceband_with_legacy_time_bounds_does_not_rewrite_science_data(tmp_path):
    path = tmp_path / 'siitdconc.nc'
    payload = np.arange(10, dtype='f4').reshape(2, 5)
    with netCDF4.Dataset(path, 'w') as ds:
        ds.source_id = 'IPSL-CM6-ESMCO2'
        ds.experiment_id = 'esm-piControl-spinup'
        ds.grid_label = 'g112'
        ds.variant_label = 'r1i1p1f1'
        ds.variable_id = 'siitdconc'
        for name, size in [('time', 2), ('iceband', 5), ('axis_nbounds', 2), ('nvertex', 4)]:
            ds.createDimension(name, size)
        axis = ds.createVariable('iceband', 'f8', ('iceband',))
        axis.units, axis.standard_name = 'm', 'sea_ice_thickness'
        axis[:] = [1, 2, 3, 4, 5]
        ds.createVariable('time_bounds', 'f8', ('time', 'axis_nbounds'))[:] = [[0, 1], [1, 2]]
        ds.createVariable('siitdconc', 'f4', ('time', 'iceband'), zlib=True)[:] = payload

    inode = path.stat().st_ino
    apply(path, tmp_path, ('fix_iceband', {}), ('normalize_bounds_dimension', {}))
    assert path.stat().st_ino == inode
    assert json.loads((tmp_path / 'log').read_text().splitlines()[0])['rewrite_count'] == 0
    with netCDF4.Dataset(path) as ds:
        assert ds['iceband'].bounds == 'iceband_bnds'
        np.testing.assert_array_equal(ds['iceband'][:], [0.45, 1.12, 2.14, 3.67, 99])
        np.testing.assert_array_equal(ds['iceband_bnds'][:],
                                      [[0, .45], [.45, 1.12], [1.12, 2.14], [2.14, 3.67], [3.67, 99]])
        assert ds['time_bounds'].dimensions == ('time', 'bnds')
        assert len(ds.dimensions['nvertex']) == 4
        np.testing.assert_array_equal(ds['siitdconc'][:], payload)


@pytest.mark.parametrize('sequence', ['normalize_first','rename_first','same_plan'])
def test_longitude_and_bound_names_and_shared_dimension_are_independent(tmp_path,sequence):
    path=tmp_path/'file.nc';make(path)
    normalization=('normalize_regular_longitude',{})
    rename=('rename_bounds_variable',dict(coordinate='lon',source='bounds_lon',target='lon_bnds'))
    merge=('normalize_bounds_dimension',{})
    if sequence=='same_plan':apply(path,tmp_path,normalization,rename,merge)
    else:
        for step in ([normalization,rename,merge] if sequence=='normalize_first' else [rename,merge,normalization]):apply(path,tmp_path,step)
    with netCDF4.Dataset(path) as ds:
        np.testing.assert_array_equal(ds['lon'][:],np.arange(144)*2.5)
        np.testing.assert_array_equal(ds['lon_bnds'][:,0],np.arange(144)*2.5-1.25)
        assert ds['lon'].bounds=='lon_bnds'


@pytest.mark.parametrize('kind', ['real_grid_difference','missing','bad_point_bounds','non_point','collision','unreviewed_label'])
def test_bad_preconditions_refuse_replacement(tmp_path,kind):
    path=tmp_path/'file.nc';make(path,label='tpt')
    step=('normalize_regular_longitude',{})
    with netCDF4.Dataset(path,'a') as ds:
        if kind=='real_grid_difference':ds['lon'][10]=31.
        elif kind=='missing':ds['lon'][10]=np.nan
        elif kind=='bad_point_bounds':ds['time_bounds'][1,1]=3.;step=('detach_instantaneous_time_bounds',{})
        elif kind=='non_point':ds.temporal_label='tavg';step=('detach_instantaneous_time_bounds',{})
        elif kind=='collision':
            ds.createDimension('bnds',3);step=('normalize_bounds_dimension',{})
        else:step=('fix_scalar_label',dict(var='science',expected='typebare',value='trees'))
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,step),tmp_path/'log')
    assert path.read_bytes()==original and not list(tmp_path.glob('*.fixtmp'))


def test_point_bounds_are_physically_removed_and_time_preserved(tmp_path):
    path=tmp_path/'file.nc';make(path,label='tpt')
    apply(path,tmp_path,('detach_instantaneous_time_bounds',{}),
        ('rename_bounds_variable',dict(coordinate='time',source='time_bounds',target='time_bnds')))
    with netCDF4.Dataset(path) as ds:
        assert 'bounds' not in ds['time'].ncattrs()
        assert 'time_bounds' not in ds.variables and 'time_bnds' not in ds.variables
        np.testing.assert_array_equal(ds['time'][:],[1,2])


@pytest.mark.parametrize('label', ['tclm','tmaxavg','tminavg'])
@pytest.mark.parametrize('rename_first',[False,True])
def test_climatology_switch_reuses_values_and_survives_old_naming_plan(tmp_path,label,rename_first):
    path=tmp_path/'file.nc';make(path,label=label)
    with netCDF4.Dataset(path,'a') as ds:
        ds['science'].cell_methods='time: mean within years time: mean over years' if label=='tclm' else 'time: maximum within days time: mean over days'
    rename=('rename_bounds_variable',dict(coordinate='time',source='time_bounds',target='time_bnds'))
    if rename_first:apply(path,tmp_path,rename)
    apply(path,tmp_path,('fix_climatology_reference',{}),rename,('normalize_bounds_dimension',{}))
    with netCDF4.Dataset(path) as ds:
        assert ds['time'].climatology=='climatology_bnds' and 'bounds' not in ds['time'].ncattrs()
        np.testing.assert_array_equal(ds['climatology_bnds'][:],[[0,1.5],[1.5,3]])


@pytest.mark.parametrize('coordinate', ['alt','tau'])
def test_registry_bounds_use_explicit_edges_and_check_centers(tmp_path,coordinate):
    path=tmp_path/'file.nc';make(path)
    spec=fixes._specs()['bounds'][coordinate]
    with netCDF4.Dataset(path,'a') as ds:
        ds.createDimension(coordinate,len(spec['centers']));v=ds.createVariable(coordinate,'f8',(coordinate,))
        v[:],v.units=spec['centers'],spec['units']
    apply(path,tmp_path,('add_registry_bounds',dict(coordinate=coordinate)),('normalize_bounds_dimension',{}))
    with netCDF4.Dataset(path) as ds:
        b=ds[f'{coordinate}_bnds'][:]
        np.testing.assert_array_equal(b,np.column_stack([spec['edges'][:-1],spec['edges'][1:]]))
        assert ds[coordinate].bounds==f'{coordinate}_bnds'


def test_final_value_verifier_detects_corruption_after_another_hook(tmp_path,monkeypatch):
    path=tmp_path/'file.nc';make(path);original=path.read_bytes()
    def corrupt(source,output):
        with netCDF4.Dataset(output,'a') as ds:ds['lon'][0]=5.e-14
    monkeypatch.setitem(REGISTRY,'test_corrupt_values',FixFunction('test_corrupt_values','metadata',lambda ds:True,True,corrupt))
    with pytest.raises(ValueError,match='Normalized longitude'):
        apply_plan(plan(path,('normalize_regular_longitude',{}),('test_corrupt_values',{})),tmp_path/'log')
    assert path.read_bytes()==original


@pytest.mark.parametrize('invalid', ['centers', 'units', 'missing_reference', 'existing_bounds'])
def test_registry_bounds_do_not_infer_or_overwrite_conflicting_values(tmp_path, invalid):
    path=tmp_path/'file.nc';make(path)
    spec=fixes._specs()['bounds']['tau']
    with netCDF4.Dataset(path,'a') as ds:
        ds.createDimension('tau',7)
        v=ds.createVariable('tau','f8',('tau',));v[:],v.units=spec['centers'],'1'
        if invalid=='centers':v[2]=3.
        elif invalid=='units':v.units='m'
        elif invalid=='missing_reference':v.bounds='missing'
        else:
            ds.createDimension('bnds',2)
            ds.createVariable('tau_bnds','f8',('tau','bnds'))[:]=np.zeros((7,2))
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,('add_registry_bounds',dict(coordinate='tau'))),tmp_path/'log')
    assert path.read_bytes()==original


@pytest.mark.parametrize('invalid', ['interval', 'time_outside', 'units', 'semantics'])
def test_climatology_rejects_unreviewed_intervals_or_semantics(tmp_path, invalid):
    path=tmp_path/'file.nc';make(path,label='tclm')
    with netCDF4.Dataset(path,'a') as ds:
        ds['science'].cell_methods='time: mean within years time: mean over years'
        if invalid=='interval':ds['time_bounds'][0,:]=[1.,1.]
        elif invalid=='time_outside':ds['time'][0]=-10.
        elif invalid=='units':ds['time_bounds'].units='hours since 1850-01-01'
        else:ds['science'].cell_methods='time: mean'
    original=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,('fix_climatology_reference',{})),tmp_path/'log')
    assert path.read_bytes()==original


def test_one_bounds_dimension_uses_rename_without_rewriting_arrays(tmp_path,monkeypatch):
    path=tmp_path/'file.nc'
    with netCDF4.Dataset(path,'w') as ds:
        ds.createDimension('axis_nbounds',2)
        ds.createVariable('time_bounds','f8',('axis_nbounds',))[:]=[1.,2.]
    def refuse_rewrite(*args,**kwargs):raise AssertionError('Unnecessary whole-file rewrite')
    monkeypatch.setattr(fixes,'_python_rewrite',refuse_rewrite)
    apply(path,tmp_path,('normalize_bounds_dimension',{}))
    with netCDF4.Dataset(path) as ds:
        assert ds['time_bounds'].dimensions==('bnds',)
        np.testing.assert_array_equal(ds['time_bounds'][:],[1.,2.])
