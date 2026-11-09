"""Hybrid coordinate metadata and explicitly sourced IPSL coefficients."""

from functools import lru_cache
import json
from pathlib import Path

import netCDF4
import numpy as np

from . import fix
from .auxiliary import ORIGINAL_PREFIX, replace_auxiliary
from .coordinates import _main_variable, _restore_coordinate_values, rename_coordinate

STANDARD_NAME = "atmosphere_hybrid_sigma_pressure_coordinate"
FORMULA = "p = ap + b*ps"


def _coordinate(ds):
    if "lev" in ds.variables:
        name = "lev"
    else:
        candidates = [n for n in ["presinter", "presniv"] if n in ds.variables]
        if len(candidates) != 1:
            raise ValueError(
                "Cannot identify a unique atmospheric coordinate to rename as lev"
            )
        name = candidates[0]
    v = ds[name]
    main = _main_variable(ds, getattr(ds, "variable_id", ""))
    if v.dimensions != (name,) or name not in main.dimensions or v.dtype.kind != "f":
        raise ValueError("The hybrid coordinate must identify one main-field dimension")
    return name


def _attributes(ds, coordinate):
    if coordinate not in ["alevel", "alevhalf"]:
        raise ValueError("Expected alevel or alevhalf")
    if getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2" or getattr(
        ds, "realm", ""
    ) not in ["atmos", "aerosol"]:
        raise ValueError(
            "Hybrid metadata correction is scoped to the reviewed IPSL atmosphere"
        )
    name = _coordinate(ds)
    if name == "lev" and getattr(ds[name], "standard_name", None) not in [
        None,
        "",
        "x",
        STANDARD_NAME,
    ]:
        raise ValueError(
            "Unexpected lev standard_name; do not overwrite another coordinate interpretation"
        )
    if any(n not in ds.variables for n in ["ap", "b", "ps"]):
        raise ValueError("ap, b and ps must exist before declaring the hybrid formula")
    if getattr(ds["ps"], "units", "") != "Pa":
        raise ValueError("ps must already use Pa; no pressure conversion is performed")
    for n, units in [("ap", "Pa"), ("b", "1"), ("ap_bnds", "Pa"), ("b_bnds", "1")]:
        if n in ds.variables and getattr(ds[n], "units", None) not in [None, units]:
            raise ValueError(
                f"Unexpected existing units on {n}; no numeric conversion is performed"
            )
    attrs = {
        name: dict(
            standard_name=STANDARD_NAME,
            units="1",
            axis="Z",
            positive="down",
            formula=FORMULA,
            formula_terms="ap: ap b: b ps: ps",
            computed_standard_name="air_pressure",
        ),
        "ap": {"units": "Pa", "long_name": "vertical coordinate formula term: ap"},
        "b": {"units": "1", "long_name": "vertical coordinate formula term: b"},
    }
    attrs[name]["long_name"] = "hybrid sigma pressure coordinate"
    # Prepare bounds attributes only for variables that already exist.
    # No numeric values or dimension lengths are inferred or rebuilt here.
    for n, units in [("ap_bnds", "Pa"), ("b_bnds", "1")]:
        if n in ds.variables:
            attrs[n] = {"units": units}
    if coordinate == "alevel":
        bound = getattr(ds[name], "bounds", None)
        if bound is None and "lev_bnds" in ds.variables:
            bound = "lev_bnds"
            attrs[name]["bounds"] = bound
        if bound is not None:
            if (
                bound not in ds.variables
                or not {"ap_bnds", "b_bnds"} <= ds.variables.keys()
            ):
                raise ValueError(
                    "Existing hybrid bounds must reference existing coefficient-bounds variables"
                )
            attrs[bound] = dict(
                formula=FORMULA, formula_terms="ap: ap_bnds b: b_bnds ps: ps"
            )
    return name, attrs


def _finish_metadata(source, output, coordinate):
    with netCDF4.Dataset(source) as ds:
        name = _coordinate(ds)
    if name != "lev":
        # Restore values after a NetCDF identifier rename, never synthesize values.
        _restore_coordinate_values(source, output, [name], "lev")


def _verify_metadata(source, output, coordinate):
    with netCDF4.Dataset(output) as ds:
        name, attrs = _attributes(ds, coordinate)
        if name != "lev" or any(
            getattr(ds[n], a, None) != value
            for n, aa in attrs.items()
            for a, value in aa.items()
        ):
            raise ValueError(
                "Hybrid metadata did not survive the complete fix sequence"
            )


@fix(
    "fix_hybrid_coordinate_metadata",
    verify_data=True,
    after_close=_finish_metadata,
    verify_final=_verify_metadata,
)
def fix_hybrid_coordinate_metadata(ds, coordinate) -> bool:
    """Set names/formula attributes only; preserve every numeric array and its size.

    Units=1 describes the requested parametric lev, but no normalization of existing lev
    values is performed. Further QA may therefore still flag values or dimensions.
    Coefficient sourcing and bounds replacement are explicitly deferred to the user.
    """
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    name, attrs = _attributes(ds, coordinate)
    changed = False
    if name != "lev":
        changed = rename_coordinate(ds, [name], "lev")
        attrs["lev"] = attrs.pop(name)

    # 2. Set attributes only. No normalization, coefficient transfer or bounds reconstruction.
    for n, aa in attrs.items():
        for attr, value in aa.items():
            if getattr(ds[n], attr, None) != value:
                ds[n].setncattr(attr, value)
                changed = True
    # Formula coefficients are domain ancillary variables. Their bounds are
    # referenced through lev_bnds:formula_terms, not through ap:bounds/b:bounds.
    for n in ("ap", "b"):
        if "bounds" in ds[n].ncattrs():
            ds[n].delncattr("bounds")
            changed = True
    return changed


@lru_cache(maxsize=1)
def _coefficient_reference():
    ref = json.loads(Path(__file__).with_name("hybrid_coefficients.json").read_text())
    result = {}
    for size in (79, 80):
        result[size] = {
            n: np.asarray(ref[f"coefficients_{size}"][n], dtype="f8")
            for n in ("ap", "b")
        }
        if any(
            v.shape != (size,) or not np.isfinite(v).all()
            for v in result[size].values()
        ):
            raise ValueError("Invalid bundled hybrid coefficient reference")
    for n, interfaces in result[80].items():
        if not np.array_equal(result[79][n], (interfaces[:-1] + interfaces[1:]) / 2):
            raise ValueError(
                "Hybrid centers do not match the adjacent reference interfaces"
            )
    return result


def _reference_values(ds, coordinate):
    name = _coordinate(ds)
    lev = ds[name]
    size = lev.size
    if size != 79 and not (size == 80 and coordinate == "alevhalf"):
        raise ValueError("Unreviewed lev length for this hybrid coordinate")
    reference = _coefficient_reference()
    terms = reference[size]
    # This reference pressure reproduces the original lev byte-for-byte in the
    # individual QA reports. Match the complete axis, not just its length.
    pressure = terms["ap"] + 101325.0 * terms["b"]
    actual = np.ma.asarray(lev[:])
    if np.ma.getmaskarray(actual).any() or not any(
        np.array_equal(actual, values.astype(lev.dtype))
        for values in (
            pressure,
            pressure / 101325.0,
            terms["ap"] / 101325.0 + terms["b"],
        )
    ):
        raise ValueError("lev values/order do not match the reviewed hybrid reference")
    wanted = {n: (("lev",), data) for n, data in terms.items()}
    if coordinate == "alevel":
        for n, interfaces in reference[80].items():
            wanted[n + "_bnds"] = (
                ("lev", "bnds"),
                np.column_stack((interfaces[:-1], interfaces[1:])),
            )
    return wanted


def _verify_coefficients(source, output, coordinate):
    _verify_metadata(source, output, coordinate)
    with netCDF4.Dataset(output) as ds:
        for name, (dimensions, values) in _reference_values(ds, coordinate).items():
            v = ds[name]
            if v.dimensions != dimensions or not np.array_equal(v[:], values):
                raise ValueError(
                    f"Hybrid coefficient {name} differs from the reviewed reference"
                )


@fix(
    "fix_hybrid_coefficients",
    verify_data=True,
    after_close=_finish_metadata,
    data_targets=lambda p: {"ap", "b", "ap_bnds", "b_bnds"},
    verify_final=_verify_coefficients,
)
def fix_hybrid_coefficients(ds, coordinate):
    """Bind the supplied coefficients to lev and use exact interfaces for coefficient bounds."""
    _attributes(ds, coordinate)
    wanted = _reference_values(ds, coordinate)
    for name in wanted:
        v = ds.variables.get(name)
        if v is None:
            continue
        shapes = (
            ((79, 2), (80, 2), (2, 79), (2, 80))
            if name.endswith("_bnds")
            else ((79,), (80,))
        )
        if (
            v.shape not in shapes
            or v.dtype != np.dtype("f8")
            or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
        ):
            raise ValueError(
                f"Unexpected hybrid coefficient shape, type or packing for {name}"
            )

    changed = fix_hybrid_coordinate_metadata(ds, coordinate)
    if coordinate == "alevel":
        if "bnds" not in ds.dimensions:
            ds.createDimension("bnds", 2)
        dim = ds.dimensions["bnds"]
        if dim.isunlimited() or len(dim) != 2:
            raise ValueError("Hybrid coefficient bounds require bnds=2")

    for name, (dimensions, values) in wanted.items():
        v = ds.variables.get(name)
        if v is None or v.dimensions != dimensions:
            v = replace_auxiliary(
                ds, name, dimensions, values, dtype="f8", keep_original=False
            )
            if ORIGINAL_PREFIX + name in ds.variables:
                original = ds[ORIGINAL_PREFIX + name]
                if "bounds" in original.ncattrs():
                    original.delncattr("bounds")
            changed = True
        elif not np.array_equal(v[:], values):
            v[:] = values
            changed = True

    # Also attach metadata for newly created coefficient bounds. Existing lev
    # and lev bounds retain their numeric values, and absent lev bounds stay absent.
    changed = fix_hybrid_coordinate_metadata(ds, coordinate) or changed
    # Earlier releases retained these small arrays for in-place replacement.
    # The user now requests a clean file; omit existing backups in the same
    # grouped rewrite and never generate new ones for the hybrid coefficients.
    from .reviewed_structures import delete_variables

    backups = [ORIGINAL_PREFIX + name for name in ("ap", "b", "ap_bnds", "b_bnds")]
    return delete_variables(ds, backups) or changed


@fix("restore_hybrid_vertical_coefficients")
def restore_hybrid_vertical_coefficients(ds, **params):
    """Reject old reconstruction plans after the user's revised decision."""
    # Reject obsolete reconstruction plans; never read or modify coefficient arrays.
    raise ValueError(
        "Legacy hybrid coefficient reconstruction is disabled. "
        "Regenerate C074 to select the reviewed, bundled reference."
    )


P_REF = 101325.0


def _normalization_values(ds):
    """Read current coefficients without replacing them or synthesizing interfaces."""

    # Require the existing coefficients and surface pressure; never synthesize them.
    name = _coordinate(ds)
    lev = ds[name]
    if (
        name != "lev"
        or lev.dtype != np.dtype("f8")
        or any(a in lev.ncattrs() for a in ("scale_factor", "add_offset"))
        or getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or getattr(ds, "realm", "") not in ("atmos", "aerosol")
        or not {"ap", "b", "ap_bnds", "b_bnds", "ps"} <= ds.variables.keys()
        or getattr(ds["ps"], "units", "") != "Pa"
    ):
        raise ValueError(
            "Hybrid normalization requires the existing reviewed coefficients and bounds"
        )
    arrays = {}
    for n in ("ap", "b", "ap_bnds", "b_bnds"):
        v = ds[n]
        if v.dtype != np.dtype("f8") or any(
            a in v.ncattrs() for a in ("scale_factor", "add_offset")
        ):
            raise ValueError(f"Unexpected hybrid coefficient type or packing for {n}")
        a = np.ma.asarray(v[:])
        if np.ma.getmaskarray(a).any() or not np.isfinite(a).all():
            raise ValueError(f"Missing/non-finite coefficient {n}")
        a = np.asarray(a)
        if n.endswith("_bnds"):
            if a.shape != (lev.size, 2) or v.dimensions != ("lev", "bnds"):
                raise ValueError(
                    f"{n} must already have dimensions (lev,bnds); "
                    "fix coefficient dimensions first with C074/C074a and the approved reference, then rerun normalization"
                )
            arrays[n] = a
        else:
            if a.shape != lev.shape or v.dimensions != ("lev",):
                raise ValueError(
                    f"{n} must already be aligned with lev; apply C074/C074a with the approved reference before C102/C103b"
                )
            arrays[n] = a
    if getattr(ds["ap"], "units", "") != "Pa" or getattr(ds["b"], "units", "") != "1":
        raise ValueError(
            "Expected ap in Pa and dimensionless b; no coefficient conversion is allowed"
        )
    if (
        getattr(ds["ap_bnds"], "units", "Pa") != "Pa"
        or getattr(ds["b_bnds"], "units", "1") != "1"
    ):
        raise ValueError("Coefficient bounds must retain Pa/1 units")

    # Compute dimensionless levels and bounds from the approved reference pressure.
    values = arrays["ap"] / P_REF + arrays["b"]
    bounds = arrays["ap_bnds"] / P_REF + arrays["b_bnds"]

    # Accept the original pressure-valued axis or an already normalized axis on replay.
    current = np.ma.asarray(lev[:])
    pressure = arrays["ap"] + P_REF * arrays["b"]
    if np.ma.getmaskarray(current).any() or not any(
        np.allclose(current, candidate, rtol=1e-14, atol=1e-14)
        for candidate in (pressure, values, pressure / P_REF)
    ):
        raise ValueError(
            "Existing lev is neither its coefficient pressure nor the approved normalization"
        )

    # Reject non-finite values and any level outside its physical bounds.
    if (
        not np.isfinite(values).all()
        or not np.isfinite(bounds).all()
        or np.any(values < 0)
        or np.any(values > 1)
        or np.any(values < bounds.min(axis=1))
        or np.any(values > bounds.max(axis=1))
    ):
        raise ValueError(
            "Normalized lev must lie in [0,1] and within its coefficient-derived bounds"
        )
    return values, bounds


def coefficient_snapshot(ds):
    """Capture the small coefficient arrays at the normalization input boundary."""
    return {
        name: (ds[name].dtype, ds[name].dimensions, np.asarray(ds[name][:]).copy())
        for name in ("ap", "b", "ap_bnds", "b_bnds")
    }


def _verify_normalization(source, output, coefficient_baseline=None):

    # Compare with the normalization input, after any approved reference binding.
    # A standalone call retains the original-file baseline for backward compatibility.
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        baseline = (
            coefficient_baseline
            if coefficient_baseline is not None
            else coefficient_snapshot(src)
        )
        values, bounds = _normalization_values(dst)
        if (
            not np.array_equal(dst["lev"][:], values)
            or dst["lev_bnds"].dimensions != ("lev", "bnds")
            or not np.array_equal(dst["lev_bnds"][:], bounds)
            or dst["lev"].bounds != "lev_bnds"
            or dst["lev_bnds"].formula_terms != "ap: ap_bnds b: b_bnds ps: ps"
        ):
            raise ValueError("Hybrid normalized values/bounds were not retained")
        for n in ("ap", "b", "ap_bnds", "b_bnds"):
            dtype, dimensions, values_before = baseline[n]
            if (
                dtype != dst[n].dtype
                or dimensions != dst[n].dimensions
                or not np.array_equal(values_before, dst[n][:])
            ):
                raise ValueError(
                    f"Normalization must not change coefficient values: {n}"
                )
        if any("bounds" in dst[n].ncattrs() for n in ("ap", "b")):
            raise ValueError("Direct bounds references remain on ap/b")
        if any(n.startswith(ORIGINAL_PREFIX) for n in dst.variables):
            raise ValueError(
                "Temporary fix_original_* variables remain after normalization"
            )


@fix(
    "normalize_hybrid_coordinate",
    verify_data=True,
    data_targets=lambda p: {"lev", "lev_bnds"},
    verify_final=_verify_normalization,
)
def normalize_hybrid_coordinate(ds):
    """Apply the approved ap/101325+b definition, preserving all coefficient arrays."""
    values, pairs = _normalization_values(ds)
    lev = ds["lev"]
    if getattr(lev, "bounds", "") not in ("", "lev_bnds"):
        raise ValueError("Unexpected lev bounds name")
    if "bnds" in ds.dimensions and (
        len(ds.dimensions["bnds"]) != 2 or ds.dimensions["bnds"].isunlimited()
    ):
        raise ValueError("Hybrid coordinate bounds require fixed bnds=2")
    if "lev_bnds" in ds.variables:
        v = ds["lev_bnds"]
        if (
            v.dimensions != ("lev", "bnds")
            or v.dtype != np.dtype("f8")
            or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
        ):
            raise ValueError("Unexpected lev_bnds representation")

    # All calculations and containment checks above complete before any edit.
    changed = False
    if not np.array_equal(lev[:], values):
        lev[:] = values
        changed = True
    attrs = dict(
        standard_name=STANDARD_NAME,
        units="1",
        axis="Z",
        positive="down",
        bounds="lev_bnds",
        formula=FORMULA,
        formula_terms="ap: ap b: b ps: ps",
        computed_standard_name="air_pressure",
    )
    from .reviewed_structures import set_attrs, delete_variables

    changed = set_attrs(lev, attrs) or changed
    if "lev_bnds" not in ds.variables:
        if "bnds" not in ds.dimensions:
            ds.createDimension("bnds", 2)
        ds.createVariable("lev_bnds", "f8", ("lev", "bnds"))[:] = pairs
        changed = True
    elif not np.array_equal(ds["lev_bnds"][:], pairs):
        ds["lev_bnds"][:] = pairs
        changed = True
    changed = (
        set_attrs(
            ds["lev_bnds"],
            dict(formula=FORMULA, formula_terms="ap: ap_bnds b: b_bnds ps: ps"),
        )
        or changed
    )
    if "units" in ds["lev_bnds"].ncattrs():
        ds["lev_bnds"].delncattr("units")
        changed = True
    for n in ("ap", "b"):
        if "bounds" in ds[n].ncattrs():
            ds[n].delncattr("bounds")
            changed = True
    # These are explicitly approved temporary arrays, never active formula terms.
    backups = [n for n in ds.variables if n.startswith(ORIGINAL_PREFIX)]
    return delete_variables(ds, backups) or changed
