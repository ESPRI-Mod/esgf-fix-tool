"""amip (IPSL-CM6-ESMCO2) subhr site series: dimension order (C126) and file names to the minute (C127)."""

import json

import netCDF4
import numpy as np
import pytest
from test_scoped_waivers import outcome

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Outcome
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.time_range import filename_seconds_target
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.ruleset import RuleSet

AMIP = "IPSL-CM6-ESMCO2.amip.r1i1p1f1"
NAME = "cl_tpt-al-hs-u_subhr_glb_g111_IPSL-CM6-ESMCO2_amip_r1i1p1f1_{}.nc"


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load(*default_rule_paths())


@pytest.mark.parametrize("var,z,ids", [("cl", "lev", "alevel"), ("rld", "presinter", "alevhalf")])
def test_site_series_are_transposed(tmp_path, rules, var, z, ids):
    finding = ("[CMIP7] [COORD002] Data-variable coordinate order",
               (f"'{var}' dimensions in netCDF/C order are ['time', '{z}', 'site']; coordinate IDs ['{ids}', 'site', "
                "'time1'] require ['time', 'site', 'lev'] as their required order."))
    dataset = f"CMIP7.ATM.{var}.tpt-al-hs-u.subhr.glb.g111.{AMIP}"
    assert outcome(tmp_path, rules, finding, dataset) == (Outcome.FIX, "C126")


def test_file_names_with_seconds_are_renamed(tmp_path, rules):
    finding = ("[CMIP7] [FILE001] DRS Filename Vocabulary Check",
               (f"Filename '{NAME.format('19790101003000-20040101000000')}' has validation errors: extra term "
                "19790101003000-20040101000000 invalidated by the optional collection time_range at position 8"))
    dataset = f"CMIP7.ATM.cl.tpt-al-hs-u.subhr.glb.g111.{AMIP}"
    assert outcome(tmp_path, rules, finding, dataset) == (Outcome.FIX, "C127")


@pytest.mark.parametrize("source", ["19790101003015-20040101000000", "197901010030-200401010000"])
def test_only_zero_seconds_are_trimmed(tmp_path, source):
    with pytest.raises(ValueError, match="00 seconds"):
        filename_seconds_target(tmp_path / NAME.format(source), source)


def site_file(path, frequency="subhr"):
    with netCDF4.Dataset(path, "w") as ds:
        ds.frequency = frequency
        ds.createDimension("time", 3)
        ds.createDimension("lev", 2)
        ds.createDimension("site", 4)
        t = ds.createVariable("time", "f8", ("time",))
        t.units, t.calendar = "days since 1979-01-01", "proleptic_gregorian"
        t[:] = [1 / 48, 2 / 48, 3 / 48]
        ds.createVariable("cl", "f4", ("time", "lev", "site"))[:] = np.arange(24).reshape(3, 2, 4)


def test_apply_renames_to_the_minute_and_replays(tmp_path):
    path = tmp_path / NAME.format("19790101003000-20040101000000")
    site_file(path)
    target = tmp_path / NAME.format("197901010030-200401010000")
    plan = FixPlan("test", "synthetic", {str(path): [
        Step("C127", "trim_filename_seconds", dict(source="19790101003000-20040101000000"))]})
    before = path.read_bytes()
    assert apply_plan(plan, tmp_path / "apply")["changed"] == 1
    assert not path.exists() and target.read_bytes() == before
    assert json.loads((tmp_path / "apply").read_text())["final_file"] == str(target)
    assert apply_plan(plan, tmp_path / "replay")["changed"] == 0


def test_seconds_are_only_trimmed_for_subhr(tmp_path):
    path = tmp_path / NAME.format("19790101003000-20040101000000")
    site_file(path, frequency="1hr")
    plan = FixPlan("test", "synthetic", {str(path): [
        Step("C127", "trim_filename_seconds", dict(source="19790101003000-20040101000000"))]})
    with pytest.raises(ValueError, match="only reviewed for frequency subhr"):
        apply_plan(plan, tmp_path / "apply")
    assert path.exists()


def test_apply_transposes_a_site_series(tmp_path):
    path = tmp_path / NAME.format("197901010030-200401010000")
    site_file(path)
    plan = FixPlan("test", "synthetic", {str(path): [
        Step("C126", "fix_data_layout", dict(var="cl", target_order=["time", "site", "lev"]))]})
    assert apply_plan(plan, tmp_path / "apply")["changed"] == 1
    with netCDF4.Dataset(path) as ds:
        assert ds["cl"].dimensions == ("time", "site", "lev")
        assert np.array_equal(ds["cl"][:], np.arange(24).reshape(3, 2, 4).transpose(0, 2, 1))
