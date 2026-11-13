"""Approved residual QA v4 cases, using existing recipes wherever possible."""

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.cli.common import classification
from esgf_fix_tool.plan import FixPlan, Step


def apply(path, tmp, steps):
    plan = FixPlan("test", "synthetic", {str(path): steps})
    before = path.read_bytes()
    apply_plan(plan, tmp / "dry", dry_run=True)
    assert path.read_bytes() == before
    apply_plan(plan, tmp / "log")
    after = path.read_bytes()
    assert (
        apply_plan(plan, tmp / "again")["changed"] == 0 and path.read_bytes() == after
    )
    return json.loads((tmp / "log").read_text())


@pytest.mark.parametrize("dtype", ["f4", "f8"])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
def test_msfty_uses_shared_transport_endpoint_without_changing_other_points(tmp_path, dtype, fmt):
    p = tmp_path / "msfty.nc"
    values = np.r_[np.linspace(-78, 89.36998, 331), 89.36998].astype(dtype)
    with netCDF4.Dataset(p, "w", format=fmt) as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.variable_id = "msfty"
        for name, size in [("time", 1), ("basin", 3), ("lev", 75), ("rlat", 332)]:
            ds.createDimension(name, size)
        ds.createVariable("rlat", dtype, ("rlat",))[:] = values
        ds["rlat"].setncatts(
            dict(name="rlat", standard_name="grid_latitude", units="degrees", axis="Y")
        )
        ds.createVariable("lev", "f8", ("lev",))[:] = np.arange(75)
        ds["lev"].setncatts(dict(units="m", standard_name="depth", axis="Z"))
        main = ds.createVariable(
            "msfty",
            "f4",
            ("time", "basin", "lev", "rlat"),
            zlib=fmt.startswith("NETCDF4"),
            complevel=2,
        )
        main[:] = np.arange(main.size).reshape(main.shape)
    inode = p.stat().st_ino
    row = apply(p, tmp_path, [Step("C025", "fix_msfty_coordinates", {})])
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds["rlat"][:-1], values[:-1])
        assert ds["rlat"][-1] == np.asarray(89.36999, dtype=dtype)
        assert np.all(np.diff(ds["rlat"][:]) > 0)
        np.testing.assert_array_equal(
            ds["msfty"][:], np.arange(ds["msfty"].size).reshape(ds["msfty"].shape)
        )
    assert p.stat().st_ino == inode and row["rewrite_count"] == 0


@pytest.mark.parametrize("index", [20, 330])
def test_unapproved_rlat_nonmonotonicity_is_not_repaired(tmp_path, index):
    from esgf_fix_tool.fixes.reviewed_structures import _approved_rlat

    data = np.linspace(-78, 89.36998, 332)
    data[index] = data[index - 1]
    with pytest.raises(ValueError, match="duplicated terminal"):
        _approved_rlat(data)


@pytest.mark.parametrize(
    "var,area,grid",
    [
        ("bigthetao", "sea", "g010"),
        ("masso", "sea", "g010"),
        ("siarea", "u", "g011"),
        ("siarea", "u", "g012"),
        ("siextent", "u", "g011"),
        ("siextent", "u", "g012"),
        ("sisnmass", "si", "g011"),
        ("sisnmass", "si", "g012"),
        ("sivol", "u", "g011"),
        ("sivol", "u", "g012"),
        ("so", "sea", "g010"),
        ("sos", "sea", "g010"),
        ("thetao", "sea", "g010"),
        ("tos", "sea", "g010"),
        ("volo", "sea", "g010"),
    ],
)
def test_orphan_grid_deletion_preserves_each_reduced_field(tmp_path, var, area, grid):
    p = tmp_path / "reduced.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.setncatts(
            dict(
                source_id="IPSL-CM6-ESMCO2",
                variable_id=var,
                horizontal_label="hm",
                grid_label=grid,
                branded_variable=f"{var}_tavg-u-hm-{area}",
            )
        )
        for name, size in [("time", 3), ("y", 1), ("x", 1), ("nvertex", 4)]:
            ds.createDimension(name, size)
        for name in ("nav_lat", "nav_lon"):
            ds.createVariable(name, "f8", ("y", "x"))[:] = 0
            ds[name].bounds = "bounds_" + name
            ds.createVariable("bounds_" + name, "f8", ("y", "x", "nvertex"))[:] = 0
        main = ds.createVariable(var, "f4", ("time", "y", "x"), zlib=True, complevel=2)
        main[:] = np.arange(3).reshape(3, 1, 1)
        main.coordinates = ""
        filters = main.filters()
    row = apply(
        p,
        tmp_path,
        [
            Step("C098", "drop_unused_horizontal_bounds", {"var": "bounds_nav_lat"}),
            Step("C098", "drop_unused_horizontal_bounds", {"var": "bounds_nav_lon"}),
        ],
    )
    with netCDF4.Dataset(p) as ds:
        assert set(ds.variables) == {var} and ds[var].dimensions == ("time", "y", "x")
        np.testing.assert_array_equal(ds[var][:], np.arange(3).reshape(3, 1, 1))
        assert ds[var].filters() == filters and "nvertex" not in ds.dimensions
    assert row["rewrite_count"] == 1


def test_redundant_grid_referenced_by_another_field_cannot_be_deleted(tmp_path):
    p = tmp_path / "active.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.setncatts(
            dict(
                source_id="IPSL-CM6-ESMCO2",
                variable_id="masso",
                horizontal_label="hm",
                grid_label="g010",
                branded_variable="masso_tavg-u-hm-sea",
            )
        )
        ds.createDimension("x", 1)
        ds.createDimension("y", 1)
        ds.createVariable("masso", "f4", ("y", "x"))[:] = 1
        ds["masso"].coordinates = "nav_lat"
        ds.createVariable("nav_lat", "f8", ("y", "x"))[:] = 0
    before = p.read_bytes()
    with pytest.raises(ValueError, match="still referenced"):
        apply_plan(
            FixPlan(
                "t",
                "s",
                {
                    str(p): [
                        Step(
                            "C098", "drop_unused_horizontal_bounds", {"var": "nav_lat"}
                        )
                    ]
                },
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before


def test_existing_klevp1_and_bnds_recipes_remove_only_invalid_standard_names(tmp_path):
    p = tmp_path / "technical.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.createDimension("klevp1", 80)
        ds.createDimension("bnds", 2)
        for name, description in [
            ("klevp1", "number of layer interfaces"),
            ("bnds", "bounds"),
        ]:
            v = ds.createVariable(name, "f8", (name,))
            v[:] = np.arange(v.size)
            v.standard_name = description
    apply(
        p,
        tmp_path,
        [
            Step(
                "C004",
                "drop_descriptive_standard_name",
                {"var": "klevp1", "expected": "number of layer interfaces"},
            ),
            Step("C011", "fix_invalid_standard_names", {"expected": ["bounds"]}),
        ],
    )
    with netCDF4.Dataset(p) as ds:
        assert 'klevp1' not in ds.variables and 'klevp1' not in ds.dimensions
        assert 'standard_name' not in ds['bnds'].ncattrs()
        np.testing.assert_array_equal(ds['bnds'][:], np.arange(ds['bnds'].size))


@pytest.mark.parametrize("reverse", [False, True])
def test_integer_basin_units_survive_the_older_units_recipe(tmp_path, reverse):
    from test_basin_mask import make

    p = tmp_path / "mask.nc"
    make(p, dtype="i4")
    steps = [
        Step("C101", "convert_basin_region_mask", {}),
        Step("C007", "drop_region_units", {"var": "basin", "expected": "1"}),
    ]
    if reverse:
        steps.reverse()
    row = apply(p, tmp_path, steps)
    assert row["rewrite_count"] == 0
    with netCDF4.Dataset(p) as ds:
        assert ds["basin"].units == "1"
        assert ds["basin"].dtype == np.dtype("i4")
        np.testing.assert_array_equal(
            ds["basin"].flag_values, np.arange(11, dtype="i4")
        )


def test_approved_v4_variants_match_the_expected_existing_rules():
    root = Path("/Users/glipsl/Desktop/QA_results_v4")
    if not root.is_dir():
        pytest.skip("Local QA v4 fixture unavailable")
    source = next(p for p in root.glob("qa_result_*.json") if ".cluster." not in p.name)
    result = classification(source, default_rule_paths(), None, False)
    expected = {
        "C025": (1, "fix"),
        "C098": (15, "fix"),
        "C004": (2, "fix"),
        "C011": (2, "fix"),
        "C107a": (56, "waived"),
        "C101": (1, "fix"),
    }
    for rid, (count, outcome) in expected.items():
        items = [x for x in result.items if x.match and x.match.rule.id == rid]
        assert len({x.finding.path for x in items}) == count, rid
        assert all(str(x.outcome) == outcome for x in items), rid
