"""Missing 3-hourly steps filled with _FillValue (C122): found against the file name's axis, checked, traced."""

import os

import cftime
import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_file
from esgf_fix_tool.fixes.time_fill import fill_file
from esgf_fix_tool.plan import Step

FILL = np.float32(1e20)
NAME = "cltcalipso_tpt-u-hxy-u_3hr_glb_g111_IPSL-CM6-ESMCO2_historical_r1i1p1f1_195012310300-195101020000.nc"
# 2 of 16 steps: the default safeguard (1% of the axis, at least 1) is for real files (29220 steps, ~10 missing)
STEP = Step("C122", "fill_missing_time_steps", {"var": "cltcalipso", "max_fraction": 0.2})


def series(tmp_path, drop=("1951-01-01 00:00", "1951-01-02 00:00"), name=NAME, fill=True, shift=0.0):
    """1950-12-31 03:00 .. 1951-01-02 00:00 every 3 h (16 steps), without the steps in ``drop``."""
    path = tmp_path / name
    units = "days since 1850-01-01"
    start = cftime.date2num(cftime.datetime(1950, 12, 31, 3, calendar="standard"), units, "standard")
    times = start + 0.125 * np.arange(16)
    labels = [d.strftime("%Y-%m-%d %H:%M") for d in cftime.num2date(times, units, "standard")]
    keep = [i for i, label in enumerate(labels) if label not in drop]
    with netCDF4.Dataset(path, "w") as ds:
        ds.setncatts(dict(frequency="3hr", variable_id="cltcalipso", history="written by the model"))
        ds.createDimension("time", None)
        ds.createDimension("lat", 2)
        t = ds.createVariable("time", "f8", ("time",))
        t.setncatts(dict(units=units, calendar="standard", standard_name="time", axis="T"))
        t[:] = times[keep] + shift
        lat = ds.createVariable("lat", "f8", ("lat",))
        lat[:] = [-45.0, 45.0]
        v = ds.createVariable("cltcalipso", "f4", ("time", "lat"), fill_value=FILL if fill else False, zlib=True)
        v.units = "%"
        v[:] = np.array([[i, i + 0.5] for i in keep], dtype="f4")
    return path, keep


def test_the_missing_steps_are_inserted_filled_and_traced(tmp_path):
    path, keep = series(tmp_path)
    dry = apply_file(path, [STEP], dry_run=True)
    assert dry[0]["changed"] and dry[0]["filled_steps"] == ["1951-01-01 00:00", "1951-01-02 00:00"]
    with netCDF4.Dataset(path) as ds:
        assert len(ds.dimensions["time"]) == 14          # the dry run wrote nothing
    (entry,) = apply_file(path, [STEP])
    assert entry["changed"] and entry["time_steps_filled"] == 2 and entry["write_mode"] == "time-fill-rewrite"
    with netCDF4.Dataset(path) as ds:
        v = ds["cltcalipso"]
        v.set_auto_mask(False)
        assert len(ds.dimensions["time"]) == 16 and ds.dimensions["time"].isunlimited()
        assert np.allclose(np.diff(ds["time"][:]), 0.125)
        data = v[:]
        for new, old in enumerate(range(16)):
            if old in keep:
                assert list(data[new]) == [old, old + 0.5]   # existing values unchanged, at their place
            else:
                assert np.all(data[new] == FILL)
        assert ds.history.startswith("written by the model\n")
        assert "2 missing time step(s) of cltcalipso inserted, filled with _FillValue: 1951-01-01 00:00, " \
               "1951-01-02 00:00" in ds.history
        assert v.units == "%" and v.filters()["zlib"] and list(ds["lat"][:]) == [-45.0, 45.0]
    (again,) = apply_file(path, [STEP])                  # idempotent
    assert not again["changed"] and again["time_steps_filled"] == 0


@pytest.mark.parametrize("case,error", [
    ("off_axis", "not on the expected axis"),
    ("too_many", "more than reviewed"),
    ("no_fill", "no _FillValue"),
    ("no_range", "No YYYYMMDDhhmm"),
])
def test_anything_but_a_few_missing_steps_on_a_clean_axis_is_refused(tmp_path, case, error):
    step = STEP
    if case == "off_axis":
        path, _ = series(tmp_path, shift=0.01)
    elif case == "too_many":
        path, _ = series(tmp_path)
        step = Step("C122", "fill_missing_time_steps", {"var": "cltcalipso"})   # the default: 1 of 16 at most
    elif case == "no_fill":
        path, _ = series(tmp_path, fill=False)
    else:
        path, _ = series(tmp_path, name="cltcalipso_3hr.nc")
    before = path.read_bytes()
    with pytest.raises(ValueError, match=error):
        fill_file(path, "cltcalipso", step.params.get("max_fraction", 0.01))
    assert path.read_bytes() == before
    (entry,) = apply_file(path, [step], continue_on_fix_error=True)
    assert entry["status"] == "failed" and not entry["changed"] and path.read_bytes() == before


def test_a_hard_linked_file_needs_break_hardlinks(tmp_path):
    path, _ = series(tmp_path)
    peer = tmp_path / "drs.nc"
    os.link(path, peer)
    before = peer.read_bytes()
    with pytest.raises(ValueError, match="hard links"):
        fill_file(path, "cltcalipso", 0.2)
    fill_file(path, "cltcalipso", 0.2, break_hardlinks=True)
    assert peer.read_bytes() == before and path.read_bytes() != before
