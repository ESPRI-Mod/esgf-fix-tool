"""Reference binding must precede normalization and define its verification baseline."""

import json
from itertools import permutations

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.fixes.hybrid import _coefficient_reference
from esgf_fix_tool.plan import FixPlan, Step, order_steps


def source_file(path, fmt, count, *, bounds=True):
    ref = _coefficient_reference()
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.realm = "atmos"
        ds.variable_id = "ta"
        for name, size in [("time", 2), ("lev", 79), ("coeff", count), ("bnds", 2)]:
            ds.createDimension(name, size)
        ds.createVariable("lev", "f8", ("lev",))[:] = (
            ref[79]["ap"] + 101325 * ref[79]["b"]
        )
        ds["lev"].units = "Pa"
        for name in ("ap", "b"):
            ds.createVariable(name, "f8", ("coeff",))[:] = ref[count][name]
            ds[name].units = "Pa" if name == "ap" else "1"
            if bounds:
                ds.createVariable(name + "_bnds", "f8", ("bnds", "coeff"))[:] = np.vstack(
                    [ref[count][name]] * 2
                )
        ds.createVariable("ps", "f4", ("time",))[:] = [100000, 90000]
        ds["ps"].units = "Pa"
        ds.createVariable("ta", "i2", ("time", "lev"))[:] = np.arange(158).reshape(
            2, 79
        )
    return ref


@pytest.mark.parametrize("ids", list(permutations(["C074a", "C102", "C103b"])))
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("count", [79, 80])
def test_reference_replacement_and_normalization_share_one_verified_rewrite(
    tmp_path, monkeypatch, ids, fmt, count
):
    p = tmp_path / "ta.nc"
    ref = source_file(p, fmt, count)
    recipes = {
        "C074a": Step("C074a", "fix_hybrid_coefficients", {"coordinate": "alevel"}),
        "C102": Step("C102", "normalize_hybrid_coordinate", {}),
        "C103b": Step("C103b", "normalize_hybrid_coordinate", {}),
    }
    q = FixPlan("t", "s", {str(p): [recipes[r] for r in ids]})
    before = p.read_bytes()
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert p.read_bytes() == before
    calls = []
    write = Batch.write

    def record(batch, out):
        calls.append(out)
        return write(batch, out)

    monkeypatch.setattr(Batch, "write", record)
    apply_plan(q, tmp_path / "log")
    assert len(calls) == 1
    entry = json.loads((tmp_path / "log").read_text())
    assert entry["steps"][0]["rule"] == "C074a" and entry["rewrite_count"] == 1
    with netCDF4.Dataset(p) as ds:
        for name in ("ap", "b"):
            assert ds[name].dimensions == ("lev",)
            assert ds[name + "_bnds"].dimensions == ("lev", "bnds")
            np.testing.assert_array_equal(ds[name][:], ref[79][name])
            np.testing.assert_array_equal(
                ds[name + "_bnds"][:],
                np.column_stack((ref[80][name][:-1], ref[80][name][1:])),
            )
        np.testing.assert_array_equal(
            ds["lev"][:], ref[79]["ap"] / 101325 + ref[79]["b"]
        )
        np.testing.assert_array_equal(ds["ta"][:], np.arange(158).reshape(2, 79))
        np.testing.assert_array_equal(ds["ps"][:], [100000, 90000])
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert p.read_bytes() == after and len(calls) == 1


def test_missing_coefficient_prerequisite_fails_without_rewriting(
    tmp_path, monkeypatch
):
    p = tmp_path / "missing.nc"
    source_file(p, "NETCDF4", 80)
    with netCDF4.Dataset(p, 'a') as ds:
        ds['ps'].units = 'hPa'
    before = p.read_bytes()

    def refuse(*args):
        raise AssertionError(
            "The writer must not run before valid normalization input exists"
        )

    monkeypatch.setattr(Batch, "write", refuse)
    with pytest.raises(ValueError):
        apply_plan(
            FixPlan(
                "t", "s", {str(p): [Step("C102", "normalize_hybrid_coordinate", {})]}
            ),
            tmp_path / "log",
        )
    assert p.read_bytes() == before


def test_dependency_order_preserves_unrelated_steps_and_never_inserts_a_fix():
    steps = [
        Step("C001", "drop_var_attr", {}),
        Step("C102", "normalize_hybrid_coordinate", {}),
        Step("C074a", "fix_hybrid_coefficients", {}),
        Step("C103b", "normalize_hybrid_coordinate", {}),
    ]
    assert [s.rule for s in order_steps(steps)] == ["C001", "C074a", "C102", "C103b"]
    assert order_steps([steps[1]]) == [steps[1]]


@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("count", [79, 80])
@pytest.mark.parametrize("existing_bounds", [False, True])
@pytest.mark.parametrize("normalize", [False, True])
def test_coefficient_creation_and_replacement_always_install_their_metadata(
    tmp_path, fmt, count, existing_bounds, normalize
):
    """Check the creation boundary itself, without requiring C084/C084b later."""
    p = tmp_path / "ta.nc"
    reference = source_file(p, fmt, count, bounds=existing_bounds)
    with netCDF4.Dataset(p, "a") as ds:
        ds.vertical_label = "al"
        # Simulate an older file with missing/stale metadata and direct bounds
        # references. Both numeric replacement and new-array creation must fix it.
        for term in ("ap", "b"):
            ds[term].bounds = term + "_bnds"
            if existing_bounds:
                ds[term + "_bnds"].long_name = "outdated coefficient label"
    method = "normalize_hybrid_coordinate" if normalize else "fix_hybrid_coefficients"
    params = {} if normalize else {"coordinate": "auto"}
    q = FixPlan("test", "qa", {str(p): [Step("C102" if normalize else "C074a", method, params)]})
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    with netCDF4.Dataset(p) as ds:
        for term in ("ap", "b"):
            assert ds[term].long_name == f"vertical coordinate formula term: {term}"
            assert ds[term + "_bnds"].long_name == f"vertical coordinate formula term: {term}(k+1/2)"
            assert "bounds" not in ds[term].ncattrs()
            assert ds[term].dimensions == ("lev",)
            assert ds[term + "_bnds"].dimensions == ("lev", "bnds")
            np.testing.assert_array_equal(ds[term][:], reference[79][term])
        assert ds["ap"].units == ds["ap_bnds"].units == "Pa"
        assert "units" not in ds["b"].ncattrs() and "units" not in ds["b_bnds"].ncattrs()
        assert ds["lev"].formula == "p = ap + b*ps"
        assert ds["lev"].formula_terms == "ap: ap b: b ps: ps"
        if normalize:
            assert ds["lev"].bounds == "lev_bnds"
            assert ds["lev_bnds"].formula_terms == "ap: ap_bnds b: b_bnds ps: ps"
        np.testing.assert_array_equal(ds["ta"][:], np.arange(158).reshape(2, 79))
        np.testing.assert_array_equal(ds["ps"][:], [100000, 90000])
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert p.read_bytes() == after
