"""Final user decisions: cleanup, scoped exceptions and DR metadata precedence."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest
from test_final_coordinates import hybrid_file
from test_integrated_axes import basin_file

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.fixes.layouts import _attribute_type
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet


def apply_replay(path, tmp, steps):
    q = FixPlan("t", "s", {str(path): steps})
    before = path.read_bytes()
    apply_plan(q, tmp / "dry", dry_run=True)
    assert path.read_bytes() == before
    apply_plan(q, tmp / "log")
    after = path.read_bytes()
    assert apply_plan(q, tmp / "again")["changed"] == 0
    assert path.read_bytes() == after


@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("label", ["al", "alh"])
@pytest.mark.parametrize("reverse", [False, True])
def test_79_point_alh_omits_bounds_while_al_retains_them_and_active_values(
    tmp_path, fmt, label, reverse
):
    p = tmp_path / "hybrid.nc"
    ref = hybrid_file(p, fmt)
    with netCDF4.Dataset(p, "a") as ds:
        ds.vertical_label = label
        ds.createDimension("klev", 79)
        ds.createVariable("klev", "i4", ("klev",))[:] = np.arange(79)
        ds.createVariable("lev_bnds", "f8", ("lev", "bnds"))[:] = 0.0
        ds["lev"].bounds = "lev_bnds"
        ds["lev"].computed_standard_name = "air_pressure"
        raw = {
            n: ds[n][:].tobytes() for n in ("ap", "b", "ap_bnds", "b_bnds", "ps", "ta")
        }
    steps = [
        Step("C074b", "fix_hybrid_coefficients", {"coordinate": "auto"}),
        Step("C102", "normalize_hybrid_coordinate", {}),
    ]
    if reverse:
        steps.reverse()
    apply_replay(p, tmp_path, steps)
    with netCDF4.Dataset(p) as ds:
        for n in ("ap", "b", "ps", "ta"):
            assert ds[n][:].tobytes() == raw[n]
        np.testing.assert_array_equal(
            ds["lev"][:], ref[79]["ap"] / 101325 + ref[79]["b"]
        )
        assert "computed_standard_name" not in ds["lev"].ncattrs()
        assert "units" not in ds["b"].ncattrs()
        assert "klev" not in ds.variables and "klev" not in ds.dimensions
        for n in ("lev_bnds", "ap_bnds", "b_bnds"):
            assert (n in ds.variables) == (label == "al")
        assert ("bounds" in ds["lev"].ncattrs()) == (label == "al")
        assert ds["lev"].long_name == "hybrid sigma pressure coordinate"
        suffix = "" if label == "al" else "(k)"
        assert ds["ap"].long_name == "vertical coordinate formula term: ap" + suffix
        if label == "al":
            assert ds["ap_bnds"][:].tobytes() == raw["ap_bnds"]
            assert ds["b_bnds"][:].tobytes() == raw["b_bnds"]
            assert "units" not in ds["b_bnds"].ncattrs()


def test_half_level_bounds_still_used_by_another_variable_are_not_deleted(tmp_path):
    p = tmp_path / "used.nc"
    hybrid_file(p)
    with netCDF4.Dataset(p, "a") as ds:
        ds.vertical_label = "alh"
        ds["ta"].ancillary_variables = "ap_bnds"
    before = p.read_bytes()
    with pytest.raises(ValueError, match="still referenced"):
        apply_plan(
            FixPlan(
                "t",
                "s",
                {
                    str(p): [
                        Step("C074b", "fix_hybrid_coefficients", {"coordinate": "auto"})
                    ]
                },
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before


@pytest.mark.parametrize("name", ["klev", "klev1", "klevp1"])
@pytest.mark.parametrize("active", [False, True])
def test_technical_index_removed_only_if_unused(tmp_path, name, active):
    p = tmp_path / "index.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.variable_id = "field"
        ds.createDimension(name, 4)
        ds.createVariable(name, "i4", (name,))[:] = np.arange(4)
        ds.createVariable("field", "f4", (name,) if active else ())[:] = 1
    apply_replay(p, tmp_path, [Step("C115", "clean_retired_metadata", {})])
    with netCDF4.Dataset(p) as ds:
        assert (name in ds.variables) == active and (name in ds.dimensions) == active
        assert np.all(ds["field"][:] == 1)


@pytest.mark.parametrize("used", [False, True])
def test_all_backup_prefixes_retired_and_active_reference_blocks_deletion(
    tmp_path, used
):
    p = tmp_path / "temporary.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.variable_id = "field"
        ds.createDimension("lat", 3)
        ds.createDimension("nvertex", 2)
        ds.createVariable("fix_original_lat_bnds", "f8", ("lat", "nvertex"))[:] = 0
        ds.createVariable("fix_original_custom", "f4", ())[:] = 2
        ds.createVariable("field", "f4", ("lat",))[:] = [1, 2, 3]
        if used:
            ds["field"].ancillary_variables = "fix_original_custom"
    q = FixPlan("t", "s", {str(p): [Step("C115", "clean_retired_metadata", {})]})
    before = p.read_bytes()
    if used:
        with pytest.raises(ValueError, match="still referenced"):
            apply_plan(q, tmp_path / "log")
        assert p.read_bytes() == before
    else:
        apply_replay(p, tmp_path, q.files[str(p)])
        with netCDF4.Dataset(p) as ds:
            assert not any(n.startswith("fix_original_") for n in ds.variables)
            assert "nvertex" not in ds.dimensions
            np.testing.assert_array_equal(ds["field"][:], [1, 2, 3])


@pytest.mark.parametrize("legacy", ["axis_nbounds", "axis_bounds", "nvertex"])
def test_clean_two_endpoint_merge_has_no_persistent_backup_and_keeps_four_vertices(
    tmp_path, legacy
):
    p = tmp_path / "bounds.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.variable_id = "field"
        ds.createDimension("bnds", 2)
        ds.createDimension(legacy, 2)
        ds.createDimension("lat", 3)
        ds.createDimension("vertices", 4)
        ds.createVariable("bnds", "i4", ("bnds",))[:] = [0, 1]
        ds.createVariable("lat", "f8", ("lat",))[:] = [1, 2, 3]
        ds["lat"].bounds = "lat_bnds"
        ds.createVariable("lat_bnds", "f8", ("lat", legacy))[:] = np.arange(6).reshape(
            3, 2
        )
        ds.createVariable("field", "f4", ("lat",), zlib=True, complevel=2)[:] = [
            10,
            20,
            30,
        ]
        ds.createVariable("polygon", "f8", ("lat", "vertices"))[:] = 0
    calls = []
    write = Batch.write

    def record(batch, out):
        calls.append(out)
        return write(batch, out)

    from unittest.mock import patch

    with patch.object(Batch, "write", record):
        apply_replay(p, tmp_path, [Step("C068", "normalize_bounds_dimension", {})])
    assert len(calls) == 1
    with netCDF4.Dataset(p) as ds:
        assert legacy not in ds.dimensions
        assert ds["lat_bnds"].dimensions == ("lat", "bnds")
        assert ds["field"].filters()["complevel"] == 2
        assert len(ds.dimensions["vertices"]) == 4
        assert not any(n.startswith("fix_original_") for n in ds.variables)
        np.testing.assert_array_equal(ds["field"][:], [10, 20, 30])


@pytest.mark.parametrize(
    "var,description", [("ta", "Air Temperature"), ("zg", "Geopotential height")]
)
@pytest.mark.parametrize("kind", ["missing", "char", "string"])
def test_description_identical_in_text_and_nc_char_type_without_losing_comment(
    tmp_path, var, description, kind
):
    p = tmp_path / "description.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.variable_id = var
        ds.branded_variable = var + "_tpt-al-hxy-u"
        ds.frequency = "6hr"
        v = ds.createVariable(var, "f4")
        v.assignValue(42)
        v.comment = "Keep comment"
        if kind == "char":
            v.description = description
        elif kind == "string":
            v.setncattr_string("description", description)
    apply_replay(p, tmp_path, [Step("C116", "harmonize_main_description", {})])
    with netCDF4.Dataset(p) as ds:
        assert (
            ds[var].description == description
            and _attribute_type(ds[var], "description") == 2
        )
        assert ds[var].comment == "Keep comment" and ds[var].getValue() == 42


def test_basin_removal_never_deletes_bounds_on_another_product(tmp_path):
    p = tmp_path / "other.nc"
    basin_file(p)
    with netCDF4.Dataset(p, "a") as ds:
        ds.variable_id = "other"
        ds.createDimension("bnds", 2)
        ds.createVariable("lat_bnds", "f8", ("lat", "bnds"))[:] = 0
        ds["lat"].bounds = "lat_bnds"
    before = p.read_bytes()
    with pytest.raises(ValueError, match="eight reviewed"):
        apply_plan(
            FixPlan(
                "t", "s", {str(p): [Step("C103d", "drop_basin_latitude_bounds", {})]}
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before


def test_v5_scope_and_description_peer_expansion():
    root = Path("/Users/glipsl/Desktop/QA_results_v5")
    if not root.is_dir():
        pytest.skip("Local QA fixture unavailable")
    report = QAReport.load(
        next(p for p in root.glob("qa_result_*.json") if ".cluster." not in p.name)
    )
    c = classify(
        report, RuleSet.load(Path(__file__).parents[1] / "src/esgf_fix_tool/rules/post_apply.yaml")
    )
    for rid, count in {
        "C115": 2,
        "C116": 2,
        "C068a": 2,
        "C074b": 6,
        "C103d": 8,
        "C106a": 5,
        "C107": 81,
        "C108": 81,
    }.items():
        items = [i for i in c.items if i.match and i.match.rule.id == rid]
        assert len({i.finding.path for i in items}) == count, rid
        assert all(i.outcome == Outcome.FIX for i in items), rid
    p = FixPlan.from_classification(c, only_rules={"C116"})
    assert len(p.files) == 4 and all("_tpt-al-hxy-u_6hr_" in f for f in p.files)
    bounds_plan = FixPlan.from_classification(c, only_rules={'C068a'})
    assert len(bounds_plan.files) == 4


@pytest.mark.parametrize(
    "var",
    [
        "hfbasin",
        "hfbasinpmadv",
        "hfbasinpmdiff",
        "htovgyre",
        "htovovrt",
        "sltbasin",
        "sltovgyre",
        "sltovovrt",
    ],
)
def test_eight_basin_products_rebuild_bounds_and_drop_only_retired_aliases(tmp_path, var):
    p = tmp_path / "basin.nc"
    basin_file(p)
    with netCDF4.Dataset(p, "a") as ds:
        if var != "htovovrt":
            ds.renameVariable("htovovrt", var)
        ds.variable_id = var
        ds.branded_variable = var + "_tavg-u-hyb-sea"
        ds.createDimension("bnds", 2)
        ds.createVariable("lat_bnds", "f8", ("lat", "bnds"))[:] = 0
        ds["lat"].bounds = "lat_bnds"
        ds.createVariable("nav_lon", "f8", ("lat",))[:] = 0
        ds.createVariable("basin", "i4", ("basin",))[:] = [1, 2, 3]
        lat = ds["lat"][:].tobytes()
        sector = ds["sector"][:].tobytes()
    apply_replay(p, tmp_path, [Step("C103d", "drop_basin_latitude_bounds", {})])
    with netCDF4.Dataset(p) as ds:
        assert ds["lat"].bounds == "lat_bnds"
        assert np.all(np.diff(ds["lat_bnds"][:], axis=1) > 0)
        assert "nav_lon" not in ds.variables and ds["lat"][:].tobytes() == lat
        assert ds["sector"][:].tobytes() == sector
        np.testing.assert_array_equal(ds["basin"][:], [1, 2, 3])
        assert np.all(ds[var][:] == 10)


def test_basin_bounds_kept_on_different_grid_even_for_same_variable(tmp_path):
    p = tmp_path / "different_grid.nc"
    basin_file(p)
    with netCDF4.Dataset(p, "a") as ds:
        ds.grid_label = "g111"
        ds.createDimension("bnds", 2)
        ds.createVariable("lat_bnds", "f8", ("lat", "bnds"))[:] = 0
        ds["lat"].bounds = "lat_bnds"
    before = p.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(
            FixPlan(
                "t", "s", {str(p): [Step("C103d", "drop_basin_latitude_bounds", {})]}
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before
