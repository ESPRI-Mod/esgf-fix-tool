"""CLI summaries distinguish automatic steps from ignored and unresolved findings."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan


def invoke(tmp_path, findings, args=()):
    rules = tmp_path / 'rules.yaml'
    rules.write_text('''
- id: FIX
  check: QA
  message: fix.*
  action: fix
  fix: drop_var_attr
  params: {var: tas, attr: history}
- id: WAIVE
  check: QA
  message: waive
  action: waive
- id: REVIEW
  check: QA
  message: review
  action: review
- id: DEFER
  check: QA
  message: defer
  action: defer
- id: OTHER
  check: QA
  message: other
  action: other_step
- id: UPSTREAM
  check: QA
  message: upstream
  action: upstream_only
- id: MISSING
  check: QA
  message: missing
  action: fix
  fix: unavailable_recipe
''')
    messages = {}
    paths = set()
    for message, file, dataset in findings:
        path = str(tmp_path / (file + '.nc'))
        paths.add(path)
        messages.setdefault(message, {}).setdefault(dataset, []).append(path)
    for path in paths:
        Path(path).write_bytes(b'Plan must not open NetCDF files')
    report = tmp_path / 'qa.json'
    report.write_text(json.dumps({'fail': {'3': {'QA': messages}}}))
    output = tmp_path / 'plan.json'
    result = CliRunner().invoke(app, ['plan', str(report), '--report-only', '-r', str(rules), '-o', str(output), *args])
    assert result.exit_code == 0, result.output
    for path in paths:
        assert Path(path).read_bytes() == b'Plan must not open NetCDF files'
    return result.output, FixPlan.read(output)


def test_mixed_files_and_duplicate_reports_count_only_distinct_automatic_steps(tmp_path):
    text, plan = invoke(tmp_path, [
        ('fix first diagnostic', 'mixed', 'dataset'),
        ('fix first diagnostic', 'mixed', 'alias'),
        ('fix second diagnostic', 'mixed', 'dataset'),
        ('waive', 'mixed', 'dataset'), ('waive', 'ignored_only', 'dataset'),
        ('waive', 'ignored_only', 'alias'),
        ('review', 'review_only', 'dataset'),
    ])
    assert '1 fixes candidats sur 1 fichiers' in text
    assert '2 diagnostics ignorés (waive) sur 2 fichiers' in text
    assert '1 diagnostics en attente ou à traiter ailleurs sur 1 fichiers (review=1)' in text
    assert len(plan.files) == 1
    assert next(iter(plan.files)).endswith('mixed.nc')
    assert len(next(iter(plan.files.values()))) == 1


def test_known_false_positives_converge_to_zero_without_counting_their_files(tmp_path):
    text, plan = invoke(tmp_path, [('waive', 'a', 'dataset'), ('waive', 'b', 'dataset')])
    assert '0 fixes candidats sur 0 fichiers' in text
    assert '2 diagnostics ignorés (waive) sur 2 fichiers' in text
    assert '0 diagnostics en attente ou à traiter ailleurs sur 0 fichiers' in text
    assert plan.files == {}


@pytest.mark.parametrize('message,status', [
    ('review', 'review'), ('defer', 'deferred'), ('other', 'other_step'),
    ('upstream', 'upstream'), ('missing', 'fix_missing'), ('unmatched', 'unknown'),
])
def test_every_unresolved_action_is_visible_but_not_planned(tmp_path, message, status):
    text, plan = invoke(tmp_path, [(message, 'file', 'dataset')])
    assert '0 fixes candidats sur 0 fichiers' in text
    assert f'1 diagnostics en attente ou à traiter ailleurs sur 1 fichiers ({status}=1)' in text
    assert not plan.files


def test_filtering_a_fix_does_not_present_it_as_ignored_or_resolved(tmp_path):
    text, plan = invoke(tmp_path, [
        ('fix', 'a', 'dataset'), ('waive', 'a', 'dataset'), ('review', 'b', 'dataset'),
    ], ['--exclude-rule', 'FIX'])
    assert '0 fixes candidats sur 0 fichiers' in text
    assert 'Filtres : 1 fixes non sélectionnés sur 1 fichiers' in text
    assert '1 diagnostics ignorés (waive) sur 1 fichiers' in text
    assert '(review=1)' in text
    assert not plan.files
