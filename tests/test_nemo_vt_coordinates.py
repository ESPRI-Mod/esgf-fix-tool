"""C119 uses V for sfy and T for bottom diagnostics, without reading science."""

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

SPECS = {
    'sfy': ('V', 'g169', 'ocean_salt_y_transport'),
    'sob': ('T', 'g112', 'sea_water_salinity_at_sea_floor'),
    'tob': ('T', 'g112', 'sea_water_potential_temperature_at_sea_floor'),
}


def make(path, var, fmt='NETCDF4', present=False, canonical=False):
    grid, label, standard = SPECS[var]
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.setncatts(dict(variable_id=var, branded_variable=var+'_tavg-u-hxy-sea', grid_label=label,
                         horizontal_label='hxy', source_id='Another_model', experiment_id='Another_experiment',
                         history='Preserve global history'))
        ds.createDimension('time', None); ds.createDimension('y', 331); ds.createDimension('x', 360)
        ds.createVariable('time', 'f8', ('time',))[:] = [15.5, 45.]
        options = dict(zlib=True, complevel=2, chunksizes=(1, 31, 60)) if fmt.startswith('NETCDF4') else {}
        main = ds.createVariable(var, 'i2', ('time', 'y', 'x'), fill_value=-32767, **options)
        main.scale_factor=.25; main.add_offset=3.; main.set_auto_maskandscale(False)
        values=(np.arange(2*331*360, dtype='i4')%30000).astype('i2').reshape(2,331,360)
        values[:,::11,::7]=-32767; main[:]=values
        main.standard_name=standard; main.long_name='Keep scientific name'
        main.coordinates='latitude longitude' if canonical else 'nav_lat nav_lon'
        main.comment='Keep scientific comment'; main.cell_methods='time: mean'
        if present:
            for key, (data, attrs) in reference(grid).items():
                name={'nav_lat':'latitude','nav_lon':'longitude'}[key] if canonical else key
                v=ds.createVariable(name, 'f8', ('y','x'), fill_value=False); v[:]=data; v.setncatts(attrs)
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        v=ds[var]
        return values, v.filters(), v.chunking(), {a:v.getncattr(a) for a in v.ncattrs() if a!='coordinates'}


def plan(path, var):
    grid, label, _ = SPECS[var]
    dataset=f'CMIP7.OCE.{var}.tavg-u-hxy-sea.mon.glb.{label}.AnyModel.any_experiment.r1i1p1f1'
    findings=[Finding(3, '[CMIP7] [COORD011] Horizontal grid coordinates',
                      "The required horizontal coordinate(s) are absent: ['longitude', 'latitude'].", dataset, str(path))]
    for coord in ('nav_lat','nav_lon'):
        findings.append(Finding(3, '[CF-Conventions] §5 Coordinate Systems',
                        f"The 'coordinates' attribute of variable '{var}' references non-existent variable '{coord}'.",dataset,str(path)))
    q=FixPlan.from_classification(classify(QAReport(None,{},findings),RuleSet.load(*default_rule_paths())))
    assert q.files[str(path)]==[Step('C119','inject_nemo_u_coordinates',{})]
    return q


@pytest.mark.parametrize('var', SPECS)
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('present',[False,True])
def test_correct_staggered_grid_is_injected_directly_and_replays(tmp_path,monkeypatch,var,fmt,present):
    path=tmp_path/'ocean.nc'; raw,filters,chunks,attrs=make(path,var,fmt,present)
    original=path.read_bytes(); inode=path.stat().st_ino
    grid,_,_=SPECS[var]; q=plan(path,var)

    def forbid(*args): raise AssertionError('Injecting coordinates must not rewrite the file')
    monkeypatch.setattr(Batch,'write',forbid)
    getitem=Variable.__getitem__
    def guarded(v,key):
        assert v.name!=var, 'Do not read the scientific matrix for coordinate injection'
        return getitem(v,key)
    monkeypatch.setattr(Variable,'__getitem__',guarded)
    apply_plan(q,tmp_path/'dry',dry_run=True); assert path.read_bytes()==original
    apply_plan(q,tmp_path/'log')
    assert path.stat().st_ino==inode and json.loads((tmp_path/'log').read_text())['rewrite_count']==0
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds[var][:].tobytes()==raw.tobytes()
        assert ds[var].filters()==filters and ds[var].chunking()==chunks
        assert set(ds[var].ncattrs())-{'coordinates'}==set(attrs)
        for a,value in attrs.items(): np.testing.assert_array_equal(ds[var].getncattr(a),value)
        assert ds.history=='Preserve global history'
        canonical=grid=='T'
        assert ds[var].coordinates==('latitude longitude' if canonical else 'nav_lat nav_lon')
        for key,(data,metadata) in reference(grid).items():
            name={'nav_lat':'latitude','nav_lon':'longitude'}[key] if canonical else key
            assert ds[name].dimensions==('y','x') and ds[name].dtype==np.dtype('f8')
            assert ds[name][:].tobytes()==data.tobytes()
            for attr,value in metadata.items(): assert ds[name].getncattr(attr)==value
            if canonical: assert key not in ds.variables
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected


@pytest.mark.parametrize('var', SPECS)
def test_already_canonical_coordinates_are_preserved(tmp_path,var):
    path=tmp_path/'ocean.nc';make(path,var,present=True,canonical=True)
    before=path.read_bytes()
    assert apply_plan(plan(path,var),tmp_path/'log')['changed']==0
    assert path.read_bytes()==before


@pytest.mark.parametrize('var', SPECS)
def test_u_coordinates_cannot_be_substituted_for_v_or_t(tmp_path,var):
    path=tmp_path/'ocean.nc';make(path,var,present=True)
    with netCDF4.Dataset(path,'a') as ds:
        ds['nav_lon'][:]=reference('U')['nav_lon'][0]
    before=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,var),tmp_path/'log')
    assert path.read_bytes()==before


@pytest.mark.parametrize('grid', ['V','T'])
def test_new_json_profiles_have_independent_origins_and_checksums(grid):
    doc=json.loads((Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/nemo_u_coordinates.json').read_text())
    profile=doc['additional_grids'][grid]
    expected=('gphiv','glamv') if grid=='V' else ('gphit','glamt')
    for (key,(data,_)),origin in zip(reference(grid).items(),expected):
        spec=profile['coordinates'][key]
        assert spec['source_variable']==origin and spec['source_record']==0
        assert hashlib.sha256(data.astype('<f8').tobytes()).hexdigest()==spec['sha256']
    if grid=='T': assert reference(grid)['nav_lat'][0][:,97].tobytes() != reference('V')['nav_lat'][0][:,97].tobytes()


def test_bottom_canonical_names_compose_with_existing_horizontal_rename_steps(tmp_path):
    path=tmp_path/'ocean.nc';make(path,'sob')
    q=plan(path,'sob')
    q.files[str(path)][:0]=[Step('C071','rename_horizontal_coordinate',{'role':'latitude'}),
                           Step('C072','rename_horizontal_coordinate',{'role':'longitude'})]
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['sob'].coordinates=='latitude longitude'
        assert not {'nav_lat','nav_lon'}&ds.variables.keys()
    assert apply_plan(q,tmp_path/'again')['changed']==0
