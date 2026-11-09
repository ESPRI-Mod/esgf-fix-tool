"""Guarded metadata fixes for the reviewed CF and CMIP7 recommendation variants."""

import numpy as np

from . import fix
from .attributes import set_var_attr_if_matches
from .coordinates import _replace_references
import json
from pathlib import Path


# Exact standard names and units observed in the individual consistency reports.
TEMPERATURES = {
    "ta": ("air_temperature", "K"),
    "tas": ("air_temperature", "K"),
    "tatp": ("tropopause_air_temperature", "K"),
    "tdps": ("dew_point_temperature", "K"),
    "ts": ("surface_temperature", "K"),
    "tslsi": ("surface_temperature", "K"),
    "tsl": ("soil_temperature", "K"),
    "sitempsnic": ("sea_ice_surface_temperature", "K"),
    "bigthetao": ("sea_water_conservative_temperature", "degC"),
    "thetao": ("sea_water_potential_temperature", "degC"),
    "thetaot": ("sea_water_potential_temperature", "degC"),
    "tob": ("sea_water_potential_temperature_at_sea_floor", "degC"),
    "tos": ("sea_surface_temperature", "degC"),
    "tossq": ("square_of_sea_surface_temperature", "degC2"),
}
TENDENCIES = {
    "tnt": "",
    "tnta": "_due_to_advection",
    "tntc": "_due_to_convection",
    "tntd": "_due_to_diffusion",
    "tntmp": "_due_to_model_physics",
    "tntpbl": "_due_to_boundary_layer_mixing",
    "tntr": "_due_to_radiative_heating",
    "tntrlcs": "_due_to_longwave_heating_assuming_clear_sky",
    "tntrscs": "_due_to_shortwave_heating_assuming_clear_sky",
    "tntscp": "_due_to_stratiform_cloud_and_precipitation",
    "tntscpbl": "_due_to_stratiform_cloud_and_precipitation_and_boundary_layer_mixing",
}
OBSERVED_TEMPERATURE_METHODS = {
    "area: depth: time: mean where sea",
    "area: mean time: maximum",
    "area: mean time: maximum within days time: mean over days",
    "area: mean time: minimum",
    "area: mean time: minimum within days time: mean over days",
    "area: mean time: point",
    "area: mean where air time: point",
    "area: mean where land time: mean",
    "area: mean where sea time: mean",
    "area: mean where sea time: point",
    "area: time: mean",
    "area: time: mean (over land and sea ice)",
    "area: time: mean where air",
    "area: time: mean where ice_sheet",
    "area: time: mean where sea_ice (mask=siconc)",
    "longitude: time: mean where air",
}


@fix("add_temperature_units_metadata")
def add_temperature_units_metadata(ds, var):
    """Distinguish absolute temperatures from increments per unit time."""
    v = ds.variables.get(var)
    if v is None or getattr(v, "units_metadata", ""):
        return False

    # Distinguish temperature tendencies from absolute temperatures using reviewed metadata.
    if var in TENDENCIES:
        expected = ("tendency_of_air_temperature" + TENDENCIES[var], "K s-1")
        value = "temperature: difference"
    else:
        expected = TEMPERATURES.get(var)
        value = "temperature: on_scale"
    current = (getattr(v, "standard_name", None), getattr(v, "units", None))
    if (
        current != expected
        or getattr(v, "cell_methods", "") not in OBSERVED_TEMPERATURE_METHODS
    ):
        raise ValueError(f"Unreviewed temperature semantics for {var}: {current!r}")

    # Add the qualifier only; do not convert or rewrite temperature values.
    v.setncattr("units_metadata", value)
    return True


@fix("describe_numeric_basin")
def describe_numeric_basin(ds):
    """Describe the observed one-dimensional IPSL basin mask with categorical flags."""
    v = ds.variables.get("basin")
    expected_name = "Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)"
    if (
        v is None
        or v.dimensions != ("basin",)
        or v.dtype.kind != "f"
        or getattr(v, "standard_name", "") != "region"
        or getattr(v, "long_name", "") != expected_name
        or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
    ):
        raise ValueError("Unexpected basin coordinate or code mapping")
    codes = np.asarray([1, 2, 3], dtype=v.dtype)
    current = np.ma.asarray(v[:])
    if np.ma.getmaskarray(current).any() or not np.array_equal(current, codes):
        raise ValueError("Unexpected basin values or ordering")
    meanings = "global_ocean atlantic_ocean indo_pacific_ocean"

    # The source long_name identifies all three codes. Keep any conflicting flags for review.
    units = getattr(v, "units", None)
    values_ok = "flag_values" in v.ncattrs()
    meanings_ok = "flag_meanings" in v.ncattrs()
    if (
        units not in (None, "1")
        or "flag_masks" in v.ncattrs()
        or values_ok
        and (
            np.asarray(v.flag_values).dtype.newbyteorder("=")
            != v.dtype.newbyteorder("=")
            or not np.array_equal(v.flag_values, codes)
        )
        or meanings_ok
        and v.flag_meanings != meanings
    ):
        raise ValueError("Conflicting existing basin metadata")
    if not values_ok:
        v.setncattr("flag_values", codes)
    if not meanings_ok:
        v.setncattr("flag_meanings", meanings)
    if units == "1":
        v.delncattr("units")
    return not (values_ok and meanings_ok and units is None)


BASIN_MASK_MEANINGS = (
    "global_land southern_ocean atlantic_ocean pacific_ocean arctic_ocean "
    "indian_ocean mediterranean_sea black_sea hudson_bay baltic_sea red_sea"
)
BASIN_MASK_FILL = np.int32(-2147483647)


def _basin_mask_values(ds):
    """Inspect only the approved 2-D region mask; never reinterpret a basin axis."""
    v = ds.variables.get("basin")
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or getattr(ds, "branded_variable", "") != "basin_ti-u-hxy-u"
        or getattr(ds, "grid_label", "") != "g112"
        or v is None
        or v.dimensions != ("y", "x")
        or v.shape != (332, 362)
        or v.dtype.kind not in "fiu"
        or getattr(v, "standard_name", "") != "region"
        or getattr(v, "long_name", "") != "Region Selection Index"
        or getattr(v, "units", None) not in (None, "1")
        or any(
            a in v.ncattrs()
            for a in ("scale_factor", "add_offset", "_Unsigned", "flag_masks")
        )
    ):
        raise ValueError("Expected the reviewed 2-D basin region selection mask")

    # Preserve the missing-point mask even if an earlier recipe disabled automatic
    # masking. In particular, float fill=1e20 must never be cast straight to int32.
    a = np.ma.asarray(v[:])
    raw = np.asarray(a.data)
    missing = np.ma.getmaskarray(a).copy()
    for attr in ("_FillValue", "missing_value"):
        if attr in v.ncattrs():
            for fill in np.asarray(v.getncattr(attr)).reshape(-1):
                missing |= np.isnan(raw) if np.isnan(fill) else raw == fill
    values = raw[~missing]
    if (
        not np.isfinite(values).all()
        or np.any(values != np.floor(values))
        or np.any(values < 0)
        or np.any(values > 10)
    ):
        raise ValueError(
            "Basin mask contains non-integer values or codes outside 0..10"
        )
    data = np.full(v.shape, BASIN_MASK_FILL, dtype="i4")
    data[~missing] = values.astype("i4")
    return v, data, missing


def _verify_basin_mask(source, output):
    import netCDF4

    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        _, expected, mask = _basin_mask_values(src)
        v, actual, actual_mask = _basin_mask_values(dst)
        if (
            v.dtype != np.dtype("i4")
            or not np.array_equal(actual_mask, mask)
            or not np.array_equal(actual, expected)
            or "units" in v.ncattrs()
            or np.asarray(getattr(v, "flag_values", [])).dtype != np.dtype("i4")
            or not np.array_equal(v.flag_values, np.arange(11, dtype="i4"))
            or getattr(v, "flag_meanings", "") != BASIN_MASK_MEANINGS
        ):
            raise ValueError(
                "Integer basin mask did not retain its codes, mask and approved flags"
            )


@fix(
    "convert_basin_region_mask",
    verify_data=True,
    data_targets=lambda p: {"basin"},
    verify_final=_verify_basin_mask,
)
def convert_basin_region_mask(ds):
    """Store the user-approved 0..10 mask as int32, without recoding any region."""
    v, data, _ = _basin_mask_values(ds)
    attrs = {a: v.getncattr(a) for a in v.ncattrs()}
    changed = False

    # 1. A storage-type change requires a clean, single grouped rewrite. Prepare
    # it in memory and let apply verify/replace the file atomically. No backup
    # variable or obsolete float mask is left in the published NetCDF file.
    if v.dtype != np.dtype("i4"):
        if not hasattr(ds, "replaceVariable"):
            raise ValueError("Run this storage-type correction through fix_tool apply")
        old = v
        v = ds.replaceVariable(
            "basin", "i4", old.dimensions, fill_value=BASIN_MASK_FILL
        )
        from .layouts import _copy_attrs

        _copy_attrs(
            getattr(old, "_header", old),
            v,
            skip=(
                "_FillValue",
                "missing_value",
                "flag_values",
                "flag_meanings",
                "units",
                "valid_min",
                "valid_max",
                "valid_range",
                "actual_range",
            ),
        )
        v[:] = data
        if "missing_value" in attrs:
            v.setncattr("missing_value", BASIN_MASK_FILL)
        changed = True

    # 2. Set typed flag values and the exact approved meaning for each integer.
    # No units are added. The standard_name=region checker warning may persist.
    wanted = dict(
        standard_name="region",
        long_name="Region Selection Index",
        flag_values=np.arange(11, dtype="i4"),
        flag_meanings=BASIN_MASK_MEANINGS,
    )
    if "units" in v.ncattrs():
        if v.units != "1":
            raise ValueError("Unexpected physical units on the basin region mask")
        v.delncattr("units")
        changed = True
    for a, value in wanted.items():
        current = v.getncattr(a) if a in v.ncattrs() else None
        if (
            current is None
            or np.asarray(current).dtype != np.asarray(value).dtype
            or not np.array_equal(current, value)
        ):
            v.setncattr(a, value)
            changed = True
    return changed


@fix("drop_olevel_bounds_units")
def drop_olevel_bounds_units(ds):
    bounds = ds.variables.get("olevel_bounds")
    if bounds is None or "units" not in bounds.ncattrs():
        return False
    axis = bounds.dimensions[0] if bounds.ndim == 2 else None
    parent = ds.variables.get(axis) if axis in ("olevel", "lev") else None
    if (
        parent is None
        or getattr(parent, "bounds", None) != "olevel_bounds"
        or parent.dimensions != (axis,)
        or bounds.shape[-1] != 2
        or getattr(parent, "units", None) != "m"
        or bounds.units != parent.units
    ):
        raise ValueError(
            "olevel_bounds units are not a reviewed duplicate of the parent units"
        )
    bounds.delncattr("units")
    return True


TIME_NAMES = {
    "tavg": "Time Intervals",
    "tmax": "Time Intervals",
    "tmin": "Time Intervals",
    "tpt": "Time Points",
    "tclm": "Monthly Climatology",
    "tmaxavg": "Monthly Mean Daily Statistics",
    "tminavg": "Monthly Mean Daily Statistics",
}


@fix("set_reviewed_time_long_name")
def set_reviewed_time_long_name(ds, value):
    v = ds.variables.get("time")
    if (
        v is None
        or v.dimensions != ("time",)
        or getattr(v, "standard_name", "") != "time"
        or TIME_NAMES.get(getattr(ds, "temporal_label", "")) != value
    ):
        raise ValueError("Time coordinate does not match the reviewed temporal label")
    return set_var_attr_if_matches(ds, "time", "long_name", "Time axis", value)


COORDINATE_LONG_NAMES = {
    "dbze": ("", "CloudSat Simulator Equivalent Radar Reflectivity Factor"),
    "scatratio": ("", "lidar backscattering ratio"),
    "lat": ("", "Latitude"),
    "nav_lat": ("Latitude", "latitude"),
    "nav_lon": ("Longitude", "longitude"),
}


@fix("set_reviewed_coordinate_long_name")
def set_reviewed_coordinate_long_name(ds, coordinate, value):
    observed = COORDINATE_LONG_NAMES.get(coordinate)
    if observed is None or observed[1] != value:
        raise ValueError(f"Unreviewed long_name for {coordinate}: {value!r}")
    expected, wanted = observed
    if coordinate in ("dbze", "scatratio", "lat"):
        v = ds.variables.get(coordinate)
        if v is None or v.dimensions != (coordinate,):
            raise ValueError(f"Expected a one-dimensional coordinate {coordinate}")
    else:
        names = [name for name in (coordinate, wanted) if name in ds.variables]
        if len(names) != 1:
            raise ValueError(f"Expected one reviewed alias for {wanted}")
        v = ds[names[0]]
        if v.ndim != 2 or getattr(v, "standard_name", "") != wanted:
            raise ValueError(f"Unexpected curvilinear coordinate for {wanted}")
    return set_var_attr_if_matches(ds, v.name, "long_name", expected, wanted)


def _rename_fixed_dimension(ds, source, target, size):
    if source in ds.variables or target in ds.variables:
        raise ValueError(f"Cannot rename {source}: coordinate variable collision")
    dim = ds.dimensions.get(source)
    if dim is None or dim.isunlimited() or len(dim) != size or target in ds.dimensions:
        raise ValueError(
            f"Cannot rename {source} to {target}: unexpected length or collision"
        )
    ds.renameDimension(source, target)
    return True


@fix("rename_scalar_strlen")
def rename_scalar_strlen(ds, coordinate):
    v = ds.variables.get(coordinate)
    # C099 may already have removed duplicate type(type,str_len). Its sector
    # alias carries the same labels, so normalize that surviving dimension.
    if coordinate == "type" and v is None and "sector" in ds.variables:
        candidate = ds["sector"]
        if candidate.dimensions[:1] == ("type",):
            v = candidate
    if (
        coordinate not in ("seasurface", "type", "sector")
        or v is None
        or v.dtype != np.dtype("S1")
    ):
        raise ValueError("Expected a reviewed character scalar")
    if v.dimensions[-1:] == ("strlen",):
        return False
    text_axis = (
        coordinate == "sector" or coordinate == "type" and v.dimensions[:1] == ("type",)
    )
    if v.dimensions[-1:] != ("str_len",) or v.ndim != (2 if text_axis else 1):
        raise ValueError(f"Unexpected string length dimension on {coordinate}")
    return _rename_fixed_dimension(ds, "str_len", "strlen", 255)


@fix("rename_ocean_vertices_dimension")
def rename_ocean_vertices_dimension(ds):
    # Both geographic bounds share the same four-vertex dimension. Accept names
    # on either side of C067 so the recipes work in either order or on replay.
    bounds = []
    for aliases in (
        ("bounds_nav_lat", "vertices_latitude"),
        ("bounds_nav_lon", "vertices_longitude"),
    ):
        names = [name for name in aliases if name in ds.variables]
        if len(names) != 1:
            raise ValueError(f"Expected one ocean vertex variable among {aliases}")
        bounds.append(ds[names[0]])
    if any(v.ndim != 3 or v.shape[-1] != 4 for v in bounds):
        raise ValueError("Expected two arrays of four ocean vertices per cell")
    trailing = {v.dimensions[-1] for v in bounds}
    if trailing == {"vertices"}:
        return False
    if trailing != {"nvertex"}:
        raise ValueError("Ocean vertex variables do not share nvertex")
    return _rename_fixed_dimension(ds, "nvertex", "vertices", 4)


def _verify_redundant_text_label(source, output, var):
    import netCDF4
    from .final_coordinates import ICE_PRODUCTS, _verify_ice_passages

    with netCDF4.Dataset(output) as ds:
        if any(name in ds.variables for name in (var, var + "_label")):
            raise ValueError("The redundant label variable was not removed")
        _label_rows(ds["sector"])
        ice = var == "line" and getattr(ds, "variable_id", "") in ICE_PRODUCTS
    if ice:
        _verify_ice_passages(source, output)


@fix(
    "rename_redundant_text_label",
    verify_data=True,
    data_targets=lambda p: {"sector"},
    verify_final=_verify_redundant_text_label,
)
def rename_redundant_text_label(ds, var):
    """Remove duplicate labels only after sector retains their ordered information.

    The registered name is kept so older fix plans still call this reviewed method.
    """
    from .coordinates import add_text_coordinate_alias
    from .reviewed_structures import delete_variables
    from .final_coordinates import ICE_PRODUCTS, fix_ice_passage_sector

    if var not in ("line", "type"):
        raise ValueError("Unreviewed redundant text coordinate")
    redundant = [name for name in (var, var + "_label") if name in ds.variables]
    if not redundant:
        return False

    # The four ice passages have a separate authoritative repair for corrupt
    # labels. Delegate rather than guessing labels or depending on recipe order.
    if var == "line" and getattr(ds, "variable_id", "") in ICE_PRODUCTS:
        return fix_ice_passage_sector(ds)

    # Validate all source labels before creating or deleting anything.
    decoded = []
    for name in redundant:
        v = ds[name]
        if v.ndim != 2 or v.dimensions[0] != var:
            raise ValueError("Expected character labels on their namesake dimension")
        decoded.append(_label_rows(v)[1])
    if any(labels != decoded[0] for labels in decoded):
        raise ValueError("Redundant labels disagree; no variable may be deleted")
    if "sector" not in ds.variables:
        add_text_coordinate_alias(ds, redundant[0], "sector")
    sector = ds["sector"]
    if sector.dimensions[:1] != (var,) or _label_rows(sector)[1] != decoded[0]:
        raise ValueError(
            "sector does not retain the same ordered labels; no variable may be deleted"
        )

    # Retarget actual references and retain the indexing dimension and data order.
    for name in redundant:
        _replace_references(ds, name, "sector")
    for v in ds.variables.values():
        if "coordinates" in v.ncattrs():
            v.setncattr("coordinates", " ".join(dict.fromkeys(v.coordinates.split())))
    return delete_variables(ds, redundant)


def _label_rows(v):
    v.set_auto_maskandscale(False)
    v.set_auto_chartostring(False)
    a = np.asarray(v[:])
    if a.dtype != np.dtype("S1") or a.ndim != 2:
        raise ValueError("Expected a two-dimensional character coordinate")
    labels = [row.tobytes().decode("ascii").rstrip("\x00 ") for row in a]
    if any(not s or any(ord(c) < 32 or ord(c) > 126 for c in s) for s in labels):
        raise ValueError("Corrupted labels require an authoritative ordered source")
    return a, labels


def _verify_text_padding(source, output, coordinate="sector"):
    import netCDF4

    with netCDF4.Dataset(output) as ds:
        raw, labels = _label_rows(ds[coordinate])
        if any(
            row.tobytes() != label.encode("ascii").ljust(raw.shape[-1], b" ")
            for row, label in zip(raw, labels)
        ):
            raise ValueError("Character padding did not survive the combined fixes")


@fix(
    "normalize_text_padding",
    verify_data=True,
    data_targets=lambda p: {p.get("coordinate", "sector")},
    verify_final=_verify_text_padding,
)
def normalize_text_padding(ds, coordinate="sector"):
    """Replace trailing NUL padding with spaces. Preserve every label and its order."""
    if coordinate != "sector" or coordinate not in ds.variables:
        raise ValueError("Expected the reviewed sector coordinate")
    v = ds[coordinate]
    raw, labels = _label_rows(v)

    # Rebuild trailing padding with spaces while preserving every label and its order.
    data = np.full(raw.shape, b" ", dtype="S1")
    for row, label in zip(data, labels):
        encoded = label.encode("ascii")
        row[: len(encoded)] = np.frombuffer(encoded, dtype="S1")
    if np.array_equal(raw, data):
        return False
    v[:] = data
    return True


@fix("fix_registry_comment")
def fix_registry_comment(ds, var, replace_expected=False):
    """Remove only the audited Markdown escapes from a registry-compatible comment."""

    # Load the audited observed/expected text for this exact branded variable.
    entries = json.loads(Path(__file__).with_name("comment_specs.json").read_text())
    entry = entries.get(getattr(ds, "branded_variable", ""))
    if entry is None or entry["var"] != var or var not in ds.variables:
        raise ValueError("No audited comment normalization for this branded variable")
    if bool(entry.get("replace_expected", False)) != replace_expected:
        raise ValueError(
            "Replacing a scientific comment requires the approved replacement recipe"
        )
    v = ds[var]
    current = getattr(v, "comment", "")
    variants = entry["variants"]
    if any(current == item["value"] for item in variants):
        return False

    # Refuse changed source text instead of overwriting an unrelated comment.
    selected = next((item for item in variants if current == item["observed"]), None)
    if selected is None:
        raise ValueError("The current comment differs from the audited value")
    v.setncattr("comment", selected["value"])
    return True


@fix("drop_unused_horizontal_bounds")
def drop_unused_horizontal_bounds(ds, var):
    """Detach bounds only from an unreferenced auxiliary left by a reduction."""
    if var not in ("nav_lat", "nav_lon") or var not in ds.variables:
        raise ValueError("Expected an orphan original horizontal auxiliary")
    v = ds[var]
    if "bounds" not in v.ncattrs():
        return False
    if getattr(ds, "horizontal_label", "") not in ("hm", "hyb"):
        raise ValueError(
            "Only the reviewed horizontally reduced products are supported"
        )

    # Keep any bounds reference still attached to an active coordinate.
    for other in ds.variables.values():
        if var in getattr(other, "coordinates", "").split():
            raise ValueError(f"{var} is still an active auxiliary coordinate")
    if v.dimensions == (var,):
        raise ValueError("Do not remove bounds from a dimension coordinate")
    v.delncattr("bounds")
    return True
