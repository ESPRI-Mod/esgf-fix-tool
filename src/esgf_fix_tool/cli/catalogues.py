"""Discover the complete catalogue in its reviewed first-match order."""

from pathlib import Path

import typer


# Shipped inside the package, so installed copies (wheel, Docker image) find them too.
DEFAULT_RULES_DIR = Path(__file__).resolve().parents[1] / "rules"
PRIORITY = (
    "post_apply",
    "basin_coordinates",
    "variable_attributes",
    "standard_name_modifiers",
    "units_and_standard_names",
    "vertical_coordinate_names",
    "required_coordinates",
    "coordinate_metadata",
    "dimension_order",
    "coordinate_values_bounds",
    "horizontal_coordinates",
    "surface_and_hybrid",
    "qa_metadata",
    "grid_labels",
)


def default_rule_paths():
    """Use the packaged catalogues, independently of the invocation directory."""
    root = DEFAULT_RULES_DIR
    paths = sorted(
        p for p in root.glob("*") if p.is_file() and p.suffix.lower() in (".yaml", ".yml")
    )
    if not paths:
        raise typer.BadParameter(
            f"Default catalogues not found in {root}; provide -r PATH.",
            param_hint="--rules",
        )
    rank = {name: i for i, name in enumerate(PRIORITY)}
    # Curated rules win; historical fallbacks are always loaded last.
    return sorted(
        paths,
        key=lambda p: (
            2
            if p.stem == "catalogue_fallbacks"
            else 0
            if p.stem in rank
            else 1,
            rank.get(p.stem, len(rank)),
            p.name,
        ),
    )


def resolve_catalogues(selectors):
    """Explicit paths select catalogues; bare identifiers select rules within them."""
    paths, ids = [], set()
    for value in selectors or ():
        path = Path(value)
        is_path = (
            path.exists()
            or path.suffix.lower() in (".yaml", ".yml")
            or "/" in value
            or "\\" in value
        )
        if is_path:
            if not path.is_file():
                raise typer.BadParameter(
                    f"Rule catalogue does not exist or is not a file: {value}",
                    param_hint="--rules",
                )
            if path.resolve() not in {p.resolve() for p in paths}:
                paths.append(path)
        else:
            ids.add(value)
    return paths or default_rule_paths(), ids
