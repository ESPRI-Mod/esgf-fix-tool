"""Reviewed COORD001 corrections; no physical-coordinate conversion or hybrid-formula inference."""

import json
from pathlib import Path
import re

import numpy as np
import netCDF4

from . import fix


REFERENCE_ATTRS = ("coordinates", "bounds", "climatology", "ancillary_variables",
                   "cell_measures", "formula_terms", "grid_mapping", "cell_methods")


def reference_matches(value, attr, name):
    """Find references while preserving prose in cell_methods comments/qualifiers."""
    pattern = r"(?<![\w/])" + re.escape(name) + r"(?![\w/])"
    if attr == 'cell_methods':
        # Only names before ':' are axes. Mask comments without shifting offsets,
        # so replacements can be made against the original text byte for byte.
        visible = list(value)
        depth = 0
        for index, char in enumerate(value):
            if char == '(':
                depth += 1
            if depth or char == ')':
                visible[index] = ' '
            if char == ')':
                depth -= 1
                if depth < 0:
                    raise ValueError('Unbalanced parentheses in cell_methods')
        if depth:
            raise ValueError('Unbalanced parentheses in cell_methods')
        value = ''.join(visible)
        pattern += r"(?=\s*:)"
    return list(re.finditer(pattern, value))


def _replace_references(ds, old, new):
    for v in ds.variables.values():
        for attr in REFERENCE_ATTRS:
            value = getattr(v, attr, None)
            if isinstance(value, str):
                matches = reference_matches(value, attr, old)
                if matches:
                    for match in reversed(matches):
                        value = value[:match.start()] + new + value[match.end():]
                    v.setncattr(attr, value)
    for group in ds.groups.values():
        # A child may shadow a root variable/dimension of the same name.
        if old in group.variables or old in group.dimensions:
            raise ValueError(f"Ambiguous child-group reference to {old}: nothing applied")
        _replace_references(group, old, new)


def _restore_coordinate_values(source_path, output_path, sources, target, rename_dimension=True):
    """netCDF4 can lose renamed coordinate data: restore raw values on a fresh handle, then verify."""
    with netCDF4.Dataset(source_path) as original:
        present = [name for name in sources if name in original.variables]
        if len(present) != 1:
            raise ValueError("Cannot identify the original coordinate for post-close restoration")
        v = original.variables[present[0]]
        v.set_auto_maskandscale(False)
        v.set_auto_chartostring(False)
        raw = np.asarray(v[...]).copy()
    with netCDF4.Dataset(output_path, "a") as result:
        v = result.variables[target]
        if v.shape != raw.shape:
            raise ValueError("Renamed coordinate shape differs from the original")
        v.set_auto_maskandscale(False)
        v.set_auto_chartostring(False)
        if v.dtype == str and not v.dimensions:
            v.assignValue(raw.item())
        else:
            v[...] = raw


@fix("rename_coordinate", verify_data=True, after_close=_restore_coordinate_values)
def rename_coordinate(ds, sources: list[str], target: str, rename_dimension: bool = True) -> bool:
    """Rename identifiers; the writer's registered after-close hook restores coordinate values."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    present = [name for name in sources if name in ds.variables]
    if not present and target in ds.variables:
        return False
    if len(present) != 1:
        raise ValueError(f"Expected exactly one source coordinate among {sources}; found {present}")
    old = present[0]
    if target in ds.variables or (rename_dimension and target in ds.dimensions):
        raise ValueError(f"Coordinate rename {old} -> {target} would overwrite an existing name")
    v = ds.variables[old]
    if rename_dimension and (old not in ds.dimensions or v.dimensions != (old,)):
        raise ValueError(f"{old} must be a one-dimensional coordinate on its own dimension")
    if not rename_dimension:
        is_string = v.dtype == str
        if not is_string and v.dtype.kind not in "SU":
            raise ValueError(f"{old} must be a text coordinate")
        if v.ndim != (0 if is_string else 1):
            raise ValueError(f"{old} must be a scalar text coordinate")
    # Never trust values before close: the writer restores them on a fresh handle and checks all data.

    # 2. Rename identifiers and references; after-close restoration protects raw coordinates.
    ds.renameVariable(old, target)
    if rename_dimension:
        ds.renameDimension(old, target)
    if "name" in v.ncattrs() and v.getncattr("name") == old:
        v.setncattr("name", target)
    _replace_references(ds, old, target)
    return True


def _restore_auxiliary_values(source_path, output_path, var, source, target, coordinate):
    _restore_coordinate_values(source_path, output_path, [coordinate], coordinate)


@fix("rename_auxiliary_dimension", verify_data=True, after_close=_restore_auxiliary_values)
def rename_auxiliary_dimension(ds, var: str, source: str, target: str, coordinate: str) -> bool:
    """Rename an index dimension to the already-existing text coordinate's canonical name."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    if var not in ds.variables or coordinate not in ds.variables:
        raise ValueError(f"Data variable {var} or auxiliary coordinate {coordinate} is absent")
    data, coord = ds.variables[var], ds.variables[coordinate]
    if source not in ds.dimensions and target in ds.dimensions:
        if target in data.dimensions and coord.dimensions[0] == target:
            return False
    if source not in ds.dimensions or target in ds.dimensions:
        raise ValueError(f"Cannot rename auxiliary dimension {source} -> {target}: missing source or collision")
    if source not in data.dimensions or not coord.dimensions or coord.dimensions[0] != source:
        raise ValueError(f"{coordinate} does not identify {var}'s dimension {source}")
    if not (coord.dtype == str or coord.dtype.kind in "SU"):
        raise ValueError(f"{coordinate} is not a text auxiliary coordinate")

    # 2. Change only the dimension identity and references, keeping label values intact.
    ds.renameDimension(source, target)
    _replace_references(ds, source, target)
    return True


def _main_variable(ds, expected):
    actual = getattr(ds, "variable_id", None)
    if not isinstance(actual, str) or actual.casefold() != expected.casefold():
        raise ValueError(f"variable_id does not agree with reviewed registry variable {expected}")
    candidates = [name for name in ds.variables if name.casefold() == expected.casefold()]
    if len(candidates) != 1:
        raise ValueError(f"Expected one data variable {expected}; found {candidates}")
    return ds.variables[candidates[0]]


def _attach(v, name):
    current = getattr(v, "coordinates", "")
    if not isinstance(current, str):
        raise ValueError("The data variable's coordinates attribute is not text")
    if name not in current.split():
        v.setncattr("coordinates", (current.rstrip() + " " + name).strip())


@fix("add_registry_scalar", verify_data=True)
def add_registry_scalar(ds, target: str) -> bool:
    """Add a missing scalar and explicit registry bounds from the bundled, verified branded-variable map."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    if target in ds.variables:
        return False
    specs = json.loads(Path(__file__).with_name("coordinate_specs.json").read_text())
    brand = getattr(ds, "branded_variable", "")
    entry = specs["brands"].get(brand.casefold()) if isinstance(brand, str) else None
    if entry is None or target not in entry["coordinates"]:
        raise ValueError(f"No reviewed registry scalar {target} for branded_variable={brand!r}")
    spec = specs["coordinates"][entry["coordinates"][target]]
    data = _main_variable(ds, entry["variable"])
    bounds = spec.get("bounds")
    bound_name = target + "_bnds"
    if bounds is not None and bound_name in ds.variables:
        raise ValueError(f"Cannot create {bound_name}: name already used")

    # 2. Create the reviewed scalar and its explicit DR bounds; no inferred coordinates.
    v = ds.createVariable(target, "f8", ())
    v.setncatts(spec["attributes"])
    v.assignValue(spec["value"])
    if bounds is not None:
        dim = "bnds"
        if dim in ds.dimensions and (len(ds.dimensions[dim]) != 2 or ds.dimensions[dim].isunlimited()):
            dim = target + "_nbounds"
        if dim not in ds.dimensions:
            ds.createDimension(dim, 2)
        elif len(ds.dimensions[dim]) != 2 or ds.dimensions[dim].isunlimited():
            raise ValueError(f"Cannot use {dim} for scalar bounds")
        b = ds.createVariable(bound_name, "f8", (dim,))
        b[:] = bounds
        v.setncattr("bounds", bound_name)
    _attach(data, target)
    return True


@fix("add_text_coordinate_alias", verify_data=True)
def add_text_coordinate_alias(ds, source: str, target: str = "sector") -> bool:
    """Keep the registry-named text coordinate and add the alias required by the scanned checker."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    if target in ds.variables:
        return False
    if source not in ds.variables:
        raise ValueError(f"Text source coordinate {source} is absent")
    v = ds.variables[source]
    is_string = v.dtype == str
    if not is_string and v.dtype.kind not in "SU":
        raise ValueError(f"{source} is numeric: categorical labels must be validated before adding {target}")
    if v.ndim != (1 if is_string else 2):
        raise ValueError(f"{source} is not a one-dimensional text auxiliary coordinate")
    variable = getattr(ds, "variable_id", "")
    data = _main_variable(ds, variable)
    if v.dimensions[0] not in data.dimensions:
        raise ValueError(f"{source} is not defined on a dimension of the data variable")
    attrs = {a: v.getncattr(a) for a in v.ncattrs() if a != "_FillValue"}
    raw = None
    if not is_string:
        # Write valid CF character padding immediately. Never copy undecodable
        # or interior control bytes into a newly created sector coordinate.
        from .qa_metadata import _label_rows
        original, labels = _label_rows(v)
        raw = np.full(original.shape, b' ', dtype='S1')
        for row, label in zip(raw, labels):
            row[:len(label)] = np.frombuffer(label.encode('ascii'), dtype='S1')
        attrs.pop('units', None)
    if attrs.get("name") == source:
        attrs["name"] = target
    kwargs = {"fill_value": v.getncattr("_FillValue")} if "_FillValue" in v.ncattrs() else {}

    # 2. Copy the text representation, then attach the alias without replacing the source.
    alias = ds.createVariable(target, v.datatype, v.dimensions, **kwargs)
    alias.setncatts(attrs)
    v.set_auto_maskandscale(False)
    v.set_auto_chartostring(False)
    alias.set_auto_maskandscale(False)
    alias.set_auto_chartostring(False)
    alias[:] = v[:] if raw is None else raw
    _attach(data, target)
    return True


@fix('fix_ocean_vertical_metadata')
def fix_ocean_vertical_metadata(ds):
    """Complete the identity of the existing NEMO depth axis without changing values."""
    name = 'lev' if 'lev' in ds.variables else 'olevel'
    v = ds.variables.get(name)
    if (getattr(ds, 'source_id', '') != 'IPSL-CM6-ESMCO2'
            or getattr(ds, 'vertical_label', '') != 'ol' or v is None
            or v.dimensions != (name,) or v.dtype.kind != 'f'
            or getattr(v, 'units', '') != 'm'
            or getattr(v, 'standard_name', '') not in ('', 'depth')
            or getattr(v, 'positive', 'down') != 'down'):
        raise ValueError('Expected the reviewed dimensional NEMO depth coordinate')
    values = np.ma.asarray(v[:])
    if (np.ma.getmaskarray(values).any() or not np.isfinite(values).all()
            or not np.all(np.diff(values) > 0) or values.min() < 0 or values.max() > 12000):
        raise ValueError('Ocean depths must be finite, positive and strictly increasing')
    changed = rename_coordinate(ds, [name], 'lev') if name != 'lev' else False
    v = ds['lev']
    for attr, value in dict(standard_name='depth', axis='Z', positive='down',
                            long_name='ocean depth coordinate').items():
        if getattr(v, attr, None) != value:
            v.setncattr(attr, value)
            changed = True
    # Keep the newly recognized coordinate compliant with its lev_bnds naming
    # requirement in the same pass. This is an identifier edit, never new edges.
    bound = getattr(v, 'bounds', '')
    if bound == 'olevel_bounds':
        from .bounds import rename_bounds_variable
        changed = rename_bounds_variable(ds, 'lev', bound, 'lev_bnds') or changed
        bound = 'lev_bnds'
    if bound in ds.variables and getattr(ds[bound], 'units', None) == 'm':
        ds[bound].delncattr('units')
        changed = True
    return changed
