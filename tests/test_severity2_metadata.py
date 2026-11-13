"""Residual November QA recommendations and their shared-recipe interactions."""

from itertools import permutations
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from test_final_coordinates import hybrid_file
from test_monthly_climatology import fixture

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import Outcome, classify
from esgf_fix_tool.fixes.climatology import _ordered_methods
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import QAReport
from esgf_fix_tool.ruleset import RuleSet


@pytest.mark.parametrize(
    "label,stat,period",
    [
        ("tclm", "mean", "years"),
        ("tmaxavg", "maximum", "days"),
        ("tminavg", "minimum", "days"),
    ],
)
@pytest.mark.parametrize("prefix", ["area: mean ", "area: ", ""])
def test_complete_temporal_order_preserves_statistics_and_expands_shared_area(
    label, stat, period, prefix
):
    temporal = f"time: {stat} within {period} time: mean over {period}"
    want = ("area: mean " if prefix else "") + temporal
    assert _ordered_methods(prefix + temporal, label) == want
    assert _ordered_methods(want, label) == want


@pytest.mark.parametrize(
    "label,stat,var",
    [
        ("tmaxavg", "maximum", "tas"),
        ("tminavg", "minimum", "tas"),
        ("tmaxavg", "maximum", "sfcWind"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_daily_statistics_reorder_in_place_without_any_numeric_reduction(
    tmp_path, monkeypatch, label, stat, var, reverse
):
    p = tmp_path / "daily.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.variable_id = var
        ds.temporal_label = label
        ds.source_id = "IPSL-CM6-ESMCO2"
        ds.createDimension("time", 120)
        ds.createVariable("time", "f8", ("time",))[:] = np.arange(120)
        ds.createVariable(var, "f4", ("time",), zlib=True, complevel=2)[:] = np.arange(
            120
        )
        ds[
            var
        ].cell_methods = f"area: mean time: {stat} within days time: mean over days"
        ds[var].standard_name = "air_temperature" if var == "tas" else "wind_speed"
        ds[var].units = "K" if var == "tas" else "m s-1"

    def refuse(*args):
        raise AssertionError("Attribute reordering must not rewrite the file")

    monkeypatch.setattr(Batch, "write", refuse)
    steps = [Step("C106r", "reorder_climatology_cell_methods", {})]
    if var == "tas":
        steps.append(Step("C075", "add_temperature_units_metadata", {"var": "tas"}))
    if reverse:
        steps.reverse()
    q = FixPlan("test", "s", {str(p): steps})
    before = p.read_bytes()
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert p.read_bytes() == before
    inode = p.stat().st_ino
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert len(ds.dimensions["time"]) == 120
        assert (
            ds[var].cell_methods
            == f"area: mean time: {stat} within days time: mean over days"
        )
        np.testing.assert_array_equal(ds[var][:], np.arange(120))
        assert ds[var].filters()["complevel"] == 2
        if var == "tas":
            assert ds[var].units_metadata == "temperature: on_scale"
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "again")["changed"] == 0
    assert p.read_bytes() == after and p.stat().st_ino == inode


def test_existing_twelve_month_climatology_only_reorders_metadata(
    tmp_path, monkeypatch
):
    p = tmp_path / "monthly.nc"
    fixture(p)
    q = FixPlan(
        "test", "s", {str(p): [Step("C106", "rebuild_monthly_climatology", {})]}
    )
    apply_plan(q, tmp_path / "first")
    with netCDF4.Dataset(p, "a") as ds:
        original = {n: ds[n][:].tobytes() for n in ds.variables}
        ds[
            "pfull"
        ].cell_methods = "area: mean time: mean within years time: mean over years"
        ds["ps"].cell_methods = "time: mean within years time: mean over years area: mean"

    def refuse(*args):
        raise AssertionError("An existing climatology must not be averaged again")

    monkeypatch.setattr(Batch, "write", refuse)
    apply_plan(q, tmp_path / "second")
    with netCDF4.Dataset(p) as ds:
        assert len(ds.dimensions["time"]) == 12
        assert ds["ps"].cell_methods.startswith("area: mean time:")
        assert ds["pfull"].cell_methods.startswith("area: mean time:")
        assert all(ds[n][:].tobytes() == a for n, a in original.items())


@pytest.mark.parametrize(
    "text",
    [
        "area: sum time: mean within years time: mean over years",
        "area: mean time: maximum within years time: mean over years",
        "area: mean time: mean within years time: mean over days",
    ],
)
def test_unknown_statistic_or_qualifier_is_not_reinterpreted(text):
    with pytest.raises(ValueError):
        _ordered_methods(text, "tclm")


@pytest.mark.parametrize("order", list(permutations(["coefs", "normalize", "names"])))
def test_hybrid_names_cleanup_and_normalization_never_undo_each_other(tmp_path, order):
    p = tmp_path / "hybrid.nc"
    hybrid_file(p, backup=True)
    with netCDF4.Dataset(p, "a") as ds:
        ds["lev"].long_name = "Atmospheric Model Level"
        for n in ("ap", "b"):
            ds[n].long_name = "vertical coordinate formula term: " + n
            ds[n + "_bnds"].coordinates = ""
        ds["ta"].unused = ""
        ds["ta"].valid_zero = 0.0
        ds["ta"].comment = "Keep me"
        ds.empty_global = ""
        numeric = {
            n: ds[n][:].tobytes() for n in ("ap", "b", "ap_bnds", "b_bnds", "ps", "ta")
        }
    choices = {
        "coefs": [Step("C074a", "fix_hybrid_coefficients", {"coordinate": "auto"})],
        "normalize": [Step("C102", "normalize_hybrid_coordinate", {})],
        "names": [
            Step(
                "C084",
                "set_var_attr_if_matches",
                {"var": n, "attr": "long_name", "expected": old, "value": new},
            )
            for n, old, new in [
                (
                    "ap",
                    "vertical coordinate formula term: ap",
                    "vertical coordinate formula term: ap(k)",
                ),
                (
                    "b",
                    "vertical coordinate formula term: b",
                    "vertical coordinate formula term: b(k)",
                ),
                ("lev", "Atmospheric Model Level", "hybrid sigma pressure coordinate"),
            ]
        ],
    }
    q = FixPlan("test", "s", {str(p): [s for key in order for s in choices[key]]})
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert not any(n.startswith("fix_original_") for n in ds.variables)
        assert ds["lev"].long_name == "hybrid sigma pressure coordinate"
        for n in ("ap", "b"):
            assert ds[n].long_name == f"vertical coordinate formula term: {n}(k)"
        assert all(
            not (isinstance(v.getncattr(a), str) and not v.getncattr(a).strip())
            for v in ds.variables.values()
            for a in v.ncattrs()
        )
        assert (
            ds.empty_global == ""
            and ds["ta"].valid_zero == 0
            and ds["ta"].comment == "Keep me"
        )
        assert all(ds[n][:].tobytes() == data for n, data in numeric.items())
    after = p.read_bytes()
    assert apply_plan(q, tmp_path / "again")["changed"] == 0
    assert p.read_bytes() == after


@pytest.mark.parametrize("only_standard_name", [False, True])
def test_existing_hybrid_attribute_rules_also_clean_legacy_backups(
    tmp_path, only_standard_name
):
    p = tmp_path / "hybrid.nc"
    hybrid_file(p, backup=True)
    with netCDF4.Dataset(p, "a") as ds:
        ds["lev"].standard_name = (
            "x" if only_standard_name else "atmosphere_hybrid_sigma_pressure_coordinate"
        )
        ds["ap_bnds"].coordinates = ""
        ds["ap"].long_name = "vertical coordinate formula term: ap"
    step = (
        Step(
            "C014",
            "fix_invalid_standard_names",
            {
                "expected": ["x"],
                "replacement": "atmosphere_hybrid_sigma_pressure_coordinate",
                "requires": ["ap", "b"],
                "var": "lev",
            },
        )
        if only_standard_name
        else Step(
            "C084",
            "set_var_attr_if_matches",
            {
                "var": "ap",
                "attr": "long_name",
                "expected": "vertical coordinate formula term: ap",
                "value": "vertical coordinate formula term: ap(k)",
            },
        )
    )
    q = FixPlan("test", "s", {str(p): [step]})
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert (
            "fix_original_ap" not in ds.variables
            and "fix_original_b" not in ds.variables
        )
        assert "coordinates" not in ds["ap_bnds"].ncattrs()


@pytest.mark.parametrize(
    "var,comment", [("ta", "Air Temperature"), ("zg", "Geopotential height")]
)
@pytest.mark.parametrize("existing", [None, "Keep existing comment"])
def test_approved_comment_added_only_when_missing_and_description_kept(
    tmp_path, var, comment, existing
):
    p = tmp_path / "main.nc"
    with netCDF4.Dataset(p, "w") as ds:
        v = ds.createVariable(var, "f4")
        v.assignValue(12)
        v.description = "Keep description"
        if existing:
            v.comment = existing
    step = Step(
        "C002b",
        "set_var_attr_if_matches",
        {"var": var, "attr": "comment", "expected": "", "value": comment},
    )
    q = FixPlan("test", "s", {str(p): [step]})
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert (
            ds[var].comment == (existing or comment)
            and ds[var].description == "Keep description"
        )
        assert ds[var].getValue() == 12


def test_actual_residual_report_has_complete_rule_coverage():
    root = Path("/Users/glipsl/Desktop/QA_results_v4")
    if not root.is_dir():
        pytest.skip("Local QA fixture unavailable")
    report = QAReport.load(
        next(p for p in root.glob("qa_result_*.json") if ".cluster." not in p.name)
    )
    c = classify(
        report, RuleSet.load(Path(__file__).parents[1] / "src/esgf_fix_tool/rules/post_apply.yaml")
    )
    targets = {"C075": 1, "C106": 2,  "C084": 23, "C086": 1, "C002b": 2}
    daily = [i for i in c.items if i.match and i.match.rule.id == 'C106r']
    assert len({i.finding.path for i in daily}) == 3
    assert all(i.outcome == Outcome.WAIVED for i in daily)
    for rid, minimum in targets.items():
        matches = [i for i in c.items if i.match and i.match.rule.id == rid]
        assert len({i.finding.path for i in matches}) >= minimum, rid
        assert all(i.outcome == Outcome.FIX for i in matches), rid


def test_ta_point_temperature_qualifier_is_added_without_rewriting_values(
    tmp_path, monkeypatch
):
    p = tmp_path / "ta.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.variable_id = "ta"
        ds.createDimension("time", 2)
        ds.createVariable("ta", "f4", ("time",), zlib=True, complevel=2)[:] = [250, 280]
        ds["ta"].standard_name = "air_temperature"
        ds["ta"].units = "K"
        ds["ta"].cell_methods = "area: mean time: point"

    def refuse(*args):
        raise AssertionError("Temperature qualifier is an attribute update")

    monkeypatch.setattr(Batch, "write", refuse)
    q = FixPlan(
        "test",
        "s",
        {str(p): [Step("C075", "add_temperature_units_metadata", {"var": "ta"})]},
    )
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert ds["ta"].units_metadata == "temperature: on_scale"
        np.testing.assert_array_equal(ds["ta"][:], [250, 280])
    assert apply_plan(q, tmp_path / "again")["changed"] == 0


def test_rlat_recommendation_uses_existing_metadata_method_in_place(
    tmp_path, monkeypatch
):
    p = tmp_path / "msfty.nc"
    with netCDF4.Dataset(p, "w") as ds:
        ds.variable_id = "msfty"
        ds.createDimension("rlat", 332)
        ds.createVariable("rlat", "f8", ("rlat",))[:] = np.linspace(-84, 89, 332)
        ds["rlat"].standard_name = "grid_latitude"
        ds["rlat"].units = "degrees"
        ds["rlat"].long_name = "Latitude"

    def refuse(*args):
        raise AssertionError("A coordinate name is metadata only")

    monkeypatch.setattr(Batch, "write", refuse)
    q = FixPlan(
        "test",
        "s",
        {
            str(p): [
                Step(
                    "C086",
                    "set_reviewed_coordinate_long_name",
                    {"coordinate": "rlat", "value": "Grid Latitude"},
                )
            ]
        },
    )
    apply_plan(q, tmp_path / "log")
    with netCDF4.Dataset(p) as ds:
        assert ds["rlat"].long_name == "Grid Latitude"
        np.testing.assert_array_equal(ds["rlat"][:], np.linspace(-84, 89, 332))
    assert apply_plan(q, tmp_path / "again")["changed"] == 0
