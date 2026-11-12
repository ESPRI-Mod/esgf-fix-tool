"""Continue file failures without reporting false successes or swallowing cancellation."""

import json

import netCDF4
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.fixes import REGISTRY, FixFunction
from esgf_fix_tool.plan import FixPlan, Step


def files_and_plan(tmp):
    paths = [tmp / name for name in ("first.nc", "bad.nc", "last.nc")]
    for path in paths:
        with netCDF4.Dataset(path, "w") as ds:
            ds.createVariable("v", "i4").history = "original"
    good = Step("C001", "drop_var_attr", {"var": "v", "attr": "history"})
    bad = Step("C098", "drop_unused_horizontal_bounds", {"var": "invalid_name"})
    return paths, FixPlan(
        "test",
        "source",
        {str(p): [bad if i == 1 else good] for i, p in enumerate(paths)},
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_failure_is_logged_and_next_file_is_processed(tmp_path, capsys, dry_run):
    paths, plan = files_and_plan(tmp_path)
    before = paths[1].read_bytes()
    events = []
    progress = []
    log = tmp_path / "fixlog.jsonl"

    def observe(done, total, path, changed, elapsed):
        progress.append((done, total, changed, len(log.read_text().splitlines())))

    stats = apply_plan(
        plan,
        log,
        continue_on_error=True,
        dry_run=dry_run,
        events=events.append,
        progress=observe,
    )
    assert stats == {"files": 3, "changed": 2, "unchanged": 0, "failed": 1}
    assert progress == [(1, 3, True, 1), (2, 3, None, 2), (3, 3, True, 3)]
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["status"] for r in rows] == ["done", "failed", "done"]
    assert rows[1]["file"] == str(paths[1])
    assert rows[1]["error"]["type"] == "ValueError"
    assert "Traceback (most recent call last)" in rows[1]["error"]["traceback"]
    assert rows[1]["planned_steps"][0]["rule"] == "C098"
    assert rows[1]["rewrite_count"] is None
    assert paths[1].read_bytes() == before
    assert not any(
        e.path == str(paths[1]) and e.kind == "fix" and e.status == "done"
        for e in events
    )
    assert any(
        e.path == str(paths[2]) and e.kind == "fix" and e.status == "done"
        for e in events
    )
    for p in (paths[0], paths[2]):
        with netCDF4.Dataset(p) as ds:
            assert ("history" in ds["v"].ncattrs()) == dry_run
    assert "Traceback (most recent call last)" in capsys.readouterr().err


def test_missing_file_is_an_individual_failure_in_continue_mode(tmp_path):
    paths, plan = files_and_plan(tmp_path)
    paths[1].unlink()
    stats = apply_plan(plan, tmp_path / "log", continue_on_error=True)
    assert stats["files"] == 3 and stats["failed"] == 1 and stats["changed"] == 2


@pytest.mark.parametrize(
    "method", ["sync_filename_grid_label", "set_reduced_grid_label"]
)
def test_individual_plan_validation_failure_does_not_block_other_files(
    tmp_path, method
):
    paths, plan = files_and_plan(tmp_path)
    before = paths[1].read_bytes()
    plan.files[str(paths[1])] = [Step("bad-plan", method, {})]
    events = []
    stats = apply_plan(
        plan, tmp_path / "log", continue_on_error=True, events=events.append
    )
    assert stats == {"files": 3, "changed": 2, "unchanged": 0, "failed": 1}
    assert paths[1].read_bytes() == before
    assert any(
        e.kind == "file" and e.path == str(paths[1]) and e.status == "failed"
        for e in events
    )


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_cancellation_still_stops_the_plan(tmp_path, monkeypatch, interrupt):
    paths, plan = files_and_plan(tmp_path)

    def stop(ds):
        raise interrupt("stop requested")

    monkeypatch.setitem(
        REGISTRY, "stop_test", FixFunction("stop_test", "metadata", stop)
    )
    plan.files[str(paths[1])] = [Step("test", "stop_test", {})]
    with pytest.raises(interrupt):
        apply_plan(plan, tmp_path / "log", continue_on_error=True)
    with netCDF4.Dataset(paths[2]) as ds:
        assert ds["v"].history == "original"


@pytest.mark.parametrize("quiet", [False, True])
def test_cli_continues_by_default_and_exits_nonzero_after_the_summary(tmp_path, quiet):
    paths, plan = files_and_plan(tmp_path)
    path = tmp_path / "plan.json"
    plan.dump(path)
    log = tmp_path / "log.jsonl"
    result = CliRunner().invoke(
        app, ["apply", str(path), "--fixlog", str(log), *(["--quiet"] if quiet else [])]
    )
    assert result.exit_code == 1, result.output
    assert "Traceback (most recent call last)" in result.output
    assert "'failed': 1" in result.output and "'files': 3" in result.output
    assert len(log.read_text().splitlines()) == 3
    with netCDF4.Dataset(paths[2]) as ds:
        assert "history" not in ds["v"].ncattrs()
    if not quiet:
        assert "[2/3] failed" in result.output and "[3/3]" in result.output


def test_fail_fast_option_keeps_the_previous_stop_behavior(tmp_path):
    paths, plan = files_and_plan(tmp_path)
    path = tmp_path / "plan.json"
    plan.dump(path)
    result = CliRunner().invoke(
        app, ["apply", str(path), "--fixlog", str(tmp_path / "log"), "--fail-fast"]
    )
    assert result.exit_code != 0
    with netCDF4.Dataset(paths[2]) as ds:
        assert ds["v"].history == "original"
