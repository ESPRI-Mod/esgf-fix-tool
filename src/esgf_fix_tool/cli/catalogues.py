"""Discover the complete catalogue in its reviewed first-match order."""

from pathlib import Path

import typer


DEFAULT_RULES_DIR = Path(__file__).resolve().parents[3] / "rules"
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
    """Use the source project's rules, independently of the invocation directory."""
    root = DEFAULT_RULES_DIR if DEFAULT_RULES_DIR.is_dir() else Path.cwd() / "rules"
    # scp overlays may leave retired files at the destination. Do not rediscover
    # those duplicate catalogues after their entries moved to thematic files.
    retired = {"cmip7_ipsl.yaml", "seed_20261029.yaml"}
    paths = sorted(
        p
        for p in root.glob("*")
        if p.is_file()
        and p.suffix.lower() in (".yaml", ".yml")
        and p.name not in retired
    )
    if not paths:
        raise typer.BadParameter(
            "Default catalogues not found; run from the project or provide -r PATH.",
            param_hint="--rules",
        )
    rank = {name: i for i, name in enumerate(PRIORITY)}
    # Curated rules win; historical fallbacks are always loaded last.
    return sorted(
        paths,
        key=lambda p: (
            2
            if p.stem == "catalogue_fallbacks" or p.stem.startswith("seed_")
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
