"""Attribute-level fixes (no data rewrite)."""

from . import fix


@fix("drop_var_attr")
def drop_var_attr(ds, var: str, attr: str) -> bool:
    """Remove ``attr`` from variable ``var`` (e.g. CF appendix A: no 'history' on data variables)."""
    v = ds.variables.get(var)
    if v is None or attr not in v.ncattrs():
        return False
    v.delncattr(attr)
    return True


@fix("rename_var_attr")
def rename_var_attr(ds, var: str, old: str, new: str) -> bool:
    """Rename attribute ``old`` to ``new`` on ``var`` (e.g. 'description' → 'comment'); keeps an existing ``new``."""
    v = ds.variables.get(var)
    if v is None or old not in v.ncattrs():
        return False
    if new in v.ncattrs():
        v.delncattr(old)
    else:
        v.renameAttribute(old, new)
    return True


@fix("set_var_attr")
def set_var_attr(ds, var: str, attr: str, value) -> bool:
    """Set ``var:attr = value`` (e.g. a missing axis='Z')."""
    v = ds.variables.get(var)
    if v is None or (attr in v.ncattrs() and v.getncattr(attr) == value):
        return False
    v.setncattr(attr, value)
    return True
