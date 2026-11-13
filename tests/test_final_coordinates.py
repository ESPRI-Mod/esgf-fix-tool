"""Approved normalization, passage order, unit conversion, bounds and comments."""
import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.fixes.hybrid import _coefficient_reference
from esgf_fix_tool.fixes.final_coordinates import ICE_PASSAGES, ICE_RADII


def exercise(path, tmp, steps, monkeypatch, writes):
    q=FixPlan('test','synthetic',{str(path):steps});before=path.read_bytes()
    assert apply_plan(q,tmp/'dry.jsonl',dry_run=True)['changed']==1
    assert path.read_bytes()==before
    calls=[];original=Batch.write
    def record(batch,out):calls.append(out);return original(batch,out)
    monkeypatch.setattr(Batch,'write',record)
    assert apply_plan(q,tmp/'apply.jsonl')['changed']==1
    entry=json.loads((tmp/'apply.jsonl').read_text())
    target=Path(entry.get('final_file',str(path)))
    after=target.read_bytes();inode=target.stat().st_ino
    assert apply_plan(q,tmp/'replay.jsonl')['changed']==0
    assert target.read_bytes()==after and target.stat().st_ino==inode and len(calls)==writes
    return target


def hybrid_file(path,fmt='NETCDF4',transposed=False,backup=False):
    ref=_coefficient_reference()
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.realm='atmos';ds.variable_id='ta'
        ds.createDimension('lev',79);ds.createDimension('bnds',2);ds.createDimension('time',2)
        ds.createVariable('lev','f8',('lev',))[:]=ref[79]['ap']+101325*ref[79]['b']
        ds['lev'].units='1'
        ds.createVariable('ta','f4',('time','lev'))[:]=np.arange(158).reshape(2,79)
        ds.createVariable('ps','f4',('time',))[:]=[100000,90000];ds['ps'].units='Pa'
        for n in ('ap','b'):
            ds.createVariable(n,'f8',('lev',))[:]=ref[79][n];ds[n].units='Pa' if n=='ap' else '1';ds[n].bounds=n+'_bnds'
            pairs=np.column_stack((ref[80][n][:-1],ref[80][n][1:]))
            dims=('bnds','lev') if transposed else ('lev','bnds')
            ds.createVariable(n+'_bnds','f8',dims)[:]=pairs.T if transposed else pairs
            ds[n+'_bnds'].units='Pa' if n=='ap' else '1'
            if backup:ds.createVariable('fix_original_'+n,'f8',('lev',))[:]=ref[79][n]
    return ref


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('transposed',[False,True])
@pytest.mark.parametrize('backup',[False,True])
def test_normalization_preserves_coefficients_and_science(tmp_path,monkeypatch,fmt,transposed,backup):
    p=tmp_path/'hybrid.nc';ref=hybrid_file(p,fmt,transposed,backup)
    with netCDF4.Dataset(p) as ds:
        original={n:ds[n][:].tobytes() for n in ('ap','b','ap_bnds','b_bnds','ps','ta')}
    exercise(p,tmp_path,[Step('C102','normalize_hybrid_coordinate',{}),
                         Step('C103b','normalize_hybrid_coordinate',{})],monkeypatch,int(backup or transposed))
    with netCDF4.Dataset(p) as ds:
        expected=ref[79]['ap']/101325+ref[79]['b']
        interfaces=ref[80]['ap']/101325+ref[80]['b']
        pairs=np.column_stack((interfaces[:-1],interfaces[1:]))
        np.testing.assert_array_equal(ds['lev'][:],expected)
        np.testing.assert_array_equal(ds['lev_bnds'][:],pairs)
        assert ds['lev'].bounds=='lev_bnds' and ds['lev'].units=='1'
        assert ds['lev_bnds'].formula_terms=='ap: ap_bnds b: b_bnds ps: ps'
        assert 'units' not in ds['lev_bnds'].ncattrs()
        assert np.all((pairs.min(axis=1)<=expected)&(expected<=pairs.max(axis=1)))
        for n,raw in original.items():
            if transposed and n.endswith('_bnds'):
                raw = np.frombuffer(raw, dtype='f8').reshape(2,79).T.tobytes()
            assert ds[n][:].tobytes()==raw,n
        assert 'bounds' not in ds['ap'].ncattrs() and 'bounds' not in ds['b'].ncattrs()
        assert not any(n.startswith('fix_original_') for n in ds.variables)


@pytest.mark.parametrize('reverse',[False,True])
def test_normalization_composes_with_existing_coefficient_metadata_fix(tmp_path,monkeypatch,reverse):
    p=tmp_path/'hybrid.nc';hybrid_file(p)
    steps=[Step('C074a','fix_hybrid_coefficients',{'coordinate':'alevel'}),Step('C102','normalize_hybrid_coordinate',{})]
    if reverse:steps.reverse()
    exercise(p,tmp_path,steps,monkeypatch,0)


@pytest.mark.parametrize('bad',['bounds','lev','packing'])
def test_invalid_hybrid_normalization_aborts_before_writing(tmp_path,bad):
    p=tmp_path/'hybrid.nc';hybrid_file(p)
    with netCDF4.Dataset(p,'a') as ds:
        if bad=='bounds':ds['ap_bnds'].scale_factor=2.
        elif bad=='lev':ds['lev'][:]=42
        else:ds['lev'].scale_factor=2.
    before=p.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(FixPlan('t','s',{str(p):[Step('C102','normalize_hybrid_coordinate',{})]}),tmp_path/'log')
    assert p.read_bytes()==before


@pytest.mark.parametrize('product',['siareaacrossline','simassacrossline','sisnmassacrossline'])
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('reverse',[False,True])
def test_ice_sector_resize_composes_with_names_strlen_and_filename(tmp_path,monkeypatch,product,fmt,reverse):
    name=f'{product}_tavg-u-ht-u_mon_glb_g112_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1_185001-185912.nc'
    p=tmp_path/name
    with netCDF4.Dataset(p,'w',format=fmt) as ds:
        ds.setncatts(dict(source_id='IPSL-CM6-ESMCO2',experiment_id='esm-piControl-spinup',variant_label='r1i1p1f1',
                         variable_id=product,frequency='mon',grid_label='g014',region='glb',horizontal_label='ht',
                         branded_variable=product+'_tavg-u-ht-u'))
        ds.createDimension('time',None);ds.createDimension('line',4);ds.createDimension('str_len',255)
        for n in ('line','sector'):
            v=ds.createVariable(n,'S1',('line','str_len'));v[:]=np.full((4,255),b'\1',dtype='S1');v.standard_name='region'
        ds.createVariable(product,'f4',('time','line'))[:]=np.arange(8).reshape(2,4);ds[product].coordinates='sector line'
    steps=[Step('C099','rename_redundant_text_label',{'var':'line'}),
           Step('C087','rename_scalar_strlen',{'coordinate':'sector'}),
           Step('C105','fix_ice_passage_sector',{}),
           Step('C097','sync_filename_grid_label',{'source':'g112','target':'g014'})]
    if reverse:steps.reverse()
    target=exercise(p,tmp_path,steps,monkeypatch,1)
    with netCDF4.Dataset(target) as ds:
        ds.set_auto_chartostring(False)
        assert set(ds.variables)=={product,'sector'}
        assert set(ds.dimensions)=={'time','line','strlen'} and len(ds.dimensions['strlen'])==21
        assert [row.tobytes().decode().rstrip() for row in ds['sector'][:]]==list(ICE_PASSAGES)
        assert ds[product].coordinates=='sector'
        np.testing.assert_array_equal(ds[product][:],np.arange(8).reshape(2,4))


@pytest.mark.parametrize('units',['microne','micron','m'])
def test_radius_is_converted_once_in_place(tmp_path,monkeypatch,units):
    p=tmp_path/'radius.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='jpdftaureicemodis';ds.createDimension('effectRadIc',6)
        ds.createVariable('effectRadIc','f8',('effectRadIc',))[:]=ICE_RADII*1e-6;ds['effectRadIc'].units=units
        ds.createVariable('jpdftaureicemodis','i2',('effectRadIc',))[:]=np.arange(6)
    exercise(p,tmp_path,[Step('C095a','convert_ice_radius',{}),Step('C105a','convert_ice_radius',{})],monkeypatch,0)
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds['effectRadIc'][:],ICE_RADII)
        np.testing.assert_array_equal(ds['jpdftaureicemodis'][:],np.arange(6))


def test_unapproved_radius_values_are_not_replaced(tmp_path):
    p=tmp_path/'radius.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='jpdftaureicemodis';ds.createDimension('effectRadIc',6)
        ds.createVariable('effectRadIc','f8',('effectRadIc',))[:]=np.arange(6)*1e-6;ds['effectRadIc'].units='micron'
    for units,values in [('micron',np.arange(6)*1e-6),('m',ICE_RADII)]:
        with netCDF4.Dataset(p,'a') as ds:
            ds['effectRadIc'][:]=values;ds['effectRadIc'].units=units
        before=p.read_bytes()
        with pytest.raises(ValueError):
            apply_plan(FixPlan('t','s',{str(p):[Step('C105a','convert_ice_radius',{})]}),tmp_path/'log')
        assert p.read_bytes()==before


THERMAL_NODES=np.array([.0004887585535,.001955034214,.005865102642,.013685239498,.02932551321,
 .060606060634,.123167155482,.248289345178,.49853372457,.999022483354,1.749755620608,2.500488758784,
 3.501466276352,5.503421311488,9.50733138176,17.515151522304,33.530791803392,65.562072365568])


def test_thermal_bounds_use_supplied_model_scheme_and_keep_node_values(tmp_path,monkeypatch):
    p=tmp_path/'thermal.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='tsl';ds.createDimension('depth',18)
        ds.createVariable('depth','f8',('depth',))[:]=THERMAL_NODES;ds['depth'].units='m'
        ds.createVariable('tsl','f4',('depth',))[:]=np.arange(18)+270.
    exercise(p,tmp_path,[Step('C103a','add_reviewed_coordinate_bounds',{'coordinate':'depth'})],monkeypatch,0)
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds['depth'][:],THERMAL_NODES)
        bounds=ds['depth_bnds'][:]
        assert bounds.shape==(18,2) and bounds[10,1]==2. and bounds[-1,1]==90.
        assert np.all(bounds[:,0]<=THERMAL_NODES) and np.all(THERMAL_NODES<=bounds[:,1])


@pytest.mark.parametrize('units,factor',[('Pa',1.),('hPa',100.),('mb',100.)])
def test_plev7c_explicit_dr_edges_follow_parent_units(tmp_path,monkeypatch,units,factor):
    p=tmp_path/'pressure.nc';centers=np.array([90000.,74000.,62000.,50000.,37500.,24500.,9000.])
    edges=np.array([100000.,80000.,68000.,56000.,44000.,31000.,18000.,0.])
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.branded_variable='clisccp_tavg-p7c-hxy-air';ds.createDimension('plev',7)
        ds.createVariable('plev','f8',('plev',))[:]=centers/factor;ds['plev'].units=units
        ds.createVariable('clisccp','f4',('plev',))[:]=np.arange(7)
    exercise(p,tmp_path,[Step('C103c','add_reviewed_coordinate_bounds',{'coordinate':'plev'})],monkeypatch,0)
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds['plev'][:],centers/factor)
        np.testing.assert_array_equal(ds['plev_bnds'][:],np.column_stack((edges[:-1],edges[1:]))/factor)


COMMENT_SPECS=json.loads((Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/comment_specs.json').read_text())
@pytest.mark.parametrize('brand',[b for b,s in COMMENT_SPECS.items() if s.get('replace_expected')])
def test_authorized_scientific_comment_replacement_is_exact_and_idempotent(tmp_path,monkeypatch,brand):
    spec=COMMENT_SPECS[brand];var=spec['var'];variant=spec['variants'][0];p=tmp_path/'comment.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.branded_variable=brand;ds.createDimension('t',2)
        ds.createVariable(var,'f4',('t',))[:]=[11,22];ds[var].comment=variant['observed']
    exercise(p,tmp_path,[Step('C112','fix_registry_comment',{'var':var,'replace_expected':True})],monkeypatch,0)
    with netCDF4.Dataset(p) as ds:
        assert ds[var].comment==variant['value'];np.testing.assert_array_equal(ds[var][:],[11,22])


@pytest.mark.parametrize('coordinate',['depth','plev'])
def test_unapproved_bound_centers_abort_without_changes(tmp_path,coordinate):
    p=tmp_path/'bad.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.variable_id='tsl'
        ds.branded_variable='clisccp_tavg-p7c-hxy-air'
        n=18 if coordinate=='depth' else 7
        ds.createDimension(coordinate,n)
        ds.createVariable(coordinate,'f8',(coordinate,))[:]=np.arange(n)+1
        ds[coordinate].units='m' if coordinate=='depth' else 'Pa'
    before=p.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(FixPlan('t','s',{str(p):[Step('C103','add_reviewed_coordinate_bounds',{'coordinate':coordinate})]}),tmp_path/'log')
    assert p.read_bytes()==before


@pytest.mark.parametrize('brand',[b for b,s in COMMENT_SPECS.items() if s.get('replace_expected')])
def test_changed_comment_content_is_not_silently_overwritten(tmp_path,brand):
    spec=COMMENT_SPECS[brand];var=spec['var'];p=tmp_path/'changed.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.branded_variable=brand;ds.createVariable(var,'f4').comment='Different content since the QA snapshot'
    before=p.read_bytes()
    with pytest.raises(ValueError,match='differs'):
        apply_plan(FixPlan('t','s',{str(p):[Step('C112','fix_registry_comment',{'var':var,'replace_expected':True})]}),tmp_path/'log')
    assert p.read_bytes()==before


def test_actual_report_classifies_all_approved_variants_including_conventional_latitude_bounds():
    from esgf_fix_tool.report import QAReport
    from esgf_fix_tool.ruleset import RuleSet
    from esgf_fix_tool.classify import classify, Outcome
    root=Path('/Users/glipsl/Desktop/QA_results_v3_fixed')
    if not root.is_dir():pytest.skip('Local QA fixture is unavailable')
    report=QAReport.load(next(p for p in root.glob('qa_result_*.json') if '.cluster.' not in p.name))
    c=classify(report,RuleSet.load(Path(__file__).parents[1]/
                                    'src/esgf_fix_tool/rules/post_apply.yaml'))
    counts={'C102':62,'C103a':2,'C103b':56,'C103c':1,'C105':3,'C105a':1,'C112':5}
    for rid,count in counts.items():
        found=[item for item in c.items if item.match and item.match.rule.id==rid]
        assert len({item.finding.path for item in found})==count,rid
        assert all(item.outcome is Outcome.FIX for item in found),rid
    lat=[item for item in c.items if item.match and item.match.rule.id=='C103']
    assert len({item.finding.path for item in lat})==8
    assert all(item.outcome is Outcome.FIX for item in lat)
