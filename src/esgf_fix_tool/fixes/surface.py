"""Keep categorical seasurface labels from being interpreted as vertical axes."""

import netCDF4

from . import fix


def _surface_coordinate(ds):
    """Validate the categorical representation without reading label bytes.

    Removing axis is independent of scalar-label correction. Non-ASCII bytes
    must neither block an attribute-only fix nor be silently decoded/replaced.
    """
    if "seasurface" not in ds.variables:
        raise ValueError("Scalar coordinate seasurface is absent")
    variable = ds["seasurface"]
    if not (
        variable.dtype is str and variable.ndim == 0
        or variable.dtype is not str
        and variable.dtype.kind == "S"
        and variable.dtype.itemsize == 1
        and variable.ndim == 1
    ):
        raise ValueError("seasurface must be a scalar string or a one-dimensional character label")
    return variable


def _verify_surface_axis(source, output):
    with netCDF4.Dataset(output) as ds:
        if "axis" in _surface_coordinate(ds).ncattrs():
            raise ValueError("The seasurface axis attribute was not removed")


@fix("drop_osurf_axis", verify_data=True, verify_final=_verify_surface_axis)
def drop_osurf_axis(ds):
    """Remove the vertical marker without changing labels, units or positive."""
    v = _surface_coordinate(ds)
    axis = getattr(v, "axis", None)
    if axis not in (None, "", "Z"):
        raise ValueError("Unreviewed axis on seasurface")
    if "axis" not in v.ncattrs():
        return False

    # A categorical label is not a physical vertical coordinate. Remove only
    # its axis attribute; do not invent units or a positive direction.
    v.delncattr("axis")
    return True
