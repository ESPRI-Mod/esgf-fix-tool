"""User-confirmed removal of the vertical axis marker from categorical osurf labels."""
import netCDF4

from . import fix
from .bounds import _check_label


def _verify_surface_axis(source, output):
    with netCDF4.Dataset(source) as original, netCDF4.Dataset(output) as result:
        for ds in [original, result]:
            _check_label(ds, 'seasurface', 'osurf', 'ocean_surface_layer')
        if 'axis' in result['seasurface'].ncattrs():
            raise ValueError('The categorical seasurface axis attribute survived or was restored by another fix')


@fix('drop_osurf_axis', verify_data=True, verify_final=_verify_surface_axis)
def drop_osurf_axis(ds) -> bool:
    """Remove only axis=Z from a reviewed scalar string label; leave all values/other attrs intact."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    _check_label(ds, 'seasurface', 'osurf', 'ocean_surface_layer')
    v = ds['seasurface']
    if 'axis' not in v.ncattrs():
        return False
    if v.getncattr('axis') != 'Z':
        raise ValueError('Unexpected axis on categorical seasurface; only the reviewed Z is removed')

    # 2. Remove axis=Z only; do not add units, positive or a numeric depth.
    v.delncattr('axis')
    return True
