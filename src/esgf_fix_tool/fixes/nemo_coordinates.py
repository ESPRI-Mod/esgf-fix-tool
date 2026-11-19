"""Inject explicitly sourced NEMO U/V/T coordinates for reviewed ocean products."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np

from . import fix

# Keep the registry name for compatibility with existing C119 plans.
PRODUCTS = {
    'sfx': ('U', 'g168', 'ocean_salt_x_transport'),
    'sfy': ('V', 'g169', 'ocean_salt_y_transport'),
    'sob': ('T', 'g112', 'sea_water_salinity_at_sea_floor'),
    'tob': ('T', 'g112', 'sea_water_potential_temperature_at_sea_floor'),
}
ORIGINS = {'U': ('gphiu', 'glamu'), 'V': ('gphiv', 'glamv'), 'T': ('gphit', 'glamt')}
CANONICAL = {'nav_lat': 'latitude', 'nav_lon': 'longitude'}
ALIASES = {'nav_lat': ('nav_lat', 'latitude'), 'nav_lon': ('nav_lon', 'longitude')}


@lru_cache(maxsize=3)
def reference(grid="U"):
    """Load portable JSON only; never require the large local domain NetCDF."""
    if grid not in ORIGINS:
        raise ValueError('Unreviewed NEMO reference grid')
    root = json.loads(Path(__file__).with_name('nemo_u_coordinates.json').read_text())
    document = root if grid == 'U' else root.get('additional_grids', {}).get(grid, {})
    expected_label = {'U': 'g168', 'V': 'g169', 'T': 'g112'}[grid]
    brands = (document.get('branded_variables') if grid == 'T'
              else [document.get('branded_variable')])
    expected_brands = [name+'_tavg-u-hxy-sea' for name, spec in PRODUCTS.items() if spec[0] == grid]
    if (root['schema_version'] != 1 or document.get('grid') != 'NEMO '+grid
            or document.get('grid_label') != expected_label
            or brands != expected_brands
            or document.get('dimensions') != {'y': 331, 'x': 360}
            or set(document.get('coordinates', {})) != set(ALIASES)):
        raise ValueError('Unexpected bundled NEMO '+grid+' reference')
    result = {}
    for name, origin, role, units in [
        ('nav_lat', ORIGINS[grid][0], 'latitude', 'degrees_north'),
        ('nav_lon', ORIGINS[grid][1], 'longitude', 'degrees_east'),
    ]:
        spec = document['coordinates'][name]
        values = np.asarray(spec['values'], dtype='<f8')
        attrs = spec['attributes']
        if (spec['source_variable'] != origin or spec['source_record'] != 0
                or spec['dimensions'] != ['y', 'x'] or spec['dtype'] != 'float64'
                or values.shape != (331, 360) or not np.isfinite(values).all()
                or attrs['standard_name'] != role or attrs['units'] != units
                or hashlib.sha256(values.tobytes()).hexdigest() != spec['sha256']):
            raise ValueError(f'Invalid bundled {grid}-point coordinate: {name}')
        if name == 'nav_lat' and (values.min() < -90 or values.max() > 90):
            raise ValueError('Invalid latitude range in NEMO '+grid+' reference')
        values.setflags(write=False)
        result[name] = (values, attrs)
    return result


def _scope(ds):
    """Check geometry and scientific identity using headers only."""
    name = getattr(ds, 'variable_id', '')
    if name not in PRODUCTS:
        raise ValueError('Unreviewed product for NEMO coordinate injection')
    grid, label, standard_name = PRODUCTS[name]
    main = ds.variables.get(name)
    if (getattr(ds, 'branded_variable', '') != name+'_tavg-u-hxy-sea'
            or getattr(ds, 'grid_label', '') != label
            or getattr(ds, 'horizontal_label', '') != 'hxy'
            or main is None or main.dimensions != ('time', 'y', 'x')
            or main.shape[-2:] != (331, 360)
            or getattr(main, 'standard_name', '') != standard_name
            or any(ds.dimensions[n].isunlimited() for n in ('y', 'x'))):
        raise ValueError(f'Expected the reviewed {name} {label} {grid} grid on y=331, x=360')
    text = getattr(main, 'coordinates', '')
    tokens = text.split() if isinstance(text, str) else []
    if len(tokens) != 2 or any(sum(t in names for t in tokens) != 1 for names in ALIASES.values()):
        raise ValueError(f'{name} must reference one latitude and one longitude auxiliary')
    return main, tokens, grid


def _existing(ds, name, values, attrs):
    """Never overwrite an existing coordinate belonging to another grid."""
    v = ds[name]
    actual = np.ma.asarray(v[:])
    if (v.dimensions != ('y', 'x') or v.dtype != np.dtype('f8')
            or np.ma.getmaskarray(actual).any()
            or not np.array_equal(actual, values)
            or any(a in v.ncattrs() for a in ('scale_factor', 'add_offset', '_Unsigned'))
            or getattr(v, 'standard_name', '') not in ('', attrs['standard_name'])
            or getattr(v, 'units', '') not in ('', attrs['units'])):
        raise ValueError(f'Existing {name} differs from the supplied NEMO coordinate reference')


def _verify(source, output):
    with netCDF4.Dataset(output) as ds:
        main, tokens, grid = _scope(ds)
        for key, (values, attrs) in reference(grid).items():
            names = [n for n in ALIASES[key] if n in ds.variables]
            if len(names) != 1 or names[0] not in tokens:
                raise ValueError('Injected coordinates are absent or not associated with '+main.name)
            name = names[0]
            if grid == 'T' and name != CANONICAL[key]:
                raise ValueError('Bottom diagnostics require canonical latitude/longitude names')
            _existing(ds, name, values, attrs)
            if (ds[name].standard_name != attrs['standard_name']
                    or ds[name].units != attrs['units']
                    or not getattr(ds[name], 'long_name', '').strip()):
                raise ValueError(f'Missing CF metadata on injected coordinate {name}')


@fix('inject_nemo_u_coordinates', verify_data=True, verify_final=_verify)
def inject_nemo_u_coordinates(ds):
    """Inject each approved grid; keep all science arrays and their storage intact."""
    main, tokens, grid = _scope(ds)
    pending = []
    mapping = {}

    # Validate both roles before creating either array. Canonical aliases from
    # earlier iterations are accepted, but conflicting/duplicate arrays are refused.
    for key, (values, attrs) in reference(grid).items():
        aliases = ALIASES[key]
        present = [n for n in aliases if n in ds.variables]
        if len(present) > 1:
            raise ValueError(f'Ambiguous NEMO coordinate aliases: {aliases}')
        requested = next(t for t in tokens if t in aliases)
        existing = present[0] if present else None
        name = CANONICAL[key] if grid == 'T' else (existing or requested)
        if name in ds.dimensions:
            raise ValueError('The injected auxiliary name must not collide with a dimension')
        if existing:
            _existing(ds, existing, values, attrs)
        pending.append((name, values, attrs, existing))
        mapping[requested] = name

    # New variables use existing y/x dimensions. The shared writer can append
    # them directly without copying or reading the scientific matrix.
    changed = False
    for name, values, attrs, existing in pending:
        if existing and existing != name:
            # Bottom coordinates are published under the canonical names in
            # this same pass; shared reference renaming retains existing data.
            from .coordinates import _replace_references

            ds.renameVariable(existing, name)
            _replace_references(ds, existing, name)
            changed = True
        if not existing:
            ds.createVariable(name, 'f8', ('y', 'x'), fill_value=False)[:] = values
            changed = True
        v = ds[name]
        for attr, value in attrs.items():
            if attr == 'long_name' and getattr(v, attr, '').strip():
                continue  # Keep an existing reviewed/CV long_name after a rename.
            if getattr(v, attr, None) != value:
                v.setncattr(attr, value)
                changed = True
    wanted = [mapping[t] for t in tokens]
    if wanted != tokens:
        main.setncattr('coordinates', ' '.join(wanted))
        changed = True
    return changed
