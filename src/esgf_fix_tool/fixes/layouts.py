"""Reviewed dimension permutations and singleton removal; no averaging or arbitrary axis deletion."""

import ctypes
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np

from ..data_integrity import data_blocks
from ..progress import activity
from . import fix
from .coordinates import _restore_coordinate_values, rename_coordinate


@lru_cache(maxsize=1)
def _attribute_type_query():
    # Query the very same netCDF C library used by this Python extension (no additional package).
    query = ctypes.CDLL(netCDF4._netCDF4.__file__).nc_inq_atttype
    query.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    query.restype = ctypes.c_int
    return query


def _attribute_type(obj, name):
    xtype = ctypes.c_int()
    varid = obj._varid if isinstance(obj, netCDF4.Variable) else -1  # NC_GLOBAL
    status = _attribute_type_query()(
        obj._grpid, varid, name.encode("utf-8"), ctypes.byref(xtype)
    )
    if status:
        raise ValueError(
            f"Cannot preserve the storage type of attribute {name}: netCDF status {status}"
        )
    return xtype.value


def _copy_attrs(source, target, skip=()):
    for name in source.ncattrs():
        if name in skip:
            continue
        if _attribute_type(source, name) == 12:  # NC_STRING, distinct from NC_CHAR
            target.setncattr_string(name, source.getncattr(name))
        else:
            target.setncattr(name, source.getncattr(name))


def _raw(v):
    v.set_auto_maskandscale(False)
    v.set_auto_chartostring(False)


def _check_layout(ds, var, target_order, aliases, drop):
    if var not in ds.variables:
        raise ValueError(f"Data variable {var} is absent")
    v = ds.variables[var]
    current = tuple(aliases.get(d, d) for d in v.dimensions)
    target = tuple(target_order)
    if len(set(current)) != len(current) or len(set(target)) != len(target):
        raise ValueError("Repeated dimensions are not supported for a layout change")
    extra = set(current) - set(target)
    if set(target) - set(current) or extra - set(drop):
        raise ValueError(f"Unreviewed dimension change {current} -> {target}")
    for old, canonical in zip(v.dimensions, current):
        if canonical in extra and len(ds.dimensions[old]) != 1:
            raise ValueError(f"Cannot remove non-singleton dimension {old} from {var}")
    return current


def _transform(data, current, target):
    removed = tuple(i for i, d in enumerate(current) if d not in target)
    if removed:
        data = np.squeeze(data, axis=removed)
    kept = [d for d in current if d in target]
    return np.transpose(data, [kept.index(d) for d in target])


def _mapped_slab(slab, current, target):
    return tuple(slab[current.index(d)] for d in target)


def _verify_variable(source, output, target_order, aliases=None):
    """Compare each rectangular raw input block with its declared output permutation/squeeze."""
    aliases = aliases or {}
    current = tuple(aliases.get(d, d) for d in source.dimensions)
    target = tuple(target_order)
    if output.dimensions != target or source.dtype != output.dtype:
        raise ValueError(
            "Output variable dimensions or dtype differ from the declared layout"
        )
    wanted_shape = tuple(source.shape[current.index(d)] for d in target)
    if output.shape != wanted_shape:
        raise ValueError("Output variable shape differs from the declared layout")
    _raw(source)
    _raw(output)
    for slab in data_blocks(source, "Comparaison des valeurs brutes"):
        a = np.asarray(source[slab])
        expected = _transform(a, current, target)
        actual = np.asarray(output[_mapped_slab(slab, current, target)])
        if expected.dtype.kind == "O":
            equal = np.array_equal(expected, actual)
        else:
            equal = expected.tobytes(order="C") == actual.tobytes(order="C")
        if not equal:
            raise ValueError(
                f"Raw values do not match the declared permutation of {source.name}"
            )


def _same_attrs(a, b):
    if set(a.ncattrs()) != set(b.ncattrs()):
        return False
    for attr in a.ncattrs():
        if _attribute_type(a, attr) != _attribute_type(b, attr):
            return False
        av, bv = np.asarray(a.getncattr(attr)), np.asarray(b.getncattr(attr))
        if av.dtype != bv.dtype or av.shape != bv.shape:
            return False
        if av.dtype.kind in "biufc":
            if not np.array_equal(av, bv, equal_nan=True):
                return False
        elif not np.array_equal(av, bv):
            return False
    return True


def _creation_options(v, dimensions):

    # Preserve storage settings and reject filters this rewrite backend cannot reproduce.
    options = {
        "fill_value": v.getncattr("_FillValue")
        if "_FillValue" in v.ncattrs()
        else False
    }
    filters = v.filters() or {}
    if filters.get("zlib"):
        options.update(
            compression="zlib",
            complevel=filters["complevel"],
            shuffle=filters["shuffle"],
        )
    elif any(filters.get(k) for k in ["zstd", "bzip2", "szip", "blosc"]):
        raise ValueError(
            "Python layout rewrite currently supports uncompressed/zlib files; unsupported filter retained untouched"
        )
    if filters.get("fletcher32"):
        options["fletcher32"] = True
    chunks = v.chunking()
    if isinstance(chunks, list):
        options["chunksizes"] = tuple(chunks[v.dimensions.index(d)] for d in dimensions)
    elif chunks == "contiguous":
        options["contiguous"] = True
    if v.dtype != str and v.dtype.kind not in "SUO":
        options["endian"] = v.endian()
    return options


def _python_rewrite(
    source_path, output_path, var=None, target_order=None, dimension_map=None
):
    dimension_map = dimension_map or {}
    if var is not None and dimension_map:
        raise ValueError(
            "Apply layout permutations and dimension merging as separate rewrite phases"
        )
    with netCDF4.Dataset(source_path) as src:
        if src.groups or src.cmptypes or src.enumtypes or src.vltypes:
            raise ValueError(
                "Python layout rewrite requires a flat file without custom data types"
            )
        with netCDF4.Dataset(output_path, "w", format=src.data_model) as dst:
            _copy_attrs(src, dst)
            for name, d in src.dimensions.items():
                target_name = dimension_map.get(name, name)
                if target_name in dst.dimensions:
                    existing = dst.dimensions[target_name]
                    if (
                        len(existing) != len(d)
                        or existing.isunlimited() != d.isunlimited()
                    ):
                        raise ValueError(
                            f"Cannot merge unequal dimensions into {target_name}"
                        )
                else:
                    dst.createDimension(
                        target_name, None if d.isunlimited() else len(d)
                    )

            # Copy each variable in bounded C-order blocks; preserve packing and compression.
            for name, v in src.variables.items():
                source_order = tuple(target_order) if name == var else v.dimensions
                dimensions = tuple(dimension_map.get(d, d) for d in source_order)
                new = dst.createVariable(
                    name, v.datatype, dimensions, **_creation_options(v, source_order)
                )
                _raw(v)
                _raw(new)
                for slab in data_blocks(v, "Réécriture NetCDF"):
                    data = np.asarray(v[slab])
                    if name == var:
                        new[_mapped_slab(slab, v.dimensions, dimensions)] = _transform(
                            data, v.dimensions, dimensions
                        )
                    else:
                        new[slab] = data
                # Apply packing/quantization metadata only AFTER writing already-packed raw data.
                _copy_attrs(v, new, skip=["_FillValue"])


def _verify_rewrite(
    source_path, output_path, var=None, target_order=None, dimension_map=None
):
    dimension_map = dimension_map or {}
    with netCDF4.Dataset(source_path) as src, netCDF4.Dataset(output_path) as dst:
        if src.data_model != dst.data_model or set(src.variables) != set(dst.variables):
            raise ValueError(
                "Layout rewrite changed the file format or variable inventory"
            )
        if not _same_attrs(src, dst) or {
            dimension_map.get(d, d) for d in src.dimensions
        } != set(dst.dimensions):
            raise ValueError(
                "Layout rewrite changed global metadata or the dimension inventory"
            )
        for name, d in src.dimensions.items():
            out_dim = dst.dimensions[dimension_map.get(name, name)]
            if len(d) != len(out_dim) or d.isunlimited() != out_dim.isunlimited():
                raise ValueError(f"Layout rewrite changed dimension {name}")
        for name, v in src.variables.items():
            out = dst.variables[name]
            source_order = tuple(target_order) if name == var else v.dimensions
            expected_order = tuple(dimension_map.get(d, d) for d in source_order)
            _verify_variable(v, out, expected_order, dimension_map)
            if not _same_attrs(v, out) or v.filters() != out.filters():
                raise ValueError(
                    f"Layout rewrite changed attributes or compression of {name}"
                )
            chunks = v.chunking()
            expected_chunks = (
                [chunks[v.dimensions.index(d)] for d in source_order]
                if isinstance(chunks, list)
                else chunks
            )
            if out.chunking() != expected_chunks:
                raise ValueError(f"Layout rewrite changed chunk sizes of {name}")


def verify_original_layout(source_path, output_path, var, target_order, aliases=None):
    """Final check against the unchanged original, after every fix's after-close hook."""
    with (
        netCDF4.Dataset(source_path) as original,
        netCDF4.Dataset(output_path) as current,
    ):
        _verify_variable(
            original.variables[var], current.variables[var], target_order, aliases
        )


def _finish_layout(
    source_path, output_path, var, target_order, aliases=None, drop=None, backend="auto"
):
    aliases = aliases or {}
    drop = drop or []
    # Restore any coordinate renamed by the preparatory method before reading the rewrite source.
    with netCDF4.Dataset(source_path) as original:
        recover = [
            (old, new) for old, new in aliases.items() if old in original.variables
        ]
    for old, new in recover:
        _restore_coordinate_values(source_path, output_path, [old], new)
    with netCDF4.Dataset(output_path) as current:
        _check_layout(current, var, target_order, {}, drop)
        rewrite = current.variables[var].dimensions != tuple(target_order)
    if not rewrite:
        return {"backend": "none", "layout_verified": True}
    if backend not in ["auto", "python", "nco"]:
        raise ValueError(f"Unknown layout backend {backend}")
    ncpdq = shutil.which("ncpdq") if backend != "python" and not drop else None
    if backend == "nco" and ncpdq is None:
        raise RuntimeError(
            "ncpdq is unavailable in this Python environment (or singleton removal was requested)"
        )
    fd, rebuilt = tempfile.mkstemp(
        prefix=".layout-", suffix=".fixtmp", dir=Path(output_path).parent
    )
    os.close(fd)
    try:
        if ncpdq:
            command = [
                ncpdq,
                "-O",
                "-h",
                "-a",
                ",".join(target_order),
                str(output_path),
                rebuilt,
            ]
            with activity("Permutation NCO ncpdq"):
                result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode:
                raise RuntimeError(f"ncpdq failed: {result.stderr[-2000:]}")
            # Keep provenance in fixlog, not additional global attributes added by NCO.
            with (
                netCDF4.Dataset(output_path) as old,
                netCDF4.Dataset(rebuilt, "a") as new,
            ):
                for attr in set(new.ncattrs()) - set(old.ncattrs()):
                    new.delncattr(attr)
                _copy_attrs(old, new)
            used = "ncpdq"
        else:
            _python_rewrite(output_path, rebuilt, var, target_order)
            used = "python"
        _verify_rewrite(output_path, rebuilt, var, target_order)
        os.replace(rebuilt, output_path)
        return {"backend": used, "layout_verified": True}
    finally:
        if os.path.exists(rebuilt):
            os.unlink(rebuilt)


@fix(
    "fix_data_layout",
    kind="structural",
    verify_data=True,
    after_close=_finish_layout,
    layout_change=True,
)
def fix_data_layout(
    ds,
    var: str,
    target_order: list[str],
    aliases: dict | None = None,
    drop: list[str] | None = None,
    backend: str = "auto",
    drop_shared: bool = False,
) -> bool:
    """Preflight a reviewed layout, normalize coordinate names, then rewrite/verify after close."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    aliases = aliases or {}
    drop = drop or []
    canonical = _check_layout(ds, var, target_order, aliases, drop)
    shared = set(drop) & ds.dimensions.keys() if drop_shared else set()
    for name in shared:
        if name in target_order or name in aliases or len(ds.dimensions[name]) != 1:
            raise ValueError(
                f"Cannot remove shared dimension {name}: expected a singleton absent from the target"
            )
    changed = False

    # 2. Prepare approved identifier renames before the controlled structural rewrite.
    for old, new in aliases.items():
        if old in ds.variables or old in ds.dimensions:
            changed = rename_coordinate(ds, [old], new) or changed

    # 3. Remove only stale auxiliary references; singleton sizes were checked above.
    if drop:
        data = ds.variables[var]
        if "coordinates" in data.ncattrs():
            tokens = data.getncattr("coordinates").split()
            kept = [
                name
                for name in tokens
                if name not in ds.variables
                or set(ds.variables[name].dimensions).issubset(target_order)
            ]
            if kept != tokens:
                data.setncattr("coordinates", " ".join(kept))
                changed = True
    return changed or bool(shared) or canonical != tuple(target_order)
