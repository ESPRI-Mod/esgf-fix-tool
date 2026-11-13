"""--break-hardlinks: a hard-linked file (e.g. already in the DRS) is copied before a metadata update,
so its other links keep the previous content. Without it, such files are refused."""
import json
import os

import netCDF4
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan, Step

STEP = Step("C001", "drop_var_attr", {"var": "tas", "attr": "history"})


def linked_pair(tmp_path, history="drop me"):
    """A WORK file and its hard link in a DRS directory, as esgdrs --link leaves them."""
    work = tmp_path / "work" / "tas.nc"
    work.parent.mkdir()
    with netCDF4.Dataset(work, "w") as ds:
        ds.createDimension("n", 2)
        v = ds.createVariable("tas", "f4", ("n",))
        v[:] = [1.5, 2.5]
        if history:
            v.history = history
    drs = tmp_path / "drs" / "v20261113" / "tas.nc"
    drs.parent.mkdir(parents=True)
    os.link(work, drs)
    return work, drs


def history(path):
    with netCDF4.Dataset(path) as ds:
        return getattr(ds["tas"], "history", None)


def plan(path):
    return FixPlan("test", "report", {str(path): [STEP]})


def test_hardlinked_file_is_refused_by_default(tmp_path):
    work, drs = linked_pair(tmp_path)
    with pytest.raises(PermissionError, match="--break-hardlinks"):
        apply_plan(plan(work), tmp_path / "fixlog.jsonl")
    assert history(work) == history(drs) == "drop me" and work.stat().st_nlink == 2


def test_break_hardlinks_fixes_work_and_keeps_the_drs_version(tmp_path):
    work, drs = linked_pair(tmp_path)
    log = tmp_path / "fixlog.jsonl"
    stats = apply_plan(plan(work), log, break_hardlinks=True)
    assert stats["changed"] == 1
    assert history(work) is None and history(drs) == "drop me"
    assert work.stat().st_nlink == 1 and drs.stat().st_nlink == 1
    assert not list(work.parent.glob(".*linkbreak"))
    (record,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert record["steps"][0]["hardlinks_broken"] == 1 and record["write_mode"] == "in-place"
    with netCDF4.Dataset(work) as ds:
        assert ds["tas"][:].tolist() == [1.5, 2.5]


def test_dry_run_reports_the_link_but_copies_nothing(tmp_path):
    work, drs = linked_pair(tmp_path)
    log = tmp_path / "fixlog.jsonl"
    apply_plan(plan(work), log, dry_run=True, break_hardlinks=True)
    assert work.stat().st_nlink == 2 and history(work) == history(drs) == "drop me"
    assert json.loads(log.read_text())["steps"][0]["hardlinks_broken"] == 1


def test_a_file_needing_no_change_keeps_its_links(tmp_path):
    work, drs = linked_pair(tmp_path, history=None)
    apply_plan(plan(work), tmp_path / "fixlog.jsonl", break_hardlinks=True)
    assert work.stat().st_nlink == 2 and os.path.samefile(work, drs)


def test_cli_option(tmp_path):
    work, drs = linked_pair(tmp_path)
    plan(work).dump(tmp_path / "plan.json")
    args = ["apply", str(tmp_path / "plan.json"), "--fixlog", str(tmp_path / "fixlog.jsonl"), "--quiet"]
    refused = CliRunner().invoke(app, args)
    assert refused.exit_code == 1 and history(work) == "drop me"
    result = CliRunner().invoke(app, [*args, "--break-hardlinks"])
    assert result.exit_code == 0, result.output
    assert history(work) is None and history(drs) == "drop me"
