"""The remaining v6 center-coordinate fixes and universal endpoint dimension rename."""

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.fixes.hybrid import STANDARD_NAME, _coefficient_reference
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet

ROOT = Path(__file__).parents[1]


def center_file(path, variable, transposed=False):
    ref = _coefficient_reference()
    with netCDF4.Dataset(path, "w") as ds:
        ds.setncatts(
            {
                "source_id": "IPSL-CM6-ESMCO2",
                "realm": "atmos",
                "variable_id": variable,
                "branded_variable": variable + "_tpt-al-hxy-u",
                "vertical_label": "al",
                "frequency": "6hr",
            }
        )
        for n, size in [("time", 2), ("lev", 79), ("lat", 3), ("lon", 4), ("bnds", 2)]:
            ds.createDimension(n, size)
        lev = ds.createVariable("lev", "f8", ("lev",))
        lev[:] = ref[79]["ap"] + 101325 * ref[79]["b"]
        lev.units = "1"
        lev.standard_name = STANDARD_NAME
        for name in ("ap", "b"):
            v = ds.createVariable(name, "f8", ("lev",))
            v[:] = ref[79][name]
            if name == "ap":
                v.units = "Pa"
            dims = ("bnds", "lev") if transposed else ("lev", "bnds")
            pairs = np.column_stack((ref[80][name][:-1], ref[80][name][1:]))
            ds.createVariable(name + "_bnds", "f8", dims)[:] = (
                pairs.T if transposed else pairs
            )
        ds.createVariable("ps", "f4", ("time", "lat", "lon"))[:] = 100000.0
        ds["ps"].units = "Pa"
        v = ds.createVariable(
            variable,
            "f4",
            ("time", "lev", "lat", "lon"),
            zlib=True,
            complevel=2,
            chunksizes=(1, 10, 3, 4),
            fill_value=np.float32(1e20),
        )
        v[:] = np.arange(v.size, dtype="f4").reshape(v.shape)
    return ref


@pytest.mark.parametrize("variable", ["ta", "zg"])
@pytest.mark.parametrize(
    "period", ["185001010600-185501010000", "185501010600-186001010000"]
)
@pytest.mark.parametrize("selected", [["C102"], ["C103b"], ["C102", "C103b"]])
def test_each_ta_zg_file_normalizes_and_adds_center_bounds_in_place(
    tmp_path, monkeypatch, variable, period, selected
):
    p = (
        tmp_path
        / f"{variable}_tpt-al-hxy-u_6hr_glb_g111_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1_{period}.nc"
    )
    ref = center_file(p, variable)
    with netCDF4.Dataset(p) as ds:
        raw = {
            n: ds[n][:].tobytes()
            for n in (variable, "ps", "ap", "b", "ap_bnds", "b_bnds")
        }
        filters = ds[variable].filters()
        chunks = ds[variable].chunking()

    def refuse(*args):
        raise AssertionError(
            "Small lev and bounds edits must not stream the geophysical field"
        )

    monkeypatch.setattr(Batch, "write", refuse)
    q = FixPlan(
        "test",
        "v6",
        {str(p): [Step(rid, "normalize_hybrid_coordinate", {}) for rid in selected]},
    )
    before = p.read_bytes()
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert p.read_bytes() == before
    inode = p.stat().st_ino
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    with netCDF4.Dataset(p) as ds:
        expected = ref[79]["ap"] / 101325 + ref[79]["b"]
        pairs = np.column_stack(
            (
                ref[80]["ap"][:-1] / 101325 + ref[80]["b"][:-1],
                ref[80]["ap"][1:] / 101325 + ref[80]["b"][1:],
            )
        )
        np.testing.assert_array_equal(ds["lev"][:], expected)
        np.testing.assert_array_equal(ds["lev_bnds"][:], pairs)
        assert ds["lev"].bounds == "lev_bnds"
        assert ds["lev_bnds"].dimensions == ("lev", "bnds")
        assert ds["lev_bnds"].formula_terms == "ap: ap_bnds b: b_bnds ps: ps"
        assert np.all(pairs.min(axis=1) <= expected) and np.all(
            expected <= pairs.max(axis=1)
        )
        assert np.max(ds["lev"][:]) <= 1
        assert all(ds[n][:].tobytes() == values for n, values in raw.items())
        assert ds[variable].filters() == filters and ds[variable].chunking() == chunks
        assert (
            "units" not in ds["b"].ncattrs()
            and "computed_standard_name" not in ds["lev"].ncattrs()
        )
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "again")["changed"] == 0
    assert p.read_bytes() == after and p.stat().st_ino == inode


def test_80_point_al_axes_need_center_bounds_reference_and_are_not_reclassified_as_alh(
    tmp_path,
):
    p = tmp_path / "edt.nc"
    ref = _coefficient_reference()[80]
    with netCDF4.Dataset(p, "w") as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.realm = "atmos"
        ds.variable_id = "edt"
        ds.vertical_label = "al"
        ds.createDimension("lev", 80)
        ds.createDimension("time", 1)
        ds.createDimension("bnds", 2)
        ds.createVariable("lev", "f8", ("lev",))[:] = ref["ap"] / 101325 + ref["b"]
        ds["lev"].standard_name = STANDARD_NAME
        ds["lev"].bounds = "lev_bnds"
        for name in ("ap", "b"):
            ds.createVariable(name, "f8", ("lev",))[:] = ref[name]
        ds["ap"].units = "Pa"
        ds.createVariable("lev_bnds", "f8", ("lev", "bnds"))[:] = 0.0
        ds.createVariable("ps", "f4", ("time",))[:] = 100000.0
        ds["ps"].units = "Pa"
        ds.createVariable("edt", "f4", ("time", "lev"))[:] = 42.0
    before = p.read_bytes()
    with pytest.raises(ValueError, match="80 layer centers"):
        apply_plan(
            FixPlan(
                "t", "s", {str(p): [Step("C102", "normalize_hybrid_coordinate", {})]}
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before
    # Metadata cleanup must preserve possible user-supplied al bounds, even at 80 levels.
    apply_plan(
        FixPlan(
            "t",
            "s",
            {
                str(p): [
                    Step(
                        "C107",
                        "drop_var_attr",
                        {"var": "lev", "attr": "computed_standard_name"},
                    )
                ]
            },
        ),
        tmp_path / "metadata",
    )
    with netCDF4.Dataset(p) as ds:
        assert ds["lev"].bounds == "lev_bnds" and "lev_bnds" in ds.variables


@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("mode", ["unused", "used", "merge"])
def test_axis_nbounds_is_normalized_even_when_another_fix_selected_the_file(
    tmp_path, monkeypatch, fmt, mode
):
    p = tmp_path / "ocean.nc"
    with netCDF4.Dataset(p, "w", format=fmt) as ds:
        ds.variable_id = "field"
        ds.createDimension("axis_nbounds", 2)
        ds.createDimension("y", 3)
        ds.createDimension("vertices", 4)
        if mode == "merge":
            ds.createDimension("bnds", 2)
        ds.createVariable(
            "field", "f4", ("y",), zlib=fmt.startswith("NETCDF4"), complevel=2
        )[:] = [1, 2, 3]
        ds["field"].history = "remove me"
        ds.createVariable("vertices_latitude", "f8", ("y", "vertices"))[:] = np.arange(
            12
        ).reshape(3, 4)
        if mode != "unused":
            ds.createVariable("arbitrary_aux", "f8", ("y", "axis_nbounds"))[:] = (
                np.arange(6).reshape(3, 2)
            )
    calls = []
    write = Batch.write

    def record(batch, out):
        calls.append(out)
        return write(batch, out)

    monkeypatch.setattr(Batch, "write", record)
    q = FixPlan("t", "s", {str(p): [Step("C001", "drop_variable_histories", {})]})
    before = p.read_bytes()
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert p.read_bytes() == before
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert "axis_nbounds" not in ds.dimensions and len(ds.dimensions["bnds"]) == 2
        assert len(ds.dimensions["vertices"]) == 4
        np.testing.assert_array_equal(ds["field"][:], [1, 2, 3])
        np.testing.assert_array_equal(
            ds["vertices_latitude"][:], np.arange(12).reshape(3, 4)
        )
        if mode != "unused":
            assert ds["arbitrary_aux"].dimensions == ("y", "bnds")
            np.testing.assert_array_equal(
                ds["arbitrary_aux"][:], np.arange(6).reshape(3, 2)
            )
        if fmt.startswith("NETCDF4"):
            assert ds["field"].filters()["complevel"] == 2
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "again")["changed"] == 0 and p.read_bytes() == after
    assert len(calls) == (1 if mode == "merge" else 0)


def test_wrong_endpoint_size_is_rejected_before_writing(tmp_path):
    p = tmp_path / "wrong.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.createDimension("axis_nbounds", 3)
        ds.createVariable("field", "f4", ("axis_nbounds",))[:] = [1, 2, 3]
        ds["field"].history = "keep on failure"
    before = p.read_bytes()
    with pytest.raises(ValueError, match="size-two"):
        apply_plan(
            FixPlan("t", "s", {str(p): [Step("C001", "drop_variable_histories", {})]}),
            tmp_path / "log",
        )
    assert p.read_bytes() == before


def test_real_v6_report_plans_four_center_fixes_and_five_ocean_endpoint_fixes():
    root = Path("/Users/glipsl/Desktop/QA_results_v6")
    if not root.is_dir():
        pytest.skip("Local QA fixture unavailable")
    report = QAReport.load(
        next(p for p in root.glob("qa_result_*.json") if ".cluster." not in p.name)
    )
    classified = classify(report, RuleSet.load(ROOT / "src/esgf_fix_tool/rules/post_apply.yaml"))
    plan = FixPlan.from_classification(classified)
    for variable in ("ta", "zg"):
        paths = [
            p
            for p in plan.files
            if Path(p).name.startswith(variable + "_tpt-al-hxy-u_6hr_")
        ]
        assert len(paths) == 2
        for path in paths:
            assert {"C102", "C103b"} <= {step.rule for step in plan.files[path]}
    for variable in ("edt", "evu"):
        findings = [
            i
            for i in classified.items
            if i.match
            and i.match.rule.id == "C103e"
            and Path(i.finding.path).name.startswith(variable + "_")
        ]
        assert len(findings) == 1 and findings[0].outcome == Outcome.FIX
        assert any(
            s.fix == "center_hybrid_interfaces"
            for s in plan.files.get(findings[0].finding.path, [])
        )
    ocean = [i for i in classified.items if i.match and i.match.rule.id == "C068b"]
    assert {Path(i.finding.path).name.split("_", 1)[0] for i in ocean} == {
        "areacello",
        "basin",
        "deptho",
        "hfgeou",
        "sftof",
    }
    assert all(i.outcome == Outcome.FIX for i in ocean)
