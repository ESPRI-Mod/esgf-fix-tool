"""scen7-vl/hl (IPSL-CM6-ESMCO2): activity_id (C124), file names ending after their data (C125), cltcalipso (C122*)."""

import json

import netCDF4
import numpy as np
import pytest
from test_scoped_waivers import SERIES_GAP, outcome

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Outcome
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.time_range import filename_time_target
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.ruleset import RuleSet

SCEN = "IPSL-CM6-ESMCO2.scen7-vl.r1i1p1f1"
ACTIVITY = ("[CMIP7] [ATTR004] Global attribute 'activity_id' vocabulary check",
            "'SCENARIOMIP' not found in collection 'activity' for NetCDF attribute 'activity_id'")


def time_range(source, target):
    return ("[CMIP7] [TIME003] Check Time Range vs Filename",
            (f"The filename time range '{source}' does not match the first and last data endpoints, which resolve to "
             f"'{target}'. The first endpoint is 15.5 (decoded: 2022-01-16 12:00)."))


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load(*default_rule_paths())


def test_activity_id_in_capitals_is_fixed(tmp_path, rules):
    dataset = f"CMIP7.ATM.tas.tavg-h2m-hxy-u.mon.glb.g111.{SCEN}"
    assert outcome(tmp_path, rules, ACTIVITY, dataset) == (Outcome.FIX, "C124")


@pytest.mark.parametrize("variable,frequency", [("abs550aer.tavg-u-hxy-u", "mon"), ("cltcalipso.tavg-u-hxy-u", "day")])
def test_a_name_ending_after_the_data_is_renamed(tmp_path, rules, variable, frequency):
    dataset = f"CMIP7.ATM.{variable}.{frequency}.glb.g111.{SCEN}"
    assert outcome(tmp_path, rules, time_range("202201-210012", "202201-203912"), dataset) == (Outcome.FIX, "C125")


def test_cltcalipso_3hr_keeps_its_own_decision(tmp_path, rules):
    dataset = f"CMIP7.ATM.cltcalipso.tpt-u-hxy-u.3hr.glb.g111.{SCEN}"
    finding = time_range("210001010300-210101010000", "210001010300-210012312100")
    assert outcome(tmp_path, rules, finding, dataset) == (Outcome.FIX, "C122b")
    assert outcome(tmp_path, rules, SERIES_GAP, dataset) == (Outcome.FIX, "C122c")


def test_other_experiments_are_not_renamed(tmp_path, rules):
    dataset = "CMIP7.ATM.abs550aer.tavg-u-hxy-u.mon.glb.g111.IPSL-CM6-ESMCO2.historical.r1i1p1f1"
    assert outcome(tmp_path, rules, time_range("185001-201412", "185001-193912"), dataset)[1] != "C125"


@pytest.mark.parametrize("source,target", [
    ("202201-210012", "202301-203912"),     # the start moves
    ("202201-210012", "202201-210112"),     # the end moves later
    ("202201-210012", "20220101-20391231"),  # another granularity
])
def test_only_an_earlier_end_is_accepted(tmp_path, source, target):
    with pytest.raises(ValueError, match="Only an earlier end"):
        filename_time_target(tmp_path / f"x_{source}.nc", source, target)


def monthly(path, months):
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("time", months)
        t = ds.createVariable("time", "f8", ("time",))
        t.units, t.calendar = "days since 2022-01-01", "proleptic_gregorian"
        t[:] = [15.5 + 30.4375 * i for i in range(months)]
        ds.createVariable("abs550aer", "f4", ("time",))[:] = np.arange(months)


NAME = "abs550aer_tavg-u-hxy-u_mon_glb_g111_IPSL-CM6-ESMCO2_scen7-vl_r1i1p1f1_{}.nc"


def test_apply_renames_only_and_replays(tmp_path):
    path = tmp_path / NAME.format("202201-210012")
    monthly(path, 216)                                  # 2022-01 .. 2039-12
    target = tmp_path / NAME.format("202201-203912")
    plan = FixPlan("test", "synthetic", {str(path): [
        Step("C125", "sync_filename_time_range", dict(source="202201-210012", target="202201-203912"))]})
    before, inode = path.read_bytes(), path.stat().st_ino
    assert apply_plan(plan, tmp_path / "dry", dry_run=True)["changed"] == 1
    assert path.read_bytes() == before and not target.exists()
    assert apply_plan(plan, tmp_path / "apply")["changed"] == 1
    assert not path.exists() and target.stat().st_ino == inode and target.read_bytes() == before
    record = json.loads((tmp_path / "apply").read_text())
    assert record["filename_renamed"] and record["final_file"] == str(target)
    assert apply_plan(plan, tmp_path / "replay")["changed"] == 0
    assert target.read_bytes() == before


def test_data_not_ending_where_the_new_name_says_is_refused(tmp_path):
    path = tmp_path / NAME.format("202201-210012")
    monthly(path, 228)                                  # 2022-01 .. 2040-12, not 2039-12
    plan = FixPlan("test", "synthetic", {str(path): [
        Step("C125", "sync_filename_time_range", dict(source="202201-210012", target="202201-203912"))]})
    with pytest.raises(ValueError, match="The data cover 202201-204012"):
        apply_plan(plan, tmp_path / "apply")
    assert path.exists() and not (tmp_path / NAME.format("202201-203912")).exists()


def test_never_over_an_existing_file(tmp_path):
    path = tmp_path / NAME.format("202201-210012")
    monthly(path, 216)
    target = tmp_path / NAME.format("202201-203912")
    target.write_bytes(b"existing")
    plan = FixPlan("test", "synthetic", {str(path): [
        Step("C125", "sync_filename_time_range", dict(source="202201-210012", target="202201-203912"))]})
    with pytest.raises(FileExistsError):
        apply_plan(plan, tmp_path / "apply")
    assert path.exists() and target.read_bytes() == b"existing"
