"""Follow-up regressions: compose fixes without changing science arrays or labels."""
import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.fixes.hybrid import _coefficient_reference


def run(path, tmp, steps):
    plan = FixPlan('test', 'synthetic', {str(path): steps})
    original = path.read_bytes()
    assert apply_plan(plan, tmp/'dry.jsonl', dry_run=True)['changed'] == 1
    assert path.read_bytes() == original
    assert apply_plan(plan, tmp/'apply.jsonl')['changed'] == 1
    return plan


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('old,new', [('typebare','bare_ground'), ('typec3pft','c3_plant_functional_types'),
                                   ('typec4pft','c4_plant_functional_types')])
@pytest.mark.parametrize('reverse', [False, True])
def test_label_edit_composes_with_strlen_rename(tmp_path, fmt, old, new, reverse):
    path = tmp_path/'file.nc'
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.createDimension('str_len',255)
        ds.createVariable('type','S1',('str_len',))[:] = np.frombuffer(old.encode().ljust(255,b'\0'),dtype='S1')
    steps = [Step('C058','fix_scalar_label',dict(var='type',expected=old,value=new)),
             Step('C087','rename_scalar_strlen',dict(coordinate='type'))]
    if reverse: steps.reverse()
    plan = run(path,tmp_path,steps)
    with netCDF4.Dataset(path) as ds:
        assert ds['type'].dimensions == ('strlen',)
        assert ds['type'][:].tobytes() == new.encode().ljust(255,b' ')
    assert apply_plan(plan,tmp_path/'replay.jsonl')['changed'] == 0


def grid_file(path):
    with netCDF4.Dataset(path, 'w') as ds:
        ds.setncatts(dict(source_id='IPSL-CM6-ESMCO2',experiment_id='esm-piControl-spinup',
                         variant_label='r1i1p1f1',variable_id='siarea',frequency='mon',
                         grid_label='g011',region='nh',horizontal_label='hm',branded_variable='siarea_tavg-u-hm-u'))
        ds.createDimension('time',2)
        ds.createVariable('siarea','i2',('time',))[:] = [2,3]


def test_filename_sync_is_in_place_and_replayable(tmp_path):
    name='siarea_tavg-u-hm-u_mon_nh_g112_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1_185001-185912.nc'
    path=tmp_path/name;grid_file(path)
    original=path.read_bytes();inode=path.stat().st_ino
    plan=run(path,tmp_path,[Step('C097','sync_filename_grid_label',dict(source='g112',target='g011'))])
    target=tmp_path/name.replace('_g112_','_g011_')
    assert not path.exists() and target.stat().st_ino == inode and target.read_bytes() == original
    assert target.stat().st_nlink == 1
    record=json.loads((tmp_path/'apply.jsonl').read_text())
    assert record['final_file'] == str(target) and record['filename_renamed']
    assert record['rewrite_count'] == 0 and record['write_mode'] == 'rename-only'
    assert apply_plan(plan,tmp_path/'replay.jsonl',only_under=tmp_path)['changed'] == 0
    assert target.read_bytes() == original


def test_filename_collision_aborts_before_other_files_are_written(tmp_path):
    path=tmp_path/'siarea_tavg-u-hm-u_mon_nh_g112_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1_185001-185912.nc'
    grid_file(path)
    target=path.with_name(path.name.replace('_g112_','_g011_'));target.write_bytes(b'existing')
    first=tmp_path/'first.nc'
    with netCDF4.Dataset(first,'w') as ds:
        ds.createVariable('v','i4').history='keep'
    before=first.read_bytes(),path.read_bytes()
    plan=FixPlan('test','synthetic',{
        str(first):[Step('C001','drop_var_attr',dict(var='v',attr='history'))],
        str(path):[Step('C097','sync_filename_grid_label',dict(source='g112',target='g011'))]})
    with pytest.raises(FileExistsError):apply_plan(plan,tmp_path/'log')
    assert before==(first.read_bytes(),path.read_bytes()) and target.read_bytes()==b'existing'


@pytest.mark.parametrize('reverse',[False,True])
def test_text_padding_names_and_strlen_preserve_label_order(tmp_path,reverse):
    p=tmp_path/'labels.nc';labels=['drake_passage','bering_strait','barents_opening']
    with netCDF4.Dataset(p,'w') as ds:
        ds.createDimension('line',3);ds.createDimension('str_len',255);ds.createDimension('time',2)
        a=np.full((3,255),b'\0',dtype='S1')
        for row,label in zip(a,labels):row[:len(label)]=np.frombuffer(label.encode(),dtype='S1')
        for n in ('line','sector'):
            v=ds.createVariable(n,'S1',('line','str_len'));v[:]=a;v.setncattr('name',n)
        v=ds.createVariable('mfo','i2',('time','line'));v[:]=[[11,12,13],[21,22,23]];v.coordinates='sector line'
    steps=[Step('C100','normalize_text_padding',{}),Step('C099','rename_redundant_text_label',dict(var='line')),
           Step('C087','rename_scalar_strlen',dict(coordinate='sector'))]
    if reverse:steps.reverse()
    plan=run(p,tmp_path,steps)
    with netCDF4.Dataset(p) as ds:
        assert ds['sector'].dimensions==('line','strlen')
        assert 'line' not in ds.variables and 'line_label' not in ds.variables
        assert [row.tobytes().decode().rstrip(' ') for row in ds['sector'][:]]==labels
        assert ds['mfo'].coordinates=='sector'
        np.testing.assert_array_equal(ds['mfo'][:],[[11,12,13],[21,22,23]])
    assert apply_plan(plan,tmp_path/'replay.jsonl')['changed']==0


def test_control_characters_are_rejected_without_partial_writes(tmp_path):
    p=tmp_path/'bad.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.createDimension('line',1);ds.createDimension('str_len',255)
        ds.createVariable('sector','S1',('line','str_len'))[:]=np.full((1,255),b'\1',dtype='S1')
    original=p.read_bytes()
    with pytest.raises(ValueError,match='Corrupted'):
        apply_plan(FixPlan('test','s',{str(p):[Step('C100','normalize_text_padding',{})]}),tmp_path/'log')
    assert p.read_bytes()==original


def test_new_text_alias_uses_space_padding_and_preserves_source_labels(tmp_path):
    p=tmp_path/'new_alias.nc';label='bering_strait'
    with netCDF4.Dataset(p,'w') as ds:
        ds.variable_id='mfo';ds.createDimension('oline',1);ds.createDimension('str_len',255)
        raw=label.encode().ljust(255,b'\0')
        v=ds.createVariable('line','S1',('oline','str_len'));v[:]=np.frombuffer(raw,dtype='S1').reshape(1,255);v.units='1'
        ds.createVariable('mfo','f4',('oline',))[:]=[12.]
    plan=run(p,tmp_path,[Step('C034','add_text_coordinate_alias',dict(source='line',target='sector'))])
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        assert ds['line'][:].tobytes()==raw
        assert ds['sector'][:].tobytes()==label.encode().ljust(255,b' ')
        assert 'units' not in ds['sector'].ncattrs()
    assert apply_plan(plan,tmp_path/'log')['changed']==0


@pytest.mark.parametrize('name',['olevel','lev'])
def test_ocean_depth_identity_keeps_values_and_science_in_place(tmp_path,name):
    p=tmp_path/'ocean.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.vertical_label='ol';ds.variable_id='thetao'
        ds.createDimension(name,3);ds.createDimension('time',2)
        v=ds.createVariable(name,'f8',(name,));v[:]=[.5,20,1000];v.units='m';v.positive='down'
        ds.createDimension('bnds',2)
        v.bounds='olevel_bounds'
        b=ds.createVariable('olevel_bounds','f8',(name,'bnds'));b[:]=[[0,1],[1,50],[50,2000]];b.units='m'
        ds.createVariable('thetao','i2',('time',name))[:]=[[1,2,3],[4,5,6]]
    inode=p.stat().st_ino
    plan=run(p,tmp_path,[Step('C020','fix_ocean_vertical_metadata',{})])
    assert p.stat().st_ino==inode
    with netCDF4.Dataset(p) as ds:
        assert ds['lev'].standard_name=='depth' and ds['lev'].axis=='Z'
        assert ds['lev'].bounds=='lev_bnds' and 'units' not in ds['lev_bnds'].ncattrs()
        np.testing.assert_array_equal(ds['lev_bnds'][:],[[0,1],[1,50],[50,2000]])
        np.testing.assert_array_equal(ds['lev'][:],[.5,20,1000])
        np.testing.assert_array_equal(ds['thetao'][:],[[1,2,3],[4,5,6]])
    assert apply_plan(plan,tmp_path/'replay.jsonl')['changed']==0


def test_eighty_interface_coefficients_use_reference_without_normalizing_lev(tmp_path):
    p=tmp_path/'interfaces.nc';ref=_coefficient_reference();pressure=ref[80]['ap']+101325*ref[80]['b']
    with netCDF4.Dataset(p,'w') as ds:
        ds.source_id='IPSL-CM6-ESMCO2';ds.realm='atmos';ds.variable_id='rld'
        ds.createDimension('lev',80);ds.createDimension('klev',79);ds.createDimension('time',2)
        v=ds.createVariable('lev','f8',('lev',));v[:]=pressure;v.units='Pa'
        for n in ('ap','b'):ds.createVariable(n,'f8',('klev',))[:]=ref[79][n]
        ps=ds.createVariable('ps','f4',('time',));ps[:]=[1e5,9e4];ps.units='Pa'
        ds.createVariable('rld','i2',('time','lev'))[:]=np.arange(160).reshape(2,80)
    inode=p.stat().st_ino
    plan=run(p,tmp_path,[Step('C074','fix_hybrid_coefficients',dict(coordinate='alevhalf'))])
    assert p.stat().st_ino!=inode
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds['lev'][:],pressure)
        for n in ('ap','b'):
            assert ds[n].dimensions==('lev',) and 'bounds' not in ds[n].ncattrs()
            np.testing.assert_array_equal(ds[n][:],ref[80][n])
        assert 'lev_bnds' not in ds.variables
    assert apply_plan(plan,tmp_path/'replay.jsonl')['changed']==0


def test_unapproved_latitude_bounds_remain_in_review(tmp_path):
    from esgf_fix_tool.classify import classify,Outcome
    from esgf_fix_tool.report import QAReport,Finding
    from esgf_fix_tool.rules import RuleSet
    rules=RuleSet.load(Path(__file__).parents[1]/'rules/post_apply.yaml')
    findings=[Finding(3,'[CMIP7] [COORD008] Coordinate bounds',
                      "It is required for 'lat' to have a 'bounds' attribute according to its coordinate definition.",'dataset','file.nc')]
    result=classify(QAReport(Path('s'),{},findings),rules)
    assert result.items[0].outcome is Outcome.REVIEW
    assert not FixPlan.from_classification(result).files
