"""Cell-indexed polygon renames compose without copying scientific data."""

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


MAPPING = dict(lat="latitude", lon="longitude", bounds_lat="vertices_latitude", bounds_lon="vertices_longitude")


def fixture(path, grid, fmt):
    with netCDF4.Dataset(path, "w", format=fmt) as ds:
        ds.variable_id = "zg"
        ds.horizontal_label = "hxy"
        ds.grid_label = grid
        ds.source_id = "IPSL-CM7A"
        ds.history = "Global history stays."
        for n, size in [("time", None), ("plev", 2), ("cell", 5), ("nvertex", 6), ("axis_nbounds", 2)]:
            ds.createDimension(n, size)
        for short, role, unit in [("lat", "latitude", "degrees_north"), ("lon", "longitude", "degrees_east")]:
            coord = ds.createVariable(short, "f8", ("cell",))
            coord[:] = np.arange(5)
            coord.standard_name, coord.units, coord.bounds = role, unit, "bounds_" + short
            vertices = np.arange(30, dtype="f8").reshape(5, 6)
            vertices[0, -1] = vertices[0, -2]  # Preserve existing pentagon padding.
            ds.createVariable("bounds_" + short, "f8", ("cell", "nvertex"))[:] = vertices
        ds.createVariable("plev", "f8", ("plev",))[:] = [90000, 50000]
        ds.createVariable("time", "f8", ("time",))[:] = [1, 2]
        ds.createVariable("time_bounds", "f8", ("time", "axis_nbounds"))[:] = [[0, 1], [1, 2]]
        v = ds.createVariable("zg", "f4", ("time", "plev", "cell"), zlib=fmt.startswith("NETCDF4"), complevel=2)
        v[:] = np.arange(20).reshape(2, 2, 5)
        v.coordinates = "lat lon"


def steps_for(path):
    messages = [
        ("COORD011", "Horizontal grid coordinates", f"{role.title()} grid variable '{short}' was identified by standard_name='{role}', but its required variable name is '{role}'.")
        for short, role in [("lat", "latitude"), ("lon", "longitude")]
    ] + [
        ("COORD009", "Coordinate bounds naming", f"'{short}' bounds='bounds_{short}'; the required name is 'vertices_{role}'.")
        for short, role in [("lat", "latitude"), ("lon", "longitude")]
    ] + [("COORD009", "Coordinate bounds naming recommendations", "Vertex variable 'bounds_lat' uses trailing dimension 'nvertex'; the configured recommended name is 'vertices'.")]
    findings = [Finding(3, f"[CMIP7] [{code}] {check}", msg, "CMIP7.ATM.zg.tpt-p7h-hxy-air.6hr.glb.g243.IPSL-CM7A.piControl-spinup.r1i1p1f1", str(path)) for code, check, msg in messages]
    c = classify(QAReport(path.with_suffix(".json"), {}, findings), RuleSet.load(*default_rule_paths()))
    assert [x.match.rule.id if x.match else None for x in c.items] == ["C071", "C072", "C067", "C067", "C088"]
    return FixPlan.from_classification(c).files[str(path)]


@pytest.mark.parametrize("grid", ["g243", "g245"])
@pytest.mark.parametrize("fmt", ["NETCDF4", "NETCDF4_CLASSIC", "NETCDF3_64BIT_OFFSET"])
@pytest.mark.parametrize("order", [(0, 1, 2, 3, 4), (4, 3, 2, 1, 0), (2, 0, 4, 3, 1)])
def test_names_vertices_and_bounds_compose_in_place(tmp_path, monkeypatch, grid, fmt, order):
    path = tmp_path / "zg.nc"
    fixture(path, grid, fmt)
    with netCDF4.Dataset(path) as ds:
        original = {n: v[:].tobytes() for n, v in ds.variables.items()}
        storage = {n: (v.filters(), v.chunking()) for n, v in ds.variables.items()}
    steps = steps_for(path)
    plan = FixPlan("test", "qa", {str(path): [steps[i] for i in order]})

    def refuse(*args, **kwargs):
        raise AssertionError("Coordinate renames must not copy the main field")

    monkeypatch.setattr(Batch, "write", refuse)
    before, inode = path.read_bytes(), path.stat().st_ino
    assert apply_plan(plan, tmp_path / "dry", dry_run=True)["changed"] == 1
    assert path.read_bytes() == before
    assert apply_plan(plan, tmp_path / "log")["changed"] == 1
    with netCDF4.Dataset(path) as ds:
        assert set(ds.variables) == {MAPPING.get(n, n) for n in original}
        assert len(ds.dimensions["cell"]) == 5
        assert len(ds.dimensions["vertices"]) == 6 and len(ds.dimensions["bnds"]) == 2
        assert "nvertex" not in ds.dimensions and "axis_nbounds" not in ds.dimensions
        assert ds["zg"].dimensions == ("time", "plev", "cell")
        assert ds["zg"].coordinates == "latitude longitude"
        assert ds["latitude"].bounds == "vertices_latitude"
        assert ds["longitude"].bounds == "vertices_longitude"
        for n, raw in original.items():
            out = ds[MAPPING.get(n, n)]
            assert out[:].tobytes() == raw
            assert (out.filters(), out.chunking()) == storage[n]
        assert ds.history == "Global history stays."
    after = path.read_bytes()
    assert apply_plan(plan, tmp_path / "again")["changed"] == 0
    assert path.read_bytes() == after and path.stat().st_ino == inode


@pytest.mark.parametrize("invalid", ["collision", "shape", "role", "grid"])
def test_invalid_mesh_preflight_changes_nothing(tmp_path, invalid):
    path = tmp_path / "bad.nc"
    fixture(path, "g243", "NETCDF4")
    with netCDF4.Dataset(path, "a") as ds:
        if invalid == "collision":
            ds.createVariable("latitude", "f8", ("cell",))
        elif invalid == "shape":
            ds["lat"].bounds = "time_bounds"
        elif invalid == "role":
            ds["lat"].standard_name = "grid_latitude"
        else:
            ds.grid_label = "g999"
    before = path.read_bytes()
    q = FixPlan("test", "qa", {str(path): [Step("C071", "rename_horizontal_coordinate", {"role": "latitude"})]})
    with pytest.raises(ValueError):
        apply_plan(q, tmp_path / "log")
    assert path.read_bytes() == before


def test_renames_survive_a_separately_required_grouped_rewrite(tmp_path):
    path = tmp_path / "rewrite.nc"
    fixture(path, "g243", "NETCDF4")
    with netCDF4.Dataset(path, "a") as ds:
        ds.createDimension("klev", 1)
        ds.createVariable("klev", "i4", ("klev",))[:] = 0
    with netCDF4.Dataset(path) as ds:
        original = {n: v[:].tobytes() for n, v in ds.variables.items() if n != "klev"}
    q = FixPlan("test", "qa", {str(path): steps_for(path)})
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    with netCDF4.Dataset(path) as ds:
        for n, values in original.items():
            assert ds[MAPPING.get(n, n)][:].tobytes() == values
        assert "klev" not in ds.variables
    assert apply_plan(q, tmp_path / "again")["changed"] == 0


@pytest.mark.parametrize("grid", ["g243", "g245"])
@pytest.mark.parametrize("metadata_first", [False, True])
@pytest.mark.parametrize("canonical_message", [False, True])
def test_lowercase_long_names_compose_with_all_horizontal_renames(
    tmp_path, monkeypatch, grid, metadata_first, canonical_message
):
    path = tmp_path / "names.nc"
    fixture(path, grid, "NETCDF4")
    with netCDF4.Dataset(path, "a") as ds:
        ds["lat"].long_name = "Latitude"
        ds["lon"].long_name = "Longitude"
    with netCDF4.Dataset(path) as ds:
        raw = {n: v[:].tobytes() for n, v in ds.variables.items()}
        filters = ds["zg"].filters()
    findings = []
    for short, role in [("lat", "latitude"), ("lon", "longitude")]:
        coordinate = role if canonical_message else short
        findings.append(Finding(
            2, "[CMIP7] [COORD004] Coordinate metadata recommendations",
            f"'{coordinate}' long_name='{role.title()}'; the recommended value is '{role}'.",
            "CMIP7.ATM.zg.tpt-p7h-hxy-air.6hr.glb.g243.IPSL-CM7A.piControl-spinup.r1i1p1f1", str(path),
        ))
    c = classify(QAReport(path.with_suffix(".json"), {}, findings), RuleSet.load(*default_rule_paths()))
    assert all(item.match and item.match.rule.id == "C086" for item in c.items)
    metadata = FixPlan.from_classification(c).files[str(path)]
    names = steps_for(path)
    q = FixPlan("test", "qa", {str(path): metadata + names if metadata_first else names + metadata})

    def refuse(*args, **kwargs):
        raise AssertionError("Changing long_name must not rewrite science data")

    monkeypatch.setattr(Batch, "write", refuse)
    original, inode = path.read_bytes(), path.stat().st_ino
    apply_plan(q, tmp_path / "dry", dry_run=True)
    assert path.read_bytes() == original
    assert apply_plan(q, tmp_path / "log")["changed"] == 1
    with netCDF4.Dataset(path) as ds:
        assert ds["latitude"].long_name == "latitude"
        assert ds["longitude"].long_name == "longitude"
        assert ds["zg"].filters() == filters
        assert all(ds[MAPPING.get(n, n)][:].tobytes() == data for n, data in raw.items())
    corrected = path.read_bytes()
    assert apply_plan(q, tmp_path / "again")["changed"] == 0
    assert path.read_bytes() == corrected and path.stat().st_ino == inode
