"""A stale 454-step QA plan can require changes to only four current files."""

import hashlib
import json

import netCDF4
import numpy as np
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.plan_preflight import verify_plan


def make(path, changed=True):
    with netCDF4.Dataset(path,'w') as ds:
        ds.createDimension('n',2)
        v=ds.createVariable('tas','i2',('n',),zlib=True,complevel=2)
        v[:]=[3,7]
        if changed:
            v.history='remove'
        ds.reviewed='different from expected'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_counts_four_changes_and_prunes_371_conforming_files_from_stale_qa(tmp_path, monkeypatch):
    paths=[tmp_path/f'file-{i:03}.nc' for i in range(375)]
    for i,path in enumerate(paths):
        make(path,changed=i<4)
    before={path:(sha(path),path.stat().st_ino) for path in paths}
    report=tmp_path/'qa.json'
    report.write_text(json.dumps({'fail':{'3':{'QA':{
        'history':{'dataset':[str(p) for p in paths]},
        'description':{'dataset':[str(p) for p in paths[:79]]},
    }}}}))
    rules=tmp_path/'rules.yaml'
    rules.write_text('''
- id: H
  check: QA
  message: history
  action: fix
  fix: drop_var_attr
  params: {var: tas, attr: history}
- id: D
  check: QA
  message: description
  action: fix
  fix: drop_var_attr
  params: {var: tas, attr: description}
''')
    output=tmp_path/'plan.json'

    def refuse(*args,**kwargs):
        raise AssertionError('Planning must never write a grouped NetCDF output')

    monkeypatch.setattr(Batch,'write',refuse)
    args=['plan',str(report),'-r',str(rules),'-o',str(output)]
    offline=CliRunner().invoke(app,[*args,'--report-only'])
    assert offline.exit_code==0,offline.output
    assert '454 fixes candidats sur 375 fichiers (non vérifiés)' in offline.output
    result=CliRunner().invoke(app,args)
    assert result.exit_code==0,result.output
    assert '4 fixes à appliquer sur 4 fichiers' in result.output
    assert '371 fichiers retirés du plan' in result.output
    plan=FixPlan.read(output)
    assert set(plan.files)=={str(p) for p in paths[:4]}
    assert all(len(steps)==2 for steps in plan.files.values())  # retain selected no-op prerequisites
    assert plan.preflight['counts']['changed_steps']==4
    assert plan.preflight['counts']['noop_steps']==450
    assert all((sha(p),p.stat().st_ino)==before[p] for p in paths)
    assert apply_plan(plan,tmp_path/'apply')['changed']==4
    again=CliRunner().invoke(app,args)
    assert again.exit_code==0,again.output
    assert '0 fixes à appliquer sur 0 fichiers' in again.output
    assert not FixPlan.read(output).files


def test_unreadable_or_failed_files_are_retained_as_unverified_not_conforming(tmp_path):
    good=tmp_path/'good.nc';bad=tmp_path/'bad.nc';missing=tmp_path/'missing.nc'
    make(good);bad.write_bytes(b'not a NetCDF')
    steps=[Step('H','drop_var_attr',dict(var='tas',attr='history'))]
    plan=FixPlan('test','test',{str(p):steps for p in (good,bad,missing)})
    counts=verify_plan(plan)
    assert counts['changed_files']==1 and counts['changed_steps']==1
    assert counts['unverified_files']==2 and counts['unverified_steps']==2
    assert counts['conforming_files']==0
    assert len(plan.files)==3
    assert plan.preflight['files'][str(missing)]['status']=='unverified'


def test_partial_failures_and_independent_changes_remain_distinct(tmp_path):
    path=tmp_path/'partial.nc';make(path);before=sha(path)
    steps=[Step('H','drop_var_attr',dict(var='tas',attr='history')),
           Step('F','set_global_attr_if_matches',dict(attr='reviewed',expected='expected',value='new'))]
    plan=FixPlan('test','test',{str(path):steps})
    counts=verify_plan(plan)
    assert counts['changed_steps']==1 and counts['unverified_steps']==1
    assert counts['changed_files']==counts['unverified_files']==1
    assert plan.preflight['files'][str(path)]['status']=='partial'
    assert len(plan.files[str(path)])==2 and sha(path)==before


def test_path_mapping_happens_before_read_only_verification(tmp_path):
    copied=tmp_path/'copy';copied.mkdir()
    path=copied/'file.nc';make(path,changed=False)
    report=tmp_path/'qa.json';report.write_text(json.dumps({'fail':{'3':{'[CF-Conventions] appendix_a':{
        '[Appendix A] Attribute "history" should not be present in non-coordinate data (D) variable "tas".':
        {'dataset':['/unavailable/source/file.nc']}}}}}))
    out=tmp_path/'plan.json'
    result=CliRunner().invoke(app,['plan',str(report),'--map-path',f'/unavailable/source={copied}','-o',str(out)])
    assert result.exit_code==0,result.output
    assert '0 fixes à appliquer sur 0 fichiers' in result.output
    p=FixPlan.read(out)
    assert p.preflight['counts']['unverified_files']==0
    assert p.preflight['counts']['conforming_files']==1


def test_structural_preview_never_writes_or_reencodes_source(tmp_path,monkeypatch):
    path=tmp_path/'float.nc'
    with netCDF4.Dataset(path,'w') as ds:
        ds.createDimension('lev',2)
        ds.createVariable('lev','f4',('lev',))[:]=[1,2]
    original=path.read_bytes()
    plan=FixPlan('test','test',{str(path):[Step('P','promote_coordinate_to_double',{'var':'lev'})]})

    def refuse(*args,**kwargs):
        raise AssertionError('Plan cannot rewrite a NetCDF')

    monkeypatch.setattr(Batch,'write',refuse)
    assert verify_plan(plan)['changed_files']==1
    assert path.read_bytes()==original
    with netCDF4.Dataset(path) as ds:
        assert ds['lev'].dtype==np.dtype('f4')


def test_hardlinks_are_only_checked_never_broken_during_planning(tmp_path):
    import os

    path=tmp_path/'linked.nc';make(path);link=tmp_path/'alias.nc';os.link(path,link)
    before=sha(path);inode=path.stat().st_ino
    steps=[Step('H','drop_var_attr',dict(var='tas',attr='history'))]
    p=FixPlan('test','test',{str(path):steps})
    assert verify_plan(p)['unverified_files']==1
    assert verify_plan(p,break_hardlinks=True)['changed_files']==1
    assert sha(path)==before and sha(link)==before
    assert path.stat().st_ino==link.stat().st_ino==inode and path.stat().st_nlink==2


def test_legacy_plan_without_preflight_still_loads(tmp_path):
    path=tmp_path/'legacy.json'
    path.write_text(json.dumps(dict(rules_version='old',report='old',files={})))
    assert FixPlan.read(path).preflight=={}
