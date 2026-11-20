"""C070 keeps all 331 basin-transport rows and exposes latitude, including recovery from the former rlat output."""

import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.basin_latitude_bounds import conventional_bounds
from esgf_fix_tool.fixes.msfty_latitude import reference_331
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

PRODUCTS = ('hfbasin', 'hfbasinpmadv', 'hfbasinpmdiff', 'htovgyre', 'htovovrt',
            'sltbasin', 'sltovgyre', 'sltovovrt')
MESSAGE = "It is required for 'lat' on the configured rectilinear grid to be a 1-D coordinate variable; found 'nav_lat' with dimensions ['y', 'x']. Dependent horizontal-grid checks could not be evaluated."


def plan(path, var):
    finding = Finding(3, '[CMIP7] [COORD011] Horizontal grid coordinates', MESSAGE,
                      f'CMIP7.OCE.{var}.tavg-u-hyb-sea.mon.glb.g112.A_model.any_experiment.r1i1p1f1', str(path))
    result = FixPlan.from_classification(classify(QAReport(None, {}, [finding]), RuleSet.load(*default_rule_paths())))
    assert result.files[str(path)] == [Step('C070', 'fix_basin_profile_latitude', {'var': var})]
    return result


def make(path, var='hfbasin', fmt='NETCDF4', alias='nav_lat', squeezed=False, packed=False, previous_rlat=False):
    values = reference_331().copy()
    if not previous_rlat: values[:32] = 0
    if previous_rlat: alias, squeezed = 'rlat', True
    dimension = 'rlat' if previous_rlat else ('lat' if squeezed else 'y')
    horizontal = (dimension,) if squeezed else (dimension, 'x')
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.setncatts(dict(variable_id=var, branded_variable=var+'_tavg-u-hyb-sea',
                         horizontal_label='hyb', grid_label='g112', source_id='A_model',
                         experiment_id='any_experiment', history='Preserve global history'))
        ds.createDimension('time', None)
        for name, length in [('basin', 3), (dimension, 331), ('strlen', 21), ('bnds', 2)]:
            ds.createDimension(name, length)
        if not squeezed: ds.createDimension('x', 1)
        ds.createVariable('time', 'f8', ('time',))[:] = [15.5, 45., 74.5]
        coordinate = ds.createVariable(alias, 'f8', horizontal)
        coordinate[:] = values.reshape(coordinate.shape)
        coordinate.standard_name='latitude'; coordinate.units='degrees_north'; coordinate.long_name='Latitude'
        coordinate.comment='Keep coordinate comment'
        if previous_rlat:
            coordinate.standard_name='grid_latitude'; coordinate.units='degrees'; coordinate.long_name='Grid Latitude'
            coordinate.bounds='rlat_bnds'
            ds.createVariable('rlat_bnds','f8',('rlat','bnds'))[:] = conventional_bounds(values)
            ds['rlat_bnds'].comment='Conventional 1-D grid-latitude bounds from adjacent coordinate midpoints. Both outer limits use the adjacent half-step without forcing pole values. These are not native NEMO transport-section boundaries.'
        ds.createVariable('nav_lon', 'f8', horizontal)[:] = 0
        ds['nav_lon'].standard_name='longitude'; ds['nav_lon'].units='degrees_east'
        ds.createVariable('basin', 'i4', ('basin',))[:] = [1, 2, 3]
        ds['basin'].long_name='Keep numerical basin coordinate'
        sector = ds.createVariable('sector', 'S1', ('basin', 'strlen'))
        sector[:] = np.stack([np.frombuffer(n.ljust(21).encode('ascii'), dtype='S1') for n in
                              ('global_ocean', 'atlantic_arctic_ocean', 'indian_pacific_ocean')])
        sector.standard_name='region'
        dimensions = ('time', 'basin') + horizontal
        shape = (3, 3, 331) + (() if squeezed else (1,))
        data = np.ma.array(np.arange(np.prod(shape), dtype='f4').reshape(shape), mask=False)
        data.mask[:, :, :32] = True
        dtype, fill = ('i2', -32767) if packed else ('f4', 1e20)
        options = dict(zlib=True, complevel=2, chunksizes=(1, 1, 100)+(() if squeezed else (1,))) if fmt.startswith('NETCDF4') else {}
        main = ds.createVariable(var, dtype, dimensions, fill_value=fill, **options)
        if packed: main.scale_factor=2.; main.add_offset=1.
        main[:] = data
        main.coordinates='sector' if previous_rlat else 'sector '+alias+' nav_lon'; main.comment='Keep scientific comment'
        main.long_name='Keep scientific name'; main.cell_methods='depth: longitude: sum where sea time: mean'
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False); ds.set_auto_chartostring(False)
        return {n: (v[:].copy(), v.filters(), v.chunking(), v.dtype) for n, v in ds.variables.items()}


@pytest.mark.parametrize('var', PRODUCTS)
@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
def test_all_eight_products_keep_every_row_and_raw_value_in_one_rewrite(tmp_path, monkeypatch, var, fmt):
    path=tmp_path/'transport.nc'; originals=make(path, var, fmt)
    before=path.read_bytes(); q=plan(path, var)
    writes=[]; original_write=Batch.write

    def record(batch, output):
        assert path.read_bytes()==before
        writes.append(output)
        return original_write(batch, output)

    monkeypatch.setattr(Batch, 'write', record)
    apply_plan(q, tmp_path/'dry', dry_run=True)
    assert path.read_bytes()==before and not writes
    assert apply_plan(q, tmp_path/'log')['changed']==1 and len(writes)==1
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==1
    with netCDF4.Dataset(path) as ds:
        assert ds[var].dimensions==('time', 'basin', 'lat') and ds[var].shape==(3, 3, 331)
        assert len(ds.dimensions['basin'])==3 and len(ds.dimensions['lat'])==331
        assert not {'x', 'y', 'rlat'}.intersection(ds.dimensions)
        assert not {'nav_lat', 'latitude', 'rlat'}.intersection(ds.variables)
        np.testing.assert_array_equal(ds['lat'][:], reference_331())
        assert ds['lat'].dimensions==('lat',) and ds['lat'].standard_name=='latitude'
        assert ds['lat'].units=='degrees_north' and ds['lat'].axis=='Y' and ds['lat'].long_name=='Latitude'
        assert ds['lat'].comment=='Keep coordinate comment' and ds['lat'].bounds=='lat_bnds'
        assert ds['lat_bnds'].dimensions==('lat', 'bnds')
        np.testing.assert_array_equal(ds['lat_bnds'][:], conventional_bounds(reference_331()))
        assert ds['lat'][-1]==reference_331()[-1]
        assert np.ma.getmaskarray(ds[var][:,:,:32]).all()
        ds.set_auto_maskandscale(False); ds.set_auto_chartostring(False)
        assert ds[var][:].tobytes()==originals[var][0][..., 0].tobytes()
        assert ds[var].filters()==originals[var][1] and ds[var].dtype==originals[var][3]
        old_chunks=originals[var][2]
        assert ds[var].chunking()==(old_chunks[:-1] if isinstance(old_chunks, list) else old_chunks)
        for name in ('basin', 'sector', 'time'):
            assert ds[name][:].tobytes()==originals[name][0].tobytes()
        assert ds['nav_lon'].dimensions==('lat',) and np.all(ds['nav_lon'][:]==0)
        assert ds[var].coordinates=='sector lat'
        assert ds[var].long_name=='Keep scientific name' and ds[var].comment=='Keep scientific comment'
        assert ds[var].cell_methods=='depth: longitude: sum where sea time: mean'
        assert ds.history=='Preserve global history'
    corrected=path.read_bytes()
    assert apply_plan(q, tmp_path/'again')['changed']==0
    assert path.read_bytes()==corrected and len(writes)==1


@pytest.mark.parametrize('alias,squeezed', [('latitude', False), ('lat', True)])
def test_prior_coordinate_renames_and_packing_are_supported(tmp_path, alias, squeezed):
    path=tmp_path/'transport.nc'; originals=make(path, alias=alias, squeezed=squeezed, packed=True)
    apply_plan(plan(path, 'hfbasin'), tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert np.ma.getmaskarray(ds['hfbasin'][:,:,:32]).all()
        assert ds['hfbasin'].scale_factor==2. and ds['hfbasin'].add_offset==1.
        ds.set_auto_maskandscale(False)
        expected=originals['hfbasin'][0] if squeezed else originals['hfbasin'][0][...,0]
        assert ds['hfbasin'][:].tobytes()==expected.tobytes()


@pytest.mark.parametrize('failure', ['valid_second_time', 'wrong_tail', 'partial_prefix', 'wrong_grid',
                                     'packed_latitude', 'nonzero_longitude', 'verification'])
def test_guards_or_failed_verification_leave_source_untouched(tmp_path, monkeypatch, failure):
    path=tmp_path/'transport.nc'; make(path)
    with netCDF4.Dataset(path, 'a') as ds:
        if failure=='valid_second_time': ds['hfbasin'][1, 2, 31, 0]=0.
        elif failure=='wrong_tail': ds['nav_lat'][50, 0]+=.001
        elif failure=='partial_prefix': ds['nav_lat'][31, 0]=reference_331()[31]
        elif failure=='wrong_grid': ds.grid_label='g111'
        elif failure=='packed_latitude': ds['nav_lat'].scale_factor=.5
        elif failure=='nonzero_longitude': ds['nav_lon'][50, 0]=1.
    if failure=='verification':
        def refuse(*args): raise ValueError('Injected failed output verification')
        monkeypatch.setattr(Batch, 'verify', refuse)
    before=path.read_bytes()
    with pytest.raises(ValueError): apply_plan(plan(path, 'hfbasin'), tmp_path/'log')
    assert path.read_bytes()==before and not list(tmp_path.glob('*.fixtmp'))


def test_bounds_recipe_after_c070_is_a_noop_on_the_331_point_transport(tmp_path):
    path=tmp_path/'transport.nc'; make(path)
    q=plan(path, 'hfbasin'); q.files[str(path)].append(Step('C103','add_basin_latitude_bounds',{}))
    apply_plan(q, tmp_path/'log')
    before=path.read_bytes()
    q.files[str(path)] = [Step('C103','add_basin_latitude_bounds',{})]
    assert apply_plan(q, tmp_path/'again')['changed']==0 and path.read_bytes()==before


def test_actual_sector_only_coordinates_attribute_is_kept(tmp_path):
    path=tmp_path/'transport.nc'; make(path)
    with netCDF4.Dataset(path,'a') as ds: ds['hfbasin'].coordinates='sector'
    apply_plan(plan(path,'hfbasin'),tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['hfbasin'].coordinates=='sector'
        assert ds['hfbasin'].dimensions==('time','basin','lat')
        assert ds['lat'].bounds=='lat_bnds'


@pytest.mark.parametrize('var', PRODUCTS)
@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
def test_previous_rlat_files_are_repaired_in_place_without_reading_science(tmp_path, monkeypatch, var, fmt):
    from esgf_fix_tool.preview import Variable

    path=tmp_path/'previous.nc'
    original=make(path,var,fmt,previous_rlat=True)
    before=path.read_bytes(); inode=path.stat().st_ino
    finding=Finding(3,'[CMIP7] [COORD011] Horizontal grid coordinates',
                    "The required horizontal coordinate(s) are absent: ['latitude'].",
                    f'CMIP7.OCE.{var}.tavg-u-hyb-sea.mon.glb.g112.AnyModel.any_experiment.r1i1p1f1',str(path))
    q=FixPlan.from_classification(classify(QAReport(None,{},[finding]),RuleSet.load(*default_rule_paths())))
    assert q.files[str(path)]==[Step('C070','fix_basin_profile_latitude',{'var':var})]

    def forbid(*args):raise AssertionError('Renaming an existing axis must not rebuild the file')
    monkeypatch.setattr(Batch,'write',forbid)
    getitem=Variable.__getitem__
    def guarded(variable,key):
        assert variable.name!=var,'Metadata recovery must not read the scientific matrix'
        return getitem(variable,key)
    monkeypatch.setattr(Variable,'__getitem__',guarded)
    apply_plan(q,tmp_path/'dry',dry_run=True)
    assert path.read_bytes()==before
    assert apply_plan(q,tmp_path/'log')['changed']==1 and path.stat().st_ino==inode
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==0
    with netCDF4.Dataset(path) as ds:
        assert ds[var].dimensions==('time','basin','lat') and ds[var].coordinates=='sector'
        assert ds['lat'].dimensions==('lat',) and len(ds.dimensions['lat'])==331
        assert ds['lat'].standard_name=='latitude' and ds['lat'].units=='degrees_north'
        assert ds['lat'].long_name=='Latitude' and ds['lat'].axis=='Y' and ds['lat'].bounds=='lat_bnds'
        assert ds['lat_bnds'].dimensions==('lat','bnds')
        assert 'grid-latitude' not in ds['lat_bnds'].comment
        assert not {'rlat','rlat_bnds'}.intersection(ds.variables)
        assert 'rlat' not in ds.dimensions
        ds.set_auto_maskandscale(False); ds.set_auto_chartostring(False)
        assert ds['lat'][:].tobytes()==original['rlat'][0].tobytes()
        assert ds['lat_bnds'][:].tobytes()==original['rlat_bnds'][0].tobytes()
        for name in (var,'basin','sector','time','nav_lon'):
            assert ds[name][:].tobytes()==original[name][0].tobytes()
            assert ds[name].filters()==original[name][1] and ds[name].chunking()==original[name][2]
        assert ds[var].comment=='Keep scientific comment' and ds.history=='Preserve global history'
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected


@pytest.mark.parametrize('var',['msfty','unrelated'])
def test_missing_latitude_diagnostic_does_not_apply_c070_to_other_sector_products(var):
    f=Finding(3,'[CMIP7] [COORD011] Horizontal grid coordinates',
              "The required horizontal coordinate(s) are absent: ['latitude'].",
              f'CMIP7.OCE.{var}.tavg-ol-ht-sea.mon.glb.g112.AnyModel.any_experiment.r1i1p1f1','file.nc')
    match=RuleSet.load(*default_rule_paths()).match(f)
    assert match is None or match.rule.id!='C070'
