import json
import os
import shutil

import netCDF4
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import Decision, Outcome, classify
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet
from esgf_fix_tool.seed import template_to_regex

HISTORY = ('[Appendix A] Attribute "history" should not be present in non-coordinate data (D) variable "tas". '
           "This attribute may only appear in global attributes (G).")
PATH003 = "DRS project root 'MIP-DRS7' not found in the file path '/x/tas.nc'."

RULES = """
- id: C001
  check: '\\[CF-Conventions\\] appendix_a'
  message: '\\[Appendix A\\] Attribute "history" should not be present in non-coordinate data \\(D\\) variable "(?P<var>[^"]+)"'
  action: fix
  fix: drop_var_attr
  params: {var: "{var}", attr: history}
- id: P003
  check: '\\[CMIP7\\] \\[PATH003\\]'
  message: '.*'
  action: defer
- id: R1
  check: '\\[CMIP7\\] \\[COORD008\\]'
  message: '.*'
  action: review
"""


def make_nc(path):
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("time", 3)
        v = ds.createVariable("tas", "f4", ("time",))
        v[:] = [1, 2, 3]
        v.history = "made by the model"
        v.description = "air temperature"
        ds.history = "global history"


def make_report(tmp_path, nc, extra=None, errors=None):
    fail = {"3": {"[CF-Conventions] appendix_a": {HISTORY: {"ds1": [str(nc)]}},
                  "[CMIP7] [PATH003] DRS Directory Vocabulary Check": {PATH003: {"ds1": [str(nc)]}}}}
    fail["3"].update(extra or {})
    p = tmp_path / "qa_result_test.json"
    p.write_text(json.dumps({"fail": fail, "error": errors or {}, "info": {"files": "1"}}))
    return p


@pytest.fixture
def rules(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(RULES)
    return RuleSet.load(f)


def test_report_inverts_to_files(tmp_path):
    nc = tmp_path / "tas.nc"
    report = QAReport.load(make_report(tmp_path, nc))
    assert report.files == {str(nc)}
    assert {f.check for f in report.by_file()[str(nc)]} == {
        "[CF-Conventions] appendix_a", "[CMIP7] [PATH003] DRS Directory Vocabulary Check"}


def test_cluster_report_is_refused(tmp_path):
    p = tmp_path / "qa_result_x.cluster.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        QAReport.load(p)


def test_decisions(tmp_path, rules):
    nc = tmp_path / "tas.nc"
    report = QAReport.load(make_report(tmp_path, nc))
    c = classify(report, rules)
    assert {i.outcome for i in c.items} == {Outcome.FIX, Outcome.DEFERRED}
    assert c.decision() is Decision.KNOWN
    assert classify(report, rules, previous=report).decision() is Decision.NO_CHANGE
    assert classify(report, rules, final=True).decision() is Decision.NO_CHANGE  # deferred PATH003 left at the gate

    review = {"[CMIP7] [COORD008] Coordinate bounds": {"overlap": {"ds1": [str(nc)]}}}
    assert classify(QAReport.load(make_report(tmp_path, nc, review)), rules).decision() is Decision.NEW_RULE
    unknown = {"[CMIP7] [ZZZ999] Something new": {"boom": {"ds1": [str(nc)]}}}
    assert classify(QAReport.load(make_report(tmp_path, nc, unknown)), rules).decision() is Decision.NEW_RULE
    crash = {"[CF-Conventions] check_geographic_region": {"Exception: …": {"ds1": [str(nc)]}}}
    assert classify(QAReport.load(make_report(tmp_path, nc, errors=crash)), rules).decision() is Decision.CHECKER_ERROR


def test_apply_never_touches_a_hardlinked_source(tmp_path, rules):
    src = tmp_path / "source.nc"
    make_nc(src)
    staged = tmp_path / "staging" / "tas.nc"
    staged.parent.mkdir()
    os.link(src, staged)  # what `cp -al` does
    assert os.stat(staged).st_nlink == 2
    # Direct metadata writes must never modify a hardlinked simulator original.
    unsafe = FixPlan.from_classification(classify(QAReport.load(make_report(tmp_path, staged)), rules))
    before = src.read_bytes()
    with pytest.raises(PermissionError, match="hardlinks"):
        apply_plan(unsafe, tmp_path / "refused.jsonl")
    assert src.read_bytes() == staged.read_bytes() == before
    independent = tmp_path / "independent.nc"
    shutil.copyfile(src, independent)
    os.replace(independent, staged)

    plan = FixPlan.from_classification(classify(QAReport.load(make_report(tmp_path, staged)), rules))
    assert [s.fix for s in plan.files[str(staged)]] == ["drop_var_attr"]
    stats = apply_plan(plan, tmp_path / "fixlog.jsonl")
    assert stats == {"files": 1, "changed": 1, "unchanged": 0}

    with netCDF4.Dataset(staged) as ds:
        assert "history" not in ds["tas"].ncattrs()
        assert ds.history == "global history"  # global attribute kept
    with netCDF4.Dataset(src) as ds:
        assert ds["tas"].history == "made by the model"  # source untouched
    assert os.stat(staged).st_ino != os.stat(src).st_ino
    assert os.stat(src).st_nlink == 1

    # idempotent: a second run changes nothing and leaves no temp file behind
    assert apply_plan(plan, tmp_path / "fixlog.jsonl") == {"files": 1, "changed": 0, "unchanged": 1}
    assert sorted(p.name for p in staged.parent.iterdir()) == ["tas.nc"]


def test_template_to_regex_matches_the_exact_message():
    template = "Units \"{A}\" for variable {B} must be convertible to canonical units \"{C}\" (2 occurrences, e.g. A='1')"
    rx = template_to_regex(template, generalize_quotes=True)
    import re
    m = re.match(rx, 'Units "1" for variable basin must be convertible to canonical units "None"')
    assert m and m.group("B") == "basin"


def test_map_paths_and_only_under(tmp_path, rules):
    src = tmp_path / "source" / "ATM" / "tas.nc"
    src.parent.mkdir(parents=True)
    make_nc(src)
    stg = tmp_path / "staging"
    shutil.copytree(tmp_path / "source", stg)

    plan = FixPlan.from_classification(classify(QAReport.load(make_report(tmp_path, src)), rules))
    with pytest.raises(PermissionError):  # the report was made on the source: refuse
        apply_plan(plan, tmp_path / "log.jsonl", only_under=stg)
    assert plan.map_paths(str(tmp_path / "source"), str(stg)) == 1
    assert apply_plan(plan, tmp_path / "log.jsonl", only_under=stg)["changed"] == 1
    with netCDF4.Dataset(src) as ds:
        assert ds["tas"].history == "made by the model"  # the source was never touched


def test_no_change_across_source_and_staging(tmp_path, rules):
    """Iteration 1 on the source, iteration 2 on the staging copy: same findings must count as 'no change'."""
    def report(root, name):
        nc = f"{root}/ATM/tas.nc"
        msg = f"DRS project root 'MIP-DRS7' not found in the file path '{nc}'."
        fail = {"3": {"[CF-Conventions] appendix_a": {HISTORY: {"ds1": [nc]}},
                      "[CMIP7] [PATH003] DRS Directory Vocabulary Check": {msg: {"ds1": [nc]}}}}
        p = tmp_path / name
        p.write_text(json.dumps({"fail": fail, "error": {}, "info": {"parent_dir": root}}))
        return QAReport.load(p)

    first, second = report("/work/src/CMIP7", "qa_result_1.json"), report("/scratch/stg", "qa_result_2.json")
    assert first.keys() == second.keys()
    assert classify(second, rules, previous=first).decision() is Decision.NO_CHANGE


def test_progress_and_fixlog_flushed(tmp_path, rules):
    staged = tmp_path / "staging" / "tas.nc"
    staged.parent.mkdir()
    make_nc(staged)
    plan = FixPlan.from_classification(classify(QAReport.load(make_report(tmp_path, staged)), rules))
    log = tmp_path / "fixlog.jsonl"
    seen = []

    def progress(done, total, path, changed, elapsed):
        seen.append((done, total, changed, len(log.read_text().splitlines())))  # log line already on disk

    apply_plan(plan, log, progress=progress)
    assert seen == [(1, 1, True, 1)]


def test_plan_only_one_rule(tmp_path, rules):
    nc = tmp_path / "tas.nc"
    c = classify(QAReport.load(make_report(tmp_path, nc)), rules)
    assert FixPlan.from_classification(c, {"C001"}).files
    assert FixPlan.from_classification(c, {"OTHER"}).files == {}
