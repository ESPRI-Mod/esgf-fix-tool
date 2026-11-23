"""Repair domain captions without touching injected coordinate/scientific values."""

import netCDF4
import pytest
from test_nemo_u_coordinates import make as make_u
from test_nemo_vt_coordinates import make as make_vt

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


@pytest.mark.parametrize('var,point,grid', [('sfx','U','g168'), ('sfy','V','g169')])
@pytest.mark.parametrize('canonical', [False,True])
@pytest.mark.parametrize('method', ['C086','C119'])
def test_old_injected_captions_are_repaired_in_place(tmp_path, monkeypatch, var, point, grid, canonical, method):
    path=tmp_path/'file.nc'
    if var=='sfx':
        make_u(path,present=('nav_lat','nav_lon'),canonical=canonical)
    else:
        make_vt(path,var,present=True,canonical=canonical)
    names=('latitude','longitude') if canonical else ('nav_lat','nav_lon')
    findings=[]
    with netCDF4.Dataset(path,'a') as ds:
        for name,role in zip(names,('latitude','longitude')):
            old=f'{role.title()} at NEMO {point} points'
            ds[name].long_name=old
            findings.append(Finding(1,'[CMIP7] [COORD004] Coordinate metadata recommendations',
                f"'{name}' long_name='{old}'; the recommended value is '{role}'.",
                f'CMIP7.OCE.{var}.tavg-u-hxy-sea.mon.glb.{grid}.AnyModel.historical.r1i1p1f1 (1 file affected)',str(path)))
        ds.set_auto_maskandscale(False)
        before={n:(v[:].tobytes(),v.filters(),v.chunking()) for n,v in ds.variables.items()}
    if method=='C086':
        result=classify(QAReport(None,{},findings),RuleSet.load(*default_rule_paths()))
        assert all(i.match.rule.id=='C086' for i in result.items)
        plan=FixPlan.from_classification(result)
    else:
        plan=FixPlan('test','test',{str(path):[Step('C119','inject_nemo_u_coordinates',{})]})

    def refuse(*args,**kwargs):
        raise AssertionError('A coordinate long_name update must not rewrite the file')

    monkeypatch.setattr(Batch,'write',refuse)
    original,inode=path.read_bytes(),path.stat().st_ino
    assert apply_plan(plan,tmp_path/'dry',dry_run=True)['changed']==1
    assert path.read_bytes()==original
    assert apply_plan(plan,tmp_path/'log')['changed']==1
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        assert ds[names[0]].long_name=='latitude'
        assert ds[names[1]].long_name=='longitude'
        for name,(values,filters,chunks) in before.items():
            assert ds[name][:].tobytes()==values
            assert ds[name].filters()==filters and ds[name].chunking()==chunks
    assert path.stat().st_ino==inode
    fixed=path.read_bytes()
    assert apply_plan(plan,tmp_path/'again')['changed']==0
    assert path.read_bytes()==fixed
