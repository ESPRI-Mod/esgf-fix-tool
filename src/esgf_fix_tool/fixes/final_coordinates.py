"""Guarded physical coordinate corrections approved after the latest QA review."""

import netCDF4
import numpy as np

from . import fix
from .bounds import _specs, rename_bounds_variable
from .layouts import _copy_attrs, _raw
from .reviewed_structures import delete_variables, finite, set_attrs

ICE_PASSAGES = (
    "fram_strait",
    "canadian_archipelego",
    "barents_opening",
    "bering_strait",
)
ICE_PRODUCTS = {"siareaacrossline", "simassacrossline", "sisnmassacrossline"}
ICE_RADII = np.array([5.0, 15.0, 25.0, 35.0, 50.0, 75.0])
THERMAL_EDGES = np.array(
    [
        0.0,
        0.001,
        0.004,
        0.010,
        0.022,
        0.045,
        0.092,
        0.186,
        0.374,
        0.749,
        1.500,
        2.0,
        3.001,
        4.502,
        7.505,
        13.511,
        25.523,
        49.546,
        90.0,
    ]
)


def _verify_ice_passages(source, output):
    with netCDF4.Dataset(output) as ds:
        v = ds["sector"]
        _raw(v)
        labels = [row.tobytes().decode("ascii").rstrip(" ") for row in np.asarray(v[:])]
        if (
            v.dimensions != ("line", "strlen")
            or v.shape != (4, 21)
            or labels != list(ICE_PASSAGES)
            or "line" in ds.variables
            or "line_label" in ds.variables
            or ds[ds.variable_id].coordinates != "sector"
        ):
            raise ValueError(
                "Ice passage labels/order did not survive the combined corrections"
            )


@fix(
    "fix_ice_passage_sector",
    verify_data=True,
    data_targets=lambda p: {"sector"},
    verify_final=_verify_ice_passages,
)
def fix_ice_passage_sector(ds):
    """Write the four explicitly supplied labels at their original line indices."""
    main_name = getattr(ds, "variable_id", "")
    main = ds.variables.get(main_name)
    dim = "line" if "line" in ds.dimensions else "siline"
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or main_name not in ICE_PRODUCTS
        or main is None
        or main.dimensions != ("time", dim)
        or main.shape[1] != 4
        or "sector" not in ds.variables
        or ds["sector"].dtype != np.dtype("S1")
        or ds["sector"].dimensions[:1] != (dim,)
        or ds["sector"].ndim != 2
    ):
        raise ValueError(
            "Expected one of the three reviewed four-passage sea-ice diagnostics"
        )
    redundant = [n for n in ("line", "line_label") if n in ds.variables]
    for n in redundant:
        if ds[n].dtype != np.dtype("S1") or ds[n].ndim != 2 or ds[n].shape[0] != 4:
            raise ValueError("Unexpected variable on the passage index")
    # The string length may already have been renamed by C087. Resize only a
    # private dimension used by these known labels, never another text array.
    old_length = ds["sector"].dimensions[-1]
    changed = False
    if "strlen" in ds.dimensions and len(ds.dimensions["strlen"]) != 21:
        if any(
            "strlen" in v.dimensions and n not in {"sector", *redundant}
            for n, v in ds.variables.items()
        ):
            raise ValueError(
                "Cannot resize a string dimension shared with an unreviewed variable"
            )
        retired = "__old_ice_strlen"
        if retired in ds.dimensions:
            raise ValueError("Temporary dimension collision")
        ds.renameDimension("strlen", retired)
        old_length = retired
        changed = True
    if dim != "line":
        ds.renameDimension(dim, "line")
        changed = True
    if "strlen" not in ds.dimensions:
        ds.createDimension("strlen", 21)
    data = np.full((4, 21), b" ", dtype="S1")
    for row, label in zip(data, ICE_PASSAGES):
        row[: len(label)] = np.frombuffer(label.encode("ascii"), dtype="S1")
    sector = ds["sector"]
    _raw(sector)
    if sector.dimensions != ("line", "strlen") or sector.shape != data.shape:
        old = sector
        sector = ds.replaceVariable(
            "sector", "S1", ("line", "strlen"), fill_value=False
        )
        _copy_attrs(getattr(old, "_header", old), sector, skip=["_FillValue", "units"])
        sector[:] = data
        changed = True
    elif not np.array_equal(sector[:], data):
        sector[:] = data
        changed = True
    changed = (
        set_attrs(sector, dict(standard_name="region", long_name="ocean passage"))
        or changed
    )
    if "units" in sector.ncattrs():
        sector.delncattr("units")
        changed = True
    if getattr(main, "coordinates", "") != "sector":
        main.setncattr("coordinates", "sector")
        changed = True
    # Redundant corrupted label arrays are not retained alongside the repaired sector.
    for v in ds.variables.values():
        if "coordinates" in v.ncattrs():
            text = " ".join(t for t in v.coordinates.split() if t not in redundant)
            if text != v.coordinates:
                v.setncattr("coordinates", text)
    changed = delete_variables(ds, redundant) or changed
    if (old_length != "strlen" and old_length in ds.dimensions
            and not any(old_length in v.dimensions for v in ds.variables.values())):
        ds.deleteDimension(old_length)
        changed = True
    return changed


def _verify_radius(source, output):
    with netCDF4.Dataset(output) as ds:
        if ds["effectRadIc"].units != "micron" or not np.array_equal(
            ds["effectRadIc"][:], ICE_RADII
        ):
            raise ValueError("The approved ice radius values/units were not retained")


@fix(
    "convert_ice_radius",
    verify_data=True,
    data_targets=lambda p: {"effectRadIc"},
    verify_final=_verify_radius,
)
def convert_ice_radius(ds):
    """Recognize raw metres or already-correct microns before a single conversion."""
    v = ds.variables.get("effectRadIc")
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or getattr(ds, "variable_id", "") != "jpdftaureicemodis"
        or v is None
        or v.dimensions != ("effectRadIc",)
        or v.dtype != np.dtype("f8")
        or v.shape != (6,)
        or getattr(v, "units", "") not in ("microne", "micron", "m")
        or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
        or getattr(v, "bounds", "")
    ):
        raise ValueError(
            "Unexpected ice effective radius representation or existing bounds"
        )

    # Recognize source metres and already converted microns to prevent double scaling.
    current = finite(v)
    if not (
        (
            v.units in ("microne", "micron")
            and np.allclose(current, ICE_RADII, rtol=1e-12, atol=1e-12)
        )
        or np.allclose(current * 1e6, ICE_RADII, rtol=1e-12, atol=1e-12)
    ):
        raise ValueError(
            "effectRadIc values do not match the approved metre-to-micron conversion"
        )

    # Write only the approved six coordinate values and their corrected unit.
    changed = False
    if not np.array_equal(current, ICE_RADII):
        v[:] = ICE_RADII
        changed = True
    return set_attrs(v, {"units": "micron"}) or changed


def _coordinate_bounds(ds, coordinate, pairs):
    """Add explicit reviewed pairs, preserving every existing coordinate value."""
    v = ds[coordinate]
    name = coordinate + "_bnds"
    old = getattr(v, "bounds", "")
    if old not in ("", name, coordinate + "_bounds"):
        raise ValueError("Unexpected bounds reference")
    if "bnds" in ds.dimensions and (
        len(ds.dimensions["bnds"]) != 2 or ds.dimensions["bnds"].isunlimited()
    ):
        raise ValueError("Expected fixed bnds=2")

    # Check existing bounds before adding or renaming anything.
    existing = old or name
    if existing in ds.variables:
        b = ds[existing]
        if (
            b.dimensions != (coordinate, "bnds")
            or b.shape != pairs.shape
            or not np.array_equal(finite(b), pairs)
        ):
            raise ValueError(
                "Existing bounds disagree with the approved physical definition"
            )
    elif old:
        raise ValueError("The existing bounds reference points to an absent variable")

    # Reuse matching bounds or create the approved pairs, then attach the reference.
    changed = False
    if old == coordinate + "_bounds":
        changed = rename_bounds_variable(ds, coordinate, old, name)
    if name not in ds.variables:
        if "bnds" not in ds.dimensions:
            ds.createDimension("bnds", 2)
        ds.createVariable(name, "f8", (coordinate, "bnds"))[:] = pairs
        changed = True
    return set_attrs(v, {"bounds": name}) or changed


@fix("add_reviewed_coordinate_bounds", verify_data=True)
def add_reviewed_coordinate_bounds(ds, coordinate, add_bounds=True):
    """Dispatch only the approved thermal and ISCCP pressure bounds definitions."""
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or coordinate not in ds.variables
    ):
        raise ValueError("Missing reviewed IPSL coordinate")
    v = ds[coordinate]
    if (
        v.dimensions != (coordinate,)
        or v.dtype != np.dtype("f8")
        or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
    ):
        raise ValueError("Expected a one-dimensional double coordinate")
    values = finite(v)
    if coordinate == "depth" and getattr(ds, "variable_id", "") == "tsl":
        if (
            values.shape != (18,)
            or getattr(v, "units", "") != "m"
            or not np.all(np.diff(values) > 0)
        ):
            raise ValueError("Expected 18 increasing thermal soil levels in metres")
        # This is the model-specific scheme supplied by the user, not generic midpoints.
        edges = np.empty(19)
        edges[0] = 0.0
        edges[1] = 2.0 * values[0]
        edges[2:10] = 0.5 * (values[1:9] + values[2:10])
        edges[10] = 2.0 * values[10] - 2.0
        edges[11] = 2.0
        edges[12:18] = 0.5 * (values[11:17] + values[12:18])
        edges[18] = 90.0
        if not np.allclose(edges, THERMAL_EDGES, rtol=0, atol=0.001):
            raise ValueError(
                "Thermal layer edges differ from the supplied ORCHIDEE reference"
            )
    elif (
        coordinate == "plev"
        and getattr(ds, "branded_variable", "") == "clisccp_tavg-p7c-hxy-air"
    ):
        spec = _specs()["bounds"]["plev"]
        units = getattr(v, "units", "")
        centers = np.asarray(spec["centers"])
        # This simulation stores the literal DR values in Pa even when XIOS labels
        # them mb (or a previous tool version relabelled them hPa). Do not scale them.
        if (
            units in ("Pa", "hPa", "mb")
            and values.shape == (7,)
            and np.allclose(values, centers, rtol=0, atol=1.0)
        ):
            factor, target_units = 1.0, "Pa"
        elif (
            units in ("hPa", "mb")
            and values.shape == (7,)
            and np.allclose(values * 100.0, centers, rtol=0, atol=1.0)
        ):
            factor, target_units = 100.0, "hPa"
        else:
            raise ValueError(
                "plev7c centers/units/order differ from the explicit DR values: "
                f"units={units!r}, shape={values.shape}, values={values.tolist()}, "
                f"expected Pa={centers.tolist()}"
            )
        edges = np.asarray(spec["edges"]) / factor
    else:
        raise ValueError("No approved automatic bounds for this coordinate/product")
    pairs = np.column_stack((edges[:-1], edges[1:]))
    if (
        not (np.all(np.diff(edges) > 0) or np.all(np.diff(edges) < 0))
        or np.any(values < pairs.min(axis=1))
        or np.any(values > pairs.max(axis=1))
    ):
        raise ValueError(
            "The coordinate values are not enclosed by the approved bounds"
        )
    # C095 selects metadata only; excluding C103c must not reintroduce its bounds
    # operation indirectly through the separate units diagnostic.
    if not add_bounds and coordinate != "plev":
        raise ValueError("Metadata-only pressure correction requires plev7c")
    changed = _coordinate_bounds(ds, coordinate, pairs) if add_bounds else False
    if coordinate == "plev":
        changed = set_attrs(v, {"units": target_units}) or changed
    return changed
