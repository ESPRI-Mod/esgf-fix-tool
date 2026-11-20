"""Reviewed basin profiles: squeeze x=1, retain the approved 331/332-point lat coordinate."""

import os
import tempfile
from pathlib import Path

import netCDF4
import numpy as np

from ..data_integrity import data_blocks
from . import fix
from .coordinates import REFERENCE_ATTRS, _main_variable, _replace_references
from .horizontal import _specs
from .layouts import _copy_attrs, _creation_options, _raw, _same_attrs, _transform

OLD_END = 89.36998
NEW_END = 89.36999
ROWS = 332


def _profile(ds, var):

    # Restrict the conversion to the reviewed basin-profile branding and geometry.
    brand = getattr(ds, "branded_variable", "")
    spec = _specs()["brands"].get(brand)
    if (
        not spec
        or spec["variable"] != var
        or not brand.endswith("-hyb-sea")
        or getattr(ds, "horizontal_label", "") != "hyb"
        or getattr(ds, "grid_label", "") != "g112"
    ):
        raise ValueError(
            "Rectilinear conversion is restricted to the eight reviewed basin profiles"
        )
    main = _main_variable(ds, var)
    if any(n in ds.dimensions and len(ds.dimensions[n]) == 331 for n in ("y", "lat", "rlat")):
        return _profile_331(ds, var)
    if main.dimensions == ("time", "basin", "y", "x"):
        if len(ds.dimensions["x"]) != 1 or len(ds.dimensions["y"]) != ROWS:
            raise ValueError(
                "Expected exactly y=332,x=1; no row is removed or averaged"
            )
        if (
            "lat" in ds.dimensions
            or "lat" in ds.variables
            or "nav_lat" not in ds.variables
        ):
            raise ValueError("Missing nav_lat or existing lat collision")
        name = "nav_lat"
        if ds[name].dimensions != ("y", "x") or getattr(ds[name], "bounds", None):
            raise ValueError("Unreviewed profile-latitude geometry or bounds")
        spatial = [
            n for n, v in ds.variables.items() if {"x", "y"}.intersection(v.dimensions)
        ]
        if set(spatial) - {var, "nav_lat", "nav_lon", "longitude"}:
            raise ValueError(
                "Additional spatial arrays require an explicit layout review"
            )
    elif main.dimensions == ("time", "basin", "lat"):
        name = "lat"
        if (
            name not in ds.variables
            or ds[name].dimensions != ("lat",)
            or len(ds.dimensions["lat"]) != ROWS
        ):
            raise ValueError("Unexpected canonical profile latitude")
    else:
        raise ValueError(
            "Unreviewed main-field layout; expected time,basin,y,x or time,basin,lat"
        )
    v = ds[name]
    if (
        v.dtype != np.dtype("f8")
        or getattr(v, "standard_name", "") != "latitude"
        or getattr(v, "units", "") != "degrees_north"
        or any(
            a in v.ncattrs()
            for a in [
                "scale_factor",
                "add_offset",
                "valid_range",
                "valid_min",
                "valid_max",
            ]
        )
    ):
        raise ValueError("Expected an unpacked double latitude in degrees_north")
    _raw(v)
    values = np.asarray(v[:]).reshape(ROWS).copy()
    if (
        not np.isfinite(values).all()
        or not (np.diff(values[:-1]) > 0).all()
        or values[0] < -90
    ):
        raise ValueError(
            "Only a duplicated terminal latitude is approved; earlier latitudes must increase"
        )
    if abs(values[-2] - OLD_END) > 2.0e-7:
        raise ValueError(
            "Penultimate latitude differs from the reviewed 89.36998 value"
        )
    if values[-1] != NEW_END and values[-1] != values[-2]:
        raise ValueError(
            "Unexpected terminal latitude; only the reviewed duplicate or corrected value is accepted"
        )
    values[-1] = NEW_END
    if not (np.diff(values) > 0).all():
        raise ValueError(
            "Approved endpoint did not produce a strictly increasing latitude"
        )
    return name, values



def _profile_331(ds, var):
    """Validate all retained rows and only restore a verified empty southern prefix."""
    from .msfty_latitude import reference_331, restore_empty_prefix
    from .reviewed_structures import finite

    main = _main_variable(ds, var)
    axes = [n for n in ("y", "lat", "rlat") if n in main.dimensions]
    if len(axes) != 1 or len(ds.dimensions[axes[0]]) != 331:
        raise ValueError("Expected exactly 331 meridional rows")
    dimension = axes[0]
    expected = ("time", "basin", dimension)
    if main.dimensions not in (expected, expected + ("x",)) or len(ds.dimensions["basin"]) != 3:
        raise ValueError("Expected time,basin,meridional-row with an optional trailing singleton x")
    if "x" in ds.dimensions and len(ds.dimensions["x"]) != 1:
        raise ValueError("Cannot remove non-singleton x from a basin profile")
    names = [n for n in ("nav_lat", "latitude", "lat", "rlat") if n in ds.variables]
    if len(names) != 1:
        raise ValueError("Missing or ambiguous profile latitude")
    name = names[0]
    coordinate = ds[name]
    if (coordinate.dimensions not in ((dimension,), (dimension, "x"))
            or coordinate.size != 331 or coordinate.dtype != np.dtype("f8")
            or (getattr(coordinate, "standard_name", ""), getattr(coordinate, "units", ""))
            not in (("latitude", "degrees_north"), ("grid_latitude", "degrees"))
            or any(a in coordinate.ncattrs() for a in ("scale_factor", "add_offset"))):
        raise ValueError("Expected an unpacked double latitude or grid_latitude coordinate")
    if dimension != "lat" and "lat" in ds.dimensions:
        raise ValueError("Existing lat dimension would collide with the source row axis")
    values = finite(coordinate).reshape(-1)
    restored = restore_empty_prefix(ds, values, var)
    wanted = values if restored is None else restored
    if not np.array_equal(wanted, reference_331()):
        raise ValueError("The 331-point basin profile differs from the supplied T-column 97 reference")

    # Retain zero-longitude auxiliaries as unreferenced arrays, as in the old
    # C070 recipe. Never erase a different longitude or an unknown spatial field.
    spatial = {n for n, v in ds.variables.items() if {dimension, "x"}.intersection(v.dimensions)}
    allowed = {var, name, "nav_lon", "longitude", "lat_bnds", "rlat_bnds"}
    if spatial - allowed:
        raise ValueError("Additional profile spatial arrays require an explicit review")
    for longitude in ("nav_lon", "longitude"):
        if longitude in ds.variables:
            v = ds[longitude]
            if v.dimensions not in ((dimension,), (dimension, "x")) or not np.all(finite(v) == 0):
                raise ValueError("Only an all-zero profile longitude may be detached")
    ref = getattr(coordinate, "bounds", "")
    if ref not in ("", "lat_bnds", "rlat_bnds"):
        raise ValueError("Unexpected profile latitude bounds reference")
    if ref:
        if ref not in ds.variables or ds[ref].dimensions[0] != dimension or ds[ref].shape != (331, 2):
            raise ValueError("Unexpected profile latitude bounds geometry")
        finite(ds[ref])
    return name, wanted


def _prepare_profile_331(ds, var):
    """Prepare names, CF attributes and conventional bounds; defer the science squeeze."""
    from .auxiliary import replace_auxiliary
    from .basin_latitude_bounds import add_basin_latitude_bounds
    from .reviewed_structures import finite, set_attrs

    name, wanted = _profile(ds, var)
    dimension = next(n for n in ("y", "lat", "rlat") if n in ds[var].dimensions)
    changed = False
    if name != "lat":
        ds.renameVariable(name, "lat")
        _replace_references(ds, name, "lat")
        changed = True
    if dimension != "lat":
        ds.renameDimension(dimension, "lat")
        changed = True
    coordinate = ds["lat"]
    if coordinate.dimensions != ("lat",):
        coordinate = replace_auxiliary(ds, "lat", ("lat",), wanted, dtype="f8")
        changed = True
    elif not np.array_equal(finite(coordinate), wanted):
        coordinate[:] = wanted
        changed = True
    changed = set_attrs(coordinate, dict(
        standard_name="latitude", units="degrees_north", axis="Y", long_name="Latitude"
    )) or changed
    if "name" in coordinate.ncattrs():
        changed = set_attrs(coordinate, {"name": "lat"}) or changed
    if "rlat_bnds" in ds.variables:
        if "lat_bnds" in ds.variables:
            raise ValueError("Ambiguous latitude bounds: both lat_bnds and rlat_bnds exist")
        ds.renameVariable("rlat_bnds", "lat_bnds")
        _replace_references(ds, "rlat_bnds", "lat_bnds")
        changed = True

    # Mark only x=1 for the shared writer. All other dimensions, including basin,
    # retain their identities, lengths and raw values. Nothing is averaged.
    if "x" in ds.dimensions:
        pending = getattr(ds, "_pending_shared_singletons", set()) | {"x"}
        object.__setattr__(ds, "_pending_shared_singletons", pending)
        changed = True
    for variable in ds.variables.values():
        text = getattr(variable, "coordinates", "")
        if isinstance(text, str):
            kept = " ".join(n for n in text.split() if n not in ("nav_lon", "longitude"))
            if kept != text:
                variable.setncattr("coordinates", kept)
                changed = True
    return add_basin_latitude_bounds(ds) or changed


def verify_profile_output(output, var):
    """Verify the product-specific final axis, including 331-point conventional bounds."""
    with netCDF4.Dataset(output) as ds:
        _, values = _profile(ds, var)
        if values.size == 331:
            from .basin_latitude_bounds import _verify

            if (ds[var].dimensions != ("time", "basin", "lat")
                    or ds["lat"].dimensions != ("lat",) or "x" in ds.dimensions
                    or "y" in ds.dimensions or "rlat" in ds.dimensions
                    or ds["lat"].axis != "Y" or ds["lat"].long_name != "Latitude"):
                raise ValueError("The 331-point profile did not retain its approved lat structure")
            _verify(None, output)


def _dimensions(dimensions):
    return tuple("lat" if d == "y" else d for d in dimensions if d != "x")


def _expected_attrs(name, source):
    attrs = {a: source.getncattr(a) for a in source.ncattrs() if a != "_FillValue"}
    import re

    token = re.compile(r"(?<![\w/])nav_lat(?![\w/])")
    for attr in REFERENCE_ATTRS:
        if isinstance(attrs.get(attr), str):
            attrs[attr] = token.sub("lat", attrs[attr])
    if name in ["nav_lat", "lat"]:
        attrs["axis"] = "Y"
        if attrs.get("name") == "nav_lat":
            attrs["name"] = "lat"
    return attrs


def _verify_arrays(source, output, var, *, check_metadata=False):
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        latitude, expected_lat = _profile(src, var)
        _profile(dst, var)
        mapping = {latitude: "lat"}
        if set(dst.variables) != {mapping.get(n, n) for n in src.variables}:
            raise ValueError("Profile conversion changed the variable inventory")
        for name, v in src.variables.items():
            out = dst[mapping.get(name, name)]
            expected_dims = _dimensions(v.dimensions)
            if out.dimensions != expected_dims or out.dtype != v.dtype:
                raise ValueError(f"Unexpected profile mapping for {name}")
            _raw(v)
            _raw(out)
            if name == latitude:
                if out[:].tobytes() != expected_lat.tobytes():
                    raise ValueError("Latitude changed beyond the approved last value")
            else:
                kept = tuple(d for d in v.dimensions if d != "x")
                for slab in data_blocks(v, "Comparaison du profil"):
                    expected = _transform(np.asarray(v[slab]), v.dimensions, kept)
                    target_slab = tuple(
                        slab[i] for i, d in enumerate(v.dimensions) if d != "x"
                    )
                    actual = np.asarray(out[target_slab])
                    equal = (
                        np.array_equal(expected, actual)
                        if expected.dtype.kind == "O"
                        else expected.tobytes() == actual.tobytes()
                    )
                    if not equal:
                        raise ValueError(
                            f"Profile conversion changed raw values in {name}"
                        )
            if check_metadata:
                expected_attrs = _expected_attrs(name, v)
                if name == var and "coordinates" in expected_attrs:
                    expected_attrs["coordinates"] = " ".join(
                        t
                        for t in expected_attrs["coordinates"].split()
                        if t not in ["nav_lon", "longitude"]
                    )
                actual_attrs = {
                    a: out.getncattr(a) for a in out.ncattrs() if a != "_FillValue"
                }
                if expected_attrs.keys() != actual_attrs.keys() or any(
                    not np.array_equal(np.asarray(value), np.asarray(actual_attrs[a]))
                    for a, value in expected_attrs.items()
                ):
                    raise ValueError(
                        f"Profile conversion changed unreviewed attributes on {name}"
                    )
                if v.filters() != out.filters():
                    raise ValueError(f"Compression changed for {name}")
                chunks = v.chunking()
                expected_chunks = (
                    [c for d, c in zip(v.dimensions, chunks) if d != "x"]
                    if isinstance(chunks, list)
                    else chunks
                )
                if out.chunking() != expected_chunks:
                    raise ValueError(f"Chunk mapping differs for {name}")
        expected_dims = {
            ("lat" if n == "y" else n): (len(d), d.isunlimited())
            for n, d in src.dimensions.items()
            if n != "x"
        }
        if expected_dims != {
            n: (len(d), d.isunlimited()) for n, d in dst.dimensions.items()
        }:
            raise ValueError("Profile conversion changed unrelated dimensions")
        if check_metadata and (
            src.data_model != dst.data_model or not _same_attrs(src, dst)
        ):
            raise ValueError("Profile conversion changed format or global metadata")
        if getattr(dst["lat"], "axis", None) != "Y" or any(
            t in getattr(dst[var], "coordinates", "").split()
            for t in ["nav_lat", "nav_lon", "longitude"]
        ):
            raise ValueError("Profile coordinate references were not normalized")


def _finish_profile(source, output, var):
    fd, rebuilt = tempfile.mkstemp(
        prefix=".profile-", suffix=".fixtmp", dir=Path(output).parent
    )
    os.close(fd)
    try:
        # 1. Validate the profile and obtain the single approved terminal-latitude change.
        with netCDF4.Dataset(output) as src:
            name, latitude = _profile(src, var)
            if src.groups or src.cmptypes or src.enumtypes or src.vltypes:
                raise ValueError(
                    "Profile conversion requires a flat file without custom types"
                )
            with netCDF4.Dataset(rebuilt, "w", format=src.data_model) as dst:
                _copy_attrs(src, dst)
                for n, d in src.dimensions.items():
                    if n != "x":
                        dst.createDimension(
                            "lat" if n == "y" else n,
                            None if d.isunlimited() else len(d),
                        )

                # 2. Copy raw values, squeezing only x=1; retain all 332 scientific rows.
                for n, v in src.variables.items():
                    target = "lat" if n == name else n
                    kept = tuple(d for d in v.dimensions if d != "x")
                    out = dst.createVariable(
                        target,
                        v.datatype,
                        _dimensions(v.dimensions),
                        **_creation_options(v, kept),
                    )
                    _raw(v)
                    _raw(out)
                    if n == name:
                        out[:] = latitude
                    else:
                        for slab in data_blocks(v, "Réécriture du profil"):
                            target_slab = tuple(
                                slab[i] for i, d in enumerate(v.dimensions) if d != "x"
                            )
                            out[target_slab] = _transform(
                                np.asarray(v[slab]), v.dimensions, kept
                            )
                    _copy_attrs(v, out, skip=["_FillValue"])
                _replace_references(dst, "nav_lat", "lat")
                dst["lat"].axis = "Y"
                if (
                    "name" in dst["lat"].ncattrs()
                    and dst["lat"].getncattr("name") == "nav_lat"
                ):
                    dst["lat"].setncattr("name", "lat")
                if "coordinates" in dst[var].ncattrs():
                    dst[var].coordinates = " ".join(
                        t
                        for t in dst[var].coordinates.split()
                        if t not in ["nav_lon", "longitude"]
                    )

        # 3. Verify every mapped array, metadata and compression before replacing the temporary.
        _verify_arrays(output, rebuilt, var, check_metadata=True)
        os.replace(rebuilt, output)
        return {
            "backend": "python-netCDF4",
            "profile_data_verified": True,
            "terminal_latitude": NEW_END,
        }
    finally:
        if os.path.exists(rebuilt):
            os.unlink(rebuilt)


def _profile_is_correct(ds, var):
    name, values = _profile(ds, var)
    _raw(ds[name])
    return (
        name == "lat"
        and ds[name][:].tobytes() == values.tobytes()
        and getattr(ds[name], "axis", None) == "Y"
        and not (
            "name" in ds[name].ncattrs() and ds[name].getncattr("name") == "nav_lat"
        )
        and not any(
            t in getattr(ds[var], "coordinates", "").split()
            for t in ["nav_lat", "nav_lon", "longitude"]
        )
    )


@fix(
    "fix_basin_profile_latitude",
    kind="structural",
    verify_data=True,
    after_close=_finish_profile,
    verify_final=_verify_arrays,
    data_targets=lambda p: {p["var"], "nav_lat", "latitude", "lat", "rlat", "rlat_bnds", "lat_bnds", "nav_lon", "longitude"},
)
def fix_basin_profile_latitude(ds, var) -> bool:
    """Keep every science row/value on either approved native profile.

    The 331-point variant prepares lat and conventional bounds, including
    files previously renamed to rlat by an older plan. The legacy
    332-point variant keeps lat and its reviewed terminal-value correction.
    Only the singleton x is squeezed; basin, times and data remain intact.
    """
    # Reuse this recipe for the approved 331-point grid. The old 332-point
    # convention remains unchanged; these two native profiles are not interchangeable.
    _, values = _profile(ds, var)
    if values.size == 331:
        return _prepare_profile_331(ds, var)
    return not _profile_is_correct(ds, var)
