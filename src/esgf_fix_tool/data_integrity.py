"""Bounded-memory raw-data check for identifier changes and auxiliary-coordinate additions."""

import hashlib
import itertools
import json
import math
from collections import Counter

import numpy as np

from .progress import io_progress


def _slices(shape, itemsize, budget=8 * 1024 * 1024):
    """Visit bounded, contiguous C-order slabs, keeping inner dimensions whole when possible.

    Splitting the largest axis repeatedly creates tiles spanning many time chunks. On
    compressed 4-D files that working set can exceed the HDF5 cache, decompressing the
    same chunks for every spatial tile. Consume one time slab before moving to the next.
    The traversal also gives a storage-layout-independent, canonical hash byte order.
    """
    if not shape:
        yield ()
        return
    capacity = max(1, budget // max(itemsize, 8))
    tile = [1] * len(shape)
    for axis in reversed(range(len(shape))):
        tile[axis] = min(shape[axis], capacity)
        capacity = max(1, capacity // max(1, tile[axis]))
    for start in itertools.product(*(range(0, n, max(1, t)) for n, t in zip(shape, tile))):
        yield tuple(slice(s, min(s + t, n)) for s, t, n in zip(start, tile, shape))


def data_blocks(variable, operation):
    """Expose real block progress without changing automatic masking/packing settings."""
    dtype = np.dtype(variable.dtype)
    total = math.prod(variable.shape)
    completed = 0
    io_progress(operation, variable.name, completed, total, dtype.itemsize)
    for slab in _slices(variable.shape, dtype.itemsize):
        yield slab
        completed += math.prod(s.stop-s.start for s in slab)
        io_progress(operation, variable.name, completed, total, dtype.itemsize)


def data_fingerprints(ds, exclude=()) -> Counter:
    """Count arrays by dtype, shape and raw values, irrespective of renamed identifiers.

    Counting preserves duplicates. New auxiliaries may be added, but every pre-existing array
    must remain. Automatic packing, masking and character decoding are disabled on this handle.
    """
    out = Counter()
    for name, v in ds.variables.items():
        if name in exclude:
            continue
        v.set_auto_maskandscale(False)
        v.set_auto_chartostring(False)
        digest = hashlib.sha256()
        dtype = np.dtype(v.dtype)
        for slab in data_blocks(v, 'Empreinte brute'):
            data = np.asarray(v[slab])
            if data.dtype.kind == "O":
                values = data.ravel().tolist()
                if not all(isinstance(s, str) for s in values):
                    raise ValueError("Raw-data verification does not support non-string VLEN arrays")
                for s in values:
                    encoded = s.encode("utf-8")
                    digest.update(len(encoded).to_bytes(8, "little"))
                    digest.update(encoded)
            else:
                digest.update(data.tobytes(order="C"))
        key = (json.dumps(dtype.descr), tuple(v.shape), digest.hexdigest())
        out[key] += 1
    for group in ds.groups.values():
        out.update(data_fingerprints(group))
    return out


def verify_preserved(before, after):
    if before - after:
        raise ValueError("Existing variable data changed during coordinate correction: target not replaced")
