"""Reviewed basin profiles: squeeze x=1, expose lat(lat), correct only the approved endpoint."""

import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np

from . import fix
from ..data_integrity import data_blocks
from .coordinates import _main_variable, _replace_references, REFERENCE_ATTRS
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
    data_targets=lambda p: {p["var"], "nav_lat", "lat", "nav_lon", "longitude"},
)
def fix_basin_profile_latitude(ds, var) -> bool:
    """Preserve all 332 rows and raw field values; only the final latitude value may change.

    nav_lon is retained as an unreferenced auxiliary, with its singleton x squeezed.
    No latitude bounds, regridding, grid_label or global metadata are invented.
    """
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.

    # 2. Preflight only; the after-close hook rewrites and verifies the exact squeeze.
    return not _profile_is_correct(ds, var)
