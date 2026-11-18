"""The CF pressure interpretation survives direct metadata fixes."""

import netCDF4
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

MESSAGE = (
    "§4.3.3 The standard_name of `lev` must map to the correct "
    "computed_standard_name, `['air_pressure']`"
)


@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("existing", [None, "", "air_pressure"])
@pytest.mark.parametrize("published", [False, True])
def test_c107_adds_metadata_in_place_without_model_scope(
    tmp_path, monkeypatch, fmt, existing, published
):
    path = tmp_path / "ta.nc"
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        # Do not restrict this diagnostic to the pilot model or experiment.
        ds.source_id = "another-model"
        ds.experiment_id = "another-experiment"
        ds.variable_id = "ta"
        ds.history = "Keep the global history."
        ds.createDimension("lev", 3)
        ds.createVariable("lev", "f8", ("lev",))[:] = [0.1, 0.5, 0.9]
        ds["lev"].standard_name = "atmosphere_hybrid_sigma_pressure_coordinate"
        if existing is not None:
            ds["lev"].computed_standard_name = existing
        ds.createVariable(
            "ta", "f4", ("lev",), zlib=fmt.startswith("NETCDF4"), complevel=2
        )[:] = [240, 260, 280]
    with netCDF4.Dataset(path) as ds:
        values = {name: v[:].tobytes() for name, v in ds.variables.items()}
        compression = ds["ta"].filters()
    dataset = (
        "CMIP7.CMIP.IPSL.another-model.another-experiment.r1i1p1f1.glb.mon."
        "ta.tavg-al-hxy-u.g111.v20261117"
        if published
        else "CMIP7.ATM.ta.tavg-al-hxy-u.mon.glb.g111.another-model.another-experiment.r1i1p1f1"
    )
    finding = Finding(2, "[CF-Conventions] §4.3 Vertical Coordinate", MESSAGE, dataset, str(path))
    classification = classify(
        QAReport(tmp_path / "qa.json", {}, [finding]), RuleSet.load(*default_rule_paths())
    )
    item = classification.items[0]
    assert item.outcome == Outcome.FIX and item.match.rule.id == "C107"
    plan = FixPlan.from_classification(classification)

    def refuse_rewrite(*args):
        raise AssertionError("One metadata attribute must not copy scientific arrays")

    monkeypatch.setattr(Batch, "write", refuse_rewrite)
    original, inode = path.read_bytes(), path.stat().st_ino
    changed = existing != "air_pressure"
    assert apply_plan(plan, tmp_path / "dry", dry_run=True)["changed"] == changed
    assert path.read_bytes() == original
    assert apply_plan(plan, tmp_path / "log")["changed"] == changed
    with netCDF4.Dataset(path) as ds:
        assert ds["lev"].computed_standard_name == "air_pressure"
        assert ds.history == "Keep the global history."
        assert ds["ta"].filters() == compression
        assert all(ds[name][:].tobytes() == data for name, data in values.items())
    corrected = path.read_bytes()
    assert apply_plan(plan, tmp_path / "again")["changed"] == 0
    assert path.read_bytes() == corrected and path.stat().st_ino == inode


def test_old_cmip7_recommendation_no_longer_selects_attribute_deletion(tmp_path):
    finding = Finding(
        2,
        "[CMIP7] [COORD004a] Additional permitted coordinate metadata",
        "Coordinate 'lev' defines optional attribute computed_standard_name='air_pressure'. "
        "No expected value is currently specified in the coordinate vocabulary, so this value cannot be verified.",
        "CMIP7.ATM.ta.tavg-al-hxy-u.mon.glb.g111.IPSL-CM6-ESMCO2.esm-piControl-spinup.r1i1p1f1",
        str(tmp_path / "ta.nc"),
    )
    result = classify(
        QAReport(tmp_path / "qa.json", {}, [finding]), RuleSet.load(*default_rule_paths())
    )
    assert not FixPlan.from_classification(result).files
