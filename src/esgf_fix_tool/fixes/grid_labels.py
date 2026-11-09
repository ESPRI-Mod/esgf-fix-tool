"""Grid labels for reviewed IPSL reductions and transects."""

from pathlib import Path

import numpy as np

from . import fix

REGIONAL_PRODUCTS = {
    "siarea": "u",
    "siextent": "u",
    "sisnmass": "si",
    "sivol": "u",
}
OCEAN_GLOBALS = {"bigthetao", "masso", "so", "sos", "thetao", "tos", "volo"}
OCEAN_TRANSECTS = {"hfacrossline", "mfo", "sfacrossline"}
ICE_TRANSECTS = {"siareaacrossline", "simassacrossline", "sisnmassacrossline"}
GRID_MAPPING = {
    ("g111", "hm", "glb"): "g010",
    ("g112", "hm", "glb"): "g010",
    ("g112", "hm", "nh"): "g011",
    ("g112", "hm", "sh"): "g012",
    ("g112", "ht", "glb"): "g014",
}


def _check_mapping(source, horizontal, region, target):
    if GRID_MAPPING.get((source, horizontal, region)) != target:
        raise ValueError(f"Unreviewed grid assignment {source}/{horizontal}/{region} -> {target}")


def _check_horizontal_mean(ds, main, source, region):
    name = main.name
    if name == "zostoga" and source == "g112" and region == "glb":
        # C094 explicitly assigns the grid label independently of C050.
        if (getattr(ds, "branded_variable", "") == "zostoga_tavg-u-hm-sea"
                and (main.dimensions == ("time",)
                     or main.dimensions == ("time", "y", "x") and main.shape[1:] == (332, 362))):
            return
        raise ValueError("Unexpected zostoga geometry for the reviewed grid-label override")
    if source == "g111":
        expected_brand = "co2mass_tavg-u-hm-u" if name == "co2mass" else None
    elif region == "glb":
        expected_brand = f"{name}_tavg-u-hm-sea" if name in OCEAN_GLOBALS else None
    else:
        area = REGIONAL_PRODUCTS.get(name)
        expected_brand = f"{name}_tavg-u-hm-{area}" if area else None
    if expected_brand is None or getattr(ds, "branded_variable", "") != expected_brand:
        raise ValueError("Unreviewed horizontally reduced product")
    if main.dimensions == ("time",) and all(
        dim not in ds.dimensions or len(ds.dimensions[dim]) == 1 for dim in ("y", "x")
    ):
        return
    if source == "g112" and main.dimensions == ("time", "y", "x") and main.shape[1:] == (1, 1):
        return
    raise ValueError("Expected a reduced time series with at most singleton y/x")


def _check_transect(ds, main):
    name = main.name
    if name in OCEAN_TRANSECTS:
        dimension, count, area = "oline", 16, "sea"
    elif name in ICE_TRANSECTS:
        dimension, count, area = "siline", 4, "u"
    else:
        raise ValueError("Unreviewed transect product")
    if getattr(ds, "branded_variable", "") != f"{name}_tavg-u-ht-{area}":
        raise ValueError("Unexpected transect branding")
    if main.ndim != 2 or main.dimensions[0] != "time" or main.dimensions[1] not in (dimension, "line"):
        raise ValueError("Expected a time series indexed by the reviewed transect axis")
    line = next((ds.variables[n] for n in ('sector', 'line', 'line_label') if n in ds.variables), None)
    if (line is None or line.dimensions[:1] != main.dimensions[1:]
            or line.dtype != np.dtype("S1") or line.shape not in
                ({(count, 255), (4, 21)} if name in ICE_TRANSECTS else {(count, 255)})
            or getattr(line, "standard_name", "") != "region"):
        raise ValueError("Expected character labels on the reviewed transect axis")


@fix("set_reduced_grid_label")
def set_reduced_grid_label(ds, source, horizontal, region, target):
    _check_mapping(source, horizontal, region, target)
    current = getattr(ds, "grid_label", "")
    variable = getattr(ds, "variable_id", "")
    frequencies = ("day", "mon") if variable == "zostoga" else ("mon",)
    if (current not in (source, target)
            or getattr(ds, "horizontal_label", "") != horizontal
            or getattr(ds, "region", "") != region
            or getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
            or getattr(ds, "experiment_id", "") != "esm-piControl-spinup"
            or getattr(ds, "variant_label", "") != "r1i1p1f1"
            or getattr(ds, "frequency", "") not in frequencies):
        raise ValueError("The file's grid, region or simulation differs from the reviewed case")
    main = ds.variables.get(variable)
    if main is None:
        raise ValueError("Missing declared science variable")
    if horizontal == "hm":
        _check_horizontal_mean(ds, main, source, region)
    else:
        _check_transect(ds, main)

    # Only the output grid label changes. The existing region describes the
    # intended reduced domain and the arrays retain their original geometry.
    if current == target:
        return False
    ds.setncattr("grid_label", target)
    return True


def proposed_drs_path(path, source, horizontal, region, target):
    """Compute the separate relink target from the exact DRS grid field."""
    _check_mapping(source, horizontal, region, target)
    path = Path(path)
    fields = path.name.split("_", 5)
    if (len(fields) != 6 or path.suffix != ".nc"
            or fields[1].split("-")[2:3] != [horizontal]
            or fields[3] != region or fields[4] not in (source, target)):
        raise ValueError(f"Unexpected CMIP7 filename for the grid correction: {path.name}")
    if fields[4] == target:
        return None
    fields[4] = target
    return path.with_name("_".join(fields))


def filename_grid_target(path, source, target):
    """Validate the exact filename component for an explicitly planned rename."""
    fields = Path(path).name.split('_', 5)
    if len(fields) != 6 or len(fields[1].split('-')) != 4:
        raise ValueError('Unexpected CMIP7 filename')
    horizontal, region = fields[1].split('-')[2], fields[3]
    return proposed_drs_path(path, source, horizontal, region, target)


@fix('sync_filename_grid_label')
def sync_filename_grid_label(ds, source, target):
    """Validate the dataset before the apply layer synchronizes its basename."""
    if getattr(ds, 'grid_label', '') != target:
        raise ValueError('The target filename must agree with the actual global grid_label')
    return set_reduced_grid_label(ds, source, getattr(ds, 'horizontal_label', ''),
                                  getattr(ds, 'region', ''), target)
