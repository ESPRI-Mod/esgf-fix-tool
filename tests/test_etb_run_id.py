"""Logs carry the esgf-toolbox run ID (ETB_RUN_ID, set by `etb run`), linking each line to the run that wrote it."""
import json

import netCDF4
import pytest
from test_component_deduplication import product

from esgf_fix_tool import __version__
from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.deduplicate import deduplicate
from esgf_fix_tool.plan import FixPlan, Step

STEP = Step("C001", "drop_var_attr", {"var": "tas", "attr": "history"})


def netcdf(path):
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("n", 1)
        ds.createVariable("tas", "f4", ("n",)).history = "drop me"
    return path


def fixlog_records(tmp_path, files, **options):
    log = tmp_path / "fixlog.jsonl"
    apply_plan(FixPlan("test", "report", {str(f): [STEP] for f in files}, rules_digest="abc"), log, **options)
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("run_id", ["3f2a9c1b7e44", None])
def test_fixlog_records_the_etb_run_id(tmp_path, monkeypatch, run_id):
    if run_id:
        monkeypatch.setenv("ETB_RUN_ID", run_id)
    else:
        monkeypatch.delenv("ETB_RUN_ID", raising=False)
    (record,) = fixlog_records(tmp_path, [netcdf(tmp_path / "f.nc")])
    assert record["status"] == "done" and record["etb_run_id"] == run_id


def test_failed_file_record_has_the_run_id_and_rules_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("ETB_RUN_ID", "3f2a9c1b7e44")
    broken = tmp_path / "broken.nc"
    broken.write_bytes(b"not a netCDF file")
    # continue_on_error is what the apply command uses; the Python API stops at the first error by default
    good, failed = sorted(fixlog_records(tmp_path, [netcdf(tmp_path / "f.nc"), broken], continue_on_error=True),
                          key=lambda r: r["status"])
    assert failed["status"] == "failed" and good["status"] == "done"
    for record in (good, failed):
        assert record["etb_run_id"] == "3f2a9c1b7e44" and record["rules_digest"] == "abc"


def test_deduplicate_log_records_the_run_id_and_tool_version(tmp_path, monkeypatch):
    monkeypatch.setenv("ETB_RUN_ID", "3f2a9c1b7e44")
    product(tmp_path, "SRF", "snc", surface="lnd")
    product(tmp_path, "ATM", "snc", surface="lnd")
    log = tmp_path / "deduplicate.jsonl"
    deduplicate(tmp_path, log, dry_run=True, emit=lambda text: None)
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries and all(e["etb_run_id"] == "3f2a9c1b7e44" and e["tool_version"] == __version__
                           for e in entries)
