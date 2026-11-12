"""Latest user decision: retain the DR vertical marker on seasurface labels."""

import netCDF4

from . import fix
from .bounds import _check_label


def _verify_surface_axis(source, output):
    with netCDF4.Dataset(output) as ds:
        _check_label(ds, "seasurface", "osurf", "ocean_surface_layer")
        if getattr(ds["seasurface"], "axis", None) != "Z":
            raise ValueError("The requested seasurface axis=Z was not retained")


@fix("set_osurf_axis", verify_data=True, verify_final=_verify_surface_axis)
def set_osurf_axis(ds):
    """Add only axis=Z; keep categorical labels and other metadata unchanged."""
    _check_label(ds, "seasurface", "osurf", "ocean_surface_layer")
    v = ds["seasurface"]
    axis = getattr(v, "axis", None)
    if axis not in (None, "", "Z"):
        raise ValueError("Unreviewed axis on seasurface")
    if axis == "Z":
        return False
    v.setncattr("axis", "Z")
    return True


@fix("drop_osurf_axis", verify_data=True, verify_final=_verify_surface_axis)
def drop_osurf_axis(ds):
    """Compatibility for old plans: follow the revised decision instead of undoing it."""
    return set_osurf_axis(ds)
