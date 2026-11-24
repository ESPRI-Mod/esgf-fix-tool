"""Scoped waivers (rules/waivers.yaml): false positives decided for one context only."""

import json

import pytest

from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet

HIST = "IPSL-CM6-ESMCO2.historical.r1i1p1f1"
CONTROL = "IPSL-CM6-ESMCO2.piControl.r1i1p1f1"
COVERAGE = ("[Consistency] Time coverage", "Time series starts at '1950' while other time series start at '1850'")
MIDPOINTS = ("[CMIP7] [TIME001] Check Time Squareness ",
             ("12 time values do not match the midpoints of their climatology intervals. First incident at index 0: "
              "the file contains 18277.5 (decoded: 1900-01-16 12:00)."))
TCLM_GAP = ("[Continuity] Time continuity",
            ("Gap in time axis (between files) - previous 1900-12-16 12:00:00 - current 1986-01-16 12:00:00 - "
             "delta-t 31077.0 days"))
SERIES_GAP = ("[Continuity] Time continuity",
              ("Gap in time axis (between files) - previous 1959-12-31 21:00:00 - current 1960-01-01 03:00:00 - "
               "delta-t 6.0 hours"))
AXIS = ("[CMIP7] [TIME001] Check Time Squareness ",
        "26287 time values do not match the expected axis. First incident at index 2919: the file contains 36889.125")


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load(*default_rule_paths())


def outcome(tmp_path, rules, finding, dataset, severity=3):
    check, message = finding
    path = tmp_path / "qa_result_x.json"
    path.write_text(json.dumps({"fail": {str(severity): {check: {message: {dataset: ["/sim/a.nc"]}}}},
                                "error": {}, "info": {}}))
    (item,) = classify(QAReport.load(path), rules).items
    return item.outcome, item.match.rule.id if item.match else None


@pytest.mark.parametrize("finding,dataset,severity,rule", [
    (COVERAGE, f"CMIP7.ATM.clivi.tavg-u-hxy-u.3hr.glb.g111.{HIST}", 1, "W001"),
    (MIDPOINTS, f"CMIP7.ATM.pfull.tclm-al-hxy-u.mon.glb.g111.{HIST}", 3, "W002"),
    (TCLM_GAP, f"CMIP7.ATM.phalf.tclm-alh-hxy-u.mon.glb.g111.{HIST}", 3, "W003"),
])
def test_waived_in_historical(tmp_path, rules, finding, dataset, severity, rule):
    assert outcome(tmp_path, rules, finding, dataset, severity) == (Outcome.WAIVED, rule)


@pytest.mark.parametrize("finding,dataset,severity", [
    (COVERAGE, f"CMIP7.ATM.clivi.tavg-u-hxy-u.3hr.glb.g111.{CONTROL}", 1),        # another experiment
    (COVERAGE, f"CMIP7.ATM.tas.tavg-h2m-hxy-u.day.glb.g111.{HIST}", 1),          # not 3hr
    (MIDPOINTS, f"CMIP7.ATM.pfull.tclm-al-hxy-u.mon.glb.g111.{CONTROL}", 3),      # another experiment
    (TCLM_GAP, f"CMIP7.ATM.phalf.tclm-alh-hxy-u.mon.glb.g111.{CONTROL}", 3),      # another experiment
])
def test_not_waived_outside_its_scope(tmp_path, rules, finding, dataset, severity):
    out, rule = outcome(tmp_path, rules, finding, dataset, severity)
    assert out is not Outcome.WAIVED and not (rule or "").startswith("W")


def test_real_gaps_and_axis_shifts_of_ordinary_series_are_never_waived(tmp_path, rules):
    ds = f"CMIP7.ATM.cltcalipso.tpt-u-hxy-u.3hr.glb.g111.{HIST}"     # 1 January 00:00 missing every year
    assert outcome(tmp_path, rules, SERIES_GAP, ds) == (Outcome.UPSTREAM, "S031")
    assert outcome(tmp_path, rules, AXIS, ds)[0] is Outcome.UNKNOWN
