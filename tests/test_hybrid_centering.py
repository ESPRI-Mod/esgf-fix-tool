"""LMDZ-approved interface means and reference centers, in one atomic rewrite."""

import json
from itertools import permutations

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.hybrid import _coefficient_reference
from esgf_fix_tool.plan import FixPlan, Step, order_steps
from esgf_fix_tool.report import Finding
from esgf_fix_tool.ruleset import Action, RuleSet


def interface_file(path, name="edt", fmt="NETCDF4", chunk=10, coefficient_dim="lev", dtype="f4", time_chunk=1):
    reference = _coefficient_reference()[80]
    values = np.ma.array(np.arange(2 * 80 * 3 * 4, dtype=dtype).reshape(2, 80, 3, 4) / 8, mask=False)
    values.mask[0, 20, 1, 1] = True  # Both adjacent output cells must be masked.
    values.mask[1, :, 0, 0] = True
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.setncatts(dict(source_id="IPSL-CM6-ESMCO2", realm="atmos", variable_id=name,
                         branded_variable=name + "_tavg-al-hxy-u", vertical_label="al",
                         history="Preserve the original global history"))
        for dim, size in (("time", None), ("lev", 80), ("lat", 3), ("lon", 4), ("bnds", 2)):
            ds.createDimension(dim, size)
        if coefficient_dim != "lev":
            ds.createDimension(coefficient_dim, 80)
            ds.createVariable(coefficient_dim, "i4", (coefficient_dim,))[:] = np.arange(80)
        ds.createVariable("lev", "f8", ("lev",))[:] = reference["ap"] / 101325 + reference["b"]
        for term in ("ap", "b"):
            ds.createVariable(term, "f8", (coefficient_dim,))[:] = reference[term]
        ds["ap"].units = "Pa"
        ds.createVariable("time", "f8", ("time",))[:] = [15.5, 45]
        ds["time"].bounds = "time_bnds"
        ds.createVariable("time_bnds", "f8", ("time", "bnds"))[:] = [[0, 31], [31, 59]]
        ds.createVariable("ps", "f4", ("time", "lat", "lon"))[:] = 100000
        ds["ps"].units = "Pa"
        options = dict(zlib=True, complevel=2, shuffle=True, chunksizes=(time_chunk, chunk, 3, 4)) if fmt.startswith("NETCDF4") else {}
        v = ds.createVariable(name, dtype, ("time", "lev", "lat", "lon"), fill_value=1e20, **options)
        v.setncatts(dict(units="m2 s-1", standard_name="atmosphere_heat_diffusivity" if name == "edt" else "atmosphere_momentum_diffusivity",
                        long_name="Original scientific title", comment="Original comment",
                        description="Original scientific description", cell_methods="area: time: mean"))
        if fmt == "NETCDF4":
            v.setncattr_string("description", "Original scientific description")
        v[:] = values
    return values


def plan(path, extra=()):
    return FixPlan("test", "source", {str(path): [Step("C103e", "center_hybrid_interfaces", {}), *extra]})


@pytest.mark.parametrize("name", ["edt", "evu"])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("chunk", [10, 80])
@pytest.mark.parametrize("coefficient_dim", ["lev", "klevp1"])
@pytest.mark.parametrize("dtype", ["f4", "f8"])
def test_centering_preserves_masks_science_metadata_and_storage_and_replays(
    tmp_path, monkeypatch, name, fmt, chunk, coefficient_dim, dtype
):
    path = tmp_path / f"{name}.nc"
    values = interface_file(path, name, fmt, chunk, coefficient_dim, dtype)
    original = path.read_bytes()
    with netCDF4.Dataset(path) as ds:
        old_filters = ds[name].filters()
        old_attrs = {a: ds[name].getncattr(a) for a in ds[name].ncattrs()}
        untouched = {n: ds[n][:].tobytes() for n in ("time", "time_bnds", "ps")}
    q = plan(path)
    assert apply_plan(q, tmp_path / "dry", dry_run=True)["changed"] == 1
    assert path.read_bytes() == original
    writes = []
    write = Batch.write

    def record(batch, out):
        assert path.read_bytes() == original
        writes.append(out)
        return write(batch, out)

    monkeypatch.setattr(Batch, "write", record)
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    assert len(writes) == 1
    with netCDF4.Dataset(path) as ds:
        expected = (values[:, :-1].astype("f8") * 0.5 + values[:, 1:].astype("f8") * 0.5).astype(dtype)
        actual = ds[name][:]
        assert actual.shape == (2, 79, 3, 4)
        np.testing.assert_array_equal(np.ma.getmaskarray(actual), np.ma.getmaskarray(expected))
        np.testing.assert_array_equal(actual.compressed(), expected.compressed())
        assert {a: ds[name].getncattr(a) for a in ds[name].ncattrs()} == old_attrs
        assert ds[name].filters() == old_filters
        if fmt.startswith("NETCDF4"):
            assert ds[name].chunking() == [1, min(chunk, 79), 3, 4]
        ref = _coefficient_reference()
        for term in ("ap", "b"):
            np.testing.assert_array_equal(ds[term][:], ref[79][term])
            assert ds[term].dimensions == ("lev",)
            pairs = np.column_stack((ref[80][term][:-1], ref[80][term][1:]))
            np.testing.assert_array_equal(ds[term + "_bnds"][:], pairs)
            assert ds[term + "_bnds"].long_name == f"vertical coordinate formula term: {term}(k+1/2)"
        lev, bounds = ds["lev"][:], ds["lev_bnds"][:]
        np.testing.assert_array_equal(lev, ref[79]["ap"] / 101325 + ref[79]["b"])
        assert np.all(bounds.min(axis=1) <= lev) and np.all(lev <= bounds.max(axis=1))
        assert ds["lev_bnds"].formula_terms == "ap: ap_bnds b: b_bnds ps: ps"
        assert not any(n.startswith(("fix_original_", "__hybrid")) for n in ds.variables)
        assert "klevp1" not in ds.dimensions and "__hybrid_interfaces" not in ds.dimensions
        assert all(ds[n][:].tobytes() == raw for n, raw in untouched.items())
    entry = json.loads((tmp_path / "log").read_text())
    assert entry["rewrite_count"] == 1 and all(s["data_verified"] for s in entry["steps"])
    corrected = path.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert path.read_bytes() == corrected and len(writes) == 1


@pytest.mark.parametrize("failure", ["packing", "coefficient", "level_order", "other_vertical", "nonfinite", "verification"])
def test_failure_preserves_original_and_removes_temporary_output(tmp_path, monkeypatch, failure):
    path = tmp_path / "edt.nc"
    interface_file(path)
    with netCDF4.Dataset(path, "a") as ds:
        if failure == "packing":
            ds["edt"].scale_factor = 2.0
        elif failure == "coefficient":
            ds["ap"][5] += 1
        elif failure == "level_order":
            ds["lev"][:] = ds["lev"][:][::-1]
        elif failure == "other_vertical":
            ds.createVariable("other", "f4", ("lev",))[:] = 1
        elif failure == "nonfinite":
            ds["edt"][1, 20, 1, 1] = np.inf
    if failure == "verification":
        def refuse(*args):
            raise ValueError("Injected verification failure")
        monkeypatch.setattr(Batch, "verify", refuse)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(plan(path), tmp_path / "log")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.fixtmp"))


@pytest.mark.parametrize("ids", list(permutations(["C104", "C074a", "C102"])))
def test_selected_centering_precedes_binding_and_normalization(tmp_path, ids):
    path = tmp_path / "edt.nc"
    interface_file(path)
    steps = {
        "C104": Step("C104", "center_hybrid_interfaces", {}),
        "C074a": Step("C074a", "fix_hybrid_coefficients", {"coordinate": "auto"}),
        "C102": Step("C102", "normalize_hybrid_coordinate", {}),
    }
    ordered = order_steps([steps[rid] for rid in ids])
    assert [s.rule for s in ordered] == ["C104", "C074a", "C102"]
    q = FixPlan("test", "source", {str(path): [steps[rid] for rid in ids]})
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0


@pytest.mark.parametrize("name", ["edt", "evu"])
def test_catalogue_routes_existing_bounds_diagnostic_to_approved_centering(name):
    rules = RuleSet.load(*default_rule_paths())
    finding = Finding(3, "[CMIP7] [COORD008] Coordinate bounds",
                      "It is required for 'lev' to have a 'bounds' attribute according to its coordinate definition.",
                      f"CMIP7.ATM.{name}.tavg-al-hxy-u.mon.glb.g111.IPSL-CM6-ESMCO2.esm-piControl-spinup.r1i1p1f1", "/copy.nc")
    match = rules.match(finding)
    assert match.rule.id == "C103e" and match.rule.action is Action.FIX
    assert match.rule.fix == "center_hybrid_interfaces"


def test_time_chunk_larger_than_current_unlimited_dimension_is_preserved(tmp_path):
    path = tmp_path / "edt.nc"
    interface_file(path, chunk=80, time_chunk=8)
    apply_plan(plan(path), tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds["edt"].chunking() == [8, 79, 3, 4]
        assert ds.dimensions["time"].isunlimited()


def test_two_diagnostics_do_not_average_twice_and_tiles_remain_bounded(tmp_path, monkeypatch):
    from esgf_fix_tool.data_integrity import _slices
    from esgf_fix_tool.fixes import hybrid_centering

    path = tmp_path / "edt.nc"
    expected = interface_file(path)
    calls = []

    def tiny_tiles(shape, itemsize, budget):
        for slab in _slices(shape, itemsize, budget=512):
            calls.append(slab)
            yield slab

    monkeypatch.setattr(hybrid_centering, "_slices", tiny_tiles)
    q = plan(path, [Step("C104", "center_hybrid_interfaces", {})])
    apply_plan(q, tmp_path / "log")
    assert len(calls) > 2  # Both numerical passes use small tiles with an interface halo.
    with netCDF4.Dataset(path) as ds:
        actual = ds["edt"][:]
        expected = (expected[:, :-1] * 0.5 + expected[:, 1:] * 0.5)
        np.testing.assert_array_equal(actual.mask, expected.mask)
        np.testing.assert_array_equal(actual.compressed(), expected.compressed())


@pytest.mark.parametrize("name", ["edt", "evu"])
@pytest.mark.parametrize("dataset_kind", ["legacy", "files", "version"])
@pytest.mark.parametrize("reported", ["ap_bnds", "b_bnds"])
def test_missing_bound_names_reuse_metadata_recipe_on_published_and_legacy_ids(
    tmp_path, monkeypatch, name, dataset_kind, reported
):
    p = tmp_path / f"{name}.nc"
    interface_file(p, name)
    apply_plan(plan(p), tmp_path / "center")
    with netCDF4.Dataset(p, "a") as ds:
        for term in ("ap", "b"):
            ds[term + "_bnds"].delncattr("long_name")
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        raw = {n: v[:].tobytes() for n, v in ds.variables.items()}
        filters, chunks = ds[name].filters(), ds[name].chunking()

    if dataset_kind == "legacy":
        dataset = f"CMIP7.ATM.{name}.tavg-al-hxy-u.mon.glb.g111.IPSL-CM6-ESMCO2.esm-piControl-spinup.r1i1p1f1"
    else:
        suffix = "files.d20261112" if dataset_kind == "files" else "v20261112"
        dataset = f"CMIP7.CMIP.IPSL.IPSL-CM6-ESMCO2.esm-piControl-spinup.r1i1p1f1.glb.mon.{name}.tavg-al-hxy-u.g111.{suffix}"
    finding = Finding(
        2, "[CF-Conventions] §3.3 Standard Name",
        "Attribute long_name or/and standard_name is highly recommended for variable " + reported,
        dataset, str(p),
    )
    match = RuleSet.load(*default_rule_paths()).match(finding)
    assert match is not None and match.rule.id == "C084b"
    assert match.rule.action is Action.FIX
    step = Step(match.rule.id, match.rule.fix, match.rule.resolve_params(match.groups))

    def refuse(*args):
        raise AssertionError("Adding long_name must not rewrite any scientific array")

    monkeypatch.setattr(Batch, "write", refuse)
    q = FixPlan("test", "qa", {str(p): [step]})
    before, inode = p.read_bytes(), p.stat().st_ino
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert p.read_bytes() == before
    assert apply_plan(q, tmp_path / "names")["changed"] == 1
    with netCDF4.Dataset(p) as ds:
        ds.set_auto_maskandscale(False)
        for term in ("ap", "b"):
            assert ds[term + "_bnds"].long_name == f"vertical coordinate formula term: {term}(k+1/2)"
        assert all(ds[n][:].tobytes() == data for n, data in raw.items())
        assert ds[name].filters() == filters and ds[name].chunking() == chunks
    corrected = p.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert p.read_bytes() == corrected and p.stat().st_ino == inode
