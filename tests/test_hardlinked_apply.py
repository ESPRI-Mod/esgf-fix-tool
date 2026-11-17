"""Explicit shared-inode updates retain publication links and isolate workers."""

import json
import os
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_file, apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.plan import FixPlan, Step


ATTRIBUTE = Step(
    "C107", "set_var_attr",
    {"var": "lev", "attr": "computed_standard_name", "value": "air_pressure"},
)


def linked_file(root, index=0, fmt="NETCDF4"):
    first = root / "files" / "d20261117" / f"ta-{index}.nc"
    alias = root / "v20261117" / first.name
    first.parent.mkdir(parents=True, exist_ok=True)
    alias.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(first, "w", format=fmt) as ds:
        ds.variable_id = "ta"
        ds.history = "Global history stays."
        ds.createDimension("time", 2)
        ds.createDimension("lev", 3)
        ds.createVariable("lev", "f8", ("lev",))[:] = [0.1, 0.5, 0.9]
        ds["lev"].standard_name = "atmosphere_hybrid_sigma_pressure_coordinate"
        ds.createVariable(
            "ta", "f4", ("time", "lev"),
            zlib=fmt.startswith("NETCDF4"), complevel=2,
        )[:] = np.arange(6).reshape(2, 3)
        ds["ta"].history = "Remove this variable history."
    os.link(first, alias)
    return first, alias


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
def test_shared_updates_group_each_inode_once(tmp_path, workers, dry_run, fmt):
    pairs = [linked_file(tmp_path, i, fmt) for i in range(2)]
    plan = FixPlan("test", "qa", {
        str(path): [ATTRIBUTE] for pair in pairs for path in pair
    })
    before = {first: (first.read_bytes(), first.stat().st_ino) for first, _ in pairs}
    log = tmp_path / "fixlog.jsonl"
    result = apply_plan(
        plan, log, allow_hardlinks=True, workers=workers,
        dry_run=dry_run, only_under=tmp_path,
    )
    assert result == {"files": 2, "changed": 2, "unchanged": 0, "hardlink_aliases": 2}
    assert len(plan.files) == 4  # Scheduling must not mutate the reusable input plan.
    rows = records(log)
    assert len(rows) == 2 and all(len(r["hardlink_aliases"]) == 1 for r in rows)
    assert all(r["rewrite_count"] == 0 and r["write_mode"] == "in-place" for r in rows)
    assert all(r["steps"][0]["shared_hardlinks"] for r in rows)
    if workers == 2:
        assert all(r["worker_pid"] != os.getpid() for r in rows)
    for first, alias in pairs:
        original, inode = before[first]
        assert first.stat().st_ino == alias.stat().st_ino == inode
        assert first.stat().st_nlink == 2
        with netCDF4.Dataset(alias) as ds:
            assert getattr(ds["lev"], "computed_standard_name", None) == (
                None if dry_run else "air_pressure"
            )
            assert ds.history == "Global history stays."
            np.testing.assert_array_equal(ds["ta"][:], np.arange(6).reshape(2, 3))
            if fmt.startswith("NETCDF4"):
                assert ds["ta"].filters()["complevel"] == 2
        if dry_run:
            assert first.read_bytes() == alias.read_bytes() == original
    if not dry_run:
        assert apply_plan(plan, tmp_path / "again", allow_hardlinks=True, workers=workers)["unchanged"] == 2


def test_direct_rename_preserves_links_without_copying_science(tmp_path, monkeypatch):
    first, alias = linked_file(tmp_path)
    inode = first.stat().st_ino
    steps = [
        ATTRIBUTE,
        Step("rename", "rename_coordinate", {"sources": ["lev"], "target": "height"}),
    ]

    def refuse_copy(*args, **kwargs):
        raise AssertionError("A direct update must not stream the main field")

    monkeypatch.setattr(Batch, "write", refuse_copy)
    result = apply_file(first, steps, allow_hardlinks=True)
    assert all(e["rewrite_count"] == 0 for e in result)
    with netCDF4.Dataset(alias) as ds:
        assert ds["height"].computed_standard_name == "air_pressure"
        assert ds["ta"].dimensions == ("time", "height")
        np.testing.assert_array_equal(ds["height"][:], [0.1, 0.5, 0.9])
        np.testing.assert_array_equal(ds["ta"][:], np.arange(6).reshape(2, 3))
    assert first.stat().st_ino == alias.stat().st_ino == inode


def test_distinct_alias_steps_are_merged_without_duplicate_execution(tmp_path):
    first, alias = linked_file(tmp_path)
    history = Step("C001", "drop_variable_histories", {})
    plan = FixPlan("test", "qa", {str(first): [ATTRIBUTE], str(alias): [ATTRIBUTE, history]})
    result = apply_plan(plan, tmp_path / "log", allow_hardlinks=True, workers=2)
    assert result["changed"] == 1 and result["hardlink_aliases"] == 1
    row = records(tmp_path / "log")[0]
    assert [s["rule"] for s in row["steps"]] == ["C107", "C001"]
    assert len(plan.files[str(first)]) == 1 and len(plan.files[str(alias)]) == 2
    with netCDF4.Dataset(alias) as ds:
        assert ds["lev"].computed_standard_name == "air_pressure"
        assert "history" not in ds["ta"].ncattrs()


@pytest.mark.parametrize("dry_run", [False, True])
def test_shared_structural_rewrite_is_refused_without_detaching_links(tmp_path, dry_run):
    first, alias = linked_file(tmp_path)
    # Removing a variable requires the existing grouped rewrite machinery.
    with netCDF4.Dataset(first, "a") as ds:
        ds.createDimension("klev", 1)
        ds.createVariable("klev", "i4", ("klev",))[:] = 0
    plan = FixPlan("test", "qa", {str(first): [ATTRIBUTE]})
    original, inode = first.read_bytes(), first.stat().st_ino
    with pytest.raises(PermissionError, match="structural rewrite"):
        apply_plan(plan, tmp_path / "log", allow_hardlinks=True, dry_run=dry_run)
    assert first.read_bytes() == alias.read_bytes() == original
    assert first.stat().st_ino == alias.stat().st_ino == inode
    assert not list(tmp_path.rglob("*.fixtmp"))


def test_shared_failure_is_logged_once_and_other_files_continue(tmp_path):
    first, alias = linked_file(tmp_path, 0)
    second, second_alias = linked_file(tmp_path, 1)
    invalid = Step("bad", "not_implemented", {})
    plan = FixPlan("test", "qa", {
        str(first): [invalid], str(alias): [invalid],
        str(second): [ATTRIBUTE], str(second_alias): [ATTRIBUTE],
    })
    result = apply_plan(
        plan, tmp_path / "log", allow_hardlinks=True, workers=2, continue_on_error=True,
    )
    assert result == {"files": 2, "changed": 1, "unchanged": 0, "failed": 1, "hardlink_aliases": 2}
    failed = next(r for r in records(tmp_path / "log") if r["status"] == "failed")
    assert failed["hardlink_aliases"] == [str(alias)]
    with netCDF4.Dataset(second_alias) as ds:
        assert ds["lev"].computed_standard_name == "air_pressure"


def test_cli_opt_in_updates_all_links_and_default_still_refuses(tmp_path):
    first, alias = linked_file(tmp_path)
    plan_path = tmp_path / "plan.json"
    FixPlan("test", "qa", {str(first): [ATTRIBUTE], str(alias): [ATTRIBUTE]}).dump(plan_path)
    runner = CliRunner()
    original = first.read_bytes()
    refused = runner.invoke(app, ["apply", str(plan_path), "--fixlog", str(tmp_path / "refused")])
    assert refused.exit_code == 1 and "hardlinks" in refused.output
    assert first.read_bytes() == original
    result = runner.invoke(app, [
        "apply", str(plan_path), "--allow-hardlinks", "--workers", "2",
        "--only-under", str(tmp_path), "--fixlog", str(tmp_path / "allowed"),
    ])
    assert result.exit_code == 0, result.output
    assert "'hardlink_aliases': 1" in result.output
    with netCDF4.Dataset(alias) as ds:
        assert ds["lev"].computed_standard_name == "air_pressure"


def test_shared_filename_synchronization_is_refused_before_attribute_changes(tmp_path):
    first, alias = linked_file(tmp_path)
    renamed = first.with_name(
        "ta_tavg-al-hm-u_mon_glb_g111_MODEL_EXPERIMENT_r1i1p1f1_185001-185912.nc"
    )
    first.rename(renamed)
    original = renamed.read_bytes()
    steps = [ATTRIBUTE, Step("C097", "sync_filename_grid_label", {"source": "g111", "target": "g010"})]
    with pytest.raises(PermissionError, match="synchronize filenames"):
        apply_file(renamed, steps, allow_hardlinks=True)
    assert renamed.read_bytes() == alias.read_bytes() == original


def test_only_selected_alias_is_needed_and_scope_does_not_detach_other_links(tmp_path):
    first, alias = linked_file(tmp_path)
    # A QA report can select v... alone; files/d... remains the same physical file.
    inode = alias.stat().st_ino
    plan = FixPlan("test", "qa", {str(alias): [ATTRIBUTE]})
    result = apply_plan(
        plan, tmp_path / "log", allow_hardlinks=True, only_under=alias.parent,
    )
    assert result["changed"] == 1 and result["hardlink_aliases"] == 0
    assert first.stat().st_ino == alias.stat().st_ino == inode
    with netCDF4.Dataset(first) as ds:
        assert ds["lev"].computed_standard_name == "air_pressure"
