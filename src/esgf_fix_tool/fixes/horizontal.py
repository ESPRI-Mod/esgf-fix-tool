"""Reviewed horizontal associations and curvilinear auxiliary names; preserve all arrays."""

import json
from pathlib import Path

from . import fix
from .coordinates import _main_variable, _replace_references, _restore_coordinate_values


def _specs():
    return json.loads(Path(__file__).with_name("horizontal_specs.json").read_text())


@fix("drop_unrequested_horizontal_reference", verify_data=True)
def drop_unrequested_horizontal_reference(
    ds, var: str, role: str, coordinate: str, branded_variable: str
) -> bool:
    """Remove only the reviewed tokens from this variable's coordinates attribute."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    specs = _specs()
    entry = specs["brands"].get(branded_variable)
    if (
        entry is None
        or getattr(ds, "branded_variable", "").casefold() != branded_variable.casefold()
    ):
        raise ValueError(
            "Unreviewed or mismatched branded variable for horizontal association"
        )
    if role not in entry["omit_roles"] or coordinate not in {
        specs["coordinates"][role]["source"],
        specs["coordinates"][role]["target"],
    }:
        raise ValueError(
            "This horizontal coordinate is required, or the role/name pair is unreviewed"
        )
    if var.casefold() != entry["variable"].casefold():
        raise ValueError(
            "Message variable disagrees with the reviewed branded variable"
        )
    data = _main_variable(ds, var)
    current = getattr(data, "coordinates", "")
    if not isinstance(current, str):
        raise ValueError("coordinates must be a text attribute")
    aliases = {
        specs["coordinates"][role]["source"],
        specs["coordinates"][role]["target"],
    }
    tokens = current.split()
    selected = aliases.intersection(tokens)
    for name in selected:
        if name in ds.variables and getattr(ds[name], "standard_name", None) != role:
            raise ValueError(
                "Coordinate name now refers to an unexpected standard_name"
            )
    if not selected:
        return False

    # 2. Remove exact tokens only; retain the arrays and all required coordinate roles.
    data.setncattr(
        "coordinates", " ".join(name for name in tokens if name not in aliases)
    )
    return True


def icosahedral_grid(ds):
    """Validate the reviewed cell-indexed six-vertex grids without reading arrays."""
    if getattr(ds, "grid_label", "") not in {"g243", "g245"}:
        return False
    if getattr(ds, "horizontal_label", "") != "hxy":
        raise ValueError("Icosahedral horizontal renames require an hxy product")
    data = _main_variable(ds, getattr(ds, "variable_id", ""))
    if "cell" not in data.dimensions:
        return False  # Some g245 products are stored on a rectilinear lat/lon grid.
    for role, short in (("latitude", "lat"), ("longitude", "lon")):
        names = [n for n in (short, role) if n in ds.variables]
        if len(names) != 1:
            raise ValueError(f"Missing or ambiguous icosahedral {role}")
        coord = ds[names[0]]
        if coord.dimensions != ("cell",) or getattr(coord, "standard_name", "") != role:
            raise ValueError(f"Expected {role}(cell) auxiliary coordinate")
        bound = getattr(coord, "bounds", "")
        if bound not in {"bounds_" + short, short + "_bounds", "vertices_" + role} or bound not in ds.variables:
            raise ValueError(f"Missing or unexpected {role} vertex reference")
        vertices = ds[bound]
        if (vertices.dimensions not in (("cell", "nvertex"), ("cell", "vertices"))
                or vertices.shape[-1] != 6):
            raise ValueError("Expected six stored vertices per icosahedral cell")
    return True


def _restore_horizontal(source_path, output_path, role):
    import netCDF4

    spec = _specs()["coordinates"][role]
    with netCDF4.Dataset(source_path) as source:
        ico = getattr(source, "grid_label", "") in {"g243", "g245"}
    old = ("lat" if role == "latitude" else "lon") if ico else spec["source"]
    _restore_coordinate_values(source_path, output_path, [old], spec["target"], False)


@fix("rename_horizontal_coordinate", verify_data=True, after_close=_restore_horizontal)
def rename_horizontal_coordinate(ds, role: str) -> bool:
    """Rename horizontal auxiliaries; preserve cell or y/x dimensions and values."""
    # 1. Inspect current identifiers/attributes and enforce the reviewed preconditions.
    specs = _specs()
    if role not in specs["coordinates"]:
        raise ValueError("Unreviewed horizontal coordinate role")
    spec = specs["coordinates"][role]
    ico = icosahedral_grid(ds)
    cell_or_regular = getattr(ds, "grid_label", "") in {"g243", "g245"}
    source = ("lat" if role == "latitude" else "lon") if cell_or_regular else spec["source"]
    target = spec["target"]
    if (
        getattr(ds, "horizontal_label", "") != "hxy"
        or not cell_or_regular and getattr(ds, "grid_label", "") not in specs["curvilinear_grids"]
    ):
        raise ValueError(
            "Horizontal rename is restricted to reviewed curvilinear hxy grids"
        )
    present = [name for name in [source, target] if name in ds.variables]
    if len(present) != 1:
        raise ValueError(
            "Missing or ambiguous horizontal coordinate; no existing name is overwritten"
        )
    v = ds[present[0]]
    data = _main_variable(ds, getattr(ds, "variable_id", ""))
    rectilinear = cell_or_regular and v.dimensions == (source,) and source in data.dimensions
    if not ico and not rectilinear and (v.dimensions != ("y", "x") or not {"y", "x"}.issubset(data.dimensions)):
        raise ValueError(
            "Horizontal rename requires a 2-D auxiliary coordinate on the data y/x grid"
        )
    if (
        getattr(v, "standard_name", "") != role
        or getattr(v, "units", "") != spec["units"]
        or v.dtype.kind != "f"
    ):
        raise ValueError(
            "Coordinate metadata differs from the reviewed horizontal role"
        )
    if present[0] == target:
        return False
    if target in ds.dimensions:
        raise ValueError("Horizontal target would collide with an existing dimension")

    # 2. Rename only the auxiliary variable; cell/y/x and all values are preserved.
    ds.renameVariable(source, target)
    if "name" in v.ncattrs() and v.getncattr("name") == source:
        v.setncattr("name", target)
    _replace_references(ds, source, target)
    return True
