"""Per-file monthly climatologies: equal weight per year, with bounded memory."""

import re

import netCDF4
import numpy as np

from ..data_integrity import _slices
from ..progress import io_progress
from . import fix
from .bounds import _verify_climatology, fix_climatology_reference


def axis(ds):

    # Read the existing time coordinate and its explicit interval reference.
    t = ds["time"]
    units, calendar = t.units, getattr(t, "calendar", "standard")
    ref = getattr(t, "climatology", "") or getattr(t, "bounds", "")
    if (
        ref not in ("time_bnds", "time_bounds", "climatology_bnds")
        or ref not in ds.variables
    ):
        raise ValueError("Monthly climatology requires explicit time interval bounds")
    v = ds[ref]
    if (
        t.dimensions != ("time",)
        or v.ndim != 2
        or v.dimensions[0] != "time"
        or v.shape[1] != 2
        or t.dtype != np.dtype("f8")
        or v.dtype != np.dtype("f8")
        or any(
            a in obj.ncattrs() for obj in (t, v) for a in ("scale_factor", "add_offset")
        )
    ):
        raise ValueError("Expected unpacked double time(time) and time bounds(time,2)")
    raw = np.ma.asarray(v[:])
    times = np.ma.asarray(t[:])
    if (
        np.ma.getmaskarray(raw).any()
        or np.ma.getmaskarray(times).any()
        or not np.isfinite(raw).all()
        or not np.isfinite(times).all()
    ):
        raise ValueError("Missing or non-finite time axis")
    pairs = np.asarray(raw)
    if len(times) < 12 or len(times) % 12 or np.any(np.diff(times) <= 0):
        raise ValueError("Expected complete ordered calendar years")

    # Decode dates using the stored calendar, including non-Gregorian calendars.
    dates = netCDF4.num2date(pairs, units, calendar, only_use_cftime_datetimes=True)
    mids = netCDF4.num2date(times, units, calendar, only_use_cftime_datetimes=True)
    if (
        np.any(times < pairs[:, 0])
        or np.any(times > pairs[:, 1])
        or np.any(pairs[:, 0] >= pairs[:, 1])
    ):
        raise ValueError("Time points must lie inside increasing bounds")

    def first(d):
        return d.day == 1 and (d.hour, d.minute, d.second, d.microsecond) == (
            0,
            0,
            0,
            0,
        )

    start, end = dates[0, 0].year, dates[-1, 1].year - 1
    if len(times) == 12:
        # A corrected climatology has twelve overlapping multi-year intervals.
        for m, (lo, hi) in enumerate(dates, 1):
            if (
                not first(lo)
                or not first(hi)
                or lo.year != start
                or lo.month != m
                or hi.year != end + (m == 12)
                or hi.month != m % 12 + 1
                or mids[m - 1].month != m
            ):
                raise ValueError(
                    "Existing 12-point climatology has inconsistent calendar bounds"
                )
        return None

    # Require complete consecutive calendar months before scheduling any reduction.
    if end < start or len(times) != 12 * (end - start + 1):
        raise ValueError("Monthly series must start in January and end in December")
    for i, (lo, hi) in enumerate(dates):
        y, m = start + i // 12, i % 12 + 1
        if (
            not first(lo)
            or not first(hi)
            or (lo.year, lo.month) != (y, m)
            or (hi.year, hi.month) != (y + (m == 12), m % 12 + 1)
            or (mids[i].year, mids[i].month) != (y, m)
        ):
            raise ValueError("Missing, duplicated or partial calendar month")
    if not np.allclose(pairs[1:, 0], pairs[:-1, 1], rtol=0, atol=1e-9):
        raise ValueError("Monthly time bounds are not contiguous")

    # Span the first and last contributing intervals; use one representative year.
    bounds = np.column_stack((pairs[:12, 0], pairs[-12:, 1]))
    middle = ((end - start + 1) // 2) * 12
    representative = pairs[middle : middle + 12].mean(axis=1)
    return {"bounds": bounds, "time": representative, "years": end - start + 1}


@fix("rebuild_monthly_climatology", verify_data=True, verify_final=_verify_climatology)
def rebuild_monthly_climatology(ds):
    """Mark one monthly series for the batch writer; never buffer science arrays."""

    # Limit numerical aggregation to the approved monthly climatology product.
    if (
        getattr(ds, "temporal_label", "") != "tclm"
        or getattr(ds, "frequency", "") != "mon"
    ):
        raise ValueError(
            "Only monthly tclm products have approved aggregation semantics"
        )
    state = axis(ds)
    changed = fix_climatology_reference(ds, _metadata_only=True)

    # Check every time-dependent field and update its temporal methods, including ps.
    for name, v in ds.variables.items():
        if "time" not in v.dimensions or name in ("time", "climatology_bnds"):
            continue
        if (
            v.dtype.kind != "f"
            or v.dimensions.count("time") != 1
            or any(
                a in v.ncattrs() for a in ("scale_factor", "add_offset", "_Unsigned")
            )
        ):
            raise ValueError(f"Unreviewed time-dependent dtype/packing for {name}")
        methods = getattr(v, "cell_methods", "")
        if "within years" in methods and "over years" in methods:
            if not re.search(
                r"time:\s*mean within years time:\s*mean over years", methods
            ):
                raise ValueError(f"Unsupported climatological statistic for {name}")
        else:
            if len(re.findall(r"time:", methods)) != 1 or not re.search(
                r"time:\s*mean\b", methods
            ):
                raise ValueError(f"Cannot infer monthly averaging semantics for {name}")
            methods = re.sub(
                r"time:\s*mean\b",
                "time: mean within years time: mean over years",
                methods,
            )
            v.setncattr("cell_methods", methods)
            changed = True

    # Schedule the reduction for the shared writer instead of loading science arrays.
    if state is not None:
        object.__setattr__(ds, "_monthly_climatology", state)
    return state is not None or changed


def configure(batch, preview):
    """Apply the shortened time dimension after all other schema fixes are composed."""
    state = getattr(preview, "_monthly_climatology", None)
    batch.climatology = state
    batch.climate_fields = set()
    if state is None:
        return

    # Shorten time in the final schema while preserving its unlimited/fixed status.
    origin, _, unlimited = batch.dimensions["time"]
    batch.dimensions["time"] = (origin, 12, unlimited)

    # Buffer only small time coordinates; stream the scientific fields later.
    for spec in batch.variables.values():
        if spec.name in ("time", "climatology_bnds"):
            spec.data = np.asarray(
                state["time" if spec.name == "time" else "bounds"], dtype=spec.dtype
            )
        elif "time" in spec.dimensions:
            if spec.origin is None or spec.data is not None:
                raise ValueError(
                    f"Cannot combine climatology with a buffered time edit: {spec.name}"
                )
            batch.climate_fields.add(spec.name)
        if (
            isinstance(spec.chunks, list)
            and not unlimited
            and "time" in spec.dimensions
        ):
            spec.chunks = list(spec.chunks)
            spec.chunks[spec.source_dimensions.index("time")] = min(
                12, spec.chunks[spec.source_dimensions.index("time")]
            )


def averages(source, spec):
    """Yield output slabs with equal-year, masked means in physical storage units.

    Packing is deliberately refused during preflight. A time slice and the two
    float64 accumulators occupy a bounded spatial tile, independent of file size.
    """
    source.set_auto_maskandscale(True)
    axis_index = source.dimensions.index("time")
    spatial = source.shape[:axis_index] + source.shape[axis_index + 1 :]

    # Accumulate matching calendar months with equal year weights and bounded tiles.
    for month in range(12):
        io_progress(
            "Climatologie mensuelle",
            spec.name,
            month,
            12,
            np.dtype(spec.dtype).itemsize,
        )
        for tile in _slices(spatial, 8, budget=1024 * 1024):
            shape = tuple(s.stop - s.start for s in tile)
            sums = np.zeros(shape, dtype="f8")
            counts = np.zeros(shape, dtype="i4")
            for index in range(month, source.shape[axis_index], 12):
                key = tile[:axis_index] + (index,) + tile[axis_index:]
                a = np.ma.asarray(source[key], dtype="f8")
                good = ~np.ma.getmaskarray(a) & np.isfinite(a.filled(np.nan))
                sums += np.where(good, a.filled(0), 0)
                counts += good
            missing = (
                spec.fill
                if spec.fill is not False
                else spec.attrs.get("missing_value", (np.nan, None))[0]
            )
            result = np.full(shape, missing, dtype=spec.dtype)
            np.divide(sums, counts, out=result, where=counts > 0, casting="unsafe")
            target = tile[:axis_index] + (month,) + tile[axis_index:]
            # Apply any spatial permutation/singleton removal already approved.
            kept = [i for i in spec.order if i != axis_index]
            original = [i for i in range(source.ndim) if i != axis_index]
            transformed = np.asarray(result)
            removed = tuple(i for i, d in enumerate(original) if d not in kept)
            if removed:
                transformed = np.squeeze(transformed, axis=removed)
            remaining = [i for i in original if i in kept]
            if kept != remaining:
                transformed = np.transpose(
                    transformed, [remaining.index(i) for i in kept]
                )
            yield tuple(target[i] for i in spec.order), transformed
    io_progress(
        "Climatologie mensuelle", spec.name, 12, 12, np.dtype(spec.dtype).itemsize
    )
