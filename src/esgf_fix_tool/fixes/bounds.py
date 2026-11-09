"""Reviewed labels, longitude precision, bounds references and size-two dimension identities."""
import json
import os
from pathlib import Path
import re
import tempfile

import netCDF4
import numpy as np

from . import fix
from .auxiliary import ORIGINAL_PREFIX, replace_auxiliary
from .coordinates import _replace_references
from .layouts import _python_rewrite, _raw, _verify_rewrite
from ..data_integrity import data_blocks


def _specs():
    return json.loads(Path(__file__).with_name('bounds_specs.json').read_text())


def _numeric(v):
    a = np.ma.asarray(v[...])
    if np.ma.getmaskarray(a).any() or not np.isfinite(a).all():
        raise ValueError(f'Missing/non-finite values in {v.name}')
    return np.asarray(a)


def _text(v):
    v.set_auto_maskandscale(False)
    v.set_auto_chartostring(False)
    a = np.asarray(v[...])
    if v.dtype == str and not v.dimensions:
        text = str(a.item())
    elif a.dtype.kind == 'S' and a.dtype.itemsize == 1 and a.ndim == 1:
        text = a.tobytes().decode('ascii')
    else:
        raise ValueError(f'{v.name} is not a scalar string / one-dimensional character label')
    return text.rstrip('\x00 ').strip()


def _check_label(ds, var, expected, value):
    if _specs()['labels'].get(var, {}).get(expected) != value:
        raise ValueError('Unreviewed scalar label mapping')
    if var not in ds.variables:
        raise ValueError(f'Scalar coordinate {var} is absent')
    current = _text(ds[var])
    if current not in [expected, value]:
        raise ValueError(f'Unexpected current label in {var}: {current!r}')
    return current


def _verify_label(source, output, var, expected, value):
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        _check_label(src, var, expected, value)
        if _check_label(dst, var, expected, value) != value or not _canonical_label(dst[var], value):
            raise ValueError(f'Corrected label {var} did not survive writing')
        # C087 may rename only the string-length dimension in the same batch.
        dimensions = tuple('strlen' if d == 'str_len' else d for d in src[var].dimensions)
        if (dst[var].dimensions not in (src[var].dimensions, dimensions)
                or src[var].shape != dst[var].shape or src[var].dtype != dst[var].dtype):
            raise ValueError('Label correction changed its representation')


def _canonical_label(v, value):
    if v.dtype == str:
        return str(np.asarray(v[...]).item()) == value
    raw = np.asarray(v[...]).tobytes()
    encoded = value.encode('ascii')
    return raw == encoded + b' ' * (len(raw) - len(encoded))


@fix('fix_scalar_label', verify_data=True, data_targets=lambda p: {p['var']}, verify_final=_verify_label)
def fix_scalar_label(ds, var: str, expected: str, value: str) -> bool:
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    if _check_label(ds, var, expected, value) == value and _canonical_label(ds[var], value):
        return False

    # 2. Write the approved label, preserving its scalar-string or character representation.
    v = ds[var]
    if v.dtype == str:
        v[()] = value
    else:
        encoded = value.encode('ascii')
        if len(encoded) > v.size:
            raise ValueError(f'The {var} string-length dimension is too short')
        data = np.full(v.shape, b' ', dtype='S1')
        data[:len(encoded)] = np.frombuffer(encoded, dtype='S1')
        v[...] = data
    return True


def _longitude(ds):
    if getattr(ds, 'grid_label', None) != 'g111' or 'lon' not in ds.variables:
        raise ValueError('Longitude normalization is restricted to reviewed g111 files')
    lon = ds['lon']
    bound_name = getattr(lon, 'bounds', '')
    if bound_name not in ['bounds_lon', 'lon_bnds'] or bound_name not in ds.variables:
        raise ValueError('Cannot identify the reviewed longitude bounds')
    bounds = ds[bound_name]
    if lon.dimensions != ('lon',) or lon.shape != (144,) or bounds.shape != (144, 2) or bounds.dimensions[0] != 'lon':
        raise ValueError('Longitude normalization requires 144 longitudes and 144x2 bounds')
    if getattr(lon, 'units', '') not in ['degrees_east', 'degree_east', 'degrees_E']:
        raise ValueError('Unexpected longitude units')
    for v in [lon, bounds]:
        if v.dtype.kind != 'f' or any(a in v.ncattrs() for a in ['scale_factor', 'add_offset']):
            raise ValueError('Longitude normalization requires unpacked floating-point coordinates')
    centers = np.arange(144, dtype='f8') * 2.5
    edges = np.column_stack([centers - 1.25, centers + 1.25])
    for v, wanted in [(lon, centers), (bounds, edges)]:
        if not np.allclose(_numeric(v), wanted, rtol=0, atol=1.e-10):
            raise ValueError('Coordinates differ from the reviewed regular g111 grid by more than 1e-10 degree')
    return lon, bounds, centers, edges


def _verify_longitude(source, output):
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        _longitude(src)
        lon, bounds, centers, edges = _longitude(dst)
        for v, wanted in [(lon, centers), (bounds, edges)]:
            if not np.array_equal(_numeric(v), wanted):
                raise ValueError('Normalized longitude values did not survive the other fixes')


@fix('normalize_regular_longitude', verify_data=True,
     data_targets=lambda p: {'lon', 'bounds_lon', 'lon_bnds'}, verify_final=_verify_longitude)
def normalize_regular_longitude(ds) -> bool:
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    lon, bounds, centers, edges = _longitude(ds)

    # 2. Correct only the reviewed tiny rounding differences, including stored bounds.
    changed = False
    for v, wanted in [(lon, centers), (bounds, edges)]:
        if not np.array_equal(_numeric(v), wanted):
            v[...] = wanted
            changed = True
    return changed


ICEBAND_VALUES = (0.45, 1.12, 2.14, 3.67, 99.)
ICEBAND_BOUNDS = tuple(zip((0., *ICEBAND_VALUES[:-1]), ICEBAND_VALUES))


def _iceband_coordinate(ds):
    var = getattr(ds, 'variable_id', '')
    if (getattr(ds, 'source_id', '') != 'IPSL-CM6-ESMCO2'
            or getattr(ds, 'experiment_id', '') != 'esm-piControl-spinup'
            or getattr(ds, 'grid_label', '') != 'g112'
            or getattr(ds, 'variant_label', '') != 'r1i1p1f1'
            or var not in {'siitdconc', 'siitdthick', 'siitdsnthick'}
            or var not in ds.variables or 'iceband' not in ds[var].dimensions):
        raise ValueError('Iceband correction is restricted to the reviewed IPSL sea-ice files')
    if 'iceband' not in ds.variables:
        raise ValueError('Iceband coordinate is absent')
    coord = ds['iceband']
    if (coord.dimensions != ('iceband',) or coord.shape != (5,)
            or coord.dtype.kind != 'f' or coord.dtype.itemsize != 8
            or getattr(coord, 'units', '') != 'm'
            or getattr(coord, 'standard_name', '') != 'sea_ice_thickness'
            or {'scale_factor', 'add_offset'} & set(coord.ncattrs())):
        raise ValueError('Expected five unpacked double-precision iceband values in metres')
    return coord


def _check_iceband_bounds(bounds):
    if (bounds.dimensions != ('iceband', 'bnds') or bounds.shape != (5, 2)
            or bounds.dtype.kind != 'f' or bounds.dtype.itemsize != 8
            or getattr(bounds, 'units', 'm') != 'm'
            or {'scale_factor', 'add_offset'} & set(bounds.ncattrs())
            or not np.array_equal(_numeric(bounds), ICEBAND_BOUNDS)):
        raise ValueError('Existing iceband_bnds disagrees with the confirmed model limits')


def _verify_iceband(source, output):
    with netCDF4.Dataset(output) as ds:
        coord = _iceband_coordinate(ds)
        if not np.array_equal(_numeric(coord), ICEBAND_VALUES):
            raise ValueError('The approved iceband values did not survive writing')
        if getattr(coord, 'bounds', '') != 'iceband_bnds' or 'iceband_bnds' not in ds.variables:
            raise ValueError('Missing iceband bounds reference or variable')
        _check_iceband_bounds(ds['iceband_bnds'])


@fix('fix_iceband', verify_data=True,
     data_targets=lambda p: {'iceband'}, verify_final=_verify_iceband)
def fix_iceband(ds) -> bool:
    """Publish the confirmed contiguous thickness intervals in the original class order."""
    coord = _iceband_coordinate(ds)
    current = _numeric(coord)
    axis_ok = np.array_equal(current, ICEBAND_VALUES)
    if not axis_ok and not np.array_equal(current, [1, 2, 3, 4, 5]):
        raise ValueError('Unexpected iceband values or class order; expected indices 1 to 5')
    reference = getattr(coord, 'bounds', '')
    if reference not in ('', 'iceband_bnds'):
        raise ValueError('Unexpected iceband bounds reference')
    has_bounds = 'iceband_bnds' in ds.variables
    if has_bounds:
        _check_iceband_bounds(ds['iceband_bnds'])
    if 'bnds' in ds.dimensions:
        dim = ds.dimensions['bnds']
        if len(dim) != 2 or dim.isunlimited():
            raise ValueError('bnds must be a fixed dimension of size 2')

    # Reuse the sole legacy size-two dimension by renaming it. Creating bnds first
    # would leave C068 with two dimension identities to merge and force a rewrite.
    if 'bnds' not in ds.dimensions:
        legacy = [name for name in ('axis_nbounds', 'nvertex')
                  if name in ds.dimensions and len(ds.dimensions[name]) == 2
                  and not ds.dimensions[name].isunlimited()]
        if len(legacy) == 1 and legacy[0] not in ds.variables:
            normalize_bounds_dimension(ds)
        else:
            ds.createDimension('bnds', 2)

    # The axis uses each upper limit. The associated interval begins at the
    # previous limit, starting at zero, with no change to the five science slices.
    if not has_bounds:
        ds.createVariable('iceband_bnds', 'f8', ('iceband', 'bnds'))[:] = ICEBAND_BOUNDS
    if not axis_ok:
        coord[:] = ICEBAND_VALUES
    if not reference:
        coord.setncattr('bounds', 'iceband_bnds')
    return not (axis_ok and has_bounds and reference)


def _time_bounds(ds, name):
    time, bounds = ds['time'], ds[name]
    if time.dimensions != ('time',) or bounds.dimensions[0:1] != ('time',) or bounds.shape != (time.size, 2):
        raise ValueError('Unexpected time bounds dimensions')
    for attr in ['units', 'calendar']:
        if attr in bounds.ncattrs() and bounds.getncattr(attr) != getattr(time, attr, None):
            raise ValueError(f'Time and bounds disagree on {attr}')
    return time, bounds


@fix('detach_instantaneous_time_bounds', verify_data=True)
def detach_instantaneous_time_bounds(ds) -> bool:
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    main = ds[getattr(ds, 'variable_id')]
    if getattr(ds, 'temporal_label', '') != 'tpt' or not re.search(r'\btime:\s*point\b', getattr(main, 'cell_methods', '')):
        raise ValueError('Degenerate time bounds correction requires tpt and time: point')
    time = ds['time']
    name = getattr(time, 'bounds', '')
    attached = bool(name)
    if not name:
        candidates = [n for n in ('time_bounds', 'time_bnds') if n in ds.variables]
        if not candidates:
            return False
        if len(candidates) != 1:
            raise ValueError('Ambiguous orphan instantaneous time bounds')
        name = candidates[0]
    if getattr(time, 'climatology', '') or name not in ['time_bounds', 'time_bnds'] or name not in ds.variables:
        raise ValueError('Unexpected time bounds reference')
    time, bounds = _time_bounds(ds, name)
    if not time.size:
        raise ValueError('Cannot establish degenerate bounds on an empty time axis')

    # 2. Check every existing pair is exactly [time,time] before removing its reference.
    for slab in data_blocks(time, 'Vérification des bornes instantanées'):
        t, b = np.ma.asarray(time[slab]), np.ma.asarray(bounds[slab])
        if np.ma.getmaskarray(t).any() or np.ma.getmaskarray(b).any() or not np.isfinite(t).all() or not np.isfinite(b).all():
            raise ValueError('Invalid time or bounds values')
        if not np.array_equal(b[:, 0], t) or not np.array_equal(b[:, 1], t):
            raise ValueError('Time bounds are not all exactly [time, time]')

    # 3. Physically omit the degenerate variable. Accept the old and canonical
    # names, including orphan arrays retained by previous tool versions.
    if attached:
        time.delncattr('bounds')
    from .reviewed_structures import delete_variables
    delete_variables(ds, [name])
    return True


@fix('add_registry_bounds', verify_data=True)
def add_registry_bounds(ds, coordinate: str) -> bool:
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    spec = _specs()['bounds'].get(coordinate)
    if spec is None or coordinate not in ds.variables:
        raise ValueError('No reviewed coordinate bounds definition')
    v = ds[coordinate]
    centers, edges = np.asarray(spec['centers']), np.asarray(spec['edges'])
    if v.dimensions != (coordinate,) or v.shape != centers.shape or getattr(v, 'units', '') != spec['units']:
        raise ValueError('Coordinate shape/units disagree with its reviewed definition')
    if not np.allclose(_numeric(v), centers, rtol=0, atol=spec['tolerance']):
        raise ValueError('Coordinate centers disagree with the registry; bounds were not inferred')

    # 2. Build pairs only from the explicit approved DR edges, never from midpoints.
    wanted = np.column_stack([edges[:-1], edges[1:]])
    current = getattr(v, 'bounds', '')
    name = current or f'{coordinate}_bnds'
    changed = False
    if name in ds.variables:
        b = ds[name]
        if b.dimensions[0:1] != (coordinate,) or b.shape != wanted.shape or not np.array_equal(_numeric(b), wanted):
            raise ValueError(f'Existing {name} conflicts with the reviewed bounds')
    elif current:
        raise ValueError('Existing bounds attribute points to an absent variable')
    else:
        if 'bnds' in ds.dimensions and (len(ds.dimensions['bnds']) != 2 or ds.dimensions['bnds'].isunlimited()):
            raise ValueError('Existing bnds dimension must be fixed with size 2')
        if 'bnds' not in ds.dimensions:
            ds.createDimension('bnds', 2)
        b = ds.createVariable(name, 'f8', (coordinate, 'bnds'))
        b[...] = wanted
        changed = True
    if not current:
        v.setncattr('bounds', name)
        changed = True
    return changed


def _histogram_values(ds, coordinate):
    if coordinate not in ['dbze', 'scatratio'] or coordinate not in ds.variables:
        raise ValueError('No approved DR histogram correction for this coordinate')
    spec = _specs()['bounds'][coordinate]
    v = ds[coordinate]
    if v.dimensions != (coordinate,) or v.shape != (15,) or v.dtype != np.dtype('f8'):
        raise ValueError('DR histogram correction requires 15 ordered double-precision centers')
    if getattr(v, 'units', '') != spec['units'] or any(a in v.ncattrs() for a in ['scale_factor', 'add_offset']):
        raise ValueError('Histogram units or packing differ from the approved case')
    main = ds.variables.get(getattr(ds, 'variable_id', ''))
    if main is None or coordinate not in main.dimensions:
        raise ValueError('Histogram coordinate must index the declared data variable')
    current, wanted = _numeric(v), np.asarray(spec['centers'])
    if np.any(np.diff(current) <= 0):
        raise ValueError('Histogram classes are not strictly increasing; no sorting/rebinning is authorized')
    # The two reported errors concern endpoints. Check every intermediate class in its original order,
    # using the requested-value tolerances printed by the checker (0.001*abs(value), capped by spacing).
    spacing = np.abs(np.diff(wanted))
    tolerance = np.minimum(.001 * np.abs(wanted), np.r_[spacing[0], spacing])
    if np.any(np.abs(current[1:-1] - wanted[1:-1]) > tolerance[1:-1]):
        raise ValueError('Intermediate histogram classes differ from the reviewed DR ordering')
    return v, wanted, spec


def _verify_histogram(source, output, coordinate):
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        old, _, _ = _histogram_values(src, coordinate)
        v, wanted, spec = _histogram_values(dst, coordinate)
        if not np.array_equal(_numeric(v), wanted) or old.dimensions != v.dimensions or old.dtype != v.dtype:
            raise ValueError('DR histogram centers did not survive writing')
        name = getattr(v, 'bounds', '')
        if name not in dst.variables:
            raise ValueError('DR histogram bounds reference is absent')
        b = dst[name]
        edges = spec['edges']
        if b.dimensions[0:1] != (coordinate,) or b.shape != (15, 2) or not np.array_equal(
                _numeric(b), np.column_stack([edges[:-1], edges[1:]])):
            raise ValueError('DR histogram bounds did not survive writing')
        if 'units' in b.ncattrs() and b.units != spec['units']:
            raise ValueError('Histogram bounds have conflicting units')


@fix('fix_dr_histogram', verify_data=True, data_targets=lambda p: {p['coordinate']},
     verify_final=_verify_histogram)
def fix_dr_histogram(ds, coordinate: str) -> bool:
    """Apply the user-approved DR centers and edges together; keep histogram counts/order intact."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v, wanted, _ = _histogram_values(ds, coordinate)
    changed = not np.array_equal(_numeric(v), wanted)

    # 2. Update approved centers and edges together, preserving every histogram payload.
    if changed:
        v[...] = wanted
    return add_registry_bounds(ds, coordinate) or changed


def _finish_climatology(source, output):
    # netCDF-4 can resurrect the old bounds attribute after a variable rename and close.
    with netCDF4.Dataset(output, 'a') as ds:
        time = ds['time']
        if getattr(time, 'climatology', '') != 'climatology_bnds' or 'climatology_bnds' not in ds.variables:
            raise ValueError('Climatology reference did not survive writing')
        if 'bounds' in time.ncattrs():
            if time.getncattr('bounds') not in ['time_bounds', 'time_bnds', 'climatology_bnds']:
                raise ValueError('Unexpected resurrected time bounds reference')
            time.delncattr('bounds')


def _verify_climatology(source, output):
    with netCDF4.Dataset(output) as ds:
        if 'bounds' in ds['time'].ncattrs() or getattr(ds['time'], 'climatology', '') != 'climatology_bnds':
            raise ValueError('Climatology metadata was changed by another fix')


@fix('fix_climatology_reference', verify_data=True, after_close=_finish_climatology,
     verify_final=_verify_climatology)
def fix_climatology_reference(ds, _metadata_only=False) -> bool:
    if (not _metadata_only and getattr(ds, 'temporal_label', '') == 'tclm'
            and getattr(ds, 'frequency', '') == 'mon'):
        # C065/C066 must not merely label an unaggregated monthly series as a
        # climatology. Share C106's numerical reconstruction for monthly tclm.
        from .climatology import rebuild_monthly_climatology
        return rebuild_monthly_climatology(ds)
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    main = ds[getattr(ds, 'variable_id')]
    label = getattr(ds, 'temporal_label', '')
    methods = getattr(main, 'cell_methods', '')
    if label not in ['tclm', 'tmaxavg', 'tminavg'] or not re.search(r'\bwithin (?:years|days)\b', methods) or not re.search(r'\bover (?:years|days)\b', methods):
        raise ValueError('Unreviewed climatological time semantics')
    time = ds['time']
    old = getattr(time, 'bounds', '')
    clim = getattr(time, 'climatology', '')
    if clim and old and clim != old:
        raise ValueError('Conflicting bounds and climatology references')
    name = clim or old
    if name not in ['time_bounds', 'time_bnds', 'climatology_bnds'] or name not in ds.variables:
        raise ValueError('Cannot identify existing climatology bounds')
    _, bounds = _time_bounds(ds, name)
    if not time.size:
        raise ValueError('Cannot establish climatology intervals on an empty time axis')

    # 2. Validate all existing intervals; no climatological statistics are recomputed.
    for slab in data_blocks(time, 'Vérification des intervalles climatologiques'):
        t, b = np.ma.asarray(time[slab]), np.ma.asarray(bounds[slab])
        if np.ma.getmaskarray(t).any() or np.ma.getmaskarray(b).any() or not np.isfinite(t).all() or not np.isfinite(b).all() or np.any(b[:, 1] <= b[:, 0]) or np.any(t < b[:, 0]) or np.any(t > b[:, 1]):
            raise ValueError('Invalid existing climatology intervals')

    # 3. Normalize names/references; the after-close hook removes resurrected attributes.
    changed = False
    if name != 'climatology_bnds':
        if 'climatology_bnds' in ds.variables:
            raise ValueError('Climatology bounds name collision')
        ds.renameVariable(name, 'climatology_bnds')
        _replace_references(ds, name, 'climatology_bnds')
        changed = True
    if getattr(time, 'climatology', '') != 'climatology_bnds':
        time.setncattr('climatology', 'climatology_bnds')
        changed = True
    if 'bounds' in time.ncattrs():
        time.delncattr('bounds')
        changed = True
    return changed


@fix('rename_bounds_variable', verify_data=True)
def rename_bounds_variable(ds, coordinate: str, source: str, target: str) -> bool:
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    allowed = {'lat': {'bounds_lat', 'lat_bounds'}, 'lon': {'bounds_lon'},
               'nav_lat': {'bounds_nav_lat'}, 'nav_lon': {'bounds_nav_lon'},
               'latitude': {'bounds_nav_lat'}, 'longitude': {'bounds_nav_lon'}, 'time': {'time_bounds'},
               'depth': {'depth_bounds'}, 'plev': {'plev_bounds'}, 'lev': {'olevel_bounds'}}
    targets = {'lat': 'lat_bnds', 'lon': 'lon_bnds', 'nav_lat': 'vertices_latitude',
               'nav_lon': 'vertices_longitude', 'latitude': 'vertices_latitude',
               'longitude': 'vertices_longitude', 'time': 'time_bnds',
               'depth': 'depth_bnds', 'plev': 'plev_bnds', 'lev': 'lev_bnds'}
    if source not in allowed.get(coordinate, set()) or targets.get(coordinate) != target:
        raise ValueError('Unreviewed bounds variable rename')
    current_coordinate = coordinate
    aliases = {'nav_lat': ('nav_lat', 'latitude'), 'latitude': ('nav_lat', 'latitude'),
               'nav_lon': ('nav_lon', 'longitude'), 'longitude': ('nav_lon', 'longitude')}
    if coordinate in aliases:
        present = [n for n in aliases[coordinate] if n in ds.variables]
        if len(present) != 1:
            raise ValueError('Missing or ambiguous horizontal coordinate for bounds rename')
        current_coordinate = present[0]
    if current_coordinate not in ds.variables:
        raise ValueError('Coordinate is absent')
    ref = getattr(ds[current_coordinate], 'bounds', '')
    if coordinate == 'time' and not ref and (getattr(ds['time'], 'climatology', '') or getattr(ds, 'temporal_label', '') == 'tpt'):
        return False  # A separate reviewed fix detached point bounds or established climatology.
    if ref == target and target in ds.variables and source not in ds.variables:
        return False
    if ref != source or source not in ds.variables or target in ds.variables:
        raise ValueError('Bounds reference differs or target name already exists')

    # 2. Rename only the reviewed bounds variable and update CF references.
    ds.renameVariable(source, target)
    _replace_references(ds, source, target)
    return True


def _dimension_map(ds):
    if 'bnds' in ds.dimensions and (len(ds.dimensions['bnds']) != 2 or ds.dimensions['bnds'].isunlimited()):
        raise ValueError('bnds must be a fixed dimension of size 2')
    result = {d: 'bnds' for d in ['nvertex', 'axis_nbounds'] if d in ds.dimensions and len(ds.dimensions[d]) == 2 and not ds.dimensions[d].isunlimited()}
    if any(d in ds.variables for d in result):
        raise ValueError('Cannot merge a coordinate axis as a bounds index')
    if not result and 'bnds' not in ds.dimensions:
        raise ValueError('No reviewed size-two bounds dimension is present')
    return result


_LOCAL_BOUND_PARENTS = {
    'bounds_lat': 'lat', 'lat_bounds': 'lat', 'lat_bnds': 'lat',
    'bounds_lon': 'lon', 'lon_bnds': 'lon',
    'time_bounds': 'time', 'time_bnds': 'time', 'climatology_bnds': 'time',
}


def _local_bound_replacements(ds, mapping):
    """Use small replacements when only known bounds use the legacy dimension IDs."""
    source = getattr(ds, '_source', ds)
    if source.data_model not in ('NETCDF4', 'NETCDF4_CLASSIC') or 'bnds' not in ds.variables:
        return None
    if ds['bnds'].dimensions != ('bnds',):
        return None
    candidates = []
    active_bounds = {value for v in ds.variables.values() for attr in ('bounds', 'climatology')
                     if isinstance(value := getattr(v, attr, ''), str)}
    for name, v in ds.variables.items():
        if not mapping.keys() & set(v.dimensions):
            continue
        if name.startswith(ORIGINAL_PREFIX) and name.removeprefix(ORIGINAL_PREFIX) in _LOCAL_BOUND_PARENTS:
            if name in active_bounds:
                return None
            continue
        parent_name = _LOCAL_BOUND_PARENTS.get(name)
        parent = ds.variables.get(parent_name) if parent_name else None
        if (parent is None or v.dimensions[-1] not in mapping
                or v.dimensions[:-1] != parent.dimensions
                or v.dtype.kind not in 'fiu' or v.size * v.dtype.itemsize > 1024 * 1024):
            return None
        refs = [getattr(parent, attr, '') for attr in ('bounds', 'climatology')]
        if any(not isinstance(ref, str) for ref in refs):
            return None
        if name in refs:
            candidates.append(name)
        elif parent_name != 'time' or getattr(ds, 'temporal_label', '') != 'tpt' or any(refs):
            return None
        # C060 has detached degenerate time bounds. Keep the now-orphaned array.
    return candidates


def _merge_dimensions(source, output):
    with netCDF4.Dataset(output) as ds:
        mapping = _dimension_map(ds)
    if not mapping:
        return
    fd, temporary = tempfile.mkstemp(prefix='.bounds-', suffix='.fixtmp', dir=Path(output).parent)
    os.close(fd)
    try:
        _python_rewrite(output, temporary, dimension_map=mapping)
        _verify_rewrite(output, temporary, dimension_map=mapping)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@fix('normalize_bounds_dimension', verify_data=True, after_close=_merge_dimensions)
def normalize_bounds_dimension(ds) -> bool:
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    mapping = _dimension_map(ds)

    local = _local_bound_replacements(ds, mapping)
    if local is not None:
        for name in local:
            v = ds[name]
            dimensions = (*v.dimensions[:-1], 'bnds')
            _raw(v)
            replace_auxiliary(ds, name, dimensions, np.array(v[...]))
        return bool(local)

    # 2. Update references; any required size-two merge is verified after closing the file.
    for source, target in mapping.items():
        _replace_references(ds, source, target)
    if len(mapping) == 1 and 'bnds' not in ds.dimensions:
        ds.renameDimension(next(iter(mapping)), 'bnds')
    return bool(mapping)
