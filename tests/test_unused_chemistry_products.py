"""Explicit unused-product removal is separate from component duplicate policy."""

import json
import os

import netCDF4
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.deduplicate import deduplicate
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.plan_preflight import verify_plan


def product(root, variable='ch4', suffix='tclm-u-hm-air', component='CHM'):
    brand=variable+'_'+suffix
    path=root/'CMIP7'/component/(brand+'_mon_glb_g111_AnotherModel_historical_r1i1p1f1_185001-185412.nc')
    path.parent.mkdir(parents=True,exist_ok=True)
    with netCDF4.Dataset(path,'w') as ds:
        ds.setncatts(dict(variable_id=variable,branded_variable=brand,frequency='mon',region='glb',
                         grid_label='g111',source_id='AnotherModel',experiment_id='historical',variant_label='r1i1p1f1'))
        ds.createVariable(variable,'f4').assignValue(7)
    return path


def test_only_the_two_retired_products_are_removed_without_a_keeper(tmp_path):
    root=tmp_path/'sim'
    paths=[product(root,v) for v in ('ch4','n2o')]
    keep=[product(root,v,suffix=s) for v in ('ch4','n2o') for s in ('tclm-p19-hxy-air','tavg-u-hm-air')]
    keep += [product(root,component='ATM')]
    before={p:p.read_bytes() for p in paths+keep}
    dry=deduplicate(root,tmp_path/'dry',dry_run=True,emit=lambda s:None)
    assert dry['obsolete_would_delete']==2 and dry['would_delete']==0
    assert all(p.read_bytes()==data for p,data in before.items())
    result=deduplicate(root,tmp_path/'log',only_under=root,emit=lambda s:None)
    assert result['obsolete_deleted']==2 and result['deleted']==0
    assert all(not p.exists() for p in paths)
    assert all(p.read_bytes()==before[p] for p in keep)
    records=[json.loads(line) for line in (tmp_path/'log').read_text().splitlines()]
    assert all(r['operation']=='unused-product-removal' and 'keep' not in r for r in records)
    assert sum(r['status']=='deleted' for r in records)==2
    again=deduplicate(root,tmp_path/'again',only_under=root,emit=lambda s:None)
    assert again.get('obsolete_deleted',0)==0


@pytest.mark.parametrize('failure',['identity','corrupt','symlink','renamed_basename'])
def test_invalid_candidates_are_reported_and_preserved(tmp_path,failure):
    root=tmp_path/'sim';path=product(root)
    if failure=='identity':
        with netCDF4.Dataset(path,'a') as ds:ds.branded_variable='ch4_tclm-p19-hxy-air'
    elif failure=='corrupt':path.write_bytes(b'invalid NetCDF')
    elif failure=='symlink':
        target=tmp_path/'external.nc';path.rename(target);path.symlink_to(target)
    else:
        target=path.with_name(path.name.replace('AnotherModel','WrongModel'));path.rename(target);path=target
    before=path.read_bytes()
    result=deduplicate(root,tmp_path/'log',only_under=root,emit=lambda s:None)
    assert result['obsolete_failed']==1 and result['obsolete_deleted']==0
    assert path.read_bytes()==before


def test_only_under_boundary_is_required_and_hardlink_peer_is_untouched(tmp_path):
    root=tmp_path/'sim';path=product(root);peer=tmp_path/'kept.nc';os.link(path,peer)
    before=peer.read_bytes()
    for allowed in (None,tmp_path/'elsewhere'):
        with pytest.raises(ValueError):deduplicate(root,tmp_path/'log',only_under=allowed,emit=lambda s:None)
        assert path.exists()
    deduplicate(root,tmp_path/'log',only_under=root,emit=lambda s:None)
    assert not path.exists() and peer.read_bytes()==before


def test_ordinary_apply_skips_unused_recipes_then_deletes_and_replays(tmp_path):
    root=tmp_path/'sim';path=product(root)
    # This recipe would fail: these synthetic retired files deliberately have no time.
    p=FixPlan('test','test',{str(path):[Step('C066','fix_climatology_reference',{})]},
              deduplicate_root=str(root/'CMIP7'))
    before=path.read_bytes()
    dry=apply_plan(p,tmp_path/'dry',dry_run=True,only_under=root)
    assert dry['files']==0 and dry['obsolete_would_delete']==1 and path.read_bytes()==before
    result=apply_plan(p,tmp_path/'apply',only_under=root)
    assert result['files']==0 and result['obsolete_deleted']==1 and not path.exists()
    again=apply_plan(p,tmp_path/'again',only_under=root)
    assert again['files']==0 and again['obsolete_already_removed']==1


def test_plan_is_read_only_and_reserves_retired_files_for_cleanup(tmp_path):
    root=tmp_path/'sim';path=product(root)
    p=FixPlan('test','test',{str(path):[Step('C066','fix_climatology_reference',{})]},
              deduplicate_root=str(root/'CMIP7'))
    before=path.read_bytes()
    counts=verify_plan(p)
    assert counts['cleanup_files']==1 and counts['changed_files']==0
    assert not p.files and path.read_bytes()==before
    result=apply_plan(p,tmp_path/'apply',only_under=root)
    assert result['obsolete_deleted']==1 and not path.exists()


def test_sharded_or_broad_automatic_apply_never_performs_this_cleanup(tmp_path):
    root=tmp_path/'sim';path=product(root)
    p=FixPlan('test','test',{},deduplicate_root=str(root/'CMIP7'))
    result=apply_plan(p,tmp_path/'shard',only_under=root,shard=(1,2))
    assert path.exists() and 'obsolete_deleted' not in result
    p.deduplicate_root=str(tmp_path)
    result=apply_plan(p,tmp_path/'broad',only_under=tmp_path)
    assert path.exists() and 'obsolete_deleted' not in result


def test_cleanup_command_includes_the_retired_policy(tmp_path):
    root=tmp_path/'sim';path=product(root)
    result=CliRunner().invoke(app,['deduplicate',str(root),'--only-under',str(root),'--fixlog',str(tmp_path/'log')])
    assert result.exit_code==0,result.output
    assert 'obsolete_deleted' in result.output and not path.exists()
