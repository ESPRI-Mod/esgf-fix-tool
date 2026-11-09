"""Replace small arrays without changing the dimension IDs of existing NetCDF variables."""

from .layouts import _copy_attrs, _raw

ORIGINAL_PREFIX = "fix_original_"


def replace_auxiliary(ds, name, dimensions, data, *, dtype=None, keep_original=True):
    """Keep a displaced array under a separate name and write its canonical replacement."""
    original = ds.variables.get(name)
    if original is None:
        replacement = ds.createVariable(name, dtype, dimensions)
    elif not keep_original:
        if not hasattr(ds, "replaceVariable"):
            raise ValueError("Clean auxiliary replacement requires fix_tool apply")
        replacement = ds.replaceVariable(
            name,
            dtype or original.datatype,
            dimensions,
            fill_value=getattr(original, "_FillValue", False),
        )
        _copy_attrs(
            getattr(original, "_header", original), replacement, skip=["_FillValue"]
        )
    else:
        backup = ORIGINAL_PREFIX + name
        if backup in ds.variables or backup in ds.dimensions:
            raise ValueError(f"Cannot replace {name}: {backup} already exists")
        fill = getattr(original, "_FillValue", False)
        ds.renameVariable(name, backup)
        replacement = ds.createVariable(
            name, dtype or original.datatype, dimensions, fill_value=fill
        )
        # Attribute storage types come from the NetCDF header, including in a Preview.
        _copy_attrs(
            getattr(original, "_header", original), replacement, skip=["_FillValue"]
        )
    _raw(replacement)
    replacement[...] = data
    return replacement
