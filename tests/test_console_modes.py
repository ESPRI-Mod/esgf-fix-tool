"""TTY uses one live view; redirected output keeps detailed, bounded logs."""

import io
import json
import sys

import pytest

from esgf_fix_tool.cli.progress import ConsoleProgress
from esgf_fix_tool.progress import ProgressEvent


class Terminal(io.StringIO):
    def isatty(self):
        return True


def event(kind, status, path="/copy/a.nc", **kwargs):
    return ProgressEvent(kind, status, path=path, **kwargs)


def rewrite_events(path="/copy/a.nc", status="done"):
    phase = "Réécriture unique regroupée"
    yield event("phase", "start", path, phase=phase)
    for name in ("ta", "ap"):
        for completed in (0, 25, 75, 100):
            yield event("io", "progress", path, phase=phase, details=dict(
                operation="Réécriture regroupée", variable=name,
                completed=completed, total=100, itemsize=8))
    yield event("phase", status, path, phase=phase, duration=12,
                details={} if status == "done" else {"error": "disk error"})


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
def test_redirected_rewrite_has_one_line_and_keeps_phase_and_completion_events(tmp_path, capsys, parallel, status):
    events = list(rewrite_events(status=status))
    with ConsoleProgress(tmp_path / "events", parallel=parallel) as display:
        for e in events:
            display(e)
    text = capsys.readouterr().out
    assert len(text.splitlines()) == 1
    assert "Réécriture unique regroupée" in text
    assert "2 variables" in text
    assert "%" not in text and "\x1b[" not in text
    if status != "done":
        assert "disk error" in text
    records = [json.loads(line) for line in (tmp_path / "events").read_text().splitlines()]
    assert len(records) == 4
    assert sum(r["kind"] == "io" for r in records) == 2
    assert all(r['details']['completed'] == r['details']['total']
               for r in records if r['kind'] == 'io')
    assert [r['status'] for r in records if r['kind'] == 'phase'] == ['start', status]


def test_redirected_nested_transform_has_progress_and_one_rewrite_summary(tmp_path, capsys):
    with ConsoleProgress(tmp_path / "events") as display:
        display(event("phase", "start", phase="Réécriture unique regroupée"))
        display(event("io", "progress", details=dict(operation="Moyenne interfaces vers centres",
            variable="edt", completed=100, total=10000000, itemsize=8)))
        display(event("phase", "done", phase="Réécriture unique regroupée", duration=1))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert 'Moyenne interfaces vers centres' in lines[0]
    assert 'Réécriture unique regroupée' in lines[1]


def test_interactive_files_share_one_dashboard_and_preserve_tracebacks(tmp_path, monkeypatch):
    terminal, errors = Terminal(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys, "stderr", errors)
    log = tmp_path / "events"
    with ConsoleProgress(log, parallel=True) as display:
        assert display.interactive
        for index, path in enumerate(("/copy/a.nc", "/copy/b.nc"), 1):
            display(event("file", "start", path, details={"index": index, "total": 2}))
        for e in rewrite_events():
            display(e)
        # Force the current redraw to inspect active state, rather than timers.
        display.dashboard.render(force=True)
        assert set(display.dashboard.active) == {"/copy/a.nc", "/copy/b.nc"}
        display(event("file", "failed", details={"error": "disk error"}))
        print("Traceback: disk error", file=sys.stderr)
        assert display.dashboard.lines == 0
        display(event("file", "done", "/copy/b.nc", details={"changed": False}))
        assert not display.dashboard.active
        assert display.dashboard.failed == 1
        assert display.dashboard.unchanged == 1
    text = terminal.getvalue()
    assert "Terminés : 2/2" in text
    assert "a.nc" in text and "b.nc" in text
    assert "\x1b[" in text
    assert "En cours - /copy" not in text  # No legacy detail lines in the view.
    assert errors.getvalue() == "Traceback: disk error\n"
    assert sys.stderr is errors
    assert any(r["kind"] == "io" for r in map(json.loads, log.read_text().splitlines()))


def test_quiet_terminal_still_logs_without_dashboard(tmp_path, monkeypatch):
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    with ConsoleProgress(tmp_path / "events", quiet=True) as display:
        assert not display.interactive
        for e in rewrite_events():
            display(e)
    assert terminal.getvalue() == ""
    assert len((tmp_path / "events").read_text().splitlines()) == 4


@pytest.mark.parametrize('interactive', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
def test_event_log_volume_depends_on_quarters_not_block_count(tmp_path, monkeypatch, interactive, parallel):
    if interactive:
        monkeypatch.setattr(sys, 'stdout', Terminal())
    log = tmp_path / 'events'
    total = 10000000  # A large array; 1,001 blocks but only five logged milestones.
    with ConsoleProgress(log, parallel=parallel) as display:
        display(event('file', 'start', details={'index': 1, 'total': 1}))
        for operation in ('Réécriture regroupée', 'Vérification de la sortie regroupée'):
            for name in ('ta', 'ps'):
                for i in range(1001):
                    display(event('io', 'progress', details=dict(
                        operation=operation, variable=name, completed=i * 10000,
                        total=total, itemsize=8)))
        display(event('file', 'done', details={'changed': True}))
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 22
    for operation in ('Réécriture regroupée', 'Vérification de la sortie regroupée'):
        for name in ('ta', 'ps'):
            progress = [r['details']['completed'] for r in records if r['kind'] == 'io'
                        and r['details']['operation'] == operation and r['details']['variable'] == name]
            assert progress == [0, 2500000, 5000000, 7500000, total]


def test_event_log_retains_real_completion_and_repeated_operations(tmp_path):
    log = tmp_path / 'events'
    with ConsoleProgress(log, quiet=True) as display:
        for _ in range(2):
            for completed in (0, 30, 99, 100, 100):
                display(event('io', 'progress', details=dict(operation='Lecture',
                    variable='v', completed=completed, total=100, itemsize=1000000)))
    progress = [json.loads(line)['details']['completed'] for line in log.read_text().splitlines()]
    # Crossing skipped quarters does not produce made-up intermediate values.
    assert progress == [0, 30, 99, 100, 0, 30, 99, 100]


@pytest.mark.parametrize("workers", [1, 2])
def test_batch_cli_prints_one_summary_per_actual_grouped_rewrite(tmp_path, workers):
    from test_parallel_apply import make_plan
    from typer.testing import CliRunner

    from esgf_fix_tool.cli.main import app

    plan = make_plan(tmp_path / "data", count=2, structural=True)
    path = tmp_path / "plan.json"
    plan.dump(path)
    result = CliRunner().invoke(app, ["apply", str(path), "--workers", str(workers),
                                     "--fixlog", str(tmp_path / "log.jsonl")])
    assert result.exit_code == 0, result.output
    summaries = [line for line in result.output.splitlines() if "Réécriture unique regroupée" in line]
    assert len(summaries) == 2
    assert all("Étape terminée" in line and "%" not in line for line in summaries)
    assert "Fix: C001" in result.output
    assert "\x1b[" not in result.output


def test_interactive_cli_uses_only_live_view(tmp_path, monkeypatch):
    from test_parallel_apply import make_plan

    from esgf_fix_tool.cli.apply import run

    plan = make_plan(tmp_path / "data", count=2)
    path = tmp_path / "plan.json"
    plan.dump(path)
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    run(path, fixlog=tmp_path / "log.jsonl")
    assert "Terminés : 2/2" in terminal.getvalue()
    assert "En cours - " not in terminal.getvalue()
    assert "Fix: C001" not in terminal.getvalue()
    assert "'changed':" not in terminal.getvalue()


@pytest.mark.parametrize('parallel', [False, True])
def test_batch_progress_and_text_journal_use_filename_and_quarters(tmp_path, capsys, parallel):
    path = '/ccc/work/cont003/cmip7/levavasg/simulation/CMIP7/ATM/tnhusscpbl.nc'
    log = tmp_path / 'fixlog.events.jsonl'
    total = 10000000
    with ConsoleProgress(log, parallel=parallel) as display:
        display(event('file', 'start', path, details={'index': 1, 'total': 1}))
        display(event('phase', 'start', path, phase='Réécriture unique regroupée'))
        for i in range(1001):
            display(event('io', 'progress', path, phase='Réécriture unique regroupée', details=dict(
                operation='Réécriture regroupée', variable='tnhusscpbl',
                completed=i * 10000, total=total, itemsize=8)))
        display(event('phase', 'done', path, phase='Réécriture unique regroupée', duration=12))
        for state in ('start', 'prepared', 'done'):
            display(event('fix', state, path, rule='C001', title='Suppression de history',
                          params={'var': 'tnhusscpbl', 'attr': 'history'},
                          details={'changed': True, 'dry_run': False}))
        display(event('file', 'done', path, details={'changed': True}))
    output = capsys.readouterr().out
    assert '/ccc/work/' not in output
    assert all(line.startswith('[tnhusscpbl.nc]') for line in output.splitlines())
    io_lines = [line for line in output.splitlines() if '%' in line]
    assert len(io_lines) == 5
    assert [line.split(' — ')[1].split(' ')[0] for line in io_lines] == ['0.0%', '25.0%', '50.0%', '75.0%', '100.0%']
    text = (tmp_path / 'fixlog.events.log').read_text()
    assert '/ccc/work/' not in text
    assert len([line for line in text.splitlines() if '%' in line]) == 5
    assert 'Fix: C001' in text and 'attr=history' in text and 'Done' in text
    assert '"kind"' not in text and '\x1b[' not in text
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(r['path'] == path for r in records)
    assert [r['status'] for r in records if r['kind'] == 'fix'] == ['start', 'prepared', 'done']


@pytest.mark.parametrize('workers', [1, 2])
def test_text_journal_retains_tracebacks_even_in_quiet_mode(tmp_path, workers):
    from test_parallel_apply import make_plan
    from typer.testing import CliRunner

    from esgf_fix_tool.cli.main import app
    from esgf_fix_tool.plan import Step

    plan = make_plan(tmp_path / 'data', count=2)
    bad = next(iter(plan.files))
    plan.files[bad] = [Step('bad', 'not_implemented', {})]
    path = tmp_path / 'plan.json'
    plan.dump(path)
    result = CliRunner().invoke(app, ['apply', str(path), '--workers', str(workers),
                                     '--quiet', '--fixlog', str(tmp_path / 'fixlog.jsonl')])
    assert result.exit_code == 1
    text = (tmp_path / 'fixlog.events.log').read_text()
    assert 'Traceback (most recent call last)' in text and 'not_implemented' in text
    assert 'Fix: C001' in text and 'Done' in text
    assert (tmp_path / 'fixlog.jsonl').exists() and (tmp_path / 'fixlog.events.jsonl').exists()


@pytest.mark.parametrize('stage', ['open', 'write', 'close'])
def test_text_log_failure_does_not_interrupt_corrections(tmp_path, monkeypatch, stage):
    from pathlib import Path

    from test_parallel_apply import make_plan
    from typer.testing import CliRunner

    from esgf_fix_tool.cli.main import app

    original = Path.open

    class BrokenStream:
        def __init__(self, stream):
            self.stream = stream

        def write(self, text):
            if stage == 'write':
                raise OSError('text log quota exceeded')
            return self.stream.write(text)

        def flush(self):
            self.stream.flush()

        def close(self):
            self.stream.close()
            if stage == 'close':
                raise OSError('text log quota exceeded')

    def checked(path, *args, **kwargs):
        if str(path).endswith('.events.log'):
            if stage == 'open':
                raise OSError('text log quota exceeded')
            return BrokenStream(original(path, *args, **kwargs))
        return original(path, *args, **kwargs)

    plan = make_plan(tmp_path / 'data', count=1)
    path = tmp_path / 'plan.json'
    plan.dump(path)
    monkeypatch.setattr(Path, 'open', checked)
    result = CliRunner().invoke(app, ['apply', str(path), '--fixlog', str(tmp_path / 'fixlog.jsonl')])
    assert result.exit_code == 0, result.output
    assert 'Journal texte indisponible' in result.output
    assert "'changed': 1" in result.output
