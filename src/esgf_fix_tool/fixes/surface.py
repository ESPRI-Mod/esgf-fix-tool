"""Keep categorical seasurface labels from being interpreted as vertical axes."""

import netCDF4

from . import fix
from .bounds import _check_label


def _verify_surface_axis(source, output):
    with netCDF4.Dataset(output) as ds:
        _check_label(ds, "seasurface", "osurf", "ocean_surface_layer")
        if "axis" in ds["seasurface"].ncattrs():
            raise ValueError("The seasurface axis attribute was not removed")


@fix("drop_osurf_axis", verify_data=True, verify_final=_verify_surface_axis)
def drop_osurf_axis(ds):
    """Remove the vertical marker without changing labels, units or positive."""
    # Accept both labels so this fix is independent of the label-renaming fix.
    _check_label(ds, "seasurface", "osurf", "ocean_surface_layer")
    v = ds["seasurface"]
    axis = getattr(v, "axis", None)
    if axis not in (None, "", "Z"):
        raise ValueError("Unreviewed axis on seasurface")
    if "axis" not in v.ncattrs():
        return False

    # A categorical label is not a physical vertical coordinate. Remove only
    # its axis attribute; do not invent units or a positive direction.
    v.delncattr("axis")
    return True
