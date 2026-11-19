"""Rotate the reviewed half-degree river grid from signed to positive longitude."""

import netCDF4
import numpy as np

from . import fix


PRODUCTS = ("areacellr", "rivi", "rivo")


def _geometry(ds):
    """Validate current coordinates without reading any scientific matrix."""
    main = getattr(ds, "variable_id", "")
    if (
        getattr(ds, "grid_label", "") != "g131"
        or main not in PRODUCTS
        or main not in ds.variables
        or "lon" not in ds[main].dimensions
    ):
        raise ValueError("Longitude rotation is restricted to reviewed g131 river products")
    names = [n for n in ("lon", "longitude") if n in ds.variables]
    if len(names) != 1:
        raise ValueError("Missing or ambiguous longitude coordinate")
    coordinate = ds[names[0]]
    if (
        coordinate.dimensions != ("lon",)
        or coordinate.dtype.kind != "f"
        or getattr(coordinate, "standard_name", "") != "longitude"
        or getattr(coordinate, "units", "") != "degrees_east"
        or any(a in coordinate.ncattrs() for a in ("scale_factor", "add_offset"))
    ):
        raise ValueError("Expected an unpacked one-dimensional geographic longitude")
    values = np.ma.asarray(coordinate[:])
    signed = np.arange(-179.75, 180., .5)
    positive = np.arange(.25, 360., .5)
    if (
        values.shape != (720,)
        or np.ma.getmaskarray(values).any()
        or not np.isfinite(values).all()
    ):
        raise ValueError("Expected 720 finite, unmasked longitudes")
    rotate = np.allclose(values, signed, rtol=0, atol=1e-7)
    if not rotate and not np.allclose(values, positive, rtol=0, atol=1e-7):
        raise ValueError("Longitude values/order differ from the reviewed half-degree axis")
    values = np.asarray(values)

    bound_name = getattr(coordinate, "bounds", "")
    if bound_name not in ("lon_bnds", "bounds_lon") or bound_name not in ds.variables:
        raise ValueError("Expected existing longitude bounds")
    bounds = ds[bound_name]
    edges = np.ma.asarray(bounds[:])
    if (
        bounds.dimensions[0:1] != ("lon",)
        or edges.shape != (720, 2)
        or bounds.dtype.kind != "f"
        or any(a in bounds.ncattrs() for a in ("scale_factor", "add_offset"))
        or np.ma.getmaskarray(edges).any()
        or not np.isfinite(edges).all()
        or not np.allclose(edges, np.column_stack((values - .25, values + .25)), rtol=0, atol=1e-7)
    ):
        raise ValueError("Expected existing contiguous half-degree longitude bounds")
    return coordinate.name, bound_name, rotate, values, np.asarray(edges)


def _verify(source, output):
    """Check the seam explicitly; the shared writer also verifies every raw tile."""
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        _, _, rotate, values, edges = _geometry(src)
        _, _, still_signed, actual, actual_edges = _geometry(dst)
        if rotate:
            offset = np.where(values < 0, 360., 0.)
            values = np.roll(values + offset, 360)
            edges = np.roll(edges + offset[:, None], 360, axis=0)
        if still_signed or not np.array_equal(actual, values) or not np.array_equal(actual_edges, edges):
            raise ValueError("Longitude rotation did not survive the grouped rewrite")
        if np.any(actual_edges[:, 0] > actual) or np.any(actual > actual_edges[:, 1]):
            raise ValueError("Rotated longitudes lie outside their bounds")


@fix(
    "rotate_reviewed_longitude",
    verify_data=True,
    data_targets=lambda p: {"lon", "longitude", "lon_bnds", "bounds_lon"},
    verify_final=_verify,
)
def rotate_reviewed_longitude(ds):
    """Translate small coordinate buffers; defer the column rotation to one writer."""
    coordinate, bound, rotate, values, edges = _geometry(ds)
    if not rotate:
        return False

    # Keep these buffers in source-column order. The grouped writer rotates them
    # together with every scientific/auxiliary array sharing the lon dimension.
    offset = np.where(values < 0, 360., 0.)
    ds[coordinate][:] = values + offset

    # Translate both endpoints according to the cell center. Applying modulo to
    # each endpoint independently would turn the last upper bound 360 into zero.
    ds[bound][:] = edges + offset[:, None]
    object.__setattr__(ds, "_longitude_cyclic_shift", ("lon", 360))
    return True


def configure(batch, preview):
    rotation = getattr(preview, "_longitude_cyclic_shift", None)
    if rotation is None:
        return
    if batch.climatology is not None or batch.heat_fields or batch.hybrid_center_fields:
        raise ValueError("Combined scientific reductions and longitude rotation are unreviewed")
    dimension, offset = rotation
    for spec in batch.variables.values():
        if dimension in spec.dimensions:
            if spec.dimensions.count(dimension) != 1:
                raise ValueError("Repeated longitude dimensions are unreviewed")
            spec.cyclic_shifts = ((dimension, offset),)
