"""Reverse the approved g131 latitude axis and all arrays sharing its dimension."""

import netCDF4
import numpy as np

from . import fix


def _geometry(ds):
    name = getattr(ds, "variable_id", "")
    if (
        getattr(ds, "grid_label", "") != "g131"
        or name not in ("areacellr", "rivi", "rivo")
        or name not in ds.variables
        or "lat" not in ds[name].dimensions
    ):
        raise ValueError("Latitude reversal is restricted to the reviewed g131 products")
    candidates = [n for n in ("lat", "latitude") if n in ds.variables]
    if len(candidates) != 1:
        raise ValueError("Missing or ambiguous latitude coordinate")
    coordinate = ds[candidates[0]]
    if (
        coordinate.dimensions != ("lat",)
        or coordinate.dtype.kind != "f"
        or getattr(coordinate, "standard_name", "") != "latitude"
        or getattr(coordinate, "units", "") != "degrees_north"
        or any(a in coordinate.ncattrs() for a in ("scale_factor", "add_offset"))
    ):
        raise ValueError("Expected an unpacked one-dimensional geographic latitude")
    values = np.ma.asarray(coordinate[:])
    expected = np.arange(-89.75, 90., .5)
    if np.ma.getmaskarray(values).any() or values.shape != expected.shape:
        raise ValueError("Expected the 360 reviewed half-degree latitudes")
    increasing = np.allclose(values, expected, rtol=0, atol=1e-7)
    decreasing = np.allclose(values, expected[::-1], rtol=0, atol=1e-7)
    if not (increasing or decreasing):
        raise ValueError("Latitude values/order differ from the reviewed g131 axis")

    # Preserve the supplied edges. Only reverse rows and, when necessary, the
    # two endpoint columns; do not invent or recalculate geographic bounds.
    bound_name = getattr(coordinate, "bounds", "")
    if bound_name not in ("lat_bnds", "bounds_lat") or bound_name not in ds.variables:
        raise ValueError("Expected existing latitude bounds")
    bounds = ds[bound_name]
    raw = np.ma.asarray(bounds[:])
    if (
        bounds.dimensions[0:1] != ("lat",)
        or raw.shape != (360, 2)
        or bounds.dtype.kind != "f"
        or any(a in bounds.ncattrs() for a in ("scale_factor", "add_offset"))
        or np.ma.getmaskarray(raw).any()
        or not np.isfinite(raw).all()
    ):
        raise ValueError("Unexpected latitude bounds representation")
    raw = np.asarray(raw)
    ordered = np.sort(raw, axis=1)
    if np.any(values < ordered[:, 0]) or np.any(values > ordered[:, 1]):
        raise ValueError("Latitude centers must lie within their existing bounds")
    return coordinate.name, bound_name, decreasing, raw, ordered


def _verify(source, output):
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        name, bound, reverse, _raw, ordered = _geometry(src)
        output_name, output_bound, still_reversed, _, _ = _geometry(dst)
        if still_reversed or name != output_name or bound != output_bound:
            raise ValueError("Latitude ordering did not survive the grouped rewrite")
        expected_lat = src[name][:][::-1] if reverse else src[name][:]
        expected_bounds = ordered[::-1] if reverse else ordered
        if not np.array_equal(dst[name][:], expected_lat) or not np.array_equal(
            dst[bound][:], expected_bounds
        ):
            raise ValueError("Unexpected geographic coordinate/bounds changes")
        # Batch.verify also checks every reordered science tile against its
        # original raw values before the temporary file replaces the source.


@fix(
    "reverse_reviewed_latitude",
    verify_data=True,
    data_targets=lambda p: {"lat_bnds", "bounds_lat"},
    verify_final=_verify,
)
def reverse_reviewed_latitude(ds):
    """Prepare the small bounds edit and defer all row reversal to the shared writer."""
    _, bound, reverse, raw, ordered = _geometry(ds)
    changed = not np.array_equal(raw, ordered)
    if changed:
        ds[bound][:] = ordered
    if reverse:
        object.__setattr__(ds, "_latitude_reverse_dimension", "lat")
    return reverse or changed


def configure(batch, preview):
    """Apply the same reversal to every variable carrying the latitude dimension."""
    dimension = getattr(preview, "_latitude_reverse_dimension", None)
    if dimension is None:
        return
    if batch.climatology is not None or batch.heat_fields or batch.hybrid_center_fields:
        raise ValueError("Combined scientific reductions and latitude reversal are unreviewed")
    for spec in batch.variables.values():
        if dimension in spec.dimensions:
            spec.reverse_dims = (dimension,)
