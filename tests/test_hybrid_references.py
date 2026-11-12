import shutil

"""Regression tests for the user's revised names-and-attributes-only hybrid recipe."""
import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.fixes.hybrid import STANDARD_NAME
from esgf_fix_tool.plan import FixPlan, Step


def make(path,fmt='NETCDF4',name='lev',bounds=False,realm='atmos',var='ta',coefficient_count=80,coefficient_dim='coeff'):
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.realm=realm;ds.variable_id=var;ds.history='original global history'
        if fmt=='NETCDF4':ds.setncattr_string('comment','preserve NC_STRING')
        for n,size in [('time',None),(name,79),(coefficient_dim,coefficient_count),('bnds',2),('lat',2)]:ds.createDimension(n,size)
        v=ds.createVariable(name,'f8',(name,));v[:]=np.linspace(100000.,100.,79)
        v.standard_name='x';v.units='Pa';v.long_name='Atmospheric Model Level'
        for n in ['ap','b']:
            v=ds.createVariable(n,'f8',(coefficient_dim,));v[:]=np.arange(coefficient_count,dtype='f8')+(.5 if n=='b' else 0.)
            ds.createVariable(n+'_bnds','f8',('bnds',coefficient_dim))[:]=np.arange(2*coefficient_count,dtype='f8').reshape(2,coefficient_count)
        if bounds:
            ds[name].bounds='lev_bnds';ds.createVariable('lev_bnds','f8',(name,'bnds'))[:]=np.arange(158).reshape(79,2)
        ps=ds.createVariable('ps','f4',('time','lat'));ps[:]=[[1e5,9e4],[8e4,7e4]];ps.units='Pa'
        v=ds.createVariable(var,'i2',('time',name,'lat'),zlib=fmt.startswith('NETCDF4'),fill_value=-32768)
        v.scale_factor=.25;v.add_offset=4.;v.set_auto_maskandscale(False);v[:]=np.arange(316,dtype='i2').reshape(2,79,2)


def plan(path,coordinate):
    return FixPlan('test','report',{str(path):[Step('C074','fix_hybrid_coordinate_metadata',{'coordinate':coordinate})]})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('name',['lev','presinter','presniv'])
@pytest.mark.parametrize('coordinate',['alevel','alevhalf'])
@pytest.mark.parametrize('bounds',[False,True])
def test_only_names_and_attributes_change_even_with_swapped_coefficients(tmp_path,fmt,name,coordinate,bounds):
    source,target=tmp_path/'source.nc',tmp_path/'copy.nc';make(source,fmt,name,bounds,realm='aerosol' if coordinate=='alevel' else 'atmos')
    original=source.read_bytes();shutil.copyfile(source,target);p=plan(target,coordinate)
    assert apply_plan(p,tmp_path/'log',dry_run=True)['changed']==1 and target.read_bytes()==original
    assert apply_plan(p,tmp_path/'log')['changed']==1 and source.read_bytes()==original
    with netCDF4.Dataset(source) as src,netCDF4.Dataset(target) as dst:
        for ds in [src,dst]:ds.set_auto_maskandscale(False);ds.set_auto_chartostring(False)
        mapping={name:'lev'}
        assert set(dst.variables)=={mapping.get(n,n) for n in src.variables}
        for n,v in src.variables.items():
            out=dst[mapping.get(n,n)]
            assert v.shape==out.shape and v.dtype==out.dtype and v[:].tobytes()==out[:].tobytes()
            assert v.filters()==out.filters() and v.chunking()==out.chunking()
            assert out.dimensions==tuple(mapping.get(d,d) for d in v.dimensions)
        assert dst['ap'].dimensions==('coeff',) and dst['ap_bnds'].shape==(2,80)
        assert dst['lev'].standard_name==STANDARD_NAME and dst['lev'].units=='1'
        assert dst['lev'].formula=='p = ap + b*ps' and dst['lev'].formula_terms=='ap: ap b: b ps: ps'
        assert dst['lev'][0]==100000.  # No silent normalization of lev.
        assert dst['ap'].units=='Pa' and dst['b'].units=='1'
        assert dst.ncattrs()==src.ncattrs() and all(dst.getncattr(a)==src.getncattr(a) for a in src.ncattrs())
        if bounds and coordinate=='alevel':assert dst['lev_bnds'].formula_terms=='ap: ap_bnds b: b_bnds ps: ps'
        if not bounds:assert 'lev_bnds' not in dst.variables and 'bounds' not in dst['lev'].ncattrs()
    corrected=target.read_bytes();assert apply_plan(p,tmp_path/'log')['changed']==0 and target.read_bytes()==corrected


def test_old_reference_plan_is_disabled_without_reading_donor_paths(tmp_path):
    p=tmp_path/'test.nc';make(p);original=p.read_bytes()
    old=FixPlan('old','report',{str(p):[Step('C074','restore_hybrid_vertical_coefficients',{'center_reference':'/does/not/exist'})]})
    with pytest.raises(ValueError,match='disabled'):apply_plan(old,tmp_path/'log')
    assert p.read_bytes()==original


@pytest.mark.parametrize('invalid',['missing_ps','wrong_ps_units','non_atmosphere','missing_coefficient','wrong_standard_name'])
def test_metadata_preflight_fails_without_partial_changes(tmp_path,invalid):
    p=tmp_path/'test.nc';make(p)
    with netCDF4.Dataset(p,'a') as ds:
        if invalid=='missing_ps':ds.renameVariable('ps','other')
        elif invalid=='wrong_ps_units':ds['ps'].units='hPa'
        elif invalid=='non_atmosphere':ds.realm='ocean'
        elif invalid=='wrong_standard_name':ds['lev'].standard_name='air_pressure'
        else:ds.renameVariable('ap','other')
    before=p.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(p,'alevel'),tmp_path/'log')
    assert p.read_bytes()==before


@pytest.mark.parametrize('name_first',[False,True])
def test_composes_with_previously_approved_standard_name_only_fix(tmp_path,name_first):
    p=tmp_path/'test.nc';make(p)
    metadata=plan(p,'alevel').files[str(p)][0]
    name=Step('C014','fix_invalid_standard_names',dict(expected=['x'],replacement=STANDARD_NAME,var='lev',requires=['ap','b']))
    steps=[name,metadata] if name_first else [metadata,name]
    both=FixPlan('test','report',{str(p):steps})
    assert apply_plan(both,tmp_path/'log')['changed']==1
    assert apply_plan(both,tmp_path/'log')['changed']==0


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC'])
@pytest.mark.parametrize('coordinate', ['alevel', 'alevhalf'])
def test_reference_coefficients_and_deletions_share_one_clean_output(tmp_path, monkeypatch, coordinate, fmt):
    import json

    from esgf_fix_tool.batch_write import Batch, Snapshot
    from esgf_fix_tool.fixes.hybrid import _coefficient_reference

    p = tmp_path / 'zg.nc'
    make(p, fmt=fmt, var='zg', coefficient_count=80 if coordinate == 'alevel' else 79,
         coefficient_dim='klevp1' if coordinate == 'alevel' else 'klev')
    ref = _coefficient_reference()
    lev_values = ref[79]['ap'] + 101325 * ref[79]['b']
    with netCDF4.Dataset(p, 'a') as ds:
        ds['lev'][:] = lev_values
        if coordinate == 'alevhalf':
            # The old 79 coefficients can already be numerically identical.
            # The replacement must still write them to their new dimension.
            for name in ('ap', 'b'):
                ds[name][:] = ref[79][name]
        ds.temporal_label = 'tpt'
        ds['zg'].cell_methods = 'time: point'
        ds.createVariable('bnds', 'i4', ('bnds',))[:] = [1, 2]
        ds.createDimension('nvertex', 2)
        ds.createDimension('axis_nbounds', 2)
        ds.createVariable('lat', 'f8', ('lat',))[:] = [-1, 1]
        ds['lat'].bounds = 'bounds_lat'
        ds.createVariable('bounds_lat', 'f8', ('lat', 'nvertex'))[:] = [[-2, 0], [0, 2]]
        time = ds.createVariable('time', 'f8', ('time',))
        time[:] = [1, 2]
        time.bounds = 'time_bounds'
        ds.createVariable('time_bounds', 'f8', ('time', 'axis_nbounds'))[:] = [[1, 1], [2, 2]]
        ds.set_auto_maskandscale(False)
        science = ds['zg'][:].tobytes()
        pressure = ds['ps'][:].tobytes()

    writes = []
    original_write = Batch.write
    def record_write(batch, output):
        writes.append(output)
        return original_write(batch, output)

    def snapshot(self, variable):
        assert variable.name not in {'zg', 'ps'}
        original_snapshot(self, variable)

    original_snapshot = Snapshot.__init__
    monkeypatch.setattr(Snapshot, '__init__', snapshot)
    monkeypatch.setattr(Batch, 'write', record_write)
    steps = [
        Step('C060', 'detach_instantaneous_time_bounds', {}),
        Step('C067', 'rename_bounds_variable',
             dict(coordinate='lat', source='bounds_lat', target='lat_bnds')),
        Step('C068', 'normalize_bounds_dimension', {}),
        Step('C074', 'fix_hybrid_coefficients', {'coordinate': coordinate}),
    ]
    q = FixPlan('test', 'report', {str(p): steps})
    inode = p.stat().st_ino
    assert apply_plan(q, tmp_path / 'log')['changed'] == 1
    assert apply_plan(q, tmp_path / 'log')['changed'] == 0
    assert len(writes) == 1 and p.stat().st_ino != inode
    assert json.loads((tmp_path / 'log').read_text().splitlines()[0])['rewrite_count'] == 1
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['zg'][:].tobytes() == science and ds['ps'][:].tobytes() == pressure
        assert ds['lat_bnds'].dimensions == ('lat', 'bnds')
        assert 'time_bounds' not in ds.variables
        assert 'bounds' not in ds['time'].ncattrs()
        np.testing.assert_array_equal(ds['lev'][:], lev_values)
        assert 'fix_original_ap' not in ds.variables and 'fix_original_b' not in ds.variables
        for n in ('ap', 'b'):
            assert ds[n].dimensions == ('lev',)
            np.testing.assert_array_equal(ds[n][:], ref[79][n])
            if coordinate == 'alevel':
                assert ds[n + '_bnds'].dimensions == ('lev', 'bnds')
                np.testing.assert_array_equal(ds[n + '_bnds'][:],
                    np.column_stack((ref[80][n][:-1], ref[80][n][1:])))
            else:
                assert 'bounds' not in ds[n].ncattrs()
