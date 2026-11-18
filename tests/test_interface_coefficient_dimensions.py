"""Use the existing reference recipe for lev(80) with misplaced center ap/b(79)."""

import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.fixes.hybrid import P_REF, _coefficient_reference
from esgf_fix_tool.plan import FixPlan, Step


def make(path, fmt="NETCDF4"):
    ref = _coefficient_reference()
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.source_id, ds.realm, ds.variable_id = "IPSL-CM6-ESMCO2", "atmos", "smc"
        ds.branded_variable, ds.vertical_label = "smc_tavg-alh-hxy-u", "alh"
        for name, size in (("time", 2), ("lev", 80), ("klev", 79), ("bnds", 2), ("lat", 2), ("lon", 3)):
            ds.createDimension(name, size)
        lev = ds.createVariable("lev", "f8", ("lev",))
        lev[:] = ref[80]["ap"] + P_REF * ref[80]["b"]
        lev.units = "Pa"
        ds.createVariable("klev", "f8", ("klev",))[:] = np.arange(79)
        for name in ("ap", "b"):
            ds.createVariable(name, "f8", ("klev",))[:] = ref[79][name]
            ds.createVariable(name + "_bnds", "f8", ("bnds", "klev"))[:] = np.vstack((ref[80][name][:-1], ref[80][name][1:]))
        ps = ds.createVariable("ps", "f4", ("time", "lat", "lon"))
        ps[:] = np.arange(ps.size).reshape(ps.shape) + 100000
        ps.units = "Pa"
        options = dict(zlib=True, complevel=2, chunksizes=(1, 10, 2, 3)) if fmt.startswith("NETCDF4") else {}
        smc = ds.createVariable("smc", "f4", ("time", "lev", "lat", "lon"), fill_value=np.float32(1e20), **options)
        smc[:] = np.arange(smc.size, dtype="f4").reshape(smc.shape)
        smc[0, 1, 0, 0] = np.ma.masked
        smc.comment = "Original scientific comment"


def snapshot(path):
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return {name: (ds[name][:].tobytes(), ds[name].filters(), ds[name].chunking(),
                       {a: ds[name].getncattr(a) for a in ds[name].ncattrs()})
                for name in ("smc", "ps")}


def steps(mode):
    bind = Step("C074a", "fix_hybrid_coefficients", {"coordinate": "auto"})
    normalize = Step("C102", "normalize_hybrid_coordinate", {})
    return [bind] if mode == "binding" else [normalize] if mode == "normalize_only" else [normalize, bind]


@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("mode", ["binding", "normalize_only", "both"])
def test_existing_recipe_binds_80_reference_before_normalization(tmp_path, monkeypatch, fmt, mode):
    path = tmp_path / "smc.nc"
    make(path, fmt)
    before, science = path.read_bytes(), snapshot(path)
    plan = FixPlan("test", "synthetic", {str(path): steps(mode)})
    calls, write = [], Batch.write

    def track(batch, output):
        calls.append(output)
        return write(batch, output)

    monkeypatch.setattr(Batch, "write", track)
    assert apply_plan(plan, tmp_path / "dry", dry_run=True)["changed"] == 1
    assert path.read_bytes() == before and calls == []
    assert apply_plan(plan, tmp_path / "log")["changed"] == 1
    assert snapshot(path) == science and len(calls) == 1
    ref = _coefficient_reference()[80]
    with netCDF4.Dataset(path) as ds:
        assert len(ds.dimensions["lev"]) == 80
        assert ds["smc"].dimensions == ("time", "lev", "lat", "lon")
        for name in ("ap", "b"):
            assert ds[name].dimensions == ("lev",)
            np.testing.assert_array_equal(ds[name][:], ref[name])
            assert name + "_bnds" not in ds.variables
        assert "klev" not in ds.dimensions and "klev" not in ds.variables
        assert "bounds" not in ds["lev"].ncattrs()
        assert ds["lev"].formula_terms == "ap: ap b: b ps: ps"
        expected = ref["ap"] + P_REF * ref["b"] if mode == "binding" else ref["ap"] / P_REF + ref["b"]
        np.testing.assert_array_equal(ds["lev"][:], expected)
    assert json.loads((tmp_path / "log").read_text())["rewrite_count"] == 1
    corrected = path.read_bytes()
    assert apply_plan(plan, tmp_path / "replay")["unchanged"] == 1
    assert path.read_bytes() == corrected and len(calls) == 1


@pytest.mark.parametrize("fault", ["lev", "mixed", "label"])
def test_unknown_coefficients_or_staggering_are_not_replaced(tmp_path, fault):
    path = tmp_path / "smc.nc"
    make(path)
    with netCDF4.Dataset(path, "a") as ds:
        if fault in ("ap", "b", "lev"):
            ds[fault][20] += .01
        elif fault == "masked":
            ds["ap"][20] = np.ma.masked
        elif fault == "mixed":
            ds.renameVariable("b", "old_b")
            ds.createVariable("b", "f8", ("lev",))[:] = _coefficient_reference()[80]["b"]
        else:
            ds.vertical_label = "unknown"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(FixPlan("test", "synthetic", {str(path): steps("both")}), tmp_path / "log")
    assert path.read_bytes() == before


@pytest.mark.parametrize('old', ['different', 'missing'])
@pytest.mark.parametrize('workers', [1, 2])
def test_80_79_trigger_does_not_require_old_coefficient_values(tmp_path, old, workers):
    path = tmp_path / 'smc.nc'
    make(path)
    with netCDF4.Dataset(path, 'a') as ds:
        for name in ('ap', 'b'):
            ds[name][:] = np.ma.masked if old == 'missing' else np.arange(79) + .123
    science = snapshot(path)
    plan = FixPlan('test', 'synthetic', {str(path): steps('both')})
    assert apply_plan(plan, tmp_path / 'log', workers=workers)['changed'] == 1
    assert snapshot(path) == science
    with netCDF4.Dataset(path) as ds:
        for name in ('ap', 'b'):
            assert ds[name].dimensions == ('lev',)
            np.testing.assert_array_equal(ds[name][:], _coefficient_reference()[80][name])
