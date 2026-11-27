"""Approved edt/evu interpolation from 80/96 interfaces to 79/95 atmospheric centers."""

import netCDF4
import numpy as np

from ..data_integrity import _slices
from ..progress import io_progress
from . import fix, hybrid, layouts


def _scope(ds):
    name = getattr(ds, "variable_id", "")
    if (
        getattr(ds, "realm", "") != "atmos"
        or name not in ("edt", "evu")
        or getattr(ds, "branded_variable", "") != name + "_tavg-al-hxy-u"
        or hybrid._label(ds) != "al"
        or name not in ds.variables
        or ds[name].dimensions not in (("time", "lev", "lat", "lon"), ("time", "lev", "cell"))
        or ds[name].dtype.kind != "f"
        or any(
            a in ds[name].ncattrs()
            for a in ("scale_factor", "add_offset", "_Unsigned")
        )
    ):
        raise ValueError("Expected the reviewed, unpacked IPSL edt/evu al product")
    return name


def _verify_centers(source, output):
    """Verify the complete reference coordinate; Batch verifies every science tile."""
    with netCDF4.Dataset(output) as ds:
        name = _scope(ds)
        if ds[name].shape[1] not in hybrid.CENTER_INTERFACE_PAIRS:
            raise ValueError("The centered edt/evu field must have 79 or 95 levels")
        values, pairs = hybrid._normalization_values(ds)
        if (
            not np.array_equal(ds["lev"][:], values)
            or ds["lev"].bounds != "lev_bnds"
            or ds["lev_bnds"].dimensions != ("lev", "bnds")
            or not np.array_equal(ds["lev_bnds"][:], pairs)
        ):
            raise ValueError("The centered hybrid coordinate/bounds are inconsistent")
    hybrid._verify_coefficients(source, output, "alevel")


@fix(
    "center_hybrid_interfaces",
    verify_data=True,
    data_targets=lambda p: {"lev", "lev_bnds", "ap", "b", "ap_bnds", "b_bnds"},
    verify_final=_verify_centers,
)
def center_hybrid_interfaces(ds):
    """Prepare a streamed adjacent-interface mean, never buffer the science field.

    The LMDZ method was confirmed by the user on 2026-11-12. The supplied
    references contain the 79/95 centers and their 80/96 interfaces;
    no extrapolated level or pressure-dependent interpolation is introduced.
    """
    name = _scope(ds)
    count = ds[name].shape[1]
    if count in hybrid.CENTER_INTERFACE_PAIRS:
        # A replay must not average again. Reuse the existing idempotent recipe
        # to validate/complete only the small coordinate arrays and metadata.
        return hybrid.normalize_hybrid_coordinate(ds)
    center_count = next((n for n, interfaces in hybrid.CENTER_INTERFACE_PAIRS.items() if interfaces == count), None)
    if center_count is None or not hasattr(ds, "replaceVariable"):
        raise ValueError("80-to-79 or 96-to-95 interpolation requires fix_tool apply's atomic writer")
    if getattr(ds, "_hybrid_center_field", None) is not None:
        raise ValueError("A conflicting hybrid centering operation is already pending")

    # 1. Match the full source axis and coefficients, not just their lengths.
    # Keep the ordinary C102 guard for any unselected 80-center al case.
    hybrid._attributes(ds, "alevel")
    ref = hybrid._coefficient_reference()
    centre_dims = set()  # the own dimension of centre coefficients, removed with them
    for term in ("ap", "b"):
        v = ds[term]
        a = np.ma.asarray(v[:])
        stored = (
            v.dtype in ((np.dtype("f4"), np.dtype("f8")) if count == 96 else (np.dtype("f8"),))
            and not any(attr in v.ncattrs() for attr in ("scale_factor", "add_offset"))
            and not np.ma.getmaskarray(a).any()
        )
        interfaces = v.dimensions in (("lev",), ("klevp1",)) and hybrid._matches_reference(a, ref[count][term], count)
        # amip (XIOS): the data on the interfaces, but the file carries the centre coefficients, exactly the ones
        # written below; the interface axis is still checked against the reference
        centres = (len(v.dimensions) == 1 and v.dimensions[0] != "lev"
                   and hybrid._matches_reference(a, ref[center_count][term], center_count))
        if not (stored and (interfaces or centres)):
            raise ValueError(
                f"Interface {term} does not match the supplied {count}-point reference"
                f" (nor the {center_count} centres)"
            )
        if centres:
            centre_dims.add(v.dimensions[0])
    axis = ds["lev"]
    pressure = ref[count]["ap"] + hybrid.P_REF * ref[count]["b"]
    actual = np.ma.asarray(axis[:])
    if (
        axis.dimensions != ("lev",)
        or axis.dtype not in ((np.dtype("f4"), np.dtype("f8")) if count == 96 else (np.dtype("f8"),))
        or any(attr in axis.ncattrs() for attr in ("scale_factor", "add_offset"))
        or np.ma.getmaskarray(actual).any()
        or not any(
            hybrid._matches_reference(actual, v, count)
            for v in (
                pressure,
                pressure / hybrid.P_REF,
                ref[count]["ap"] / hybrid.P_REF + ref[count]["b"],
            )
        )
    ):
        raise ValueError(
            f"lev does not match the supplied {count} interfaces in their stored order"
        )
    allowed = {name, "lev", "ap", "b", "lev_bnds", "ap_bnds", "b_bnds"}
    for n, v in ds.variables.items():
        if "lev" in v.dimensions and n not in allowed:
            raise ValueError(f"Unreviewed variable on the resized lev dimension: {n}")
    if "bnds" in ds.dimensions and (
        len(ds.dimensions["bnds"]) != 2 or ds.dimensions["bnds"].isunlimited()
    ):
        raise ValueError("Hybrid coordinate bounds require fixed bnds=2")
    if ds.dimensions["lev"].isunlimited():
        raise ValueError("Expected a fixed interface dimension")

    # 2. Replace diskless headers only. Retain the original science identity so
    # Batch can read bounded tiles from the untouched interface-level source file.
    retired = "__hybrid_interfaces"
    if retired in ds.dimensions:
        raise ValueError("Reserved hybrid preparation dimension already exists")
    ds.renameDimension("lev", retired)
    ds.createDimension("lev", center_count)
    original = ds[name]
    centered = ds.replaceVariable(
        name, original.dtype, tuple("lev" if d == retired else d for d in original.dimensions)
    )
    layouts._copy_attrs(original._header, centered._header, skip=["_FillValue"])
    object.__setattr__(centered, "_source", original._source)
    for n in ("lev", "ap", "b", "lev_bnds", "ap_bnds", "b_bnds"):
        if n in ds.variables:
            ds.deleteVariable(n)
    ds.deleteDimension(retired)
    for dim in centre_dims:  # the centre coefficients' own axis (klev...): gone with them, if nothing else uses it
        if dim in ds.variables and ds[dim].dimensions == (dim,):
            users = [n for n, v in ds.variables.items() if dim in v.dimensions and n != dim]
            if not users:
                ds.deleteVariable(dim)
        if dim in ds.dimensions and not any(dim in v.dimensions for v in ds.variables.values()):
            ds.deleteDimension(dim)

    # 3. Use the supplied center coefficients and neighboring reference
    # interfaces for bounds, then delegate formulas/normalization to C074/C102.
    ds.createVariable("lev", "f8", ("lev",))[:] = (
        ref[center_count]["ap"] / hybrid.P_REF + ref[center_count]["b"]
    )
    for term in ("ap", "b"):
        ds.createVariable(term, "f8", ("lev",))[:] = ref[center_count][term]
    ds["ap"].units = "Pa"
    hybrid.normalize_hybrid_coordinate(ds)
    object.__setattr__(ds, "_hybrid_center_field", name)
    return True


def configure(batch, preview):
    """Register the one streamed transformation in the shared atomic rewrite."""
    batch.hybrid_center_fields = set()
    name = getattr(preview, "_hybrid_center_field", None)
    if name is None:
        return
    if batch.climatology is not None or batch.heat_fields or batch.layouts:
        raise ValueError(
            "Combined science transformations with hybrid centering are not reviewed"
        )
    spec = batch.variables[name]
    if (
        spec.data is not None
        or spec.origin is None
        or spec.dimensions not in (("time", "lev", "lat", "lon"), ("time", "lev", "cell"))
        or spec.source_dimensions != spec.dimensions
        or spec.order != tuple(range(len(spec.dimensions)))
    ):
        raise ValueError("Conflicting edt/evu centering transformation")
    # Retain compression and chunk layout; clip only chunks exceeding the new
    # fixed-size center dimension, for either approved reference family.
    if isinstance(spec.chunks, list):
        spec.chunks = list(spec.chunks)
        spec.chunks[1] = min(spec.chunks[1], len(preview.dimensions["lev"]))
    batch.hybrid_center_fields.add(name)


def averages(source, spec):
    """Yield masked adjacent means, rounded once to the original storage type."""
    source.set_auto_maskandscale(True)
    shape = (source.shape[0], source.shape[1] - 1, *source.shape[2:])
    done, total = 0, int(np.prod(shape))
    for tile in _slices(shape, 8, budget=2 * 1024 * 1024):
        input_tile = (tile[0], slice(tile[1].start, tile[1].stop + 1), *tile[2:])
        a = np.ma.asarray(source[input_tile], dtype="f8")
        valid = ~np.ma.getmaskarray(a)
        if not np.isfinite(a.data[valid]).all():
            raise ValueError(f"Non-finite unmasked interface value in {spec.name}")
        # Both interfaces must be valid. Never substitute a single valid neighbor
        # or average a fill sentinel as though it were a physical measurement.
        pair_valid = valid[:, :-1] & valid[:, 1:]
        data = np.where(valid, a.data, 0.0)
        means = data[:, :-1] * 0.5 + data[:, 1:] * 0.5
        missing = spec.fill
        if missing is False:
            default = netCDF4.default_fillvals[np.dtype(spec.dtype).str[1:]]
            missing = np.asarray(spec.attrs.get("missing_value", (default,))[0]).flat[0]
        with np.errstate(over="raise", invalid="raise"):
            values = np.where(pair_valid, means, missing).astype(spec.dtype)
        done += int(np.prod(values.shape))
        io_progress(
            "Moyenne interfaces vers centres",
            spec.name,
            done,
            total,
            np.dtype(spec.dtype).itemsize,
        )
        yield tile, values
