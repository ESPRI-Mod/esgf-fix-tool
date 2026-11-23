"""The invalid auxiliary name is deleted in place, with no inferred replacement."""

import json

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('current', ['cell_fraction', 'area_fraction', None])
def test_stale_qa_only_deletes_the_exact_invalid_attribute(tmp_path, monkeypatch, fmt, current):
    path=tmp_path/'field.nc'
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.createDimension('cell',3)
        ds.history='Keep global history'
        for name in ('cell_fraction','science','other'):
            v=ds.createVariable(name,'f4',('cell',),zlib=True,complevel=2)
            v[:]=[0,0.5,1]
        ds['science'].standard_name='air_temperature'
        ds['other'].standard_name='cell_fraction'  # another variable is outside this recipe
        v=ds['cell_fraction']
        v.long_name='Keep the existing descriptive name'
        v.units='1'
        if current is not None:
            v.standard_name=current
        before={n:(v[:].tobytes(),v.filters(),v.chunking(),
                   {a:v.getncattr(a) for a in v.ncattrs() if a!='standard_name'})
                for n,v in ds.variables.items()}
    report=tmp_path/'qa.json'
    message=("standard_name cell_fraction is not defined in Standard Name Table v95. "
             "Possible close match(es): ['area_fraction', 'ocean_volume_fraction', 'cloud_area_fraction']")
    report.write_text(json.dumps({'fail':{'3':{'[CF-Conventions] §3.3 Standard Name':{
        message:{'CMIP7.ATM.va.tpt-al-hxy-u.6hr.glb.g245.AnyModel.historical.r1i1p1f1':[str(path)]}}}}}))
    out=tmp_path/'plan.json'

    def refuse(*args,**kwargs):
        raise AssertionError('Deleting standard_name must never rewrite the NetCDF')

    monkeypatch.setattr(Batch,'write',refuse)
    original,inode=path.read_bytes(),path.stat().st_ino
    result=CliRunner().invoke(app,['plan',str(report),'-o',str(out)])
    assert result.exit_code==0,result.output
    assert path.read_bytes()==original
    plan=FixPlan.read(out)
    if current!='cell_fraction':
        assert not plan.files
        assert '0 fixes à appliquer sur 0 fichiers' in result.output
        return
    assert [s.rule for s in plan.files[str(path)]]==['C011a']
    assert apply_plan(plan,tmp_path/'log')['changed']==1
    assert path.stat().st_ino==inode
    with netCDF4.Dataset(path) as ds:
        assert ds.history=='Keep global history'
        assert 'standard_name' not in ds['cell_fraction'].ncattrs()
        assert ds['other'].standard_name=='cell_fraction'
        assert ds['science'].standard_name=='air_temperature'
        for name,(raw,filters,chunks,attrs) in before.items():
            v=ds[name]
            assert v[:].tobytes()==raw
            assert v.filters()==filters and v.chunking()==chunks
            assert {a:v.getncattr(a) for a in v.ncattrs() if a!='standard_name'}==attrs
        np.testing.assert_array_equal(ds['cell_fraction'][:],[0,0.5,1])
    fixed=path.read_bytes()
    assert apply_plan(plan,tmp_path/'again')['changed']==0
    assert path.read_bytes()==fixed
