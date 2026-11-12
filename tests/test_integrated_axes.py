"""Review-approved transport sums, native basin bounds and true hybrid interfaces."""

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.fixes.basin_latitude_bounds import reference
from esgf_fix_tool.fixes.hybrid import _coefficient_reference
from esgf_fix_tool.plan import FixPlan, Step


def transport_file(path, name, fmt):
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.variable_id = name
        ds.branded_variable = name + "_tavg-u-hxy-sea"
        ds.history = "Keep global history"
        for n, size in [
            ("time", None),
            ("olevel", 75),
            ("y", 3),
            ("x", 4),
            ("bnds", 2),
        ]:
            ds.createDimension(n, size)
        ds.createVariable("olevel", "f8", ("olevel",))[:] = np.arange(75)
        ds["olevel"].bounds = "olevel_bounds"
        ds.createVariable("olevel_bounds", "f8", ("olevel", "bnds"))[:] = np.arange(
            150
        ).reshape(75, 2)
        ds.createVariable("time", "f8", ("time",))[:] = [1, 2]
        ds["time"].bounds = "time_bnds"
        ds.createVariable("time_bnds", "f8", ("time", "bnds"))[:] = [[0, 1], [1, 2]]
        ds.createVariable("latitude", "f8", ("y", "x"))[:] = np.arange(12).reshape(3, 4)
        options = (
            {"zlib": True, "complevel": 2, "shuffle": True, "chunksizes": (1, 5, 3, 4)}
            if fmt.startswith("NETCDF4")
            else {}
        )
        v = ds.createVariable(
            name,
            "f4",
            ("time", "olevel", "y", "x"),
            fill_value=np.float32(1e20),
            **options,
        )
        v.units = "W"
        v.coordinates = "latitude"
        v.cell_methods = "area: mean where sea depth: sum where sea (over entire ocean column) time: mean"
        v.long_name = "Original transport title"
        v.description = "Keep this original description"
        data = np.ma.array(np.arange(1800, dtype="f4").reshape(2, 75, 3, 4), mask=False)
        data.mask[0, :, 0, 0] = True
        data.mask[1, :20, 1, 1] = True
        v[:] = data
    return data


@pytest.mark.parametrize("name", ["hfx", "hfy"])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
def test_vertical_sum_masks_metadata_compression_and_one_atomic_rewrite(
    tmp_path, monkeypatch, name, fmt
):
    p = tmp_path / "transport.nc"
    data = transport_file(p, name, fmt)
    original = p.read_bytes()
    q = FixPlan("t", "s", {str(p): [Step("C051", "integrate_heat_transport", {})]})
    assert apply_plan(q, tmp_path / "dry", dry_run=True)["changed"] == 1
    assert p.read_bytes() == original
    calls = []
    write = Batch.write

    def record(batch, out):
        assert p.read_bytes() == original
        calls.append(out)
        return write(batch, out)

    monkeypatch.setattr(Batch, "write", record)
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert "olevel" not in ds.variables and "olevel" not in ds.dimensions
        assert "olevel_bounds" not in ds.variables
        assert ds[name].dimensions == ("time", "y", "x")
        expected = data.astype("f8").sum(axis=1).astype("f4")
        np.testing.assert_array_equal(ds[name][:].mask, expected.mask)
        np.testing.assert_array_equal(ds[name][:].compressed(), expected.compressed())
        assert ds[name].long_name == "Original transport title"
        assert ds[name].description == "Keep this original description"
        assert ds[name].cell_methods.endswith("time: mean")
        assert ds[name].coordinates == "latitude"
        assert ds.history == "Keep global history"
        np.testing.assert_array_equal(ds["time_bnds"][:], [[0, 1], [1, 2]])
        if fmt.startswith("NETCDF4"):
            assert ds[name].filters()["complevel"] == 2
            assert ds[name].chunking() == [1, 3, 4]
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert p.read_bytes() == after and len(calls) == 1


@pytest.mark.parametrize(
    "failure", ["nonfinite", "retained_vertical", "failed_verification", "packing"]
)
def test_transport_failure_never_replaces_original(tmp_path, monkeypatch, failure):
    p = tmp_path / "transport.nc"
    transport_file(p, "hfx", "NETCDF4")
    with netCDF4.Dataset(p, "a") as ds:
        if failure == "nonfinite":
            ds["hfx"][1, 0, 0, 0] = np.inf
        if failure == "retained_vertical":
            ds.createVariable("other", "f4", ("olevel",))[:] = 1
        if failure == "packing":
            ds["hfx"].scale_factor = 2.0
    if failure == "failed_verification":

        def refuse(*args):
            raise ValueError("Injected failed verification")

        monkeypatch.setattr(Batch, "verify", refuse)
    before = p.read_bytes()
    q = FixPlan("t", "s", {str(p): [Step("C051", "integrate_heat_transport", {})]})
    with pytest.raises(ValueError):
        apply_plan(q, tmp_path / "log")
    assert p.read_bytes() == before
    assert not list(tmp_path.glob("*.fixtmp"))


def basin_file(path):
    centers, _ = reference()
    with netCDF4.Dataset(path, "w") as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.variable_id = "htovovrt"
        ds.branded_variable = 'htovovrt_tavg-u-hyb-sea'
        ds.grid_label = 'g112'
        ds.horizontal_label = 'hyb'
        for n, s in [("time", 2), ("basin", 3), ("lat", 332), ("strlen", 21)]:
            ds.createDimension(n, s)
        ds.createVariable("lat", "f8", ("lat",))[:] = centers
        ds["lat"][-1] = 89.36999
        ds["lat"].standard_name = "latitude"
        ds["lat"].units = "degrees_north"
        ds.createVariable("sector", "S1", ("basin", "strlen"))[:] = np.full(
            (3, 21), b" ", dtype="S1"
        )
        ds.createVariable(
            "htovovrt", "f4", ("time", "basin", "lat"), zlib=True, complevel=2
        )[:] = 10.0


def test_legacy_latitude_plan_updates_conventional_bounds_and_preserves_science(tmp_path, monkeypatch):
    p = tmp_path / 'basin.nc'; basin_file(p)
    with netCDF4.Dataset(p,'a') as ds:
        ds.createDimension('bnds',2)
        ds.createVariable('lat_bnds','f8',('lat','bnds'))[:] = reference()[1]
        ds['lat'].bounds='lat_bnds'
    calls=[];write=Batch.write
    def record(batch,out):calls.append(out);return write(batch,out)
    monkeypatch.setattr(Batch,'write',record)
    q=FixPlan('t','s',{str(p):[Step('C103','add_basin_latitude_bounds',{})]})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert ds['lat'].bounds == 'lat_bnds'
        assert np.all(np.diff(ds['lat_bnds'][:], axis=1) > 0)
        assert np.all(ds['htovovrt'][:] == 10)
    after=p.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and p.read_bytes()==after
    assert len(calls)==0


def test_native_bounds_refuse_different_latitude_axis(tmp_path):
    p = tmp_path / "basin.nc"
    basin_file(p)
    with netCDF4.Dataset(p, "a") as ds:
        ds["lat"][100] += 0.01
    before = p.read_bytes()
    with pytest.raises(ValueError, match="reference"):
        apply_plan(
            FixPlan(
                "t", "s", {str(p): [Step("C103", "add_basin_latitude_bounds", {})]}
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before


@pytest.mark.parametrize("name", ["edt", "evu", "rsu"])
@pytest.mark.parametrize("separate_binding", [False, True])
def test_80_interfaces_bind_then_normalize_without_invented_bounds(
    tmp_path, name, separate_binding
):
    p = tmp_path / "interfaces.nc"
    ref = _coefficient_reference()[80]
    with netCDF4.Dataset(p, "w") as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.realm = "atmos"
        ds.variable_id = name
        for n, s in [("time", 2), ("lev", 80), ("klevp1", 80)]:
            ds.createDimension(n, s)
        ds.createVariable("lev", "f8", ("lev",))[:] = ref["ap"] + 101325 * ref["b"]
        ds["lev"].units = "Pa"
        for n in ("ap", "b"):
            ds.createVariable(n, "f8", ("klevp1",))[:] = ref[n]
        ds.createVariable("ps", "f4", ("time",))[:] = [100000, 90000]
        ds["ps"].units = "Pa"
        ds.createVariable(name, "f4", ("time", "lev"), zlib=True, complevel=2)[:] = (
            np.arange(160).reshape(2, 80)
        )
        ds.createVariable("b_bnds", "f8", ("klevp1",))[:] = ref["b"]
        ds["b_bnds"].coordinates = ""
    steps = [Step("C102", "normalize_hybrid_coordinate", {})]
    if separate_binding:
        steps.append(Step("C074a", "fix_hybrid_coefficients", {"coordinate": "auto"}))
    q = FixPlan("t", "s", {str(p): steps})
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        np.testing.assert_array_equal(ds["lev"][:], ref["ap"] / 101325 + ref["b"])
        assert "bounds" not in ds["lev"].ncattrs() and "lev_bnds" not in ds.variables
        assert (
            ds["lev"].long_name
            == "hybrid sigma pressure coordinate"
        )
        assert ds["lev"].formula_terms == "ap: ap b: b ps: ps"
        for n in ("ap", "b"):
            assert ds[n].dimensions == ("lev",)
            np.testing.assert_array_equal(ds[n][:], ref[n])
        assert "b_bnds" not in ds.variables
        np.testing.assert_array_equal(ds[name][:], np.arange(160).reshape(2, 80))
        assert ds[name].filters()["complevel"] == 2
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert p.read_bytes() == after


def test_longitude_overlap_matches_both_original_and_renamed_bounds():
    from pathlib import Path

    from esgf_fix_tool.report import Finding
    from esgf_fix_tool.rules import RuleSet

    rules = RuleSet.load(Path(__file__).parents[1] / "rules/post_apply.yaml")
    for name in ("bounds_lon", "lon_bnds"):
        finding = Finding(
            3,
            "[CMIP7] [COORD008] Coordinate bounds",
            f"Adjacent cells in '{name}' have overlapping bounds.",
            "CMIP7.ATM.ta",
            "file.nc",
        )
        match = rules.match(finding)
        assert (
            match.rule.id == "C059" and match.rule.fix == "normalize_regular_longitude"
        )


def test_actual_v4_report_selects_each_reviewed_variant():
    from pathlib import Path

    from esgf_fix_tool.classify import Outcome, classify
    from esgf_fix_tool.report import QAReport
    from esgf_fix_tool.rules import RuleSet

    root = Path("/Users/glipsl/Desktop/QA_results_v4")
    if not root.is_dir():
        pytest.skip("Local QA fixture is unavailable")
    report = QAReport.load(
        next(p for p in root.glob("qa_result_*.json") if ".cluster." not in p.name)
    )
    items = classify(
        report, RuleSet.load(Path(__file__).parents[1] / "rules/post_apply.yaml")
    ).items
    expected = {
        "C051": 4,
        "C073a": 5,
        "C102": 23,
        "C059": 2,

        "C067": 2,
        "C068": 2,
    }
    for rid, count in expected.items():
        selected = [i for i in items if i.match and i.match.rule.id == rid]
        assert len({i.finding.path for i in selected}) == count, rid
        assert all(i.outcome == Outcome.FIX for i in selected), rid
    missing_names = [i for i in items if i.match and i.match.rule.id == 'C104']
    assert len({i.finding.path for i in missing_names}) == 2
    assert all(i.outcome == Outcome.FIX for i in missing_names)
    selected = [i for i in items if i.match and i.match.rule.id == "C103" and "msfty" in i.finding.path]
    assert len(selected) == 1 and selected[0].outcome == Outcome.FIX


@pytest.mark.parametrize(
    "method", ["fix_hybrid_coefficients", "normalize_hybrid_coordinate"]
)
def test_interface_coefficient_mismatch_is_never_silently_replaced(tmp_path, method):
    p = tmp_path / "interfaces.nc"
    ref = _coefficient_reference()[80]
    with netCDF4.Dataset(p, "w") as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.realm = "atmos"
        ds.variable_id = "edt"
        for n, size in [("time", 1), ("lev", 80), ("klevp1", 80)]:
            ds.createDimension(n, size)
        ds.createVariable("lev", "f8", ("lev",))[:] = ref["ap"] + 101325 * ref["b"]
        for n in ("ap", "b"):
            ds.createVariable(n, "f8", ("klevp1",))[:] = ref[n]
        ds["b"][20] += 0.01
        ds.createVariable("ps", "f4", ("time",))[:] = 100000
        ds["ps"].units = "Pa"
        ds.createVariable("edt", "f4", ("time", "lev"))[:] = 1.0
    before = p.read_bytes()
    params = {"coordinate": "auto"} if method == "fix_hybrid_coefficients" else {}
    with pytest.raises(ValueError, match="no value replacement"):
        apply_plan(
            FixPlan("t", "s", {str(p): [Step("test", method, params)]}),
            tmp_path / "log",
        )
    assert p.read_bytes() == before
