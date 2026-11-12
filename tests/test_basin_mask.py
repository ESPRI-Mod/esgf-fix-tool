"""Approved 2-D region indices, distinct from the unresolved three-basin axes."""
import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.fixes.qa_metadata import BASIN_MASK_MEANINGS
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet


def make(path, fmt='NETCDF4', dtype='f4'):
    data = (np.arange(332*362).reshape(332,362) % 11).astype(dtype)
    missing = np.zeros(data.shape, dtype=bool)
    missing[0,0] = missing[-1,-1] = True
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.setncatts(dict(source_id='IPSL-CM6-ESMCO2', variable_id='basin',
                         branded_variable='basin_ti-u-hxy-u', grid_label='g112'))
        ds.createDimension('y',332); ds.createDimension('x',362)
        fill = np.float32(1e20) if dtype == 'f4' else np.int32(-9999)
        v = ds.createVariable('basin',dtype,('y','x'), fill_value=fill, zlib=fmt.startswith('NETCDF4'))
        v.standard_name='region'; v.long_name='Region Selection Index'
        v.missing_value=fill; v.coordinates='latitude longitude'; v.history='remove'
        v[:] = np.ma.array(data,mask=missing)
        for name in ('latitude','longitude'):
            ds.createVariable(name,'f8',('y','x'))[:] = np.arange(data.size).reshape(data.shape)
    return data, missing


def plan(path):
    return FixPlan('test','synthetic',{str(path):[
        Step('C101','convert_basin_region_mask',{}),
        Step('C001','drop_var_attr',{'var':'basin','attr':'history'})]})


@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_mask_conversion_preserves_codes_missing_points_and_other_arrays(tmp_path,monkeypatch,fmt):
    p=tmp_path/'mask.nc'; expected, mask=make(p,fmt)
    with netCDF4.Dataset(p) as ds:
        original={n:ds[n][:].tobytes() for n in ('latitude','longitude')}
        compression=ds['basin'].filters(); chunks=ds['basin'].chunking()
    before=p.read_bytes(); q=plan(p)
    assert apply_plan(q,tmp_path/'dry.jsonl',dry_run=True)['changed']==1
    assert p.read_bytes()==before
    calls=[]; write=Batch.write
    def record(batch,output):
        calls.append(output)
        return write(batch,output)
    monkeypatch.setattr(Batch,'write',record)
    assert apply_plan(q,tmp_path/'apply.jsonl')['changed']==1 and len(calls)==1
    with netCDF4.Dataset(p) as ds:
        v=ds['basin']; actual=v[:]
        assert v.dtype==np.dtype('i4') and v.dimensions==('y','x')
        assert v.flag_values.dtype==np.dtype('i4')
        np.testing.assert_array_equal(v.flag_values,np.arange(11,dtype='i4'))
        assert v.flag_meanings==BASIN_MASK_MEANINGS
        assert 'units' not in v.ncattrs() and 'history' not in v.ncattrs()
        assert np.asarray(v._FillValue).dtype==np.dtype('i4')
        assert np.asarray(v.missing_value).dtype==np.dtype('i4')
        np.testing.assert_array_equal(np.ma.getmaskarray(actual),mask)
        np.testing.assert_array_equal(actual[~mask],expected[~mask])
        assert v.filters()==compression and v.chunking()==chunks
        assert set(ds.variables)=={'basin','latitude','longitude'}
        for n,raw in original.items():assert ds[n][:].tobytes()==raw
    after=p.read_bytes(); inode=p.stat().st_ino
    assert apply_plan(q,tmp_path/'replay.jsonl')['changed']==0
    assert p.read_bytes()==after and p.stat().st_ino==inode and len(calls)==1
    assert json.loads((tmp_path/'apply.jsonl').read_text())['rewrite_count']==1


def test_existing_int32_mask_only_needs_attributes_in_place(tmp_path,monkeypatch):
    p=tmp_path/'mask.nc';make(p,dtype='i4');inode=p.stat().st_ino
    def forbidden(*args):raise AssertionError('Integer mask was unnecessarily rewritten')
    monkeypatch.setattr(Batch,'write',forbidden)
    assert apply_plan(plan(p),tmp_path/'log')['changed']==1
    assert p.stat().st_ino==inode
    assert apply_plan(plan(p),tmp_path/'replay')['changed']==0


@pytest.mark.parametrize('bad',[2.5,11.,-1.,float('inf'),float('nan')])
def test_invalid_codes_abort_before_any_write(tmp_path,bad):
    p=tmp_path/'mask.nc';make(p)
    with netCDF4.Dataset(p,'a') as ds:ds['basin'][1,1]=bad
    before=p.read_bytes()
    with pytest.raises(ValueError,match='codes outside'):
        apply_plan(plan(p),tmp_path/'log')
    assert p.read_bytes()==before


def test_three_basin_axis_is_not_reinterpreted(tmp_path):
    p=tmp_path/'axis.nc'
    with netCDF4.Dataset(p,'w') as ds:
        ds.createDimension('basin',3)
        v=ds.createVariable('basin','f8',('basin',));v[:]=[1,2,3]
        v.standard_name='region'; v.long_name='Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)'
    before=p.read_bytes()
    with pytest.raises(ValueError,match='reviewed 2-D'):
        apply_plan(plan(p),tmp_path/'log')
    assert p.read_bytes()==before


def test_actual_report_selects_one_mask_and_distinct_sector_fixes_for_nine_axes():
    root=Path('/Users/glipsl/Desktop/QA_results_v3_fixed')
    if not root.is_dir():pytest.skip('Local QA fixture not available')
    report=next(p for p in root.glob('qa_result_*.json') if '.cluster.' not in p.name)
    rules=RuleSet.load(Path(__file__).parents[1]/'src/esgf_fix_tool/rules/post_apply.yaml')
    result=classify(QAReport.load(report),rules)
    matches=[item for item in result.items if item.match and item.match.rule.id in ('C101','C101a','C096')]
    mask=[item for item in matches if item.match.rule.id=='C101']
    axes=[item for item in matches if item.match.rule.id=='C101a']
    assert len({item.finding.path for item in mask})==1
    assert len({item.finding.path for item in axes})==9
    assert all(item.match.rule.fix=='fix_basin_sector' for item in matches if item.match.rule.id!='C101')
    selected=FixPlan.from_classification(result)
    selected.files={p:[s for s in steps if s.rule=='C101'] for p,steps in selected.files.items()}
    selected.files={p:steps for p,steps in selected.files.items() if steps}
    assert len(selected.files)==1
    assert [s.rule for steps in selected.files.values() for s in steps]==['C101']
