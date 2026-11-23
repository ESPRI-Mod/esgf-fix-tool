"""Historical products reuse guarded metadata recipes without inventing categories."""

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


def classification(path, var, brand, check, message, component="SRF"):
    dataset = (
        f"CMIP7.{component}.{var}.{brand.split('_', 1)[1]}.day.glb.g111."
        "ANOTHER_MODEL.historical.r1i1p1f1 (4 files affected)"
    )
    finding = Finding(2, check, message, dataset, str(path))
    return classify(QAReport(None, {}, [finding]), RuleSet.load(*default_rule_paths()))


def no_rewrite(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Metadata-only fixes must not rewrite scientific data")

    monkeypatch.setattr(Batch, "write", refuse)


@pytest.mark.parametrize("methods", [
    "area: mean time: point", "area: time: point (over land and sea ice)",
    "area: mean where land_or_sea_ice time: point",
])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
def test_tslsi_instantaneous_temperature_is_qualified_in_place(tmp_path, monkeypatch, methods, fmt):
    path = tmp_path / "tslsi.nc"
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.variable_id = "tslsi"
        ds.createDimension("time", 2)
        v = ds.createVariable("tslsi", "f4", ("time",), zlib=True, complevel=2)
        v[:] = [250, 280]
        v.standard_name, v.units, v.cell_methods = "surface_temperature", "K", methods
        filters, chunks = v.filters(), v.chunking()
    result = classification(path, "tslsi", "tslsi_tpt-u-hxy-lsi",
        "[CF-Conventions] §3.1.2 Temperature units",
        "Variable tslsi has a temperature related standard_name and it is recommended that the units_metadata "
        "attribute is present and has one of the values ['temperature: difference', 'temperature: on_scale', 'temperature: unknown']",
        "ATM")
    assert result.items[0].match.rule.id == "C075"
    plan = FixPlan.from_classification(result)
    no_rewrite(monkeypatch)
    original, inode = path.read_bytes(), path.stat().st_ino
    apply_plan(plan, tmp_path / "dry", dry_run=True)
    assert path.read_bytes() == original
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds["tslsi"].units_metadata == "temperature: on_scale"
        assert ds["tslsi"].cell_methods == methods
        np.testing.assert_array_equal(ds["tslsi"][:], [250, 280])
        assert (ds["tslsi"].filters(), ds["tslsi"].chunking()) == (filters, chunks)
    assert path.stat().st_ino == inode
    before = path.read_bytes()
    assert apply_plan(plan, tmp_path / "again")["changed"] == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("variant", [0, 1])
def test_fveglitter_comment_uses_exact_audited_text_in_place(tmp_path, monkeypatch, variant):
    spec = json.loads((Path(__file__).parents[1] / "src/esgf_fix_tool/fixes/comment_specs.json").read_text())
    observed, value = (spec["fVegLitter_tavg-u-hxy-lnd"]["variants"][variant][k] for k in ("observed", "value"))
    path = tmp_path / "fVegLitter.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.variable_id, ds.branded_variable = "fVegLitter", "fVegLitter_tavg-u-hxy-lnd"
        v = ds.createVariable("fVegLitter", "f4")
        v.assignValue(2)
        v.comment = observed
    result = classification(path, "fVegLitter", "fVegLitter_tavg-u-hxy-lnd",
        "[CMIP7] [ATTR004] Variable 'fVegLitter' attribute 'comment' registry expected-term check",
        f"Expected '{observed}' to contain '{value}' from registry key 'comment'.")
    assert result.items[0].match.rule.id == "C002a"
    plan = FixPlan.from_classification(result)
    no_rewrite(monkeypatch)
    inode = path.stat().st_ino
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds["fVegLitter"].comment == value
        assert ds["fVegLitter"][...] == 2
    before = path.read_bytes()
    assert path.stat().st_ino == inode
    assert apply_plan(plan, tmp_path / "again")["changed"] == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("var", ["laiVgt", "landCoverFrac"])
@pytest.mark.parametrize("coord_id", ["vegtype", "modelvegtype"])
def test_existing_vegetation_labels_and_dimension_are_preserved(tmp_path, monkeypatch, var, coord_id):
    path = tmp_path / (var + ".nc")
    with netCDF4.Dataset(path, "w") as ds:
        ds.variable_id = var
        for dim, size in [("time", 2), ("vegtype", 2), ("lat", 1), ("lon", 1), ("strlen", 8)]:
            ds.createDimension(dim, size)
        ds.createVariable("type", "S1", ("vegtype", "strlen"))[:] = np.frombuffer(b"label_a label_b ", dtype="S1").reshape(2, 8)
        v = ds.createVariable(var, "f4", ("time", "vegtype", "lat", "lon"), zlib=True, complevel=2)
        v[:] = np.arange(4).reshape(2, 2, 1, 1)
        v.coordinates = "type"
        chunks, filters = v.chunking(), v.filters()
    first = classification(path, var, var + "_tavg-u-hxy-multi",
        "[CMIP7] [COORD001] Coordinate identity and structure",
        f"The required text auxiliary coordinate 'sector' for coordinate ID '{coord_id}' is absent, so its configured dependent coordinate checks could not be evaluated.")
    second = classification(path, var, var + "_tavg-u-hxy-multi",
        "[CMIP7] [COORD002] Data-variable coordinate order",
        f"'{var}' dimensions in netCDF/C order are ['time', 'vegtype', 'lat', 'lon']; coordinate IDs ['longitude', 'latitude', '{coord_id}', 'time'] require ['time', 'type', 'lat', 'lon'] as their required order.")
    assert first.items[0].match.rule.id == "C033"
    assert second.items[0].match.rule.id == "C048"
    result = classify(QAReport(None, {}, [first.items[0].finding, second.items[0].finding]), first.rules)
    plan = FixPlan.from_classification(result)
    assert [step.rule for step in plan.files[str(path)]] == ["C048", "C033"]
    no_rewrite(monkeypatch)
    inode = path.stat().st_ino
    apply_plan(plan, tmp_path / "log")
    with netCDF4.Dataset(path) as ds:
        assert ds[var].dimensions == ("time", "type", "lat", "lon")
        assert ds["sector"].dimensions == ("type", "strlen")
        assert ds["sector"][:].tobytes() == b"label_a label_b "
        assert "sector" in ds[var].coordinates.split()
        assert (ds[var].chunking(), ds[var].filters()) == (chunks, filters)
        np.testing.assert_array_equal(ds[var][:], np.arange(4).reshape(2, 2, 1, 1))
    assert path.stat().st_ino == inode
    before = path.read_bytes()
    assert apply_plan(plan, tmp_path / "again")["changed"] == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("var", ["gppVgt", "laiVgt"])
def test_reported_missing_vegetation_axis_is_review_not_a_permutation(tmp_path, var):
    result = classification(tmp_path / "file.nc", var, var + "_tavg-u-hxy-multi",
        "[CMIP7] [COORD002] Data-variable coordinate order",
        f"'{var}' dimensions in netCDF/C order are ['time', 'lat', 'lon']; coordinate IDs ['longitude', 'latitude', 'vegtype', 'time'] require ['time', 'type', 'lat', 'lon'] as their required order.")
    assert result.items[0].match.rule.id == "C048r"
    assert result.items[0].outcome == Outcome.REVIEW
    assert not FixPlan.from_classification(result).files


def test_gppvgt_missing_labels_remains_review_without_inventing_categories(tmp_path):
    result = classification(tmp_path / "gppVgt.nc", "gppVgt", "gppVgt_tavg-u-hxy-multi",
        "[CMIP7] [COORD001] Coordinate identity and structure",
        "The required text auxiliary coordinate 'sector' for coordinate ID 'vegtype' is absent, so its configured dependent coordinate checks could not be evaluated.")
    assert result.items[0].match.rule.id == "C033r"
    assert result.items[0].outcome == Outcome.REVIEW
    assert not FixPlan.from_classification(result).files
