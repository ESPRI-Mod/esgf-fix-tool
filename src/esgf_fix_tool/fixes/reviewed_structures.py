"""Physical definitions explicitly approved by the user after the November QA."""

import netCDF4
import numpy as np

from . import fix
from .coordinates import (
    REFERENCE_ATTRS,
    _replace_references,
    reference_matches,
    rename_coordinate,
)
from .layouts import _copy_attrs, _raw
from ..progress import io_progress


def unreferenced(ds, names, ignoring=()):
    """Do not discard an array still used by a retained CF reference."""
    for name in names:
        for var, v in ds.variables.items():
            if var in names or var in ignoring:
                continue
            for attr in REFERENCE_ATTRS:
                text = getattr(v, attr, "")
                if isinstance(text, str) and reference_matches(text, attr, name):
                    raise ValueError(f"{name} is still referenced by {var}:{attr}")


def delete_variables(ds, names):
    names = set(names) & ds.variables.keys()
    if not names:
        return False
    unreferenced(ds, names)
    if not hasattr(ds, "deleteVariable"):
        raise ValueError("Physical NetCDF deletion requires fix_tool apply")
    for name in sorted(names):
        ds.deleteVariable(name)
    return True


def set_attrs(v, attrs):
    changed = False
    for name, value in attrs.items():
        if getattr(v, name, None) != value:
            v.setncattr(name, value)
            changed = True
    return changed


def finite(v):
    a = np.ma.asarray(v[:])
    if np.ma.getmaskarray(a).any() or not np.isfinite(a).all():
        raise ValueError(f"Missing or non-finite coordinate {v.name}")
    return np.asarray(a)


@fix("fix_orchidee_soil_depth", verify_data=True)
def fix_orchidee_soil_depth(ds):
    """Keep ORCHIDEE's 11 node depths and use approved midpoint layer edges."""
    name = "depth" if "depth" in ds.variables else "solay"
    v = ds.variables.get(name)
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or getattr(ds, "variable_id", "") not in ("mrsfl", "mrsll", "mrsol", "slthick")
        or v is None
        or v.dimensions != (name,)
        or v.shape != (11,)
        or v.dtype.kind != "f"
        or getattr(v, "units", "") not in ("1", "m")
        or any(a in v.ncattrs() for a in ("scale_factor", "add_offset"))
    ):
        raise ValueError("Expected the reviewed eleven ORCHIDEE depth nodes")
    nodes = finite(v).astype("f8")
    if (
        not np.all(np.diff(nodes) > 0)
        or not np.isclose(nodes[0], 0.0)
        or not np.isclose(nodes[-1], 2.0)
    ):
        raise ValueError(
            "Soil coordinate is not the approved increasing 0..2 metre node axis"
        )

    # No node is replaced or rescaled. The user supplied the midpoint convention.
    edges = np.r_[nodes[0], (nodes[:-1] + nodes[1:]) / 2.0, nodes[-1]]
    pairs = np.column_stack((edges[:-1], edges[1:]))
    ref = getattr(v, "bounds", "")
    if ref not in ("", "depth_bnds"):
        raise ValueError("Unexpected existing soil bounds reference")
    if "depth_bnds" in ds.variables:
        b = ds["depth_bnds"]
        if b.dimensions != (name, "bnds") or not np.array_equal(finite(b), pairs):
            raise ValueError(
                "Existing soil bounds conflict with the approved node midpoint convention"
            )
    if "bnds" in ds.dimensions and (
        len(ds.dimensions["bnds"]) != 2 or ds.dimensions["bnds"].isunlimited()
    ):
        raise ValueError("Soil bounds require a fixed size-two bnds dimension")

    changed = rename_coordinate(ds, ["solay"], "depth") if name == "solay" else False
    changed = (
        set_attrs(
            ds["depth"],
            dict(
                standard_name="depth",
                units="m",
                positive="down",
                axis="Z",
                long_name="depth",
                bounds="depth_bnds",
            ),
        )
        or changed
    )
    if "depth_bnds" not in ds.variables:
        if "bnds" not in ds.dimensions:
            ds.createDimension("bnds", 2)
        ds.createVariable("depth_bnds", "f8", ("depth", "bnds"))[:] = pairs
        changed = True
    return changed


BASIN_LABELS = ("global_ocean", "atlantic_arctic_ocean", "indian_pacific_ocean")
OLD_BASIN_LABELS = ("global_ocean", "atlantic_ocean", "indo_pacific_ocean")
BASIN_PRODUCTS = {
    "hfbasin",
    "hfbasinpmadv",
    "hfbasinpmdiff",
    "htovgyre",
    "htovovrt",
    "msfty",
    "sltbasin",
    "sltovgyre",
    "sltovovrt",
}


def _read_sector(v):
    _raw(v)
    if v.dtype != np.dtype("S1") or v.ndim != 2 or v.dimensions[0] != "basin":
        raise ValueError("Expected character sector labels on the basin dimension")
    return [row.tobytes().decode("ascii").rstrip("\0 ") for row in np.asarray(v[:])]


def _verify_sector(source, output):
    with netCDF4.Dataset(output) as ds:
        v = ds["sector"]
        if (
            v.dimensions != ("basin", "strlen")
            or v.shape != (3, 21)
            or sorted(_read_sector(v)) != sorted(BASIN_LABELS)
            or getattr(v, "standard_name", "") != "region"
        ):
            raise ValueError("The approved basin sector representation was not written")
        if "basin" in ds.variables:
            index = ds["basin"]
            if getattr(index, "units", "") != "1" or any(
                a in index.ncattrs()
                for a in ("standard_name", "flag_values", "flag_meanings", "flag_masks")
            ):
                raise ValueError(
                    "Numeric basin must be an index; region labels belong to sector"
                )


@fix(
    "fix_basin_sector",
    verify_data=True,
    data_targets=lambda p: {"sector"},
    verify_final=_verify_sector,
)
def fix_basin_sector(ds):
    """Expose sector regions and retain numeric basin values as dimensionless indices."""
    main_name = getattr(ds, "variable_id", "")
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or main_name not in BASIN_PRODUCTS
        or "basin" not in ds.dimensions
        or len(ds.dimensions["basin"]) != 3
        or main_name not in ds.variables
        or "basin" not in ds[main_name].dimensions
    ):
        raise ValueError(
            "Expected a reviewed three-basin diagnostic, not the 2-D basin mask"
        )
    numeric = ds.variables.get("basin")
    if numeric is not None:
        if (
            numeric.dimensions != ("basin",)
            or numeric.dtype.kind not in "fiu"
            or getattr(numeric, "long_name", "")
            not in (
                "Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)",
                "Ocean Basin Index",
            )
            or getattr(numeric, "standard_name", "") not in ("", "region")
            or getattr(numeric, "units", "1") != "1"
            or any(a in numeric.ncattrs() for a in ("scale_factor", "add_offset"))
        ):
            raise ValueError("Unreviewed numeric basin representation")
        codes = finite(numeric)
        if sorted(codes.tolist()) != [1, 2, 3]:
            raise ValueError(
                "Basin coordinate must contain each approved code 1,2,3 once"
            )
        wanted = [BASIN_LABELS[int(code) - 1] for code in codes]
        old = [OLD_BASIN_LABELS[int(code) - 1] for code in codes]
    elif "sector" in ds.variables:
        wanted = _read_sector(ds["sector"])
        if sorted(wanted) != sorted(BASIN_LABELS):
            raise ValueError(
                "Missing numeric code source for unapproved existing basin labels"
            )
        old = wanted
    else:
        raise ValueError("Neither verified basin codes nor sector labels exist")
    if "sector" in ds.variables and _read_sector(ds["sector"]) not in (old, wanted):
        raise ValueError("Existing sector labels disagree with the basin code order")
    if "strlen" in ds.dimensions and len(ds.dimensions["strlen"]) != 21:
        raise ValueError(
            "Approved basin sector requires strlen=21; existing dimension conflicts"
        )

    # Numeric code order is retained. Never reorder scientific basin slices.
    changed = False
    if (
        "sector" not in ds.variables
        or ds["sector"].dimensions != ("basin", "strlen")
        or ds["sector"].shape != (3, 21)
        or _read_sector(ds["sector"]) != wanted
    ):
        if "strlen" not in ds.dimensions:
            ds.createDimension("strlen", 21)
        if "sector" in ds.variables:
            sector = ds.replaceVariable(
                "sector", "S1", ("basin", "strlen"), fill_value=False
            )
        else:
            sector = ds.createVariable("sector", "S1", ("basin", "strlen"))
        data = np.full((3, 21), b" ", dtype="S1")
        for row, label in zip(data, wanted):
            row[: len(label)] = np.frombuffer(label.encode("ascii"), dtype="S1")
        sector[:] = data
        changed = True
    else:
        sector = ds["sector"]
    changed = (
        set_attrs(sector, dict(standard_name="region", long_name="Ocean Basin"))
        or changed
    )
    if getattr(ds[main_name], "coordinates", "") != "sector":
        ds[main_name].setncattr("coordinates", "sector")
        changed = True
    if numeric is not None:
        # CF region labels are text. The retained numeric coordinate is an index,
        # not a region string or a flag data variable. Never change its values.
        changed = (
            set_attrs(numeric, {"units": "1", "long_name": "Ocean Basin Index"})
            or changed
        )
        for attr in ("standard_name", "flag_values", "flag_meanings", "flag_masks"):
            if attr in numeric.ncattrs():
                numeric.delncattr(attr)
                changed = True
    # cell_methods remains exact, including its prose. Older outputs without a
    # numeric basin remain replayable; no absent variable is reconstructed.
    return changed


@fix("fix_msfty_coordinates", verify_data=True)
def fix_msfty_coordinates(ds):
    """Expose the approved meridional grid coordinate; do not alter any data slice."""

    # Validate the reviewed layout and accept coordinate names from earlier fixes.
    main = ds.variables.get("msfty")
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or main is None
        or "x" in main.dimensions
        or main.dimensions
        not in (("time", "basin", "lev", "y"), ("time", "basin", "lev", "rlat"))
    ):
        raise ValueError("Expected msfty after removal of the singleton x dimension")
    name = next(
        (n for n in ("rlat", "nav_lat", "latitude") if n in ds.variables), "nav_lat"
    )
    dim = "rlat" if "rlat" in ds.dimensions else "y"
    lat = ds.variables.get(name)
    if lat is None or lat.dimensions != (dim,) or lat.shape != (332,):
        raise ValueError("Expected the existing 332 meridional positions")
    finite(lat)

    # Only discard longitude auxiliaries whose values are all zero.
    lons = [n for n in ("nav_lon", "longitude") if n in ds.variables]
    for n in lons:
        lon = ds[n]
        if lon.dimensions != (dim,) or not np.all(finite(lon) == 0):
            raise ValueError("Only an all-zero nav_lon may be removed from msfty")
    lev = ds.variables.get("lev")
    if (
        lev is None
        or lev.dimensions != ("lev",)
        or lev.shape != (75,)
        or getattr(lev, "units", "") != "m"
    ):
        raise ValueError("Expected existing msfty depth in metres on lev=75")
    finite(lev)

    # Rename the existing coordinates and dimensions without moving data slices.
    changed = False
    if name != "rlat":
        ds.renameVariable(name, "rlat")
        _replace_references(ds, name, "rlat")
        changed = True
    if dim != "rlat":
        ds.renameDimension(dim, "rlat")
        changed = True
    changed = (
        set_attrs(
            ds["rlat"],
            dict(name="rlat", standard_name="grid_latitude", units="degrees", axis="Y"),
        )
        or changed
    )
    changed = set_attrs(ds["lev"], dict(standard_name="depth", axis="Z")) or changed
    if "olevel_bounds" in ds.variables:
        from .bounds import rename_bounds_variable

        changed = (
            rename_bounds_variable(ds, "lev", "olevel_bounds", "lev_bnds") or changed
        )

    # Remove obsolete longitude references before physically deleting their variables.
    for n in lons:
        for v in ds.variables.values():
            if n in getattr(v, "coordinates", "").split():
                v.setncattr(
                    "coordinates", " ".join(t for t in v.coordinates.split() if t != n)
                )
        changed = delete_variables(ds, [n]) or changed
    return changed


def _verify_zostoga(source, output):
    with netCDF4.Dataset(output) as ds:
        if (
            ds["zostoga"].dimensions != ("time",)
            or "x" in ds.dimensions
            or "y" in ds.dimensions
            or ds.grid_label != "g010"
        ):
            raise ValueError("zostoga reduction did not survive the combined fixes")


@fix(
    "reduce_replicated_zostoga",
    verify_data=True,
    data_targets=lambda p: {"zostoga"},
    verify_final=_verify_zostoga,
)
def reduce_replicated_zostoga(ds):
    """Validate exact spatial identity at every time, then keep one valid raw value."""
    from ..preview import MAX_AUX_BYTES

    v = ds.variables.get("zostoga")
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or getattr(ds, "branded_variable", "") != "zostoga_tavg-u-hm-sea"
        or v is None
        or v.dimensions not in (("time", "y", "x"), ("time",))
    ):
        raise ValueError("Expected the reviewed spatially replicated global zostoga")
    changed = False

    # Verify every time slice before reducing the replicated field to one value.
    if v.dimensions == ("time", "y", "x"):
        if v.size == 0 or v.shape[0] * max(8, v.dtype.itemsize) > MAX_AUX_BYTES:
            raise ValueError("Empty or oversized zostoga time series")
        _raw(v)
        series = np.empty(v.shape[0], dtype=v.dtype)
        fills = [
            v.getncattr(a) for a in ("_FillValue", "missing_value") if a in v.ncattrs()
        ]
        origin = getattr(v, "_source", None)
        if origin is not None and origin.get_fill_value() is not None:
            fills.append(origin.get_fill_value())
        for t in range(v.shape[0]):
            raw = np.asarray(v[t, :, :])
            valid = np.isfinite(raw)
            for fill in fills:
                for item in np.asarray(fill).reshape(-1):
                    valid &= raw != item
            values = raw[valid]
            if not values.size or np.min(values) != np.max(values):
                raise ValueError(
                    f"zostoga is not exactly spatially replicated at t={t}, or has no valid value"
                )
            series[t] = values[0]
            io_progress(
                "Vérification de la réplication spatiale",
                "zostoga",
                t + 1,
                len(series),
                raw.nbytes,
            )

        # Only after every timestep passed may the new one-dimensional field exist.
        old = v
        v = ds.replaceVariable("zostoga", old.dtype, ("time",))
        _copy_attrs(getattr(old, "_header", old), v, skip=["_FillValue", "coordinates"])
        v[:] = series
        changed = True

    # Discard only the reviewed spatial auxiliaries after preserving the time series.
    spatial = {n for n, a in ds.variables.items() if {"x", "y"} & set(a.dimensions)}
    approved = {
        "x",
        "y",
        "nav_lat",
        "nav_lon",
        "latitude",
        "longitude",
        "bounds_nav_lat",
        "bounds_nav_lon",
        "vertices_latitude",
        "vertices_longitude",
    }
    if spatial - approved:
        raise ValueError(
            f"Unreviewed spatial variables cannot be discarded: {sorted(spatial - approved)}"
        )
    if "coordinates" in v.ncattrs():
        v.delncattr("coordinates")
        changed = True
    changed = delete_variables(ds, spatial) or changed
    for dim in ("x", "y", "nvertex", "vertices"):
        if dim in ds.dimensions and not any(
            dim in a.dimensions for a in ds.variables.values()
        ):
            ds.deleteDimension(dim)
            changed = True
    if getattr(ds, "grid_label", "") != "g010":
        ds.setncattr("grid_label", "g010")
        changed = True
    return changed
