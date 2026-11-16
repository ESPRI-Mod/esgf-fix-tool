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
def test_redirected_rewrite_has_one_line_and_keeps_all_events(tmp_path, capsys, parallel, status):
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
    assert len(records) == len(events)
    assert sum(r["kind"] == "io" for r in records) == 8


def test_redirected_nested_transform_does_not_print_rewrite_percentages(tmp_path, capsys):
    with ConsoleProgress(tmp_path / "events") as display:
        display(event("phase", "start", phase="Réécriture unique regroupée"))
        display(event("io", "progress", details=dict(operation="Moyenne interfaces vers centres",
            variable="edt", completed=100, total=10000000, itemsize=8)))
        display(event("phase", "done", phase="Réécriture unique regroupée", duration=1))
    assert len(capsys.readouterr().out.splitlines()) == 1


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
    assert len((tmp_path / "events").read_text().splitlines()) == 10


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
