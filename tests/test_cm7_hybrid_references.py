"""Supplied CM7 references repair dimensions without modifying scientific data."""

from itertools import permutations

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.fixes.hybrid import _coefficient_reference, _reference_document
from esgf_fix_tool.plan import FixPlan, Step


def make_file(path, size, count, *, fmt="NETCDF4", cells=False, rounded=False,
              mixed=False, label=None, perturb=False):
    reference = _coefficient_reference()
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.setncatts(dict(variable_id="va", realm="atmos", source_id="IPSL-CM7A",
                         vertical_label=label or ("al" if size == 95 else "alh"),
                         grid_label="g243" if cells else "g245", history="original"))
        horizontal = (("cell", 3),) if cells else (("lat", 2), ("lon", 3))
        for name, length in [("time", 2), ("lev", size), ("coeff", count),
                             ("bnds", 2), *horizontal]:
            ds.createDimension(name, length)
        if mixed:
            ds.createDimension("other", 96 if count == 95 else 95)
        lev = ds.createVariable("lev", "f8", ("lev",))
        lev[:] = reference[size]["ap"] + 101325 * reference[size]["b"]
        if rounded:
            lev[:] = [float(f"{x:.15g}") for x in lev[:]]
        lev.standard_name = "x"
        lev.units = "Pa"
        for name in ("ap", "b"):
            dim = "other" if mixed and name == "b" else "coeff"
            n = len(ds.dimensions[dim])
            values = reference[n][name].copy()
            if perturb:
                interior = (values > 0) & (values < 1 if name == "b" else True)
                values[interior] = np.nextafter(values[interior], np.inf)
            ds.createVariable(name, "f8", (dim,))[:] = values
            # Both dumps use (bnds, technical-level-dimension). Copy those
            # explicit arrays, including the interface bounds that alh retires.
            bounds = np.asarray(_reference_document()[f"coefficients_{n}"][name + "_bnds"])
            ds.createVariable(name + "_bnds", "f8", ("bnds", dim))[:] = bounds.T
        ds["ap"].units = "Pa"
        dimensions = ("time", "lev", *(n for n, _ in horizontal))
        shape = tuple(len(ds.dimensions[n]) for n in dimensions)
        options = dict(zlib=True, complevel=2) if fmt.startswith("NETCDF4") else {}
        science = ds.createVariable("va", "f4", dimensions, fill_value=1e20, **options)
        values = np.ma.array(np.arange(np.prod(shape), dtype="f4").reshape(shape), mask=False)
        values.mask.flat[5] = True
        science[:] = values
        science.comment = "Keep scientific metadata"
        ps = ds.createVariable("ps", "f4", ("time", *(n for n, _ in horizontal)))
        ps[:] = 100000
        ps.units = "Pa"
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return {name: (ds[name][:].tobytes(), ds[name].filters(), ds[name].chunking())
                for name in ("va", "ps")}


def plan(path, ids=("C074a", "C102", "C103b")):
    return FixPlan("test", "qa", {str(path): [
        Step(rid, "fix_hybrid_coefficients", {"coordinate": "auto"}) if rid == "C074a"
        else Step(rid, "normalize_hybrid_coordinate", {}) for rid in ids
    ]})


@pytest.mark.parametrize("size,count", [(95, 96), (96, 95), (95, 95), (96, 96)])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("cells", [False, True])
def test_cm7_reference_binding_normalization_and_single_rewrite(tmp_path, monkeypatch, size, count, fmt, cells):
    path = tmp_path / "va.nc"
    before = make_file(path, size, count, fmt=fmt, cells=cells, rounded=True)
    original = path.read_bytes()
    q = plan(path)
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert path.read_bytes() == original
    writes = []
    write = Batch.write

    def record(batch, output):
        writes.append(output)
        return write(batch, output)

    monkeypatch.setattr(Batch, "write", record)
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    assert len(writes) == 1
    with netCDF4.Dataset(path) as ds:
        reference = _coefficient_reference()[size]
        np.testing.assert_array_equal(ds["lev"][:], reference["ap"] / 101325 + reference["b"])
        for term in ("ap", "b"):
            assert ds[term].dimensions == ("lev",)
            np.testing.assert_array_equal(ds[term][:], reference[term])
            assert ds[term].long_name.startswith("vertical coordinate formula term: ")
            assert "bounds" not in ds[term].ncattrs()
        assert ds["lev"].computed_standard_name == "air_pressure"
        assert ds["lev"].formula_terms == "ap: ap b: b ps: ps"
        if size == 95:
            assert ds["lev"].bounds == "lev_bnds"
            for term in ("ap", "b"):
                assert ds[term + "_bnds"].dimensions == ("lev", "bnds")
                np.testing.assert_array_equal(ds[term + "_bnds"][:],
                    _reference_document()["coefficients_95"][term + "_bnds"])
                assert ds[term + "_bnds"].long_name == f"vertical coordinate formula term: {term}(k+1/2)"
            bounds = ds["ap_bnds"][:] / 101325 + ds["b_bnds"][:]
            np.testing.assert_array_equal(ds["lev_bnds"][:], bounds)
            assert ds["lev_bnds"].formula_terms == "ap: ap_bnds b: b_bnds ps: ps"
            assert np.all(bounds.min(axis=1) <= ds["lev"][:])
            assert np.all(ds["lev"][:] <= bounds.max(axis=1))
        else:
            assert "bounds" not in ds["lev"].ncattrs()
            assert not {"lev_bnds", "ap_bnds", "b_bnds"} & ds.variables.keys()
        ds.set_auto_maskandscale(False)
        for name, (raw, filters, chunks) in before.items():
            assert ds[name][:].tobytes() == raw
            assert ds[name].filters() == filters and ds[name].chunking() == chunks
        assert not any(n.startswith("fix_original_") for n in ds.variables)
    after = path.read_bytes()
    assert apply_plan(q, tmp_path / "replay")["changed"] == 0
    assert path.read_bytes() == after and len(writes) == 1


@pytest.mark.parametrize("ids", list(permutations(["C074a", "C102", "C103b"])))
@pytest.mark.parametrize("size,count", [(95, 96), (96, 95)])
def test_cm7_reference_alignment_precedes_normalization(tmp_path, ids, size, count):
    path = tmp_path / "va.nc"
    make_file(path, size, count)
    assert apply_plan(plan(path, ids), tmp_path / "log")["changed"] == 1


@pytest.mark.parametrize("size", [95, 96])
def test_cm7_already_correct_binary_coefficients_are_preserved(tmp_path, size):
    path = tmp_path / "va.nc"
    make_file(path, size, size, perturb=True)
    with netCDF4.Dataset(path) as ds:
        originals = {term: ds[term][:].tobytes() for term in ("ap", "b", "ap_bnds", "b_bnds")}
    apply_plan(plan(path, ("C102",)), tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        for term in ("ap", "b"):
            assert ds[term][:].tobytes() == originals[term]
        if size == 95:
            for term in ("ap_bnds", "b_bnds"):
                actual = ds[term][:].T.copy().tobytes()
                assert actual == originals[term]


@pytest.mark.parametrize("problem", ["ap", "b", "ap_bnds", "lev", "reversed", "mixed", "packing", "masked", "centers96"])
def test_cm7_unknown_values_and_ambiguous_structures_do_not_write(tmp_path, problem):
    path = tmp_path / "va.nc"
    size = 96 if problem == "centers96" else 95
    make_file(path, size, size, mixed=problem == "mixed", label="al" if problem == "centers96" else None)
    with netCDF4.Dataset(path, "a") as ds:
        if problem in ("ap", "b", "lev"):
            ds[problem][1] += 0.01
        elif problem == "ap_bnds":
            ds[problem][0, 1] += 0.01
        elif problem == "reversed":
            ds["lev"][:] = ds["lev"][:][::-1]
        elif problem == "packing":
            ds["ap"].scale_factor = 2.
        elif problem == "masked":
            ds["ap"][2] = netCDF4.default_fillvals["f8"]
    original = path.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(plan(path), tmp_path / "log")
    assert path.read_bytes() == original


def test_supplied_cm7_centers_and_bounds_agree_with_independent_interfaces():
    ref = _coefficient_reference()
    document = _reference_document()
    assert ref[95]["ap"][0] == 40.2945521621956
    assert ref[96]["ap"][1] == 80.5891043243911
    assert ref[96]["b"][0] == 1 and ref[96]["ap"][-1] == 0
    for term in ("ap", "b"):
        np.testing.assert_allclose(ref[95][term], (ref[96][term][:-1] + ref[96][term][1:]) / 2,
                                   rtol=5e-14, atol=1e-14)
        np.testing.assert_array_equal(document["coefficients_95"][term + "_bnds"],
                                      np.column_stack((ref[96][term][:-1], ref[96][term][1:])))
    # Provenance is the supplied coefficient dump, not its unrelated lev length.
    assert document["coefficients_96"]["source_file"].startswith("airmass_")


def test_95_entry_alh_uses_95_coefficients_without_inventing_an_interface(tmp_path):
    path = tmp_path / "va.nc"
    before = make_file(path, 95, 96, label="alh")
    apply_plan(plan(path, ("C102",)), tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds["lev"].shape == (95,) and ds["va"].shape[1] == 95
        np.testing.assert_array_equal(ds["ap"][:], _coefficient_reference()[95]["ap"])
        assert "bounds" not in ds["lev"].ncattrs()
        assert not {"lev_bnds", "ap_bnds", "b_bnds"} & ds.variables.keys()
        ds.set_auto_maskandscale(False)
        assert ds["va"][:].tobytes() == before["va"][0]


def test_interface_reference_bounds_are_recorded_but_not_used_as_96_center_bounds():
    doc = _reference_document()
    for term in ("ap", "b"):
        actual = np.asarray(doc["coefficients_96"][term + "_bnds"])
        centers = _coefficient_reference()[95][term]
        expected = np.column_stack((np.r_[0 if term == "ap" else 1, centers],
                                    np.r_[centers, 0]))
        np.testing.assert_array_equal(actual, expected)
