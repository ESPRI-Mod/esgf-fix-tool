"""Delete only the explicitly reviewed component copies, never by array equality."""

import json
import re

import netCDF4
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.cli.main import app
from esgf_fix_tool.deduplicate import deduplicate


def product(root, component, variable, *, surface="u", period="185001-185912", value=1, times=None):
    """One file; its time values follow its period (one value a year, mid-January and mid-February of the first)."""
    frequency = "fx" if variable == "areacella" else "mon"
    brand = variable + ("_ti-u-hxy-" if frequency == "fx" else "_tavg-u-hxy-") + surface
    suffix = "" if frequency == "fx" else "_" + period
    p = root / "CMIP7" / component / (
        f"{brand}_{frequency}_glb_g111_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1{suffix}.nc"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(p, "w") as ds:
        ds.setncatts(dict(
            variable_id=variable, branded_variable=brand, source_id="IPSL-CM6-ESMCO2",
            experiment_id="esm-piControl-spinup", variant_label="r1i1p1f1",
            frequency=frequency, region="glb", grid_label="g111",
            realm="atmos" if component == "ATM" else "land",
        ))
        ds.createDimension("lat", 2)
        dims = ("lat",)
        if frequency != "fx":
            if times is None:
                start = (int(period[:4]) - 1850) * 365
                times = [start + 15, start + 45]
            ds.createDimension("time", len(times))
            ds.createVariable("time", "f8", ("time",))[:] = times
            ds["time"].units = "days since 1850-01-01"
            ds["time"].calendar = "proleptic_gregorian"
            dims = ("time", "lat")
        ds.createVariable(variable, "f4", dims, zlib=True, complevel=2)[:] = value
    return p


def test_four_overlaps_dry_run_deletion_and_idempotence(tmp_path):
    root = tmp_path / "simulation"
    pairs = []
    for variable, surface, period, preferred in [
        ("areacella", "u", "", "ATM"),
        ("evspsbl", "u", "185001-185412", "ATM"),
        ("evspsbl", "u", "185501-185912", "ATM"),
        ("snc", "lnd", "185001-185912", "SRF"),
    ]:
        keep = product(root, preferred, variable, surface=surface, period=period, value=10)
        other = "SRF" if preferred == "ATM" else "ATM"
        discard = product(root, other, variable, surface=surface, period=period, value=20)
        pairs.append((keep, discard))
    originals = {p: p.read_bytes() for pair in pairs for p in pair}
    log = tmp_path / "fixlog.jsonl"
    dry = deduplicate(root, log, dry_run=True, emit=lambda text: None)
    assert dry["would_delete"] == 4 and dry["failed"] == 0
    assert all(p.read_bytes() == data for p, data in originals.items())
    result = deduplicate(root, log, only_under=root, emit=lambda text: None)
    assert result["deleted"] == 4 and result["failed"] == 0
    for keep, discard in pairs:
        assert keep.read_bytes() == originals[keep] and not discard.exists()
    again = deduplicate(root, log, only_under=tmp_path, emit=lambda text: None)  # a parent is allowed too
    assert again == dict(deleted=0, would_delete=0, missing_keeper=0, failed=0)
    entries = [json.loads(x) for x in log.read_text().splitlines()]
    assert sum(e["status"] == "deleted" for e in entries) == 4


@pytest.mark.parametrize("variable,surface", [("snc", "u"), ("evspsbl", "lnd"), ("areacella", "lnd"), ("tas", "u")])
def test_other_products_are_never_deleted(tmp_path, variable, surface):
    paths = [product(tmp_path, c, variable, surface=surface) for c in ("ATM", "SRF")]
    original = [p.read_bytes() for p in paths]
    assert deduplicate(tmp_path, tmp_path / "log.jsonl", only_under=tmp_path, emit=lambda text: None)["deleted"] == 0
    assert [p.read_bytes() for p in paths] == original


def test_missing_keeper_is_never_guessed(tmp_path):
    discard = product(tmp_path, "SRF", "evspsbl", period="185001-185412")
    product(tmp_path, "SRF", "snc", surface="lnd")   # another product in the preferred component is no keeper
    result = deduplicate(tmp_path, tmp_path / "log.jsonl", only_under=tmp_path, emit=lambda text: None)
    assert result["missing_keeper"] == 1 and result["deleted"] == 0
    assert discard.exists()


def test_keepers_of_another_period_do_not_cover_it(tmp_path):
    discard = product(tmp_path, "SRF", "evspsbl", period="185001-185412")
    keep = product(tmp_path, "ATM", "evspsbl", period="185501-185912")
    log = tmp_path / "log.jsonl"
    result = deduplicate(tmp_path, log, only_under=tmp_path, emit=lambda text: None)
    assert result["failed"] == 1 and result["deleted"] == 0
    assert discard.exists() and keep.exists()
    (entry,) = [json.loads(x) for x in log.read_text().splitlines() if '"failed"' in x]
    assert entry["error"].startswith("Temporal coverage differs: 2 of the 2 time steps")


def test_periods_cut_differently_with_overlaps(tmp_path):
    """scen7-vl: the ATM snc files overlap and are not cut like SRF's; SRF's two files cover every ATM step."""
    day = dict(surface="lnd")
    keep = [product(tmp_path, "SRF", "snc", period="20220101-20711231", times=range(50), **day),
            product(tmp_path, "SRF", "snc", period="20720101-21001231", times=range(50, 79), **day)]
    discard = [product(tmp_path, "ATM", "snc", period="20220101-20711231", times=range(50), **day),
               product(tmp_path, "ATM", "snc", period="20400101-20891231", times=range(18, 68), **day),
               product(tmp_path, "ATM", "snc", period="20900101-21001231", times=range(68, 79), **day)]
    log = tmp_path / "log.jsonl"
    result = deduplicate(tmp_path, log, only_under=tmp_path, emit=lambda text: None)
    assert result["deleted"] == 3 and result["failed"] == result["missing_keeper"] == 0
    assert all(p.exists() for p in keep) and not any(p.exists() for p in discard)
    deleted = [json.loads(x) for x in log.read_text().splitlines() if '"deleted"' in x]
    assert all(e["keep"] == [str(p) for p in keep] for e in deleted)


def test_a_step_missing_from_the_keepers_retains_the_copy(tmp_path):
    product(tmp_path, "SRF", "snc", surface="lnd", period="20220101-20711231", times=range(50))
    discard = product(tmp_path, "ATM", "snc", surface="lnd", period="20400101-20891231", times=range(18, 68))
    result = deduplicate(tmp_path, tmp_path / "log.jsonl", only_under=tmp_path, emit=lambda text: None)
    assert result["failed"] == 1 and discard.exists()


def test_coverage_compares_dates_not_values(tmp_path):
    """Other time units in the kept file: the same dates are the same coverage."""
    keep = product(tmp_path, "ATM", "evspsbl", times=[15 * 24, 45 * 24])
    with netCDF4.Dataset(keep, "a") as ds:
        ds["time"].units = "hours since 1850-01-01"
    discard = product(tmp_path, "SRF", "evspsbl")
    result = deduplicate(tmp_path, tmp_path / "log.jsonl", only_under=tmp_path, emit=lambda text: None)
    assert result["deleted"] == 1 and keep.exists() and not discard.exists()


@pytest.mark.parametrize("error", ["model", "experiment", "variable", "time", "units", "corrupt", "symlink", "parent_link"])
def test_invalid_pairs_are_retained_and_reported(tmp_path, error):
    root = tmp_path / "simulation"
    discard = product(root, "SRF", "evspsbl")
    keep = product(root, "ATM", "evspsbl")
    if error == "corrupt":
        keep.write_bytes(b"Invalid NetCDF")
    elif error == "symlink":
        target = tmp_path / "keeper.nc"
        keep.rename(target)
        keep.symlink_to(target)
    elif error == "parent_link":
        folder = keep.parent
        target = tmp_path / "external-atmosphere"
        folder.rename(target)
        folder.symlink_to(target, target_is_directory=True)
    else:
        with netCDF4.Dataset(keep, "a") as ds:
            if error == "model":
                ds.source_id = "another-model"
            elif error == "experiment":
                ds.experiment_id = "historical"
            elif error == "variable":
                ds.variable_id = "other"
            elif error == "time":
                ds["time"][1] = 46
            elif error == "units":
                ds["time"].units = "hours since 1850-01-01"
    before = discard.read_bytes(), keep.read_bytes()
    result = deduplicate(root, tmp_path / "log.jsonl", only_under=root, emit=lambda text: None)
    assert result["failed"] == 1 and result["deleted"] == 0
    assert (discard.read_bytes(), keep.read_bytes()) == before


@pytest.mark.parametrize("keep_calendar,discard_calendar", [
    ("gregorian", "proleptic_gregorian"),      # seen on CM6-ESMCO2 historical: SRF gregorian, ATM proleptic
    ("standard", "gregorian"),
    (None, "proleptic_gregorian"),             # no calendar attribute: CF default, standard
    ("365_day", "noleap"),
])
def test_equivalent_calendars_are_the_same_coverage(tmp_path, keep_calendar, discard_calendar):
    root = tmp_path / "simulation"
    keep, discard = product(root, "ATM", "evspsbl"), product(root, "SRF", "evspsbl")
    for path, calendar in ((keep, keep_calendar), (discard, discard_calendar)):
        with netCDF4.Dataset(path, "a") as ds:
            if calendar is None:
                ds["time"].delncattr("calendar")
            else:
                ds["time"].calendar = calendar
    result = deduplicate(root, tmp_path / "log.jsonl", only_under=root, emit=lambda text: None)
    assert result["deleted"] == 1 and result["failed"] == 0
    assert keep.exists() and not discard.exists()


@pytest.mark.parametrize("units,calendars", [
    ("days since 1500-01-01", ("gregorian", "proleptic_gregorian")),   # before the 1582 reform: dates differ
    ("days since 1850-01-01", ("noleap", "standard")),
    ("days since 1850-01-01", ("360_day", "proleptic_gregorian")),
])
def test_different_calendars_are_retained(tmp_path, units, calendars):
    root = tmp_path / "simulation"
    keep, discard = product(root, "ATM", "evspsbl"), product(root, "SRF", "evspsbl")
    for path, calendar in zip((keep, discard), calendars):
        with netCDF4.Dataset(path, "a") as ds:
            ds["time"].units = units
            ds["time"].calendar = calendar
    log = tmp_path / "log.jsonl"
    result = deduplicate(root, log, only_under=root, emit=lambda text: None)
    assert result["failed"] == 1 and result["deleted"] == 0
    assert keep.exists() and discard.exists()
    (entry,) = [json.loads(x) for x in log.read_text().splitlines() if '"failed"' in x]
    assert entry["error"].startswith("Calendars differ")


def test_cli_works_without_a_report_or_plan(tmp_path):
    keep = product(tmp_path, "SRF", "snc", surface="lnd")
    discard = product(tmp_path, "ATM", "snc", surface="lnd")
    args = ["deduplicate", str(tmp_path), "--fixlog", str(tmp_path / "log.jsonl")]
    dry = CliRunner().invoke(app, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert discard.exists() and "would_delete" in dry.output
    result = CliRunner().invoke(app, [*args, "--only-under", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert keep.exists() and not discard.exists()


def overlap(root):
    return product(root, "ATM", "snc", surface="lnd"), product(root, "SRF", "snc", surface="lnd")


def test_deleting_requires_only_under(tmp_path):
    root = tmp_path / "simulation"
    discard, keep = overlap(root)
    log = tmp_path / "log.jsonl"
    with pytest.raises(ValueError, match="--only-under"):
        deduplicate(root, log, emit=lambda text: None)
    assert discard.exists() and keep.exists() and not log.exists()


def test_simulation_outside_only_under_is_refused(tmp_path):
    root = tmp_path / "simulation"
    discard, keep = overlap(root)
    elsewhere = tmp_path / "staging"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="not inside --only-under"):
        deduplicate(root, tmp_path / "log.jsonl", only_under=elsewhere, emit=lambda text: None)
    assert discard.exists() and keep.exists()


def plain(output):
    """CLI output without terminal colours or line wrapping (CI renders errors in colour)."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).split())


def test_cli_refuses_to_delete_without_only_under(tmp_path):
    discard, keep = overlap(tmp_path)
    args = ["deduplicate", str(tmp_path), "--fixlog", str(tmp_path / "log.jsonl")]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2 and "--only-under" in plain(result.output)
    assert discard.exists() and keep.exists()
    elsewhere = tmp_path.parent / (tmp_path.name + "-other")
    elsewhere.mkdir()
    result = CliRunner().invoke(app, [*args, "--only-under", str(elsewhere)])
    assert result.exit_code == 2 and "not inside --only-under" in plain(result.output)
    assert discard.exists() and keep.exists()
