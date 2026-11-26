"""A file name whose time range ends after the data: renamed to the data's real end (TIME003).

Seen on scen7-vl and scen7-hl (IPSL-CM6-ESMCO2): the atmosphere was restarted from 2040, and the file being
written when it stopped holds data up to 2039 only, but kept its planned end (``..._202201-210012.nc``
holds 2022-2039; ``..._204001-210012.nc`` follows). Only the end of the name changes; the data, the start
of the range and every attribute are kept. ``apply_file`` renames the file once its other fixes are
committed, as for the grid label (C097), and never over an existing file.
"""

from __future__ import annotations

import re
from pathlib import Path

import cftime

from . import fix

RANGE = re.compile(r"_(\d{6}|\d{8}|\d{12})-(\d{6}|\d{8}|\d{12})\.nc$")
FORMATS = {6: "%Y%m", 8: "%Y%m%d", 12: "%Y%m%d%H%M"}


def filename_time_target(path, source, target):
    """The path with its time range replaced (None if it already has the target), after checking that only
    the end is moved earlier."""
    path = Path(path)
    start, end = source.split("-")
    new_start, new_end = target.split("-")
    if new_start != start or len(new_end) != len(end) or not new_end < end:
        raise ValueError(f"Only an earlier end of the time range is reviewed: {source} -> {target}")
    m = RANGE.search(path.name)
    if not m:
        raise ValueError(f"No time range in the file name: {path.name}")
    current = f"{m.group(1)}-{m.group(2)}"
    if current == target:
        return None
    if current != source:
        raise ValueError(f"The file name's time range is {current}, not {source}")
    return path.with_name(path.name[:m.start()] + f"_{target}.nc")


@fix("sync_filename_time_range")
def sync_filename_time_range(ds, source, target):
    """Check that the data starts and ends where the new name says; the apply layer renames the file.
    Changes nothing in the file (rename only)."""
    time = ds.variables.get("time")
    if time is None or time.dimensions != ("time",) or not time.shape[0]:
        raise ValueError("Expected a non-empty CF time coordinate on the time dimension")
    first, last = cftime.num2date([time[0], time[-1]], time.units, calendar=getattr(time, "calendar", "standard"))
    start, end = target.split("-")
    form = FORMATS.get(len(end))
    if form is None or len(start) != len(end):
        raise ValueError(f"Unexpected time range {target}")
    data = f"{first.strftime(form)}-{last.strftime(form)}"
    if data != target:
        raise ValueError(f"The data cover {data}, not {target}: file name left unchanged")
    return False
