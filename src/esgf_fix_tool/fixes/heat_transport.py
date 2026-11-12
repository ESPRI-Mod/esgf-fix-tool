"""Approved hfx/hfy layer sums, streamed through the single atomic batch writer."""

import netCDF4
import numpy as np

from ..data_integrity import _slices
from ..progress import io_progress
from . import fix
from .coordinates import REFERENCE_ATTRS, reference_matches
from .reviewed_structures import delete_variables


def _verify_transport(source, output):
    with netCDF4.Dataset(output) as ds:
        name = ds.variable_id
        if ds[name].dimensions != ("time", "y", "x"):
            raise ValueError(
                "Vertically integrated transport must have dimensions (time,y,x)"
            )
    # Batch.verify independently recomputes every output sum from the original.


@fix("integrate_heat_transport", verify_data=True, verify_final=_verify_transport)
def integrate_heat_transport(ds):
    """Schedule an unweighted vertical sum; preserve the original field metadata."""
    name = getattr(ds, "variable_id", "")
    main = ds.variables.get(name)
    if (
        getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
        or name not in ("hfx", "hfy")
        or getattr(ds, "branded_variable", "") != name + "_tavg-u-hxy-sea"
        or main is None
        or main.dtype.kind != "f"
        or getattr(main, "units", "") != "W"
        or any(a in main.ncattrs() for a in ("scale_factor", "add_offset", "_Unsigned"))
    ):
        raise ValueError(
            "Expected the reviewed, unpacked IPSL hfx/hfy layer contributions in W"
        )
    if main.dimensions == ("time", "y", "x"):
        return False
    if main.dimensions != ("time", "olevel", "y", "x") or main.shape[1] != 75:
        raise ValueError("Expected hfx/hfy(time,olevel=75,y,x)")
    if not hasattr(ds, "deleteVariable"):
        raise ValueError(
            "Vertical integration requires fix_tool apply and its atomic writer"
        )

    # Identify only the vertical coordinate and its explicit or conventional bounds.
    # Retained arrays must not rely on that dimension or on a deleted coordinate.
    doomed = {"olevel"} & ds.variables.keys()
    parent = ds.variables.get("olevel")
    ref = getattr(parent, "bounds", "") if parent is not None else ""
    for n in (ref, "olevel_bounds", "olevel_bnds"):
        if n in ds.variables and "olevel" in ds[n].dimensions:
            doomed.add(n)
    for n, v in ds.variables.items():
        if n not in doomed and n != name and "olevel" in v.dimensions:
            raise ValueError(f"Unreviewed retained variable on olevel: {n}")
        if n not in doomed:
            for attr in REFERENCE_ATTRS:
                text = getattr(v, attr, "")
                if isinstance(text, str) and any(
                    reference_matches(text, attr, d) for d in doomed
                ):
                    raise ValueError(
                        f"Cannot preserve {n}:{attr} while dropping its vertical reference"
                    )

    # No scientific data are read or changed during preparation. The source stays
    # intact until the streamed result has passed schema and numerical checks.
    delete_variables(ds, doomed)
    object.__setattr__(ds, "_heat_transport", name)
    return True


def configure(batch, preview):
    name = getattr(preview, "_heat_transport", None)
    batch.heat_fields = set()
    if name is None:
        return
    if batch.climatology is not None:
        raise ValueError(
            "Combined monthly climatology and vertical integration is not reviewed"
        )
    spec = batch.variables[name]
    if spec.data is not None or spec.origin is None:
        raise ValueError(
            "Vertical integration cannot use a buffered or replaced science array"
        )
    if spec.dimensions != ("time", "olevel", "y", "x"):
        raise ValueError("Conflicting transport layout transformation")
    spec.dimensions = ("time", "y", "x")
    spec.order = (0, 2, 3)
    batch.dimensions.pop("olevel")
    batch.heat_fields.add(name)
    # Other dimension names/lengths are deliberately retained: e.g. bnds may also
    # belong to time bounds, and horizontal coordinates still describe the field.


def sums(source, spec):
    """Yield bounded tiles of masked vertical sums, rounded once to stored dtype."""
    source.set_auto_maskandscale(True)
    shape = (source.shape[0], source.shape[2], source.shape[3])
    done, total = 0, int(np.prod(shape))
    for tile in _slices(shape, 8, budget=1024 * 1024):
        size = tuple(s.stop - s.start for s in tile)
        values = np.zeros(size, dtype="f8")
        count = np.zeros(size, dtype="i4")
        for level in range(source.shape[1]):
            a = np.ma.asarray(source[tile[0], level, tile[1], tile[2]], dtype="f8")
            valid = ~np.ma.getmaskarray(a)
            # Non-finite unmasked data are not silently treated as missing.
            if not np.isfinite(a.data[valid]).all():
                raise ValueError(
                    f"Non-finite unmasked layer contribution in {spec.name}"
                )
            values += np.where(valid, a.data, 0)
            count += valid
        if spec.fill is not False:
            missing = spec.fill
        elif "missing_value" in spec.attrs:
            missing = spec.attrs["missing_value"][0]
        else:
            missing = netCDF4.default_fillvals[np.dtype(spec.dtype).str[1:]]
        with np.errstate(over="raise", invalid="raise"):
            result = np.where(count > 0, values, missing).astype(spec.dtype)
        done += int(np.prod(size))
        io_progress(
            "Somme verticale", spec.name, done, total, np.dtype(spec.dtype).itemsize
        )
        yield tile, result
