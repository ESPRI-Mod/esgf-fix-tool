"""Hybrid coordinate metadata and explicitly sourced IPSL coefficients."""

import json
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np

from . import fix
from .auxiliary import ORIGINAL_PREFIX, replace_auxiliary
from .coordinates import _main_variable, _restore_coordinate_values, rename_coordinate

STANDARD_NAME = "atmosphere_hybrid_sigma_pressure_coordinate"
FORMULA = "p = ap + b*ps"
BOUND_LONG_NAMES = {
    "ap_bnds": "vertical coordinate formula term: ap(k+1/2)",
    "b_bnds": "vertical coordinate formula term: b(k+1/2)",
}


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


def _label(ds):
    label = getattr(ds, "vertical_label", "")
    if not label:
        brand = getattr(ds, "branded_variable", "")
        parts = brand.rsplit("_", 1)[-1].split("-")
        if len(parts) == 4:
            label = parts[1]
    return label


def _without_bounds(ds):
    """An explicit CMIP7 label takes precedence over the numeric reference length."""
    label = _label(ds)
    if label in ("al", "alh"):
        return label == "alh"
    # Retain the reviewed interface behavior for legacy, unlabelled files whose
    # complete coordinate vector is independently checked against the reference.
    return ds[_coordinate(ds)].size == 80


def _term_long_name(ds, term):
    suffix = "" if _label(ds) == "al" else "(k)"
    return f"vertical coordinate formula term: {term}{suffix}"


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
        if n in ds.variables and getattr(ds[n], "units", None) not in [None, "", units]:
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
        ),
        "ap": {"units": "Pa", "long_name": _term_long_name(ds, "ap")},
        "b": {"long_name": _term_long_name(ds, "b")},
    }
    attrs[name]["long_name"] = "hybrid sigma pressure coordinate"
    # Prepare bounds attributes only for variables that already exist.
    # No numeric values or dimension lengths are inferred or rebuilt here.
    for n, units in [("ap_bnds", "Pa"), ("b_bnds", "1")]:
        if n in ds.variables:
            attrs[n] = {"units": units} if n == "ap_bnds" else {}
            attrs[n]["long_name"] = BOUND_LONG_NAMES[n]
    if coordinate == "alevel" and not _without_bounds(ds):
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


HYBRID_METHODS = {
    "center_hybrid_interfaces",
    "fix_hybrid_coordinate_metadata",
    "fix_hybrid_coefficients",
    "normalize_hybrid_coordinate",
}
HYBRID_BACKUPS = {ORIGINAL_PREFIX + n for n in ("ap", "b", "ap_bnds", "b_bnds")}


def is_hybrid_step(step):
    """Recognize shared recipes and the existing attribute/standard-name variants."""
    return (
        step.fix in HYBRID_METHODS
        or step.fix == "set_var_attr_if_matches"
        and step.params.get("var") in {"lev", "ap", "b", "ap_bnds", "b_bnds"}
        or step.fix == "fix_invalid_standard_names"
        and step.params.get("var") == "lev"
        or step.fix == "drop_var_attr"
        and (
            step.params.get("var") == "lev"
            and step.params.get("attr") == "computed_standard_name"
            or step.params.get("var") in {"b", "b_bnds"}
            and step.params.get("attr") == "units"
        )
    )


def _is_hybrid(ds):
    return (
        getattr(ds, "source_id", "") == "IPSL-CM6-ESMCO2"
        and getattr(ds, "realm", "") in ("atmos", "aerosol")
        and {"lev", "ap", "b"} <= ds.variables.keys()
        and getattr(ds["lev"], "standard_name", "") == STANDARD_NAME
    )


def clean_hybrid_metadata(ds):
    """Harmonize reviewed names and remove temporary/empty hybrid metadata.

    This is a selected hybrid recipe's final invariant, not a global maintenance
    operation. Preserve global attributes, numeric arrays and other metadata.
    """
    if not _is_hybrid(ds):
        return False
    from .reviewed_structures import set_attrs

    changed = set_attrs(ds["lev"], {"long_name": "hybrid sigma pressure coordinate"})
    for term in ("ap", "b"):
        changed = (
            set_attrs(ds[term], {"long_name": _term_long_name(ds, term)}) or changed
        )
    # Coefficient bounds are ancillary formula terms. Describe their interface
    # position explicitly, including arrays created by a later hybrid recipe.
    for name, long_name in BOUND_LONG_NAMES.items():
        if name in ds.variables:
            changed = set_attrs(ds[name], {"long_name": long_name}) or changed
    for name, attrs in {
        "lev": ("computed_standard_name",),
        "b": ("units",),
        "b_bnds": ("units",),
    }.items():
        if name in ds.variables:
            for attr in attrs:
                if attr in ds[name].ncattrs():
                    if attr == "units" and ds[name].getncattr(attr) not in ("", "1"):
                        raise ValueError(f"Unreviewed units on {name}")
                    ds[name].delncattr(attr)
                    changed = True
    if _without_bounds(ds):
        # Detach only approved links, then ensure no retained variable refers to
        # the retired bounds before deleting them in the grouped output.
        if "bounds" in ds["lev"].ncattrs():
            if ds["lev"].bounds != "lev_bnds":
                raise ValueError("Unexpected hybrid bounds reference")
            ds["lev"].delncattr("bounds")
            changed = True
        for n in ("ap", "b"):
            if "bounds" in ds[n].ncattrs():
                ds[n].delncattr("bounds")
                changed = True
        from .reviewed_structures import delete_variables

        changed = delete_variables(ds, {"lev_bnds", "ap_bnds", "b_bnds"}) or changed
    for v in ds.variables.values():
        for attr in v.ncattrs():
            value = v.getncattr(attr)
            if attr != "_FillValue" and isinstance(value, str) and not value.strip():
                v.delncattr(attr)
                changed = True
    from .reviewed_structures import delete_variables

    return delete_variables(ds, HYBRID_BACKUPS) or changed


def verify_hybrid_cleanup(output):
    with netCDF4.Dataset(output) as ds:
        if not _is_hybrid(ds):
            return
        for name, long_name in BOUND_LONG_NAMES.items():
            if name in ds.variables and getattr(ds[name], "long_name", None) != long_name:
                raise ValueError(
                    f"Required hybrid coefficient-bounds long_name is missing: {name}"
                )
        if HYBRID_BACKUPS & ds.variables.keys():
            raise ValueError(
                "Temporary hybrid coefficients remain after the complete fix sequence"
            )
        if any(
            isinstance(v.getncattr(a), str) and not v.getncattr(a).strip()
            for v in ds.variables.values()
            for a in v.ncattrs()
            if a != "_FillValue"
        ):
            raise ValueError("Empty variable attributes remain after hybrid correction")


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
    return clean_hybrid_metadata(ds) or changed


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
    coordinate = _resolved_coordinate(ds, coordinate)
    name = _coordinate(ds)
    lev = ds[name]
    size = lev.size
    if size != 79 and not (size == 80 and coordinate == "alevhalf"):
        raise ValueError("Unreviewed lev length for this hybrid coordinate")
    if size == 80 and _label(ds) == "al":
        raise ValueError(
            "The bundled 80-point reference describes interfaces, not 80 layer centers. "
            "Select C103e/C104 first for the approved edt/evu 80-to-79 interpolation. "
            "Other 80-center cases require approved ap_bnds/b_bnds and remain in review."
        )
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
    if coordinate == "alevel" and not _without_bounds(ds):
        for n, interfaces in reference[80].items():
            wanted[n + "_bnds"] = (
                ("lev", "bnds"),
                np.column_stack((interfaces[:-1], interfaces[1:])),
            )
    return wanted


def _verify_coefficients(source, output, coordinate):
    with netCDF4.Dataset(output) as ds:
        coordinate = _resolved_coordinate(ds, coordinate)
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
    coordinate = _resolved_coordinate(ds, coordinate)
    _attributes(ds, coordinate)
    wanted = _reference_values(ds, coordinate)
    if coordinate == "alevhalf":
        # Interface coordinates are already physical interfaces. Only rebind their
        # existing coefficients; do not replace a different staggering or values.
        for name in ("ap", "b"):
            v = ds.variables.get(name)
            if (
                v is None
                or v.dtype != np.dtype("f8")
                or not np.array_equal(v[:], wanted[name][1])
            ):
                raise ValueError(
                    f"Interface {name} differs from the supplied 80-level reference; no value replacement is authorized"
                )
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
    if coordinate == "alevel" and not _without_bounds(ds):
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


def _resolved_coordinate(ds, requested="auto"):
    """Select a supplied reference family; full lev values are checked separately."""
    if requested not in ("auto", "alevel", "alevhalf"):
        raise ValueError("Unreviewed hybrid coordinate family")
    size = ds[_coordinate(ds)].size
    if size not in (79, 80):
        raise ValueError(
            "Only the supplied 79-center and 80-interface references are approved"
        )
    return "alevel" if size == 79 else "alevhalf"


def prepare_normalization_inputs(ds):
    """Reuse C074 reference binding before C102, even without a separate QA finding.

    No synthetic interfaces: the complete lev vector must match the supplied
    reference. The 80-interface path additionally requires ap/b values to remain
    identical when their dimensions are rebound.
    """
    coordinate = _resolved_coordinate(ds)
    changed = fix_hybrid_coefficients(ds, coordinate)
    # An empty coordinates string on this ancillary conveys no association.
    if "b_bnds" in ds.variables and getattr(ds["b_bnds"], "coordinates", None) == "":
        ds["b_bnds"].delncattr("coordinates")
        changed = True
    return changed


def _normalization_values(ds):
    """Read current coefficients without replacing them or synthesizing interfaces."""

    # Require the existing coefficients and surface pressure; never synthesize them.
    name = _coordinate(ds)
    lev = ds[name]
    interfaces = lev.size == 80
    without_bounds = _without_bounds(ds)
    terms = ("ap", "b") if without_bounds else ("ap", "b", "ap_bnds", "b_bnds")
    if (
        name != "lev"
        or lev.dtype != np.dtype("f8")
        or any(a in lev.ncattrs() for a in ("scale_factor", "add_offset"))
        or getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or getattr(ds, "realm", "") not in ("atmos", "aerosol")
        or not {*terms, "ps"} <= ds.variables.keys()
        or getattr(ds["ps"], "units", "") != "Pa"
    ):
        raise ValueError(
            "Hybrid normalization requires the existing reviewed coefficients and bounds"
        )
    arrays = {}
    for n in terms:
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
    if getattr(ds["ap"], "units", "") != "Pa" or getattr(ds["b"], "units", "") not in (
        "",
        "1",
    ):
        raise ValueError(
            "Expected ap in Pa and dimensionless b; no coefficient conversion is allowed"
        )
    if not without_bounds and (
        getattr(ds["ap_bnds"], "units", "Pa") != "Pa"
        or getattr(ds["b_bnds"], "units", "1") not in ("", "1")
    ):
        raise ValueError("Coefficient bounds must retain Pa/1 units")

    # Compute dimensionless levels and bounds from the approved reference pressure.
    values = arrays["ap"] / P_REF + arrays["b"]
    bounds = None if without_bounds else arrays["ap_bnds"] / P_REF + arrays["b_bnds"]
    if interfaces:
        reference = _coefficient_reference()[80]
        if any(not np.array_equal(arrays[n], reference[n]) for n in ("ap", "b")):
            raise ValueError(
                "The 80 interfaces must match the complete supplied coefficients"
            )

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
        or (bounds is not None and not np.isfinite(bounds).all())
        or np.any(values < 0)
        or np.any(values > 1)
        or (bounds is not None and np.any(values < bounds.min(axis=1)))
        or (bounds is not None and np.any(values > bounds.max(axis=1)))
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
        if name in ds.variables
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
        if not np.array_equal(dst["lev"][:], values):
            raise ValueError("Hybrid normalized values were not retained")
        if bounds is None and "bounds" in dst["lev"].ncattrs():
            raise ValueError(
                "Layer-interface lev must not acquire invented cell bounds"
            )
        if bounds is not None and (
            dst["lev_bnds"].dimensions != ("lev", "bnds")
            or not np.array_equal(dst["lev_bnds"][:], bounds)
            or dst["lev"].bounds != "lev_bnds"
            or dst["lev_bnds"].formula_terms != "ap: ap_bnds b: b_bnds ps: ps"
        ):
            raise ValueError("Hybrid normalized values/bounds were not retained")
        for n in baseline:
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
    data_targets=lambda p: {"lev", "lev_bnds", "ap", "b", "ap_bnds", "b_bnds"},
    verify_final=_verify_normalization,
)
def normalize_hybrid_coordinate(ds):
    """Apply the approved ap/101325+b definition, preserving all coefficient arrays."""
    prepared = prepare_normalization_inputs(ds)
    values, pairs = _normalization_values(ds)
    lev = ds["lev"]
    if pairs is None:
        from .reviewed_structures import set_attrs

        changed = prepared
        if not np.array_equal(lev[:], values):
            lev[:] = values
            changed = True
        if "bounds" in lev.ncattrs():
            lev.delncattr("bounds")
            changed = True
        return (
            set_attrs(
                lev,
                dict(
                    standard_name=STANDARD_NAME,
                    units="1",
                    axis="Z",
                    positive="down",
                    long_name="hybrid sigma pressure coordinate",
                    formula=FORMULA,
                    formula_terms="ap: ap b: b ps: ps",
                ),
            )
            or changed
        )
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
    changed = prepared
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
    )
    from .reviewed_structures import delete_variables, set_attrs

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
