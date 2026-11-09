import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step


@pytest.mark.parametrize("include_layout", [False, True])
def test_hemisphere_grid_correction_keeps_region_and_reports_drs_name(tmp_path, include_layout):
    name = "siarea_tavg-u-hm-u_mon_nh_g112_IPSL-CM6-ESMCO2_esm-piControl-spinup_r1i1p1f1_185001-185912.nc"
    path = tmp_path / name
    with netCDF4.Dataset(path, "w") as ds:
        ds.setncatts({
            "source_id": "IPSL-CM6-ESMCO2", "experiment_id": "esm-piControl-spinup",
            "variant_label": "r1i1p1f1", "variable_id": "siarea", "frequency": "mon",
            "grid_label": "g112", "region": "nh", "horizontal_label": "hm",
            "branded_variable": "siarea_tavg-u-hm-u",
        })
        ds.createDimension("time", 2)
        ds.createDimension("x", 1)
        ds.createDimension("y", 1)
        ds.createVariable("siarea", "f4", ("time", "y", "x"))[:, 0, 0] = [2, 3]
    steps = [Step("C089", "set_reduced_grid_label",
                  dict(source="g112", horizontal="hm", region="nh", target="g011"))]
    if include_layout:
        steps.append(Step("C049", "fix_data_layout",
                          dict(var="siarea", target_order=["time"], drop=["y", "x"])))
    plan = FixPlan("test", "synthetic", {str(path): steps})
    inode = path.stat().st_ino
    log = tmp_path / "fixlog.jsonl"
    assert apply_plan(plan, log, only_under=tmp_path)["changed"] == 1
    assert apply_plan(plan, log, only_under=tmp_path)["changed"] == 0

    record = json.loads(log.read_text().splitlines()[0])
    target = tmp_path / name.replace("_nh_g112_", "_nh_g011_")
    assert record["drs_rename"] == {"source": str(path), "target": str(target)}
    assert record["rewrite_count"] == int(include_layout)
    assert (path.stat().st_ino == inode) == (not include_layout)
    with netCDF4.Dataset(path) as ds:
        assert ds.region == "nh" and ds.grid_label == "g011"
        assert ds["siarea"].dimensions == (("time",) if include_layout else ("time", "y", "x"))
        np.testing.assert_array_equal(ds["siarea"][:].reshape(2), [2, 3])
