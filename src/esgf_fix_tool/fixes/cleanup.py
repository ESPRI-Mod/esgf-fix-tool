"""Final removal of explicitly retired metadata, guarded by retained CF references."""

import netCDF4

from . import fix
from .coordinates import REFERENCE_ATTRS, reference_matches
from .reviewed_structures import delete_variables

INDEX_NAMES = ("klev", "klev1", "klevp1")
LEGACY_BOUNDS = ("axis_nbounds", "axis_bounds", "nvertex")


def referenced(ds, name, ignoring=()):
    return any(
        isinstance(text := getattr(v, attr, ""), str)
        and reference_matches(text, attr, name)
        for n, v in ds.variables.items()
        if n not in ignoring
        for attr in REFERENCE_ATTRS
    )


@fix("clean_retired_metadata", verify_data=True)
def clean_retired_metadata(ds):
    """Omit all fix_original_* and only genuinely unused technical indices/dimensions."""
    backups = {n for n in ds.variables if n.startswith("fix_original_")}
    changed = delete_variables(ds, backups)
    main = getattr(ds, "variable_id", "")
    for name in INDEX_NAMES:
        v = ds.variables.get(name)
        if v is not None and name != main and (v.ndim == 0 or v.dimensions == (name,)):
            others_use_dim = any(
                name in x.dimensions for n, x in ds.variables.items() if n != name
            )
            if not others_use_dim and not referenced(ds, name, ignoring={name}):
                changed = delete_variables(ds, {name}) or changed
        if name in ds.dimensions and not any(
            name in v.dimensions for v in ds.variables.values()
        ):
            ds.deleteDimension(name)
            changed = True
    # A four-vertex polygon dimension is not a two-endpoint bounds dimension.
    for name in LEGACY_BOUNDS:
        if (
            name in ds.dimensions
            and len(ds.dimensions[name]) == 2
            and not ds.dimensions[name].isunlimited()
            and not any(name in v.dimensions for v in ds.variables.values())
        ):
            ds.deleteDimension(name)
            changed = True
    return changed


def verify_retired_metadata(output):
    with netCDF4.Dataset(output) as ds:
        if any(n.startswith("fix_original_") for n in ds.variables):
            raise ValueError(
                "Temporary fix_original_* variables survived the complete fix sequence"
            )


@fix("harmonize_main_description")
def harmonize_main_description(ds):
    """Restore the two reviewed descriptions using the same NC_CHAR storage type."""
    name = getattr(ds, "variable_id", "")
    values = {"ta": "Air Temperature", "zg": "Geopotential height"}
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or name not in values
        or name not in ds.variables
        or getattr(ds, "branded_variable", "") != name + "_tpt-al-hxy-u"
        or getattr(ds, "frequency", "") != "6hr"
    ):
        raise ValueError(
            "Description harmonization is approved only for the reviewed ta/zg six-hour products"
        )
    from .layouts import _attribute_type

    v, wanted = ds[name], values[name]
    old = getattr(v, "description", None)
    if old not in (None, "", wanted):
        raise ValueError("Unexpected scientific description; no replacement approved")
    if old == wanted and _attribute_type(getattr(v, "_header", v), "description") == 2:
        return False
    if "description" in v.ncattrs():
        v.delncattr("description")
    v.setncattr("description", wanted)
    return True
