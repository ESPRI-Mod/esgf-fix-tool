"""C059 schedules g111/g245; runtime geometry excludes polygon grids."""

from pathlib import Path

import pytest

from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


@pytest.mark.parametrize("grid", ["g111", "g243", "g245", "g112", "g1110"])
@pytest.mark.parametrize("layout", ["ATM", "SRF", "published"])
@pytest.mark.parametrize("bounds", ["bounds_lon", "lon_bnds"])
def test_only_reviewed_grids_schedule_regular_longitude_precision(grid, layout, bounds):
    dataset = (
        f"CMIP7.CMIP.IPSL.AnyModel.historical.r2i1p1f2.glb.6hr.hus.tpt-al-hxy-u.{grid}.v20261118"
        if layout == "published"
        else f"CMIP7.{layout}.hus.tpt-al-hxy-u.6hr.glb.{grid}.AnyModel.historical.r2i1p1f2"
    )
    finding = Finding(
        3, "[CMIP7] [COORD008] Coordinate bounds",
        f"Adjacent cells in '{bounds}' have overlapping bounds.", dataset, "/staging/hus.nc",
    )
    classification = classify(
        QAReport(Path("qa.json"), {}, [finding]), RuleSet.load(*default_rule_paths())
    )
    item = classification.items[0]
    plan = FixPlan.from_classification(classification)
    normalizes = [s for steps in plan.files.values() for s in steps
                  if s.fix == "normalize_regular_longitude"]
    assert bool(normalizes) == (grid in ("g111", "g245"))
    assert bool(item.match and item.match.rule.id == "C059") == (grid in ("g111", "g245"))
