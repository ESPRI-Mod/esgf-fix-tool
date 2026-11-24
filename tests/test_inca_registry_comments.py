"""Extend the existing audited CV replacement to the INCA chemistry products."""
import json
from pathlib import Path

import netCDF4
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

BRANDS = ['c2h6_tavg-al-hxy-u', 'c3h6_tavg-al-hxy-u', 'c3h8_tavg-al-hxy-u', 'ch3coch3_tavg-al-hxy-u', 'ch4_tavg-al-hxy-u', 'ch4_tavg-p19-hxy-air', 'ch4_tavg-p39-hy-air', 'ch4_tclm-p19-hxy-air', 'co_tavg-al-hxy-u', 'dms_tavg-al-hxy-u', 'emioa_tavg-u-hxy-u', 'hcho_tavg-al-hxy-u', 'hno3_tavg-al-hxy-u', 'hno3_tavg-p39-hy-air', 'ho2_tavg-p39-hy-air', 'mmraerh2o_tavg-al-hxy-u', 'mmroa_tavg-al-hxy-u', 'n2o_tavg-al-hxy-u', 'n2o_tavg-p19-hxy-air', 'n2o_tavg-p39-hy-air', 'n2o_tclm-p19-hxy-air', 'no2_tavg-al-hxy-u', 'no_tavg-al-hxy-u', 'o3_tavg-al-hxy-u', 'o3_tavg-p19-hxy-air', 'o3_tavg-p39-hy-air', 'o3_tclm-p19-hxy-air', 'o3loss_tavg-al-hxy-u', 'o3prod_tavg-al-hxy-u', 'oh_tavg-al-hxy-u', 'oh_tavg-p39-hy-air', 'pan_tavg-al-hxy-u', 'photo1d_tavg-al-hxy-u', 'so2_tavg-al-hxy-u', 'wetso4_tavg-u-hxy-u']

@pytest.mark.parametrize('brand',BRANDS)
def test_all_audited_chemistry_comments_select_the_shared_recipe(tmp_path,monkeypatch,brand):
    specs=json.loads((Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/comment_specs.json').read_text())
    entry=specs[brand];var=entry['var'];variant=entry['variants'][-1]
    observed,value=variant['observed'],variant['value']
    path=tmp_path/'comment.nc'
    with netCDF4.Dataset(path,'w') as ds:
        ds.variable_id=var;ds.branded_variable=brand
        v=ds.createVariable(var,'f4');v.assignValue(7);v.comment=observed;v.long_name='Preserve other metadata'
    message=f"Expected '{observed}' to contain '{value}' from registry key 'comment'."
    finding=Finding(1,f"[CMIP7] [ATTR004] Variable '{var}' attribute 'comment' registry expected-term check",message,
                    f"CMIP7.CHM.{var}.{brand.split('_',1)[1]}.mon.glb.g111.AnyModel.historical.r1i1p1f1",str(path))
    result=classify(QAReport(Path('qa.json'),{},[finding]),RuleSet.load(*default_rule_paths()))
    assert result.items[0].match.rule.id=='C112'
    p=FixPlan.from_classification(result)
    def refuse(*args,**kwargs):raise AssertionError('Comment replacement must not rewrite scientific data')
    monkeypatch.setattr(Batch,'write',refuse)
    original=path.read_bytes();inode=path.stat().st_ino
    apply_plan(p,tmp_path/'dry',dry_run=True);assert path.read_bytes()==original
    apply_plan(p,tmp_path/'apply')
    with netCDF4.Dataset(path) as ds:
        assert ds[var].comment==value and ds[var][...]==7
        assert ds[var].long_name=='Preserve other metadata'
    assert path.stat().st_ino==inode
    fixed=path.read_bytes()
    assert apply_plan(p,tmp_path/'again')['changed']==0 and path.read_bytes()==fixed
