"""C001 covers unreported auxiliaries and variables introduced by later recipes."""

import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.fixes import REGISTRY, FixFunction
from esgf_fix_tool.plan import FixPlan, Step


def fixture(path, fmt, main="ta"):
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.variable_id = main
        ds.history = "Keep global history exactly\nincluding its newlines"
        ds.createDimension("time", 2)
        ds.createDimension("lev", 3)
        for name, dims in [
            (main, ("time", "lev")),
            ("ps", ("time",)),
            ("ap", ("lev",)),
            ("b", ("lev",)),
            ("lev", ("lev",)),
        ]:
            v = ds.createVariable(
                name, "f8", dims, zlib=fmt.startswith("NETCDF4"), complevel=2
            )
            v[:] = np.arange(v.size).reshape(v.shape)
            v.history = "history on " + name
            v.comment = "retain this metadata"
    return path


@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("main", ["ta", "zg"])
@pytest.mark.parametrize("legacy", [False, True])
def test_only_one_reported_variable_cleans_every_auxiliary_directly(
    tmp_path, fmt, main, legacy
):
    path = fixture(tmp_path / "file.nc", fmt, main)
    with netCDF4.Dataset(path) as ds:
        original = {
            name: (v[:].tobytes(), v.filters(), v.chunking())
            for name, v in ds.variables.items()
        }
        history = ds.history
    # Old plans can even mention a variable already renamed or removed.
    step = (
        Step("C001", "drop_var_attr", {"var": "no_longer_present", "attr": "history"})
        if legacy
        else Step("C001", "drop_variable_histories", {})
    )
    plan = FixPlan("test", "source", {str(path): [step]})
    before = path.read_bytes()
    inode = path.stat().st_ino
    apply_plan(plan, tmp_path / "dry", dry_run=True)
    assert path.read_bytes() == before
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds.history == history
        for name, v in ds.variables.items():
            assert "history" not in v.ncattrs()
            assert v.comment == "retain this metadata"
            assert v[:].tobytes() == original[name][0]
            assert (
                v.filters() == original[name][1] and v.chunking() == original[name][2]
            )
    assert path.stat().st_ino == inode
    assert json.loads((tmp_path / "log").read_text())["rewrite_count"] == 0
    after = path.read_bytes()
    assert (
        apply_plan(plan, tmp_path / "again")["changed"] == 0
        and path.read_bytes() == after
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("replacement", [False, True])
def test_later_auxiliary_creation_cannot_reintroduce_history(
    tmp_path, monkeypatch, reverse, replacement
):
    path = fixture(tmp_path / "aux.nc", "NETCDF4")
    if replacement:
        with netCDF4.Dataset(path, "a") as ds:
            ds.createVariable("aux", "f8", ("lev",))[:] = [1, 2, 3]

    def create_aux(ds):
        if "aux" in ds.variables and getattr(ds["aux"], "created_by_test", 0):
            return False
        aux = (
            ds.replaceVariable("aux", "f8", ("lev",))
            if "aux" in ds.variables
            else ds.createVariable("aux", "f8", ("lev",))
        )
        aux[:] = [1, 2, 3]
        aux.created_by_test = 1
        aux.history = "copied by a later recipe"
        return True

    monkeypatch.setitem(
        REGISTRY,
        "create_test_aux",
        FixFunction("create_test_aux", "metadata", create_aux),
    )
    steps = [
        Step("C001", "drop_variable_histories", {}),
        Step("test", "create_test_aux", {}),
    ]
    if reverse:
        steps.reverse()
    plan = FixPlan("test", "source", {str(path): steps})
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert all("history" not in v.ncattrs() for v in ds.variables.values())
        np.testing.assert_array_equal(ds["aux"][:], [1, 2, 3])
        assert ds.history.startswith("Keep global history")
    assert json.loads((tmp_path / "log").read_text())["rewrite_count"] == int(
        replacement
    )
    after = path.read_bytes()
    assert (
        apply_plan(plan, tmp_path / "again")["changed"] == 0
        and path.read_bytes() == after
    )


def test_other_rule_selection_does_not_silently_enable_c001(tmp_path):
    path = fixture(tmp_path / "only.nc", "NETCDF4")
    plan = FixPlan(
        "test",
        "source",
        {str(path): [Step("other", "drop_var_attr", {"var": "ta", "attr": "comment"})]},
    )
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds["ps"].history == "history on ps"
