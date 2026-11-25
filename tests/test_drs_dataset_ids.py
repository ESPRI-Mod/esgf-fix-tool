"""A final QA on the DRS names datasets by their directories; the rules see them in their WORK form."""

import json

import pytest

from esgf_fix_tool.classify import Decision, Outcome, classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.report import QAReport, work_dataset_id
from esgf_fix_tool.ruleset import RuleSet

NAME = "pfull_tclm-al-hxy-u_mon_glb_g111_IPSL-CM6-ESMCO2_historical_r1i1p1f1_190001-190012.nc"
DRS = "CMIP7.CMIP.IPSL.IPSL-CM6-ESMCO2.historical.r1i1p1f1.glb.mon.pfull.tclm-al-hxy-u.g111"
WORK_ID = "CMIP7.ATM.pfull.tclm-al-hxy-u.mon.glb.g111.IPSL-CM6-ESMCO2.historical.r1i1p1f1"
MIDPOINTS = ("[CMIP7] [TIME001] Check Time Squareness ",
             ("12 time values do not match the midpoints of their climatology intervals. First incident at index 0: "
              "the file contains 18277.5 (decoded: 1900-01-16 12:00)."))


@pytest.fixture(scope="module")
def rules():
    return RuleSet.load(*default_rule_paths())


def drs_path(version="v20261125"):
    return f"/drs/MIP-DRS7/CMIP7/CMIP/IPSL/IPSL-CM6-ESMCO2/historical/r1i1p1f1/glb/mon/pfull/tclm-al-hxy-u/g111/{version}/{NAME}"


def write(path, dataset, file, parent_dir="/drs"):
    check, message = MIDPOINTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fail": {"3": {check: {message: {dataset: [file]}}}}, "error": {},
                                "info": {"parent_dir": str(parent_dir)}}))
    return path


def work(tmp_path):
    (tmp_path / "work" / "CMIP7" / "ATM").mkdir(parents=True)
    (tmp_path / "work" / "CMIP7" / "ATM" / NAME).write_text("")
    return tmp_path / "work"


@pytest.mark.parametrize("suffix", [".v20261125", ".files.d20261125", ".latest"])
def test_a_drs_id_gets_its_work_form_back(suffix):
    assert work_dataset_id(DRS + suffix, drs_path(), {NAME: "ATM"}) == WORK_ID
    assert work_dataset_id(DRS + suffix, drs_path(), {}) == DRS + suffix     # realm unknown: kept as it is
    assert work_dataset_id(WORK_ID, "/work/CMIP7/ATM/" + NAME, {NAME: "ATM"}) == WORK_ID


def test_the_scoped_rules_match_the_final_qa_with_work(tmp_path, rules):
    report = write(tmp_path / "run" / "qa_final" / "qa_result_x_abc.json", DRS + ".v20261125", drs_path())
    (item,) = classify(QAReport.load(report, work(tmp_path)), rules, final=True).items
    assert item.finding.dataset == WORK_ID and item.outcome is Outcome.WAIVED and item.match.rule.id == "W002"
    # without the realm, the scoped waiver cannot apply: the finding blocks, never wrongly waived
    c = classify(QAReport.load(report, tmp_path / "nowhere"), rules, final=True)
    assert c.items[0].outcome is not Outcome.WAIVED and c.decision() is not Decision.CLEAN


def test_work_defaults_to_what_the_latest_loop_report_checked(tmp_path, rules):
    w = work(tmp_path)
    write(tmp_path / "run" / "qa_1" / "qa_result_a_abc.json", WORK_ID, str(w / "CMIP7" / "ATM" / NAME), "/old")
    write(tmp_path / "run" / "qa_7" / "qa_result_b_abc.json", WORK_ID, str(w / "CMIP7" / "ATM" / NAME), w)
    report = write(tmp_path / "run" / "qa_final" / "qa_result_c_abc.json", DRS + ".v20261125", drs_path())
    (item,) = classify(QAReport.load(report), rules, final=True).items
    assert item.finding.dataset == WORK_ID and item.match.rule.id == "W002"
