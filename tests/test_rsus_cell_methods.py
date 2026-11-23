"""The same reviewed registry text applies on regular and icosahedral grids."""

import json

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan

OLD = 'area: mean where land time: mean'
NEW = 'area: time: mean where land'


@pytest.mark.parametrize('grid', ['g111', 'g243', 'g245'])
@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
def test_c083_selects_and_updates_rsus_without_rewriting_science(tmp_path, monkeypatch, grid, fmt):
    path = tmp_path/'rsus.nc'
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.variable_id = 'rsus'
        ds.createDimension('time', 2)
        ds.createDimension('cell', 3)
        v = ds.createVariable('rsus', 'f4', ('time','cell'), fill_value=1e20, zlib=True, complevel=2)
        v[:] = np.ma.array(np.arange(6).reshape(2,3), mask=[[1,0,0],[0,0,0]])
        v.cell_methods = OLD
        before_values = v[:].copy()
        before_attrs = {a:v.getncattr(a) for a in v.ncattrs() if a!='cell_methods'}
        filters, chunks = v.filters(), v.chunking()
    dataset = f'CMIP7.SRF.rsus.tavg-u-hxy-lnd.mon.glb.{grid}.ANOTHER_MODEL.historical.r1i1p1f1 (1 file affected)'
    report = tmp_path/'qa.json'
    check = "[CMIP7] [ATTR004] Variable 'rsus' attribute 'cell_methods' registry expected-term check"
    message = f"Expected '{OLD}' to equal '{NEW}' from registry key 'cell_methods'."
    report.write_text(json.dumps({'fail':{'1':{check:{message:{dataset:[str(path)]}}}}}))
    output = tmp_path/'plan.json'
    result = CliRunner().invoke(app, ['plan',str(report),'-o',str(output)])
    assert result.exit_code==0, result.output
    plan = FixPlan.read(output)
    assert [s.rule for s in plan.files[str(path)]] == ['C083']

    def refuse(*args, **kwargs):
        raise AssertionError('An attribute update must not rewrite the main matrix')

    monkeypatch.setattr(Batch,'write',refuse)
    original, inode = path.read_bytes(), path.stat().st_ino
    assert apply_plan(plan,tmp_path/'dry',dry_run=True)['changed']==1
    assert path.read_bytes()==original
    assert apply_plan(plan,tmp_path/'log')['changed']==1
    with netCDF4.Dataset(path) as ds:
        v=ds['rsus']
        assert v.cell_methods==NEW
        assert {a:v.getncattr(a) for a in v.ncattrs() if a!='cell_methods'}==before_attrs
        assert v.filters()==filters and v.chunking()==chunks
        np.testing.assert_array_equal(v[:].data,before_values.data)
        np.testing.assert_array_equal(np.ma.getmaskarray(v[:]),np.ma.getmaskarray(before_values))
    assert path.stat().st_ino==inode
    fixed=path.read_bytes()
    assert apply_plan(plan,tmp_path/'again')['changed']==0
    assert path.read_bytes()==fixed
