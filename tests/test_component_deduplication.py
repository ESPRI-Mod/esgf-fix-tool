"""Delete only the explicitly reviewed component copies, never by array equality."""

import json

import netCDF4
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.cli.main import app
from esgf_fix_tool.deduplicate import deduplicate


def product(root, component, variable, *, surface="u", period="185001-185912", value=1):
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
            ds.createDimension("time", 2)
            ds.createVariable("time", "f8", ("time",))[:] = [15, 45]
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
    result = deduplicate(root, log, emit=lambda text: None)
    assert result["deleted"] == 4 and result["failed"] == 0
    for keep, discard in pairs:
        assert keep.read_bytes() == originals[keep] and not discard.exists()
    again = deduplicate(root, log, emit=lambda text: None)
    assert again == dict(deleted=0, would_delete=0, missing_keeper=0, failed=0)
    entries = [json.loads(x) for x in log.read_text().splitlines()]
    assert sum(e["status"] == "deleted" for e in entries) == 4


@pytest.mark.parametrize("variable,surface", [("snc", "u"), ("evspsbl", "lnd"), ("areacella", "lnd"), ("tas", "u")])
def test_other_products_are_never_deleted(tmp_path, variable, surface):
    paths = [product(tmp_path, c, variable, surface=surface) for c in ("ATM", "SRF")]
    original = [p.read_bytes() for p in paths]
    assert deduplicate(tmp_path, tmp_path / "log.jsonl", emit=lambda text: None)["deleted"] == 0
    assert [p.read_bytes() for p in paths] == original


def test_missing_or_different_period_keeper_is_not_enough(tmp_path):
    discard = product(tmp_path, "SRF", "evspsbl", period="185001-185412")
    keep = product(tmp_path, "ATM", "evspsbl", period="185501-185912")
    result = deduplicate(tmp_path, tmp_path / "log.jsonl", emit=lambda text: None)
    assert result["missing_keeper"] == 1 and result["deleted"] == 0
    assert discard.exists() and keep.exists()


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
    result = deduplicate(root, tmp_path / "log.jsonl", emit=lambda text: None)
    assert result["failed"] == 1 and result["deleted"] == 0
    assert (discard.read_bytes(), keep.read_bytes()) == before


def test_cli_works_without_a_report_or_plan(tmp_path):
    keep = product(tmp_path, "SRF", "snc", surface="lnd")
    discard = product(tmp_path, "ATM", "snc", surface="lnd")
    args = ["deduplicate", str(tmp_path), "--fixlog", str(tmp_path / "log.jsonl")]
    dry = CliRunner().invoke(app, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert discard.exists() and "would_delete" in dry.output
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert keep.exists() and not discard.exists()
