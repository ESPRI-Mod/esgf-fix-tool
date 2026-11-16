"""Real spawned processes must preserve per-file semantics and shared journals."""

import json
import os

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan, Step


def make_plan(root, count=4, structural=False):
    root.mkdir()
    files = {}
    for i in range(count):
        path = root / f"file{i}.nc"
        with netCDF4.Dataset(path, "w") as ds:
            ds.createDimension("x", 32)
            ds.createDimension("axis_nbounds" if structural else "bnds", 2)
            if structural:
                ds.createDimension("bnds", 2)  # A merge requires one structural rewrite.
            v = ds.createVariable("v", "f4", ("x",), zlib=True, complevel=2)
            v[:] = np.arange(32) + i
            v.history = "remove"
            ds.createVariable("bounds", "f8", ("axis_nbounds" if structural else "bnds",))[:] = [0, 1]
        files[str(path)] = [Step("C001", "drop_var_attr", {"var": "v", "attr": "history"})]
    return FixPlan("test", "synthetic", files)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("structural", [False, True])
def test_parallel_results_match_serial_and_log_every_file(tmp_path, dry_run, structural):
    serial = make_plan(tmp_path / "serial", structural=structural)
    parallel = make_plan(tmp_path / "parallel", structural=structural)
    expected = apply_plan(serial, tmp_path / "serial.log", dry_run=dry_run)
    events, callbacks = [], []
    log = tmp_path / "parallel.log"

    def observe(done, total, path, changed, elapsed):
        assert len(log.read_text().splitlines()) == done
        callbacks.append((done, path))

    before = {p: os.stat(p).st_ino for p in parallel.files}
    actual = apply_plan(parallel, log, workers=2, dry_run=dry_run,
                        events=events.append, progress=observe, only_under=tmp_path / "parallel")
    assert actual == expected
    assert [n for n, _ in callbacks] == [1, 2, 3, 4]
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert {r["file"] for r in rows} == set(parallel.files)
    assert all(r["worker_pid"] != os.getpid() for r in rows)
    assert all(r["rewrite_count"] == int(structural and not dry_run) for r in rows)
    for source, target in zip(serial.files, parallel.files):
        with netCDF4.Dataset(source) as s, netCDF4.Dataset(target) as d:
            assert list(s.dimensions) == list(d.dimensions)
            for n in s.variables:
                assert s[n][:].tobytes() == d[n][:].tobytes()
                assert s[n].ncattrs() == d[n].ncattrs()
                assert s[n].filters() == d[n].filters()
        if not structural or dry_run:
            assert os.stat(target).st_ino == before[target]
        states = [e.status for e in events if e.path == target and e.kind == "file"]
        assert states == ["start", "done"]
    if not dry_run:
        assert apply_plan(parallel, log, workers=2)["unchanged"] == 4


def test_parallel_failure_continues_and_retains_worker_traceback(tmp_path, capsys):
    plan = make_plan(tmp_path / "data")
    bad = next(iter(plan.files))
    plan.files[bad] = [Step("bad", "not_implemented", {})]
    log = tmp_path / "log"
    stats = apply_plan(plan, log, workers=2, continue_on_error=True)
    assert stats == {"files": 4, "changed": 3, "unchanged": 0, "failed": 1}
    failed = next(r for r in map(json.loads, log.read_text().splitlines()) if r["status"] == "failed")
    assert failed["file"] == bad
    assert "_run" in failed["error"]["traceback"]
    assert "Traceback" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["missing", "invalid_rename"])
def test_parallel_preflight_failure_does_not_block_other_files(tmp_path, failure):
    plan = make_plan(tmp_path / "data")
    path = next(iter(plan.files))
    if failure == "missing":
        os.unlink(path)
    else:
        plan.files[path] = [Step("bad", "sync_filename_grid_label", {})]
    result = apply_plan(plan, tmp_path / "log", workers=2, continue_on_error=True)
    assert result == {"files": 4, "changed": 3, "unchanged": 0, "failed": 1}


def test_parallel_fail_fast_stops_dispatch_and_journals_running_files(tmp_path):
    plan = make_plan(tmp_path / "data", count=12)
    for path in plan.files:
        plan.files[path] = [Step("bad", "not_implemented", {})]
    log = tmp_path / "log"
    with pytest.raises(KeyError, match="not_implemented"):
        apply_plan(plan, log, workers=2)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 2
    assert all(r["status"] == "failed" for r in rows)


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "relative"])
def test_parallel_rejects_shared_files_before_writing(tmp_path, alias, monkeypatch):
    plan = make_plan(tmp_path / "data", count=1)
    path = next(iter(plan.files))
    other = tmp_path / "alias.nc"
    if alias == "symlink":
        other.symlink_to(path)
    elif alias == "hardlink":
        os.link(path, other)
    else:
        monkeypatch.chdir(tmp_path)
        other = os.path.relpath(path)
    plan.files[str(other)] = plan.files[path]
    with pytest.raises(ValueError, match="share a physical file"):
        apply_plan(plan, tmp_path / "log", workers=2)
    with netCDF4.Dataset(path) as ds:
        assert ds["v"].history == "remove"


def test_parallel_cli_streams_events_without_mixing_file_state(tmp_path):
    plan = make_plan(tmp_path / "data")
    path, log = tmp_path / "plan.json", tmp_path / "fixlog.jsonl"
    plan.dump(path)
    result = CliRunner().invoke(app, ["apply", str(path), "--workers", "2", "--fixlog", str(log)])
    assert result.exit_code == 0, result.output
    assert "Done" in result.output and "'changed': 4" in result.output
    events = [json.loads(line) for line in (tmp_path / "fixlog.events.jsonl").read_text().splitlines()]
    assert {e["path"] for e in events} == set(plan.files)


@pytest.mark.parametrize("dry_run", [False, True])
def test_parallel_filename_renames_preserve_data_and_replay(tmp_path, dry_run):
    from test_post_apply import grid_file

    files, originals = {}, {}
    for period in ("185001-185912", "186001-186912"):
        path = tmp_path / f"siarea_tavg-u-hm-u_mon_nh_g112_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1_{period}.nc"
        grid_file(path)
        originals[path] = (path.stat().st_ino, path.read_bytes())
        files[str(path)] = [Step("C097", "sync_filename_grid_label", {"source": "g112", "target": "g011"})]
    plan = FixPlan("test", "synthetic", files)
    log = tmp_path / "log"
    assert apply_plan(plan, log, workers=2, dry_run=dry_run, only_under=tmp_path)["changed"] == 2
    for path, (inode, content) in originals.items():
        target = path if dry_run else path.with_name(path.name.replace("_g112_", "_g011_"))
        assert target.stat().st_ino == inode
        assert target.read_bytes() == content
    if not dry_run:
        assert apply_plan(plan, log, workers=2, only_under=tmp_path)["unchanged"] == 2


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_worker_count_validation(tmp_path, workers):
    with pytest.raises(ValueError, match="positive integer"):
        apply_plan(FixPlan("test", "report", {}), tmp_path / "log", workers=workers)
