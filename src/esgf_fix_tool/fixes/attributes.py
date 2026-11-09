"""Attribute-level fixes (no data rewrite)."""

from . import fix


@fix("drop_var_attr")
def drop_var_attr(ds, var: str, attr: str) -> bool:
    """Remove ``attr`` from variable ``var`` (e.g. CF appendix A: no 'history' on data variables)."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or attr not in v.ncattrs():
        return False

    # 2. Delete only this variable attribute; global history and all arrays stay intact.
    v.delncattr(attr)
    return True


@fix("rename_var_attr")
def rename_var_attr(ds, var: str, old: str, new: str) -> bool:
    """Rename attribute ``old`` to ``new`` on ``var`` (e.g. 'description' → 'comment'); keeps an existing ``new``."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or old not in v.ncattrs():
        return False

    # 2. Keep an existing target attribute; otherwise rename without changing its text.
    if new in v.ncattrs():
        v.delncattr(old)
    else:
        v.renameAttribute(old, new)
    return True


@fix("set_var_attr")
def set_var_attr(ds, var: str, attr: str, value) -> bool:
    """Set ``var:attr = value`` (e.g. a missing axis='Z')."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or (attr in v.ncattrs() and v.getncattr(attr) == value):
        return False

    # 2. Apply the rule-supplied value only after the idempotence check.
    v.setncattr(attr, value)
    return True


@fix("drop_descriptive_standard_name")
def drop_descriptive_standard_name(ds, var: str, expected: str) -> bool:
    """Remove an exact descriptive standard_name; keep its text in a missing/empty long_name."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or "standard_name" not in v.ncattrs():
        return False
    current = v.getncattr("standard_name")
    if not isinstance(current, str) or current != expected:
        return False  # another fix may already have supplied a valid standard_name

    # 2. Preserve the descriptive text before removing the exact invalid standard_name.
    long_name = v.getncattr("long_name") if "long_name" in v.ncattrs() else None
    if long_name is None or (isinstance(long_name, str) and long_name == ""):
        v.setncattr("long_name", current)
    v.delncattr("standard_name")
    return True


@fix("drop_region_units")
def drop_region_units(ds, var: str, expected: str = "1") -> bool:
    """Remove only the observed dimensionless units on a region variable; leave codes and flags alone."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or "units" not in v.ncattrs():
        return False
    if (
        v.getncattr("units") != expected
        or getattr(v, "standard_name", None) != "region"
    ):
        return False

    # 2. Remove units only; categorical codes and flags require their own recipe.
    v.delncattr("units")
    return True


@fix("fix_soil_depth_metadata")
def fix_soil_depth_metadata(ds, var: str) -> bool:
    """Correct the observed soil-depth metadata without renaming axes or inventing layer bounds."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or getattr(v, "standard_name", None) != "model_level_number":
        return False
    if getattr(v, "units", None) != "m":
        return False

    # 2. Set depth/Z/down metadata; retain meters, coordinates and physical bounds.
    v.setncattr("standard_name", "depth")
    v.setncattr("axis", "Z")
    v.setncattr("positive", "down")
    return True


@fix("fix_invalid_standard_names")
def fix_invalid_standard_names(
    ds,
    expected: list[str],
    replacement: str | None = None,
    units: str | None = None,
    var: str | list[str] | None = None,
    requires: list[str] | None = None,
) -> bool:
    """Resolve a QA message lacking variable names, using exact, reviewed full attribute values.

    Never interpret a checker suggestion as an instruction. Rules supply the explicit allowlist,
    replacement and optional units guard. Scanning current variables also tolerates prior renames.
    """
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    changed = False
    for name, v in ds.variables.items():
        if var is not None and name not in ([var] if isinstance(var, str) else var):
            continue
        current = getattr(v, "standard_name", None)
        if not isinstance(current, str) or current not in expected:
            continue
        if units is not None and getattr(v, "units", None) != units:
            continue
        required = requires
        if (
            required is None
            and replacement == "atmosphere_hybrid_sigma_pressure_coordinate"
        ):
            # Older C014 plans predate the explicit requires parameter; enforce the guard there too.
            required = ["ap", "b"]
        missing = [term for term in (required or []) if term not in ds.variables]
        if missing:
            raise ValueError(
                f"Cannot correct {name}: required coefficient variable(s) absent: {', '.join(missing)}"
            )

        # 2. Use only the explicit mapping from the rule, never the checker suggestion.
        if replacement is None:
            changed = drop_descriptive_standard_name(ds, name, current) or changed
        elif current != replacement:
            v.setncattr("standard_name", replacement)
            changed = True
    return changed


@fix("set_var_attr_if_matches")
def set_var_attr_if_matches(ds, var: str, attr: str, expected: str, value: str) -> bool:
    """Set a reviewed attribute only while its current value matches the observed diagnostic."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    v = ds.variables.get(var)
    if v is None or getattr(v, attr, "") != expected:
        return False

    # 2. Replace the observed attribute value without touching the underlying data.
    v.setncattr(attr, value)
    return True
