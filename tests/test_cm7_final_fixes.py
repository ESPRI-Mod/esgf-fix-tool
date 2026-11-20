"""CM7 follow-ups reuse existing recipes and preserve scientific storage/data."""

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.hybrid import _coefficient_reference
from esgf_fix_tool.plan import FixPlan, Step
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet


def single(path, fix, params=None):
    return FixPlan('test', 'qa', {str(path): [Step('test', fix, params or {})]})


def make_hybrid(path, name, count, fmt, cells, real, real_b=None):
    reference = _coefficient_reference()[count]
    horizontal = [('cell', 3)] if cells else [('lat', 2), ('lon', 3)]
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.setncatts(dict(variable_id=name, branded_variable=name+'_tavg-al-hxy-u',
                         vertical_label='al', realm='atmos', source_id='IPSL-CM7A' if count == 96 else 'IPSL-CM6-ESMCO2'))
        for n, size in [('time', 2), ('lev', count), ('klevp1', count), ('bnds', 2), *horizontal]:
            ds.createDimension(n, size)
        ds.createVariable('lev', 'f4' if real else 'f8', ('lev',))[:] = reference['ap'] + 101325 * reference['b']
        ds['lev'].units = 'Pa'
        for term in ('ap', 'b'):
            ds.createVariable(term, 'f4' if (real if real_b is None else real_b) and term == 'b' else 'f8', ('klevp1',))[:] = reference[term]
        ds['ap'].units = 'Pa'
        ds.createVariable('ps', 'f4', ('time', *(n for n, _ in horizontal)))[:] = 100000
        ds['ps'].units = 'Pa'
        dims = ('time', 'lev', *(n for n, _ in horizontal))
        shape = (2, count, *(size for _, size in horizontal))
        values = np.ma.array(np.arange(np.prod(shape), dtype='f4').reshape(shape)/8, mask=False)
        values.mask[0, 20, ...] = True
        options = dict(zlib=True, complevel=2, chunksizes=(1, count, *(size for _, size in horizontal))) if fmt.startswith('NETCDF4') else {}
        v = ds.createVariable(name, 'f4', dims, fill_value=1e20, **options)
        v[:] = values
        v.comment = 'Preserve scientific comment'
        v.description = 'Preserve scientific description'
    return values


@pytest.mark.parametrize('name', ['edt', 'evu'])
@pytest.mark.parametrize('count', [80, 96])
@pytest.mark.parametrize('cells', [False, True])
@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
def test_both_hybrid_families_share_one_centering_recipe(tmp_path, name, count, cells, fmt):
    path = tmp_path/'hybrid.nc'
    values = make_hybrid(path, name, count, fmt, cells, real=count == 96)
    with netCDF4.Dataset(path) as ds:
        filters = ds[name].filters()
        ps = ds['ps'][:].tobytes()
    steps = [Step('C102', 'normalize_hybrid_coordinate', {}),
             Step('C074a', 'fix_hybrid_coefficients', {'coordinate': 'auto'}),
             Step('C103e', 'center_hybrid_interfaces', {})]
    if count == 96:
        steps.append(Step('C117', 'promote_coordinate_to_double', {'var': 'lev'}))
    q = FixPlan('test', 'qa', {str(path): steps})
    before = path.read_bytes()
    apply_plan(q, tmp_path/'dry', dry_run=True)
    assert path.read_bytes() == before
    assert apply_plan(q, tmp_path/'log')['changed'] == 1
    assert json.loads((tmp_path/'log').read_text())['rewrite_count'] == 1
    with netCDF4.Dataset(path) as ds:
        actual = ds[name][:]
        expected = (values[:, :-1].astype('f8')*.5 + values[:, 1:].astype('f8')*.5).astype('f4')
        assert actual.shape[1] == count-1
        np.testing.assert_array_equal(np.ma.getmaskarray(actual), np.ma.getmaskarray(expected))
        np.testing.assert_array_equal(actual.compressed(), expected.compressed())
        assert ds[name].filters() == filters and ds['ps'][:].tobytes() == ps
        assert ds[name].comment == 'Preserve scientific comment'
        assert ds[name].description == 'Preserve scientific description'
        reference = _coefficient_reference()[count-1]
        for term in ('ap', 'b'):
            np.testing.assert_array_equal(ds[term][:], reference[term])
            assert ds[term+'_bnds'].dimensions == ('lev', 'bnds')
        assert ds['lev'].bounds == 'lev_bnds' and ds['lev'].dtype == np.dtype('f8')
        if fmt.startswith('NETCDF4'):
            assert ds[name].chunking()[1] == count-1
    corrected = path.read_bytes()
    assert apply_plan(q, tmp_path/'again')['changed'] == 0 and path.read_bytes() == corrected


@pytest.mark.parametrize('real_b', [False, True])
def test_c102_alone_promotes_and_normalizes_real_cm7_interfaces(tmp_path, real_b):
    path = tmp_path/'interfaces.nc'
    make_hybrid(path, 'smc', 96, 'NETCDF4', True, real=True, real_b=real_b)
    with netCDF4.Dataset(path, 'a') as ds:
        ds.vertical_label = 'alh';ds.branded_variable = 'smc_tavg-alh-hxy-u'
        before = ds['smc'][:].tobytes()
        ap, b = ds['ap'][:].astype('f8'), ds['b'][:].astype('f8')
    q = single(path, 'normalize_hybrid_coordinate')
    apply_plan(q, tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['lev'].dtype == np.dtype('f8') and ds['b'].dtype == np.dtype('f8')
        np.testing.assert_array_equal(ds['lev'][:], ap/101325+b)
        np.testing.assert_array_equal(ds['b'][:], b)
        assert ds['smc'][:].tobytes() == before and 'bounds' not in ds['lev'].ncattrs()
    assert apply_plan(q, tmp_path/'again')['changed'] == 0


@pytest.mark.parametrize('var', ['fNleach', 'mrsow', 'rivi'])
def test_new_comments_use_the_existing_audited_normalizer(tmp_path, var):
    specs=json.loads((Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/comment_specs.json').read_text())
    brand = var+'_tavg-u-hxy-lnd'
    observed, expected = (specs[brand]['variants'][-1][k] for k in ('observed','value'))
    message = f"Expected '{observed}' to contain '{expected}' from registry key 'comment'."
    finding=Finding(1, f"[CMIP7] [ATTR004] Variable '{var}' attribute 'comment' registry expected-term check",message,
                    f'CMIP7.SRF.{var}.tavg-u-hxy-lnd.day.glb.g131.A_MODEL.an_experiment.r1i1p1f1',str(tmp_path/'comment.nc'))
    q=FixPlan.from_classification(classify(QAReport(None,{},[finding]),RuleSet.load(*default_rule_paths())))
    assert q.files[finding.path][0].rule == 'C002a'
    with netCDF4.Dataset(finding.path,'w') as ds:
        ds.variable_id=var;ds.branded_variable=brand
        ds.createVariable(var,'f4').assignValue(2)
        ds[var].comment=observed
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(finding.path) as ds:assert ds[var].comment==expected
    assert apply_plan(q,tmp_path/'again')['changed']==0


def test_msfty_accepts_331_meridional_rows_without_resizing(tmp_path):
    path=tmp_path/'msfty.nc'
    with netCDF4.Dataset(path,'w') as ds:
        ds.variable_id='msfty'
        for n,size in [('time',1),('basin',3),('lev',75),('y',331)]:ds.createDimension(n,size)
        ds.createVariable('nav_lat','f8',('y',))[:]=np.linspace(-80,89,331)
        ds.createVariable('nav_lon','f8',('y',))[:]=0
        ds.createVariable('lev','f8',('lev',))[:]=np.arange(75)+.5;ds['lev'].units='m'
        ds.createVariable('msfty','f4',('time','basin','lev','y'),zlib=True,complevel=2)[:]=np.arange(3*75*331).reshape(1,3,75,331)
        ds['msfty'].coordinates='nav_lat nav_lon'
    with netCDF4.Dataset(path) as ds:before=ds['msfty'][:].tobytes()
    q=single(path,'fix_msfty_coordinates')
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(path) as ds:
        assert ds['msfty'].dimensions==('time','basin','lev','rlat') and ds['rlat'].shape==(331,)
        assert ds['msfty'][:].tobytes()==before and ds['rlat'].standard_name=='grid_latitude'
        assert ds['rlat'].long_name=='Grid Latitude' and ds['rlat'].units=='degrees'
        assert 'nav_lon' not in ds.variables
    assert apply_plan(q,tmp_path/'again')['changed']==0


def test_seasurface_inverse_dr_recommendation_is_waived():
    f=Finding(3,'[CMIP7] [COORD003] Coordinate metadata',"'seasurface' axis=''; the required value is 'Z'.",'CMIP7.OCE.sos.tavg-u-hxy-sea.mon.glb.g112.MODEL.experiment.r1i1p1f1','/tmp/a.nc')
    m=RuleSet.load(*default_rule_paths()).match(f)
    assert m.rule.id=='C073b' and m.rule.action.value=='waive'
