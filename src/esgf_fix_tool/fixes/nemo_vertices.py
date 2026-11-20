"""Copy reviewed XIOS cell vertices; never reconstruct NEMO geometry."""

import gzip
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np

from . import fix
from .nemo_coordinates import ALIASES, _scope, reference as center_reference

SHAPE = (331, 360)
DONORS = {'U': ('hfx', 'g168'), 'V': ('hfy', 'g169'), 'T': ('areacello', 'g112')}
NAMES = ('vertices_latitude', 'vertices_longitude')
EXCEPTION_CELLS = tuple((330, x) for x in range(196, 210))
TOLERANCE = 1e-4


def _array(spec, shape, dtype):
    data = np.asarray(spec['values'], dtype=dtype)
    if (data.shape != shape or spec['dtype'] != np.dtype(dtype).str
            or not np.isfinite(data).all()
            or hashlib.sha256(data.tobytes()).hexdigest() != spec['sha256']):
        raise ValueError('Invalid bundled NEMO vertex-reference array')
    data.setflags(write=False)
    return data


def _encode(data):
    data = np.asarray(data, dtype='<f8')
    return dict(dtype=data.dtype.str, sha256=hashlib.sha256(data.tobytes()).hexdigest(), values=data.tolist())


def _coordinates(ds):
    arrays, names, valid = {}, {}, np.ones(SHAPE, dtype=bool)
    for key, aliases in ALIASES.items():
        present = [n for n in aliases if n in ds.variables]
        if len(present) != 1:
            raise ValueError('Missing or ambiguous geographic coordinate')
        name = present[0]
        var = ds[name]
        if (var.dimensions != ('y', 'x') or var.dtype != np.dtype('f8')
                or any(a in var.ncattrs() for a in ('scale_factor', 'add_offset'))):
            raise ValueError('Expected unpacked double geographic coordinates on y,x')
        values = np.ma.asarray(var[:])
        if values.shape != SHAPE:
            raise ValueError('Unexpected geographic coordinate shape')
        valid &= ~np.ma.getmaskarray(values) & np.isfinite(values.data)
        arrays[key] = np.asarray(values.filled(0), dtype='<f8')
        names[key] = name
    valid &= ~((arrays['nav_lat'] == 0) & (arrays['nav_lon'] == 0))
    return names, arrays, valid


def _all_missing(var):
    """Read only the 14 reviewed cells across all records; restore read settings."""
    source = getattr(var, '_source', None)
    source = var if source is None else source
    if source.dimensions != ('time', 'y', 'x') or source.shape[0] == 0:
        raise ValueError('Cannot verify the northern exception without populated time records')
    mask, scale = source.mask, source.scale
    try:
        source.set_auto_maskandscale(True)
        for t in range(source.shape[0]):
            values = np.ma.asarray(source[t, 330, 196:210])
            if not np.ma.getmaskarray(values).all():
                raise ValueError(f'Northern vertex exception has valid scientific data at time index {t}')
    finally:
        source.set_auto_mask(mask)
        source.set_auto_scale(scale)


def extract_reference(sources, output):
    """Export the three supplied donors once, before parallel application starts."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if set(sources) != set(DONORS):
        raise ValueError('Supply exactly the U=hfx, V=hfy and T=areacello donor files')
    profiles = {}
    for grid, (main, label) in DONORS.items():
        path = Path(sources[grid])
        before = path.stat()
        with netCDF4.Dataset(path) as ds:
            brand = main + ('_ti-u-hxy-u' if grid == 'T' else '_tavg-u-hxy-sea')
            if (getattr(ds, 'variable_id', '') != main
                    or getattr(ds, 'branded_variable', '') != brand
                    or getattr(ds, 'grid_label', '') != label
                    or getattr(ds, 'horizontal_label', '') != 'hxy'
                    or main not in ds.variables):
                raise ValueError(f'Unexpected {grid} donor identity: {path.name}')
            _, centers, valid = _coordinates(ds)
            if not valid.any():
                raise ValueError('Reference donor has no usable geographic centers')
            arrays = {}
            for name in NAMES:
                var = ds.variables.get(name)
                if (var is None or var.dimensions != ('y', 'x', 'vertices')
                        or var.shape != SHAPE + (4,) or var.dtype != np.dtype('f8')
                        or var.ncattrs()):
                    raise ValueError('Expected the reviewed double XIOS vertices without variable attributes')
                values = np.ma.asarray(var[:])
                if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                    raise ValueError('Reference vertices contain missing or non-finite values')
                arrays[name] = _encode(values)
            exception = []
            if grid == 'V':
                _all_missing(ds[main])
                exception = [list(cell) for cell in EXCEPTION_CELLS]
            profiles[grid] = dict(
                source_file=path.name, source_variable=main, grid_label=label,
                centers={name: _encode(values) for name, values in centers.items()},
                valid_centers=valid.astype('u1').tolist(), vertices=arrays,
                masked_source_exception_cells=exception,
            )
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Reference donor changed during extraction')
    document = dict(schema_version=1, shape=list(SHAPE), vertex_count=4, profiles=profiles)
    # Gzip keeps the exact JSON doubles portable without shipping the donor matrices.
    # Exclusive creation protects an existing reference from accidental overwrite.
    encoded = json.dumps(document, allow_nan=False, separators=(',', ':')).encode('utf-8')
    with output.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as stream:
            stream.write(encoded)
    return output


@lru_cache(maxsize=1)
def reference():
    path = Path(__file__).with_name('nemo_vertices_reference.json.gz')
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        document = json.load(stream)
    if (document['schema_version'] != 1 or document['shape'] != list(SHAPE)
            or document['vertex_count'] != 4 or set(document['profiles']) != set(DONORS)):
        raise ValueError('Unexpected bundled NEMO vertex-reference schema')
    result = {}
    for grid, (main, label) in DONORS.items():
        profile = document['profiles'][grid]
        if profile['source_variable'] != main or profile['grid_label'] != label:
            raise ValueError('Unexpected bundled vertex donor identity')
        centers = {name: _array(profile['centers'][name], SHAPE, '<f8') for name in ALIASES}
        vertices = {name: _array(profile['vertices'][name], SHAPE + (4,), '<f8') for name in NAMES}
        valid = np.asarray(profile['valid_centers'])
        if valid.shape != SHAPE or not np.isin(valid, [0, 1]).all():
            raise ValueError('Invalid vertex-reference comparison mask')
        valid = valid.astype(bool)
        expected = ~((centers['nav_lat'] == 0) & (centers['nav_lon'] == 0))
        if not np.array_equal(valid, expected):
            raise ValueError('Reference center mask differs from its geographic coordinates')
        exception = [list(cell) for cell in EXCEPTION_CELLS] if grid == 'V' else []
        if profile['masked_source_exception_cells'] != exception:
            raise ValueError('Unreviewed masked vertex exception')
        result[grid] = centers, vertices, valid
    return result


def _validate(ds):
    main, tokens, grid = _scope(ds)
    names, centers, valid = _coordinates(ds)
    source, vertices, compared = reference()[grid]
    if not valid[compared].all():
        raise ValueError('Target omits geographic centers present in the donor')
    dlat = np.abs(centers['nav_lat'] - source['nav_lat'])
    dlon = np.abs((centers['nav_lon'] - source['nav_lon'] + 180) % 360 - 180)
    different = compared & ((dlat > TOLERANCE) | (dlon > TOLERANCE))
    if different.any():
        allowed = np.zeros(SHAPE, dtype=bool)
        if grid == 'V':
            allowed[330, 196:210] = True
        if (different & ~allowed).any() or (dlon[different] > TOLERANCE).any():
            raise ValueError('Target geographic centers differ from the reviewed vertex donor')
        # The exception permits only the original domain V coordinates on these
        # exact cells, never arbitrary latitude differences on masked cells.
        native = center_reference('V')
        for key in ALIASES:
            if not np.array_equal(centers[key][different], native[key][0][different]):
                raise ValueError('Northern exception differs from the reviewed native V centers')
        _all_missing(main)
    for key in ALIASES:
        name = names[key]
        if name not in tokens:
            raise ValueError('Geographic coordinate is not associated with the scientific field')
        role = 'latitude' if key == 'nav_lat' else 'longitude'
        coordinate = ds[name]
        if (getattr(coordinate, 'standard_name', '') != role
                or getattr(coordinate, 'units', '') != ('degrees_north' if key == 'nav_lat' else 'degrees_east')
                or getattr(coordinate, 'bounds', '') not in ('', 'vertices_'+role)):
            raise ValueError('Unexpected geographic coordinate metadata or bounds reference')
    if 'vertices' in ds.dimensions and (len(ds.dimensions['vertices']) != 4 or ds.dimensions['vertices'].isunlimited()):
        raise ValueError('Expected fixed vertices=4')
    for name in NAMES:
        if name in ds.variables:
            var = ds[name]
            values = np.ma.asarray(var[:])
            if (var.dimensions != ('y', 'x', 'vertices') or var.dtype != np.dtype('f8')
                    or any(a in var.ncattrs() for a in ('scale_factor', 'add_offset'))
                    or np.ma.getmaskarray(values).any()
                    or not np.array_equal(values, vertices[name])):
                raise ValueError('Existing vertices differ from the approved XIOS donor')
    return names, vertices


def _verify(source, output):
    with netCDF4.Dataset(output) as ds:
        names, _ = _validate(ds)
        for key, name in names.items():
            wanted = 'vertices_latitude' if key == 'nav_lat' else 'vertices_longitude'
            if getattr(ds[name], 'bounds', '') != wanted or wanted not in ds.variables:
                raise ValueError('Injected XIOS vertices or bounds references are absent')
    # Numeric preservation and every other planned transformation are also
    # checked by the shared writer before it replaces a structural output.


@fix('inject_nemo_vertices', verify_data=True, verify_final=_verify)
def inject_nemo_vertices(ds):
    """Append existing XIOS vertices and bounds links; preserve every center/science value."""
    names, vertices = _validate(ds)
    changed = False
    if 'vertices' not in ds.dimensions:
        ds.createDimension('vertices', 4)
        changed = True
    for name, values in vertices.items():
        if name not in ds.variables:
            ds.createVariable(name, 'f8', ('y', 'x', 'vertices'), fill_value=False)[:] = values
            changed = True
    for key, name in names.items():
        wanted = 'vertices_latitude' if key == 'nav_lat' else 'vertices_longitude'
        if getattr(ds[name], 'bounds', '') != wanted:
            ds[name].setncattr('bounds', wanted)
            changed = True
    return changed
