"""User-approved 1-D bounds and shared terminal latitude on nine NEMO products."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.basin_latitude_bounds import (
    canonical_latitude,
    conventional_bounds,
    reference,
)
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet

PRODUCTS = (
    "hfbasin", "hfbasinpmadv", "hfbasinpmdiff", "htovgyre", "htovovrt",
    "sltbasin", "sltovgyre", "sltovovrt", "msfty",
)


def profile_file(path, product, fmt="NETCDF4", existing=False):
    msfty = product == "msfty"
    coordinate = "rlat" if msfty else "lat"
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.setncatts(dict(
            source_id="IPSL-CM6-ESMCO2", variable_id=product,
            branded_variable=product + ("_tavg-ol-ht-sea" if msfty else "_tavg-u-hyb-sea"),
            grid_label="g112", horizontal_label="ht" if msfty else "hyb",
            history="Keep global history",
        ))
        for name, size in (("time", 2), ("basin", 3), (coordinate, 332), ("strlen", 21)):
            ds.createDimension(name, size)
        dims = ("time", "basin", coordinate)
        if msfty:
            ds.createDimension("lev", 75)
            ds.createVariable("lev", "f8", ("lev",))[:] = np.arange(75)
            ds["lev"].setncatts(dict(units="m", standard_name="depth", axis="Z"))
            dims = ("time", "basin", "lev", coordinate)
        lat = ds.createVariable(coordinate, "f8", (coordinate,))
        values = canonical_latitude()
        if msfty:
            values[-1] = np.nextafter(values[-2], np.inf)
        lat[:] = values
        lat.setncatts(dict(
            standard_name="grid_latitude" if msfty else "latitude",
            units="degrees" if msfty else "degrees_north", axis="Y",
            long_name="Grid Latitude" if msfty else "Latitude",
            comment="Keep coordinate comment",
        ))
        if existing:
            ds.createDimension("bnds", 2)
            ds.createVariable(coordinate + "_bnds", "f8", (coordinate, "bnds"))[:] = reference()[1]
            lat.bounds = coordinate + "_bnds"
        ds.createVariable("sector", "S1", ("basin", "strlen"))[:] = np.full((3, 21), b" ", dtype="S1")
        ds.createVariable("basin", "i4", ("basin",))[:] = [1, 2, 3]
        options = dict(zlib=True, complevel=2) if fmt.startswith("NETCDF4") else {}
        main = ds.createVariable(product, "f4", dims, **options)
        main[:] = np.arange(main.size, dtype="f4").reshape(main.shape)
        main.setncatts(dict(coordinates="sector", long_name="Keep scientific title"))
    return coordinate


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("existing", [False, True])
def test_nine_profiles_get_identical_bounds_without_rewriting_science(
    tmp_path, monkeypatch, product, fmt, existing
):
    p = tmp_path / f"{product}.nc"
    name = profile_file(p, product, fmt, existing)
    with netCDF4.Dataset(p) as ds:
        science = ds[product][:].tobytes()
        filters, chunks = ds[product].filters(), ds[product].chunking()
        metadata = {a: ds[name].getncattr(a) for a in ds[name].ncattrs() if a != "bounds"}
        basin, sector = ds["basin"][:].tobytes(), ds["sector"][:].tobytes()

    def refuse(*args):
        raise AssertionError("Only the small latitude arrays may be written")

    monkeypatch.setattr(Batch, "write", refuse)
    plan = FixPlan("test", "qa", {str(p): [Step("C103", "add_basin_latitude_bounds", {})]})
    before, inode = p.read_bytes(), p.stat().st_ino
    apply_plan(plan, tmp_path / "dry", dry_run=True)
    assert p.read_bytes() == before
    assert apply_plan(plan, tmp_path / "log")["changed"] == 1
    with netCDF4.Dataset(p) as ds:
        target = name + "_bnds"
        centers, bounds = ds[name][:], ds[target][:]
        np.testing.assert_array_equal(centers, canonical_latitude())
        np.testing.assert_array_equal(bounds, conventional_bounds(canonical_latitude()))
        assert ds[target].dimensions == (name, "bnds")
        assert ds[name].bounds == target and len(ds.dimensions["bnds"]) == 2
        assert bounds[0, 0] == -84.27803802490234
        assert bounds[-1, 1] == 89.48226798461914
        assert bounds[-1, 1] - bounds[-1, 0] > 0.1
        assert np.all(bounds[:, 1] > bounds[:, 0])
        np.testing.assert_array_equal(bounds[1:, 0], bounds[:-1, 1])
        assert np.all(bounds[:, 0] <= centers) and np.all(centers <= bounds[:, 1])
        assert {a: ds[name].getncattr(a) for a in ds[name].ncattrs() if a != "bounds"} == metadata
        assert "Conventional" in ds[target].comment and "not native NEMO" in ds[target].comment
        assert ds[product][:].tobytes() == science
        assert ds[product].filters() == filters and ds[product].chunking() == chunks
        assert ds["basin"][:].tobytes() == basin and ds["sector"][:].tobytes() == sector
        if product == "msfty":
            assert "lat" not in ds.variables and "lat_bnds" not in ds.variables
            assert centers[-1] == 89.36999
    after = p.read_bytes()
    assert apply_plan(plan, tmp_path / "replay")["changed"] == 0
    assert p.read_bytes() == after and p.stat().st_ino == inode


@pytest.mark.parametrize("reverse", [False, True])
def test_msfty_coordinate_recipe_and_bounds_share_the_same_endpoint(tmp_path, reverse):
    p = tmp_path / "msfty.nc"
    profile_file(p, "msfty")
    steps = [Step("C025", "fix_msfty_coordinates", {}), Step("C103", "add_basin_latitude_bounds", {})]
    if reverse:
        steps.reverse()
    plan = FixPlan("test", "qa", {str(p): steps})
    assert apply_plan(plan, tmp_path / "log")["changed"] == 1
    assert apply_plan(plan, tmp_path / "replay")["changed"] == 0
    with netCDF4.Dataset(p) as ds:
        assert ds["rlat"][-1] == 89.36999
        assert ds["rlat"].standard_name == "grid_latitude"
        assert ds["rlat"].units == "degrees" and ds["rlat"].axis == "Y"


def test_legacy_removal_plan_cannot_remove_new_bounds(tmp_path):
    p = tmp_path / "basin.nc"
    profile_file(p, "hfbasin")
    plan = FixPlan("test", "qa", {str(p): [
        Step("C103", "add_basin_latitude_bounds", {}),
        Step("C103d", "drop_basin_latitude_bounds", {}),
    ]})
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert ds["lat"].bounds == "lat_bnds" and "lat_bnds" in ds.variables
    assert apply_plan(plan, tmp_path / "replay")["changed"] == 0


@pytest.mark.parametrize("error", ["latitude", "grid", "brand", "bnds", "packing"])
def test_unreviewed_geometry_is_rejected_without_changing_source(tmp_path, error):
    p = tmp_path / "basin.nc"
    profile_file(p, "hfbasin")
    with netCDF4.Dataset(p, "a") as ds:
        if error == "latitude":
            ds["lat"][100] += 0.01
        elif error == "grid":
            ds.grid_label = "g111"
        elif error == "brand":
            ds.branded_variable = "hfbasin_tavg-u-hxy-u"
        elif error == "bnds":
            ds.createDimension("bnds", 4)
        elif error == "packing":
            ds["lat"].scale_factor = 2.0
    before = p.read_bytes()
    plan = FixPlan("test", "qa", {str(p): [Step("C103", "add_basin_latitude_bounds", {})]})
    with pytest.raises(ValueError):
        apply_plan(plan, tmp_path / "log")
    assert p.read_bytes() == before


def test_latest_real_qa_selects_all_nine_missing_latitude_bounds():
    root = Path("/Users/glipsl/Desktop/QA_results_v6_fixed")
    if not root.is_dir():
        pytest.skip("Local QA fixture unavailable")
    p = next(p for p in root.glob("qa_result_*.json") if ".cluster." not in p.name)
    classified = classify(QAReport.load(p), RuleSet.load(*default_rule_paths()))
    selected = [i for i in classified.items if i.match and i.match.rule.id == "C103"]
    assert len({i.finding.path for i in selected}) == 9
    assert {Path(i.finding.path).name.split("_", 1)[0] for i in selected} == set(PRODUCTS)
    assert all(i.outcome is Outcome.FIX for i in selected)
