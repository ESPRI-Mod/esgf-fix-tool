"""Approved conventional latitude bounds shared by nine NEMO diagnostics."""

import json
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np

from . import fix
from .auxiliary import replace_auxiliary
from .rectilinear import NEW_END
from .reviewed_structures import BASIN_PRODUCTS, finite, delete_variables, set_attrs


@lru_cache(maxsize=1)
def reference():
    """Read the offline native reference; its vertices are not output bounds."""
    raw = json.loads(
        Path(__file__).with_name("basin_latitude_reference.json").read_text()
    )
    centers = np.asarray(raw["centers"], dtype="f8")
    bounds = np.asarray(raw["bounds"], dtype="f8")
    if (
        centers.shape != (332,)
        or bounds.shape != (332, 2)
        or not np.isfinite(centers).all()
        or not np.isfinite(bounds).all()
    ):
        raise ValueError("Unexpected bundled native latitude reference")
    return centers, bounds


def canonical_latitude():
    """Use the transport axis including the user-approved terminal latitude."""
    values = reference()[0].copy()
    values[-1] = NEW_END
    return values


def conventional_bounds(values):
    """Midpoints and half-step extrapolation, with an explicit northern rule.

    The nearly duplicated last center must not define the outer half-step.
    Use the preceding, non-degenerate spacing instead. Do not force pole limits.
    These are conventional 1-D bands, not native NEMO transport sections.
    """
    values = np.asarray(values, dtype="f8")
    if (
        values.shape != (332,)
        or not np.isfinite(values).all()
        or not np.all(np.diff(values) > 0)
        or np.any(np.abs(values) > 90)
    ):
        raise ValueError("Conventional bounds require 332 increasing latitudes")
    edges = np.empty(333, dtype="f8")
    edges[1:-1] = 0.5 * values[:-1] + 0.5 * values[1:]
    edges[0] = values[0] - 0.5 * (values[1] - values[0])
    edges[-1] = values[-1] + 0.5 * (values[-2] - values[-3])
    pairs = np.column_stack((edges[:-1], edges[1:]))
    if (
        not np.all(np.diff(edges) > 0)
        or np.any(np.abs(edges) > 90)
        or np.any(values < pairs[:, 0])
        or np.any(values > pairs[:, 1])
    ):
        raise ValueError("Conventional latitude cells must increase and contain their centers")
    return pairs


def _inspect(ds):
    name = getattr(ds, "variable_id", "")
    msfty = name == "msfty"
    coordinate = "rlat" if msfty else "lat"
    main = ds.variables.get(name)
    lat = ds.variables.get(coordinate)
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or name not in BASIN_PRODUCTS
        or getattr(ds, "branded_variable", "")
        != name + ("_tavg-ol-ht-sea" if msfty else "_tavg-u-hyb-sea")
        or getattr(ds, "grid_label", "") != "g112"
        or getattr(ds, "horizontal_label", "") != ("ht" if msfty else "hyb")
        or main is None
        or main.dimensions != (
            ("time", "basin", "lev", "rlat") if msfty else ("time", "basin", "lat")
        )
        or lat is None
        or lat.dimensions != (coordinate,)
        or lat.shape != (332,)
        or lat.dtype != np.dtype("f8")
        or getattr(lat, "standard_name", "") != ("grid_latitude" if msfty else "latitude")
        or getattr(lat, "units", "") != ("degrees" if msfty else "degrees_north")
        or "sector" not in ds.variables
        or ds["sector"].dimensions != ("basin", "strlen")
        or ds["sector"].shape != (3, 21)
        or any(a in lat.ncattrs() for a in ("scale_factor", "add_offset"))
    ):
        raise ValueError(
            "Conventional bounds are approved only for the eight reviewed basin profiles and msfty"
        )

    # Check all positions. Only the known terminal duplicate, its former
    # one-ULP repair, or 89.36999 may differ from the supplied reference.
    actual = finite(lat)
    wanted = canonical_latitude()
    native = reference()[0]
    endings = (native[-1], np.nextafter(native[-1], np.inf), NEW_END)
    if not np.array_equal(actual[:-1], wanted[:-1]) or actual[-1] not in endings:
        raise ValueError("Latitude profile does not match the supplied T-column 97 reference")
    target = coordinate + "_bnds"
    if getattr(lat, "bounds", "") not in ("", target):
        raise ValueError("Unexpected bounds reference on the reviewed latitude profile")
    if "bnds" in ds.dimensions and (
        len(ds.dimensions["bnds"]) != 2 or ds.dimensions["bnds"].isunlimited()
    ):
        raise ValueError("Latitude bounds require fixed bnds=2")
    if target in ds.variables:
        v = ds[target]
        if (
            v.shape != (332, 2)
            or v.dimensions[0] != coordinate
            or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
        ):
            raise ValueError("Unexpected latitude bounds representation")
    return coordinate, wanted, conventional_bounds(wanted)


def _verify(source, output):
    with netCDF4.Dataset(output) as ds:
        name, wanted, pairs = _inspect(ds)
        target = name + "_bnds"
        if (
            not np.array_equal(finite(ds[name]), wanted)
            or getattr(ds[name], "bounds", "") != target
            or target not in ds.variables
            or ds[target].dimensions != (name, "bnds")
            or ds[target].dtype != np.dtype("f8")
            or not np.array_equal(finite(ds[target]), pairs)
        ):
            raise ValueError("Conventional latitude bounds did not survive the complete fix sequence")


def _clean_aliases(ds):
    """Retain the approved cleanup without deleting the new latitude bounds."""
    aliases = {
        n for n in (
            "nav_lat", "nav_lon", "latitude", "longitude",
            "bounds_nav_lat", "bounds_nav_lon",
            "vertices_latitude", "vertices_longitude",
        ) if n in ds.variables
    }
    if any(ds[n].dtype.kind not in "fiu" for n in aliases):
        raise ValueError("Unexpected text/scientific array among retired grid auxiliaries")
    changed = delete_variables(ds, aliases)
    for name in ("x", "y", "vertices", "nvertex"):
        if name in ds.dimensions and not any(
            name in v.dimensions for v in ds.variables.values()
        ):
            ds.deleteDimension(name)
            changed = True
    return changed


TARGETS = {"lat", "rlat", "lat_bnds", "rlat_bnds"}
CONVENTION = (
    "Conventional 1-D latitude bounds from adjacent coordinate midpoints. "
    "Outer limits use half-step extrapolation without forcing pole values; the north "
    "half-step uses the preceding non-degenerate spacing. "
    "These are not native NEMO transport-section boundaries."
)


@fix(
    "add_basin_latitude_bounds", verify_data=True,
    data_targets=lambda p: TARGETS, verify_final=_verify,
)
def add_basin_latitude_bounds(ds):
    """Write the common small bounds arrays directly; preserve every science field."""
    name, wanted, pairs = _inspect(ds)
    lat, target = ds[name], name + "_bnds"
    changed = False

    # Harmonize only the terminal value. Keep lat/rlat identifiers and all
    # coordinate attributes, including grid_latitude/degrees for msfty.
    if not np.array_equal(finite(lat), wanted):
        lat[:] = wanted
        changed = True
    if "bnds" not in ds.dimensions:
        ds.createDimension("bnds", 2)
        changed = True
    v = ds.variables.get(target)
    if v is None:
        v = ds.createVariable(target, "f8", (name, "bnds"))
        v[:] = pairs
        changed = True
    elif v.dimensions != (name, "bnds") or v.dtype != np.dtype("f8"):
        v = replace_auxiliary(ds, target, (name, "bnds"), pairs, dtype="f8")
        changed = True
    elif not np.array_equal(finite(v), pairs):
        v[:] = pairs
        changed = True
    changed = set_attrs(lat, {"bounds": target}) or changed
    changed = set_attrs(v, {"comment": CONVENTION}) or changed
    # Bounds inherit these properties from their coordinate; avoid duplicate
    # units/standard_name/axis warnings on the rebuilt boundary variable.
    for attr in ("units", "standard_name", "axis", "positive"):
        if attr in v.ncattrs():
            v.delncattr(attr)
            changed = True
    if name == "lat":
        return _clean_aliases(ds) or changed
    return changed


@fix(
    "drop_basin_latitude_bounds", verify_data=True,
    data_targets=lambda p: TARGETS, verify_final=_verify,
)
def drop_basin_latitude_bounds(ds):
    """Compatibility: old removal plans now honor the latest reconstruction decision."""
    return add_basin_latitude_bounds(ds)
