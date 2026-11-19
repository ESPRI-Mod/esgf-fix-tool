#!/usr/bin/env python3
"""Extract approved NEMO U/V/T geographic coordinates into the portable reference."""

import argparse
import hashlib
import json
from pathlib import Path

import netCDF4
import numpy as np


def extract(source, output):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError('Use a new JSON output path, distinct from the source')
    profiles = {}
    with netCDF4.Dataset(source) as ds:
        if {n: len(ds.dimensions[n]) for n in ('t', 'y', 'x')} != {'t': 1, 'y': 331, 'x': 360}:
            raise ValueError('Expected the supplied eORCA domain with t=1, y=331, x=360')
        for grid, latitude, longitude, label in (
            ('U', 'gphiu', 'glamu', 'g168'),
            ('V', 'gphiv', 'glamv', 'g169'),
            ('T', 'gphit', 'glamt', 'g112'),
        ):
            coordinates = {}
            for name, origin, role, units in (
                ('nav_lat', latitude, 'latitude', 'degrees_north'),
                ('nav_lon', longitude, 'longitude', 'degrees_east'),
            ):
                v = ds[origin]
                values = np.ma.asarray(v[0, :, :])
                if (v.dimensions != ('t', 'y', 'x') or v.dtype != np.dtype('f8')
                        or values.shape != (331, 360) or np.ma.getmaskarray(values).any()
                        or not np.isfinite(values).all()
                        or any(a in v.ncattrs() for a in ('scale_factor', 'add_offset'))):
                    raise ValueError(f'Unexpected source coordinate: {origin}')
                values = np.asarray(values, dtype='<f8')
                coordinates[name] = dict(
                    source_variable=origin, source_record=0,
                    dimensions=['y', 'x'], dtype='float64',
                    sha256=hashlib.sha256(values.tobytes(order='C')).hexdigest(),
                    attributes=dict(standard_name=role, units=units,
                                    long_name=role if grid == 'T' else role.title()+' at NEMO '+grid+' points'),
                    values=values.tolist(),
                )
            profile = dict(grid='NEMO '+grid, grid_label=label,
                           dimensions=dict(y=331, x=360), coordinates=coordinates)
            if grid == 'T':
                profile['branded_variables'] = ['sob_tavg-u-hxy-sea', 'tob_tavg-u-hxy-sea']
            else:
                profile['branded_variable'] = ('sfx' if grid == 'U' else 'sfy')+'_tavg-u-hxy-sea'
            profiles[grid] = profile
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    # Keep the U root schema for existing plans/tests; append the independently
    # sourced V and T grids in the same JSON instead of copying the heavy NetCDF.
    document = dict(schema_version=1, source_file=source.name,
                    source_sha256=digest.hexdigest(), **profiles['U'],
                    additional_grids={grid: profiles[grid] for grid in ('V', 'T')})
    rendered = json.loads(json.dumps(document))
    for grid, profile in profiles.items():
        target = rendered if grid == 'U' else rendered['additional_grids'][grid]
        for name in profile['coordinates']:
            target['coordinates'][name]['values'] = '__'+grid+'_'+name+'_rows__'
    text = json.dumps(rendered, ensure_ascii=False, allow_nan=False, indent=2)
    for grid, profile in profiles.items():
        for name, spec in profile['coordinates'].items():
            rows = ',\n        '.join(json.dumps(row, allow_nan=False, separators=(',', ':')) for row in spec['values'])
            text = text.replace(json.dumps('__'+grid+'_'+name+'_rows__'), '[\n        '+rows+'\n      ]')
    text += '\n'
    restored = json.loads(text)
    for profile in (restored, *restored['additional_grids'].values()):
        for name, spec in profile['coordinates'].items():
            values = np.asarray(spec['values'], dtype='<f8')
            assert hashlib.sha256(values.tobytes()).hexdigest() == spec['sha256']
    output.write_text(text)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(extract(args.source, args.output))
