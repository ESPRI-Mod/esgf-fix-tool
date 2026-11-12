"""Default catalogue coverage and explicit inclusion/exclusion through the CLI."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from esgf_fix_tool.cli.main import app
from esgf_fix_tool.cli import catalogues
from esgf_fix_tool.plan import FixPlan


HISTORY = ('[Appendix A] Attribute "history" should not be present in non-coordinate data (D) variable "tas". '
           'This attribute may only appear in global attributes (G).')


def report(tmp):
    nc=tmp/'untouched.nc';nc.write_bytes(b'Plan must not open or modify a NetCDF')
    data={'fail':{'3':{'[CF-Conventions] appendix_a':{HISTORY:{'dataset':[str(nc)]}}},
                  '1':{"[CMIP7] [ATTR004] Variable 'tas' attribute 'comment' registry expected-term check":{
                      "No variable 'tas' attribute 'comment' is defined":{'dataset':[str(nc)]}}}}}
    p=tmp/'qa_result_test.json';p.write_text(json.dumps(data))
    return p,nc


def run(tmp,args):
    source,nc=report(tmp);output=tmp/'plan.json'
    result=CliRunner().invoke(app,['plan',str(source),*args,'-o',str(output)])
    assert result.exit_code==0,result.output
    assert nc.read_bytes()==b'Plan must not open or modify a NetCDF'
    return FixPlan.read(output)


def ids(plan):return [s.rule for steps in plan.files.values() for s in steps]


def test_no_rules_option_loads_all_catalogues_from_any_directory(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    p=run(tmp_path,[])
    assert set(ids(p))=={'C001','C002'}
    names=p.rules_version.split(',')
    assert names[0]=='post_apply.yaml' and names[-1]=='catalogue_fallbacks.yaml'
    assert len(names)==len(list(catalogues.DEFAULT_RULES_DIR.glob('*.yaml')))


def test_classify_without_rules_option_loads_all_catalogues(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    source,_=report(tmp_path)
    result=CliRunner().invoke(app,['classify',str(source),'--json'])
    names=json.loads(result.output)['rules_version'].split(',')
    assert names[0]=='post_apply.yaml' and names[-1]=='catalogue_fallbacks.yaml'
    assert len(names)==len(list(catalogues.DEFAULT_RULES_DIR.glob('*.yaml')))


def test_r_selects_rule_identifiers(tmp_path):
    assert ids(run(tmp_path,['-r','C001']))==['C001']


def test_repeated_r_identifiers_are_combined(tmp_path):
    assert set(ids(run(tmp_path,['-r','C001','-r','C002','-r','C001'])))=={'C001','C002'}


def test_exclusion_filters_ids_after_matching(tmp_path):
    assert ids(run(tmp_path,['--exclude-rule','C001']))==['C002']


def test_exclusion_wins_over_inclusion(tmp_path):
    assert ids(run(tmp_path,['-r','C001','--exclude-rule','C001']))==[]


def test_only_rule_is_still_supported(tmp_path):
    assert ids(run(tmp_path,['--only-rule','C002']))==['C002']


@pytest.mark.parametrize('option',['-r','--only-rule','--exclude-rule'])
def test_unknown_identifiers_are_errors_before_plan_output(tmp_path,option):
    source,_=report(tmp_path);out=tmp_path/'plan.json'
    result=CliRunner().invoke(app,['plan',str(source),option,'C_NOT_A_RULE','-o',str(out)])
    assert result.exit_code==2 and 'unknown rule ids' in result.output
    assert not out.exists()


def custom(tmp):
    p=tmp/'custom.yaml'
    p.write_text('''
- id: FIRST
  check: '\\[CF-Conventions\\] appendix_a'
  message: '.*'
  action: fix
  fix: drop_var_attr
  params: {var: tas, attr: history}
- id: FALLBACK
  check: '\\[CF-Conventions\\] appendix_a'
  message: '.*'
  action: fix
  fix: drop_var_attr
  params: {var: tas, attr: description}
''')
    return p


def test_explicit_catalogue_replaces_defaults(tmp_path):
    p=run(tmp_path,['-r',str(custom(tmp_path))])
    assert ids(p)==['FIRST'] and p.rules_version=='custom.yaml'


def test_excluded_rule_does_not_fall_through_to_a_seed(tmp_path):
    p=run(tmp_path,['-r',str(custom(tmp_path)),'--exclude-rule','FIRST'])
    assert ids(p)==[]


def test_catalogue_paths_can_be_combined_with_identifier_selection(tmp_path):
    p=custom(tmp_path)
    assert ids(run(tmp_path,['-r',str(p),'-r','FIRST']))==['FIRST']


def test_repeated_catalogue_path_is_not_loaded_twice(tmp_path):
    p=custom(tmp_path)
    assert ids(run(tmp_path,['-r',str(p),'-r',str(p)]))==['FIRST']


def test_missing_catalogue_has_a_clear_error(tmp_path):
    source,_=report(tmp_path)
    result=CliRunner().invoke(app,['plan',str(source),'-r',str(tmp_path/'missing.yaml')])
    assert result.exit_code==2 and 'catalogue' in result.output


def test_default_discovery_adds_future_catalogues_before_seeds(tmp_path,monkeypatch):
    for n in ['seed_z.yaml','extra.yaml','post_apply.yaml','variable_attributes.yaml','other.yml']:
        (tmp_path/n).write_text('[]')
    monkeypatch.setattr(catalogues,'DEFAULT_RULES_DIR',tmp_path)
    assert [p.name for p in catalogues.default_rule_paths()]==[
        'post_apply.yaml','variable_attributes.yaml','extra.yaml','other.yml','seed_z.yaml']


def test_retired_catalogues_left_by_scp_are_not_loaded_twice(tmp_path,monkeypatch):
    for name in ('post_apply.yaml','catalogue_fallbacks.yaml','cmip7_ipsl.yaml','seed_20261029.yaml'):
        (tmp_path/name).write_text('[]')
    monkeypatch.setattr(catalogues,'DEFAULT_RULES_DIR',tmp_path)
    assert [p.name for p in catalogues.default_rule_paths()] == ['post_apply.yaml','catalogue_fallbacks.yaml']
