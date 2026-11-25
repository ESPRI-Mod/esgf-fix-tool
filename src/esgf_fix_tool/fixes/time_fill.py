"""Missing time steps of a sub-daily series, filled with the variable's _FillValue (decided case by case).

The expected axis comes from the file name's time range (``..._YYYYMMDDhhmm-YYYYMMDDhhmm.nc``) and the
``frequency``: every step of it must be in the file, except the missing ones, which are inserted with their
exact time and a fully missing field. The other values are copied unchanged, and checked after writing.
The global ``history`` says which steps were filled (``comment`` belongs to the CV, a variable
``history`` is forbidden).

The insertion changes the length of the time dimension, which the grouped rewrite of the other fixes
refuses by design (it verifies every array against the source): ``apply_file`` takes the step aside and runs
:func:`fill_file` after them, on its own (a dry run lists the steps it would fill).
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cftime
import netCDF4
import numpy as np

from .. import __version__
from ..progress import activity
from . import fix
from .layouts import _copy_attrs, _creation_options, _raw, _same_attrs

STEPS = {"1hr": timedelta(hours=1), "3hr": timedelta(hours=3), "6hr": timedelta(hours=6)}
RANGE = re.compile(r"_(\d{12})-(\d{12})\.nc$")
BLOCK = 512  # time steps copied at once


def missing_steps(ds, path, var, max_fraction=0.01):
    """The time values missing from the file, as numbers in its time units (sorted). Refuses (ValueError)
    anything but a clean subset of the expected axis."""
    frequency = getattr(ds, "frequency", "")
    if frequency not in STEPS:
        raise ValueError(f"Filling time steps is reviewed for {sorted(STEPS)} only, not frequency {frequency!r}")
    m = RANGE.search(Path(path).name)
    if not m:
        raise ValueError("No YYYYMMDDhhmm-YYYYMMDDhhmm time range in the file name")
    time = ds.variables.get("time")
    if time is None or time.dimensions != ("time",):
        raise ValueError("Expected a CF time coordinate on the time dimension")
    if getattr(time, "bounds", "") or getattr(time, "climatology", ""):
        raise ValueError("Time bounds are not reviewed for filling (a time-point series has none)")
    data = ds.variables.get(var)
    if data is None or not data.dimensions or data.dimensions[0] != "time":
        raise ValueError(f"Expected {var} with time as its first dimension")
    if "_FillValue" not in data.ncattrs():
        raise ValueError(f"{var} has no _FillValue to fill with")
    others = [n for n, v in ds.variables.items() if "time" in v.dimensions and n not in ("time", var)]
    if others:
        raise ValueError(f"Other variables on the time dimension are not reviewed for filling: {others}")
    units, calendar = time.units, getattr(time, "calendar", "standard")
    first, last = (cftime.datetime.strptime(t, "%Y%m%d%H%M", calendar=calendar) for t in m.groups())
    start, stop, step = (float(x) for x in cftime.date2num(
        [first, last, first + STEPS[frequency]], units, calendar=calendar))
    step -= start
    count = (stop - start) / step
    if step <= 0 or abs(count - round(count)) > 1e-6:
        raise ValueError("The file name's time range is not a whole number of steps")
    expected = start + step * np.arange(round(count) + 1)
    values = np.ma.asarray(time[:])
    if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
        raise ValueError("Missing or non-finite time values")
    values = np.asarray(values, dtype="f8")
    if values.size and np.any(np.diff(values) <= 0):
        raise ValueError("Time values are not strictly increasing (duplicates or disorder)")
    position = np.rint((values - start) / step)
    if np.any(np.abs(values - (start + position * step)) > 1e-6 * step) or np.any(position < 0) \
            or np.any(position > count + 1e-6):
        raise ValueError("Some time values are not on the expected axis of the file name: not a missing-step case")
    missing = np.setdiff1d(np.arange(len(expected)), position.astype(int))
    if len(missing) > max(1, max_fraction * len(expected)):
        raise ValueError(f"{len(missing)} of {len(expected)} time steps missing: more than reviewed, not filled")
    return expected[missing], time


def describe(values, time):
    calendar = getattr(time, "calendar", "standard")
    return [d.strftime("%Y-%m-%d %H:%M") for d in cftime.num2date(values, time.units, calendar=calendar)]


@fix("fill_missing_time_steps")
def fill_missing_time_steps(ds, var: str, max_fraction: float = 0.01) -> bool:
    """Never run in the grouped rewrite: apply_file takes this step aside and runs fill_file after the others."""
    raise RuntimeError("fill_missing_time_steps runs as its own phase (fill_file), not in the grouped rewrite")


def fill_file(path, var, max_fraction=0.01, *, dry_run=False, break_hardlinks=False):
    """Insert the missing steps into ``path`` (one rewrite, verified, atomic replacement). Returns the details for
    the fixlog: the filled steps."""
    path = Path(path)
    with netCDF4.Dataset(path) as src:
        values, time = missing_steps(src, path, var, max_fraction)
        filled = describe(values, time)
    details = dict(time_steps_filled=len(filled), filled_steps=filled)
    if not filled or dry_run:
        return details
    if path.stat().st_nlink > 1 and not break_hardlinks:
        raise ValueError("The file has other hard links: filling would leave them unchanged (--break-hardlinks)")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".fillmp", dir=path.parent)
    os.close(fd)
    try:
        with activity("Insertion des pas de temps manquants"):
            _rewrite(path, temporary, var, values, filled)
        with activity("Vérification des pas de temps insérés"):
            _verify(path, temporary, var, values)
        os.chmod(temporary, path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return details


def _mapping(old_times, inserted):
    """Index in the new axis of each old step, and of each inserted step."""
    new_times = np.sort(np.concatenate([old_times, inserted]))
    return new_times, np.searchsorted(new_times, old_times), np.searchsorted(new_times, inserted)


def _runs(indices):
    """(old start, new start, length) of the runs of consecutive old steps (they stay consecutive)."""
    out, i = [], 0
    while i < len(indices):
        j = i
        while j + 1 < len(indices) and indices[j + 1] == indices[j] + 1:
            j += 1
        out.append((i, int(indices[i]), j - i + 1))
        i = j + 1
    return out


def _rewrite(source, output, var, inserted, filled):
    with netCDF4.Dataset(source) as src:
        if src.groups or src.cmptypes or src.enumtypes or src.vltypes:
            raise ValueError("Filling time steps requires a flat file without custom data types")
        old_times = np.asarray(src["time"][:], dtype="f8")
        new_times, old_at, _ = _mapping(old_times, inserted)
        with netCDF4.Dataset(output, "w", format=src.data_model) as dst:
            _copy_attrs(src, dst, skip=["history"])
            note = (f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} esgf-fix-tool {__version__}: "
                    f"{len(filled)} missing time step(s) of {var} inserted, filled with _FillValue: "
                    + ", ".join(filled))
            history = getattr(src, "history", "")
            dst.setncattr("history", f"{history}\n{note}" if history else note)
            for name, d in src.dimensions.items():
                dst.createDimension(name, None if d.isunlimited() else (len(new_times) if name == "time" else len(d)))
            for name, v in src.variables.items():
                new = dst.createVariable(name, v.datatype, v.dimensions, **_creation_options(v, v.dimensions))
                _raw(v)
                _raw(new)
                if name == "time":
                    new[:] = new_times.astype(v.dtype)
                elif name == var:
                    for old_start, new_start, length in _runs(old_at):
                        for k in range(0, length, BLOCK):
                            n = min(BLOCK, length - k)
                            new[new_start + k:new_start + k + n] = v[old_start + k:old_start + k + n]
                    fill = np.full((1, *v.shape[1:]), v.getncattr("_FillValue"), dtype=v.dtype)
                    for index in np.setdiff1d(np.arange(len(new_times)), old_at):
                        new[int(index):int(index) + 1] = fill
                elif v.dimensions:
                    new[...] = v[...]
                else:
                    new.assignValue(v.getValue())
                _copy_attrs(v, new, skip=["_FillValue"])


def _verify(source, output, var, inserted):
    with netCDF4.Dataset(source) as src, netCDF4.Dataset(output) as dst:
        if set(src.variables) != set(dst.variables) or set(src.dimensions) != set(dst.dimensions):
            raise ValueError("Filling changed the variable or dimension inventory")
        a, b = dict(src.__dict__), dict(dst.__dict__)
        a.pop("history", None)
        b.pop("history", None)
        if a != {k: b[k] for k in a} or set(a) != set(b):
            raise ValueError("Filling changed global attributes other than history")
        old_times = np.asarray(src["time"][:], dtype="f8")
        new_times, old_at, new_at = _mapping(old_times, inserted)
        if len(dst.dimensions["time"]) != len(new_times) or not np.array_equal(np.asarray(dst["time"][:], "f8"),
                                                                                 new_times):
            raise ValueError("The filled time axis is not the expected one")
        for name, v in src.variables.items():
            out = dst.variables[name]
            _raw(v)
            _raw(out)
            if not _same_attrs(v, out) or v.dtype != out.dtype or v.dimensions != out.dimensions:
                raise ValueError(f"Filling changed the definition of {name}")
            if name == var:
                for old_start, new_start, length in _runs(old_at):
                    for k in range(0, length, BLOCK):
                        n = min(BLOCK, length - k)
                        if not np.array_equal(np.asarray(v[old_start + k:old_start + k + n]),
                                              np.asarray(out[new_start + k:new_start + k + n]), equal_nan=True):
                            raise ValueError(f"Filling changed existing values of {var}")
                fill = v.getncattr("_FillValue")
                for index in new_at:
                    if not np.all(np.asarray(out[int(index)]) == fill):
                        raise ValueError(f"An inserted step of {var} is not entirely _FillValue")
            elif name != "time" and not np.array_equal(np.asarray(v[...]), np.asarray(out[...]), equal_nan=True):
                raise ValueError(f"Filling changed {name}")
