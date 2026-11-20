"""Reference-based recovery of reviewed empty southern NEMO profile rows."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..progress import activity


@lru_cache(maxsize=1)
def reference_331():
    """Use the exact supplied T-column 97 profile, never the older 332-point axis."""
    raw = json.loads(Path(__file__).with_name('msfty_latitude_reference_331.json').read_text())
    values = np.asarray(raw['values'], dtype='<f8')
    if (raw['schema_version'] != 1 or raw['source_variable'] != 'gphit'
            or raw['source_index'] != {'t': 0, 'x': 97}
            or raw['shape'] != [331] or raw['empty_prefix_length'] != 32
            or raw['grid_label'] != 'g112'
            or raw['branded_variable'] != 'msfty_tavg-ol-ht-sea'
            or values.shape != (331,) or not np.isfinite(values).all()
            or not np.all(np.diff(values) > 0) or np.any(np.abs(values) > 90)
            or hashlib.sha256(values.tobytes()).hexdigest() != raw['sha256']):
        raise ValueError('Invalid bundled 331-point msfty latitude reference')
    values.setflags(write=False)
    return values


def _require_empty_rows(ds, count, var="msfty"):
    """Check all times/basins/depths in bounded reads, preserving read settings."""
    main = ds[var]
    source = getattr(main, '_source', None)
    source = main if source is None else source
    # The coordinate recipe has already checked the ordered msfty dimensions.
    # An optional trailing singleton x can still await the shared layout writer.
    row_axis = 3 if var == "msfty" else 2
    if source.shape[0] == 0 or source.shape[row_axis] != 331:
        raise ValueError(f'Cannot validate empty {var} rows without populated time records')
    mask, scale = source.mask, source.scale
    try:
        source.set_auto_maskandscale(True)
        with activity(f'Vérification des 32 lignes {var} manquantes sur tous les pas de temps'):
            for t in range(source.shape[0]):
                selection = [slice(None)] * source.ndim
                selection[0] = slice(t, t+1)
                selection[row_axis] = slice(0, count)
                values = np.ma.asarray(source[tuple(selection)])
                if not np.ma.getmaskarray(values).all():
                    raise ValueError(
                        f'{var} contains valid data in the 32 zero-latitude rows at time index {t}; '
                        'no automatic latitude replacement is authorized'
                    )
    finally:
        source.set_auto_mask(mask)
        source.set_auto_scale(scale)


def restore_empty_prefix(ds, values, var="msfty"):
    """Return a repaired coordinate only for the exact reviewed 331/32-zero case."""
    if values.shape != (331,) or not np.all(values[:32] == 0):
        return None
    from .reviewed_structures import BASIN_PRODUCTS

    if var not in BASIN_PRODUCTS:
        raise ValueError('Unreviewed NEMO profile product')
    brand = var + ('_tavg-ol-ht-sea' if var == 'msfty' else '_tavg-u-hyb-sea')
    if (getattr(ds, 'variable_id', '') != var
            or getattr(ds, 'branded_variable', '') != brand
            or getattr(ds, 'grid_label', '') != 'g112'):
        raise ValueError('Zero-prefix recovery is restricted to the reviewed g112 profiles')
    reference = reference_331()
    # Check every retained latitude. A different model grid must not borrow this
    # profile merely because its dimension also has 331 positions.
    if not np.array_equal(values[32:], reference[32:]):
        raise ValueError(f'The 299 retained {var} latitudes differ from the supplied T-column 97 reference')
    _require_empty_rows(ds, 32, var)
    result = values.copy()
    result[:32] = reference[:32]
    if not np.all(np.diff(result) > 0):
        raise ValueError('Reference recovery did not produce increasing msfty latitudes')
    return result
