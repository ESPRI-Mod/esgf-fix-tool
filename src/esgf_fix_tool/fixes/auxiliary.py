"""Replace small arrays without changing the dimension IDs of existing NetCDF variables."""

from .layouts import _copy_attrs, _raw

ORIGINAL_PREFIX = "fix_original_"


def replace_auxiliary(ds, name, dimensions, data, *, dtype=None, keep_original=False):
    """Prepare one clean replacement; the atomic writer protects the original file.

    The legacy keep_original argument is accepted but never creates a persistent
    backup variable after the user's revised cleanup decision.
    """
    original = ds.variables.get(name)
    if original is None:
        replacement = ds.createVariable(name, dtype, dimensions)
    else:
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
    _raw(replacement)
    replacement[...] = data
    return replacement
