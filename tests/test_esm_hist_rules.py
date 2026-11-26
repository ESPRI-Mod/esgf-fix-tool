"""esm-hist (CM6-ESMCO2-FT.p1.3.esm-hist.01): parent_experiment_id (C123), cltcalipso missing steps (C122*)."""

import netCDF4
import pytest
from test_scoped_waivers import AXIS, SERIES_GAP, outcome

from esgf_fix_tool.classify import Outcome
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.attributes import set_global_attr_if_matches
from esgf_fix_tool.ruleset import RuleSet

ESM_HIST = "IPSL-CM6-ESMCO2.esm-hist.r1i1p1f1"
VOCABULARY = ("[CMIP7] [ATTR004] Global attribute 'parent_experiment_id' vocabulary check",
              "'esm_piControl' not found in collection 'experiment' for NetCDF attribute 'parent_experiment_id'")
CONSISTENCY = ("[CMIP7] [ATTR007c] Consistency: experiment_id vs parent_experiment_id",
               "Inconsistency for 'parent_experiment_id': CV expects one of ['esm-piControl'], file has 'esm_piControl'.")
RANGE = ("[CMIP7] [TIME003] Check Time Range vs Filename",
         ("The filename time range '195001010300-196001010000' does not match the first and last data endpoints, "
          "which resolve to '195001010300-195912312100'."))


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load(*default_rule_paths())


@pytest.mark.parametrize("finding,severity", [(VOCABULARY, 3), (CONSISTENCY, 2)])
def test_parent_experiment_id_with_an_underscore_is_fixed(tmp_path, rules, finding, severity):
    dataset = f"CMIP7.ATM.tas.tavg-h2m-hxy-u.mon.glb.g111.{ESM_HIST}"
    assert outcome(tmp_path, rules, finding, dataset, severity) == (Outcome.FIX, "C123")


def test_only_the_exact_reviewed_value_is_replaced(tmp_path):
    path = tmp_path / "f.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.parent_experiment_id = "esm_piControl"
    with netCDF4.Dataset(path, "a") as ds:
        assert set_global_attr_if_matches(ds, "parent_experiment_id", "esm_piControl", "esm-piControl")
        assert not set_global_attr_if_matches(ds, "parent_experiment_id", "esm_piControl", "esm-piControl")
        ds.parent_experiment_id = "piControl"
        with pytest.raises(ValueError, match="differs from the reviewed value"):
            set_global_attr_if_matches(ds, "parent_experiment_id", "esm_piControl", "esm-piControl")


@pytest.mark.parametrize("finding,rule", [(AXIS, "C122"), (RANGE, "C122b"), (SERIES_GAP, "C122c")])
def test_cltcalipso_missing_steps_are_filled_in_esm_hist_too(tmp_path, rules, finding, rule):
    dataset = f"CMIP7.ATM.cltcalipso.tpt-u-hxy-u.3hr.glb.g111.{ESM_HIST}"
    message = finding[1] + (" (table_id=None, frequency=3hr, var=cltcalipso, midpoint=False)" if rule == "C122" else "")
    assert outcome(tmp_path, rules, (finding[0], message), dataset) == (Outcome.FIX, rule)


def test_cltcalipso_of_another_experiment_still_needs_a_decision(tmp_path, rules):
    dataset = "CMIP7.ATM.cltcalipso.tpt-u-hxy-u.3hr.glb.g111.IPSL-CM6-ESMCO2.piControl.r1i1p1f1"
    assert outcome(tmp_path, rules, SERIES_GAP, dataset) == (Outcome.UPSTREAM, "S031")
