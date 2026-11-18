"""The ordinary plan/apply loop includes component policy without a QA rule."""

import json

import netCDF4
import pytest
from test_component_deduplication import product
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan, Step


def overlaps(root):
    result = []
    for variable, surface, period, preferred in (
        ('areacella', 'u', '', 'ATM'),
        ('evspsbl', 'u', '185001-185412', 'ATM'),
        ('evspsbl', 'u', '185501-185912', 'ATM'),
        ('snc', 'lnd', '185001-185912', 'SRF'),
    ):
        keep = product(root, preferred, variable, surface=surface, period=period, value=10)
        discard = product(root, 'SRF' if preferred == 'ATM' else 'ATM', variable,
                          surface=surface, period=period, value=20)
        result.append((keep, discard))
    return result


def plan_cli(tmp, root, **extra):
    source = tmp/'qa.json'
    source.write_text(json.dumps(dict(info=dict(parent_dir=str(root/'CMIP7')), fail={}, error={}, **extra)))
    target = tmp/'plan.json'
    result = CliRunner().invoke(app, ['plan', str(source), '-o', str(target)])
    assert result.exit_code == 0, result.output
    plan = FixPlan.read(target)
    assert not plan.files and plan.deduplicate_root == str(root/'CMIP7')
    assert 'deduplication' in result.output
    return target, plan


def test_default_cli_apply_deletes_the_four_approved_copies_without_any_findings(tmp_path):
    root = tmp_path/'simulation'
    pairs = overlaps(root)
    original = {p: p.read_bytes() for pair in pairs for p in pair}
    target, _ = plan_cli(tmp_path, root)
    args = ['apply', str(target), '--fixlog', str(tmp_path/'apply.jsonl'), '--only-under', str(root)]
    dry = CliRunner().invoke(app, [*args, '--dry-run'])
    assert dry.exit_code == 0, dry.output
    assert all(p.read_bytes() == data for p, data in original.items())
    actual = CliRunner().invoke(app, args)
    assert actual.exit_code == 0, actual.output
    assert 'duplicates_deleted' in actual.output and 'Dédoublonnage' in actual.output
    for keep, discard in pairs:
        assert keep.read_bytes() == original[keep] and not discard.exists()
    entries = [json.loads(line) for line in (tmp_path/'apply.deduplicate.jsonl').read_text().splitlines()]
    assert sum(e['status'] == 'deleted' for e in entries) == 4
    assert 'deleted:' in (tmp_path/'apply.events.log').read_text()


@pytest.mark.parametrize('workers', [1, 2])
def test_workers_finish_before_cleanup_and_replay_never_redirects_discarded_steps(tmp_path, workers):
    root = tmp_path/'simulation'
    pairs = overlaps(root)
    steps = {}
    for keep, discard in pairs:
        for path in (keep, discard):
            with netCDF4.Dataset(path, 'a') as ds:
                ds[ds.variable_id].history = 'remove'
            steps[str(path)] = [Step('C001', 'drop_variable_histories', {})]
    # This step must never reach the preferred peer during replay.
    for _, discard in pairs:
        steps[str(discard)].append(Step('test', 'set_var_attr', dict(
            var=discard.name.split('_')[0], attr='discard_marker', value='secondary only')))
    plan = FixPlan('test', 'qa', steps, deduplicate_root=str(root/'CMIP7'))
    events = []
    result = apply_plan(plan, tmp_path/'apply.jsonl', only_under=root, workers=workers, events=events.append)
    assert result['files'] == 8 and result['duplicates_deleted'] == 4
    last_done = max(i for i, e in enumerate(events) if e.kind == 'file' and e.status == 'done')
    dedup_start = next(i for i, e in enumerate(events) if e.kind == 'phase' and e.phase == 'Dédoublonnage ATM/SRF')
    assert last_done < dedup_start
    original = {p: p.read_bytes() for p, _ in pairs}
    replay = apply_plan(plan, tmp_path/'replay.jsonl', only_under=root, workers=workers)
    assert replay['duplicates_deleted'] == 0 and replay['duplicates_already_removed'] == 4
    for keep, discard in pairs:
        assert keep.read_bytes() == original[keep] and not discard.exists()
        with netCDF4.Dataset(keep) as ds:
            assert 'history' not in ds[ds.variable_id].ncattrs()
            assert 'discard_marker' not in ds[ds.variable_id].ncattrs()


def test_report_scope_is_mapped_and_does_not_touch_the_original_simulation(tmp_path):
    source, copy = tmp_path/'source', tmp_path/'copy'
    originals, pairs = overlaps(source), overlaps(copy)
    target, plan = plan_cli(tmp_path, source)
    assert plan.map_paths(str(source), str(copy)) == 0  # no QA steps, but scope still maps
    assert plan.deduplicate_root == str(copy/'CMIP7')
    plan.dump(target)
    result = apply_plan(FixPlan.read(target), tmp_path/'apply.jsonl', only_under=copy)
    assert result['duplicates_deleted'] == 4
    assert all(p.exists() for pair in originals for p in pair)
    assert all(keep.exists() and not discard.exists() for keep, discard in pairs)


def test_legacy_plan_infers_its_single_cmip7_tree_without_scanning_other_simulations(tmp_path):
    selected, unrelated = tmp_path/'selected', tmp_path/'unrelated'
    pairs, other = overlaps(selected), overlaps(unrelated)
    path = pairs[0][0]
    plan = FixPlan('old', 'report-may-be-on-another-computer', {str(path): []})
    result = apply_plan(plan, tmp_path/'log', only_under=tmp_path)
    assert result['duplicates_deleted'] == 4
    assert all(p.exists() for pair in other for p in pair)


def test_empty_legacy_plan_can_use_a_direct_simulation_boundary(tmp_path):
    root = tmp_path/'simulation'
    overlaps(root)
    result = apply_plan(FixPlan('old', 'qa', {}), tmp_path/'log', only_under=root)
    assert result['duplicates_deleted'] == 4


def test_scope_outside_only_under_fails_before_any_file_is_changed(tmp_path):
    root = tmp_path/'simulation'
    pairs = overlaps(root)
    before = {p: p.read_bytes() for pair in pairs for p in pair}
    wrong = tmp_path/'other'
    wrong.mkdir()
    plan = FixPlan('test', 'qa', {}, deduplicate_root=str(root/'CMIP7'))
    with pytest.raises(ValueError, match='outside --only-under'):
        apply_plan(plan, tmp_path/'log', only_under=wrong)
    assert all(p.read_bytes() == data for p, data in before.items())


def test_pair_with_a_failed_planned_correction_is_retained_but_other_pairs_are_cleaned(tmp_path):
    root = tmp_path/'simulation'
    pairs = overlaps(root)
    bad = pairs[0][0]
    plan = FixPlan('test', 'qa', {str(bad): [Step('test', 'does_not_exist', {})]},
                   deduplicate_root=str(root/'CMIP7'))
    result = apply_plan(plan, tmp_path/'log.jsonl', only_under=root, continue_on_error=True)
    assert result['failed'] == 1 and result['duplicates_failed'] == 1
    assert result['duplicates_deleted'] == 3 and all(p.exists() for p in pairs[0])


def test_cli_reports_a_pair_validation_failure_as_an_error(tmp_path):
    root = tmp_path/'simulation'
    keep = product(root, 'ATM', 'areacella')
    discard = product(root, 'SRF', 'areacella')
    with netCDF4.Dataset(keep, 'a') as ds:
        ds.experiment_id = 'different'
    target, _ = plan_cli(tmp_path, root)
    result = CliRunner().invoke(app, ['apply', str(target), '--quiet', '--fixlog', str(tmp_path/'log')])
    assert result.exit_code == 1 and 'duplicates_failed' in result.output
    assert keep.exists() and discard.exists()


def test_matching_pair_can_belong_to_another_model_and_experiment(tmp_path):
    root = tmp_path/'simulation'
    paths = [product(root, c, 'areacella') for c in ('ATM', 'SRF')]
    for path in paths:
        with netCDF4.Dataset(path, 'a') as ds:
            ds.source_id, ds.experiment_id = 'OtherModel', 'piControl-spinup'
        path.rename(path.with_name(path.name.replace('IPSL-CM6-ESMCO2_esm-piControl-spinup', 'OtherModel_piControl-spinup')))
    _, plan = plan_cli(tmp_path, root)
    result = apply_plan(plan, tmp_path/'log', only_under=root)
    assert result['duplicates_deleted'] == 1 and result['duplicates_failed'] == 0


def test_terminal_dashboard_shows_deduplication_without_counting_it_as_a_netcdf_file(capsys):
    from esgf_fix_tool.cli.dashboard import Dashboard
    from esgf_fix_tool.progress import ProgressEvent

    dashboard = Dashboard()
    dashboard.update(ProgressEvent('deduplication', 'summary', path='/sim/CMIP7',
        details={'message': 'Doublons ATM/SRF : 4 supprimés'}), lambda event: '')
    dashboard.render(force=True)
    assert 'Doublons ATM/SRF : 4 supprimés' in capsys.readouterr().out
    assert not dashboard.finished and dashboard.total == 0


def test_broad_report_scope_does_not_scan_other_simulations(tmp_path):
    pairs = overlaps(tmp_path/'first') + overlaps(tmp_path/'second')
    plan = FixPlan('test', 'qa', {}, deduplicate_root=str(tmp_path))
    result = apply_plan(plan, tmp_path/'log', only_under=tmp_path)
    assert 'duplicates_deleted' not in result
    assert all(p.exists() for pair in pairs for p in pair)


def test_single_secondary_copy_is_kept_by_automatic_apply(tmp_path):
    root = tmp_path/'simulation'
    discard = product(root, 'SRF', 'areacella')
    plan = FixPlan('test', 'qa', {}, deduplicate_root=str(root/'CMIP7'))
    original = discard.read_bytes()
    result = apply_plan(plan, tmp_path/'log', only_under=root)
    assert result['duplicates_deleted'] == 0 and result['duplicates_missing_keeper'] == 1
    assert discard.read_bytes() == original
