"""Numerical monthly climatologies, calendars, composition and no-write failures."""
import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.plan import FixPlan, Step


def fixture(path, calendar='proleptic_gregorian', fmt='NETCDF4', fixed=False):
    import cftime
    units = 'days since 1850-01-01'
    dates = [cftime.datetime(1850+i//12,i%12+1,1,calendar=calendar) for i in range(121)]
    edges = netCDF4.date2num(dates,units,calendar)
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.temporal_label = 'tclm'; ds.frequency = 'mon'; ds.variable_id = 'pfull'
        ds.source_id = 'IPSL-CM6-ESMCO2'
        ds.createDimension('time',120 if fixed else None)
        ds.createDimension('x',3); ds.createDimension('bnds',2)
        t = ds.createVariable('time','f8',('time',))
        t.units = units; t.calendar = calendar; t.climatology = 'climatology_bnds'
        t[:] = (edges[:-1]+edges[1:])/2
        ds.createVariable('climatology_bnds','f8',('time','bnds'))[:] = np.column_stack((edges[:-1],edges[1:]))
        data = np.arange(120)//12 + (np.arange(120)%12)*10.
        a = ds.createVariable('pfull','f4',('time','x'),fill_value=np.float32(1e20),zlib=fmt.startswith('NETCDF4'))
        a[:] = np.repeat(data[:,None],3,axis=1)
        a[:,2] = np.float32(1e20); a[0,1] = np.float32(1e20)
        a.cell_methods = 'area: mean time: mean within years time: mean over years'
        a.history = 'remove in the same rewrite'
        ps = ds.createVariable('ps','f4',('time','x') if fmt.startswith('NETCDF3') else ('x','time'),fill_value=np.float32(1e20))
        ps[:] = np.repeat(data[:,None],3,axis=1) if fmt.startswith('NETCDF3') else np.repeat(data[None,:],3,axis=0)
        ps.cell_methods = 'area: time: mean'
        ds.createVariable('ap','f8',('x',))[:] = [1.,2.,3.]
    return edges


def plan(path, reverse=False):
    steps = [Step('C065','fix_climatology_reference',{}),
             Step('C106','rebuild_monthly_climatology',{}),
             Step('C001','drop_var_attr',{'var':'pfull','attr':'history'})]
    if reverse: steps.reverse()
    return FixPlan('t','s',{str(path):steps})


@pytest.mark.parametrize('calendar',['proleptic_gregorian','noleap','360_day'])
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('reverse',[False,True])
def test_equal_year_means_auxiliary_masks_bounds_and_replay(tmp_path,calendar,fmt,reverse):
    p = tmp_path/'pfull.nc'; edges = fixture(p,calendar,fmt)
    q = plan(p,reverse); before = p.read_bytes()
    assert apply_plan(q,tmp_path/'dry.jsonl',dry_run=True)['changed'] == 1
    assert p.read_bytes() == before
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert len(ds.dimensions['time']) == 12 and ds.dimensions['time'].isunlimited()
        np.testing.assert_array_equal(ds['pfull'][:,0], np.arange(12)*10+4.5)
        # January's missing first year must be excluded, not treated as zero.
        assert ds['pfull'][0,1] == 5.
        assert np.ma.getmaskarray(ds['pfull'][:,2]).all()
        np.testing.assert_array_equal(ds['ps'][:,0] if fmt.startswith('NETCDF3') else ds['ps'][0,:], np.arange(12)*10+4.5)
        np.testing.assert_array_equal(ds['ap'][:],[1.,2.,3.])
        np.testing.assert_array_equal(ds['climatology_bnds'][:],np.column_stack((edges[:12],edges[-12:])))
        np.testing.assert_array_equal(ds['time'][:],(edges[60:72]+edges[61:73])/2)
        assert ds['ps'].cell_methods == 'area: time: mean within years time: mean over years'
        assert 'history' not in ds['pfull'].ncattrs()
        assert 'bounds' not in ds['time'].ncattrs()
    assert json.loads((tmp_path/'log').read_text())['rewrite_count'] == 1
    after = p.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed'] == 0
    assert p.read_bytes() == after


def test_fixed_time_dimension_and_chunk_mapping(tmp_path):
    p = tmp_path/'fixed.nc'; fixture(p,fixed=True)
    apply_plan(plan(p),tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert not ds.dimensions['time'].isunlimited() and len(ds.dimensions['time']) == 12


def test_time_reference_fix_alone_also_rebuilds_unaggregated_tclm(tmp_path):
    p = tmp_path/'reference.nc'; fixture(p)
    with netCDF4.Dataset(p,'a') as ds:
        ds.renameVariable('climatology_bnds','time_bnds')
        ds.renameDimension('bnds','axis_nbounds')
        ds['time'].delncattr('climatology')
        ds['time'].bounds = 'time_bnds'
    q = FixPlan('t','s',{str(p):[Step('C065','fix_climatology_reference',{})]})
    apply_plan(q,tmp_path/'log')
    with netCDF4.Dataset(p) as ds:
        assert len(ds.dimensions['time']) == 12
        assert ds['time'].climatology == 'climatology_bnds'
        np.testing.assert_array_equal(ds['pfull'][:,0],np.arange(12)*10+4.5)


def test_corrupted_aggregated_output_is_not_installed(tmp_path,monkeypatch):
    from esgf_fix_tool.fixes import REGISTRY, FixFunction
    p = tmp_path/'atomic.nc'; fixture(p); before = p.read_bytes()
    def corrupt(source, output):
        with netCDF4.Dataset(output,'a') as ds:
            ds['ps'][0,0] = 999.
    monkeypatch.setitem(REGISTRY,'corrupt_clim',FixFunction('corrupt_clim','metadata',lambda ds: True,after_close=corrupt))
    q = plan(p); q.files[str(p)].append(Step('test','corrupt_clim',{}))
    with pytest.raises(ValueError,match='Climatological mean differs'):
        apply_plan(q,tmp_path/'log')
    assert p.read_bytes() == before


@pytest.mark.parametrize('invalid',['gap','duplicate','partial','wrong_statistic','integer','packing','label'])
def test_ambiguous_series_refused_before_write(tmp_path,invalid):
    p = tmp_path/'bad.nc'; fixture(p)
    with netCDF4.Dataset(p,'a') as ds:
        if invalid == 'gap': ds['climatology_bnds'][20,0] += 1
        elif invalid == 'duplicate': ds['time'][20] = ds['time'][19]
        elif invalid == 'partial': ds['climatology_bnds'][0,0] = 1
        elif invalid == 'wrong_statistic': ds['ps'].cell_methods = 'time: maximum'
        elif invalid == 'integer': ds.createVariable('index','i4',('time',))[:]=0
        elif invalid == 'packing': ds['pfull'].scale_factor = .1
        else: ds.temporal_label = 'tmaxavg'
    before = p.read_bytes()
    with pytest.raises(ValueError): apply_plan(plan(p),tmp_path/'log')
    assert p.read_bytes() == before and not list(tmp_path.glob('*.fixtmp'))
