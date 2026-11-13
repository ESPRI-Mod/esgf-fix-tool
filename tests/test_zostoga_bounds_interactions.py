"""Removing an auxiliary also satisfies the request to detach its bounds."""

import json
from itertools import permutations

import netCDF4
import numpy as np
import pytest
from test_reviewed_structures import zostoga_file

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step


@pytest.mark.parametrize("order", list(permutations(["C111", "lat", "lon"])))
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
def test_reduction_and_both_orphan_bounds_compose_and_replay(tmp_path, order, fmt):
    path = tmp_path / "zostoga.nc"
    zostoga_file(path, fmt)
    recipes = {
        "C111": Step("C111", "reduce_replicated_zostoga", {}),
        "lat": Step("C098", "drop_unused_horizontal_bounds", {"var": "nav_lat"}),
        "lon": Step("C098", "drop_unused_horizontal_bounds", {"var": "nav_lon"}),
    }
    plan = FixPlan("test", "source", {str(path): [recipes[r] for r in order]})
    before = path.read_bytes()
    apply_plan(plan, tmp_path / "dry", dry_run=True)
    assert path.read_bytes() == before
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds["zostoga"].dimensions == ("time",)
        assert "nav_lat" not in ds.variables and "nav_lon" not in ds.variables
        np.testing.assert_array_equal(ds["zostoga"][:], [10, 11, 12])
    row = json.loads((tmp_path / "log").read_text())
    assert row["steps"][0]["rule"] == "C111" and row["rewrite_count"] == 1
    assert [s["changed"] for s in row["steps"]] == [True, False, False]
    after = path.read_bytes()
    assert apply_plan(plan, tmp_path / "replay")["changed"] == 0
    assert path.read_bytes() == after


def test_an_active_horizontal_coordinate_is_still_protected(tmp_path):
    path = tmp_path / "unreduced.nc"
    zostoga_file(path)
    with netCDF4.Dataset(path, "a") as ds:
        ds.horizontal_label = "hm"
    before = path.read_bytes()
    plan = FixPlan(
        "t",
        "s",
        {
            str(path): [
                Step("C098", "drop_unused_horizontal_bounds", {"var": "nav_lat"})
            ]
        },
    )
    with pytest.raises(ValueError, match="still an active"):
        apply_plan(plan, tmp_path / "log")
    assert path.read_bytes() == before
