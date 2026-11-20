"""Create the four reviewed categorical labels without touching line or science."""

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.batch_write import Batch
from esgf_fix_tool.classify import classify
from esgf_fix_tool.cli.catalogues import default_rule_paths
from esgf_fix_tool.fixes.final_coordinates import ICE_PASSAGES
from esgf_fix_tool.plan import FixPlan
from esgf_fix_tool.preview import Variable
from esgf_fix_tool.report import Finding, QAReport
from esgf_fix_tool.ruleset import RuleSet

PRODUCTS=['siareaacrossline','simassacrossline','sisnmassacrossline']


def comment(var):
    specs=json.loads((Path(__file__).parents[1]/'src/esgf_fix_tool/fixes/comment_specs.json').read_text())
    return specs[var+'_tavg-u-ht-u']['variants'][0]['value']


def plan(path,var):
    f=Finding(3,'[CMIP7] [COORD001] Coordinate identity and structure',
              "The required text auxiliary coordinate 'sector' for coordinate ID 'siline' is absent, so its configured dependent coordinate checks could not be evaluated.",
              f'CMIP7.OCE.{var}.tavg-u-ht-u.mon.glb.g112.A_MODEL.an_experiment.r1i1p1f1',str(path))
    q=FixPlan.from_classification(classify(QAReport(None,{},[f]),RuleSet.load(*default_rule_paths())))
    assert q.files[str(path)][0].rule=='C035' and q.files[str(path)][0].fix=='fix_ice_passage_sector'
    return q


def make(path,var,fmt):
    with netCDF4.Dataset(path,'w',format=fmt) as ds:
        ds.variable_id=var;ds.branded_variable=var+'_tavg-u-ht-u';ds.grid_label='g112';ds.realm='seaIce'
        ds.createDimension('time',None);ds.createDimension('line',4)
        ds.createVariable('time','f8',('time',))[:]=[15.5,45]
        options=dict(zlib=True,complevel=2) if fmt.startswith('NETCDF4') else {}
        v=ds.createVariable(var,'f4',('time','line'),fill_value=1e20,**options)
        data=np.ma.array(np.arange(8,dtype='f4').reshape(2,4),mask=False);data.mask[1,2]=True;v[:]=data
        v.comment=comment(var);v.coordinates='';v.long_name='Preserve scientific name';v.cell_methods='time: mean'
    with netCDF4.Dataset(path) as ds:
        ds.set_auto_maskandscale(False)
        return ds[var][:].tobytes(),ds[var].filters(),ds[var].chunking()


@pytest.mark.parametrize('var',PRODUCTS)
@pytest.mark.parametrize('fmt',['NETCDF4','NETCDF4_CLASSIC','NETCDF3_64BIT_OFFSET'])
def test_missing_sector_is_appended_directly_and_replays(tmp_path,monkeypatch,var,fmt):
    path=tmp_path/'passages.nc';raw,filters,chunks=make(path,var,fmt)
    original=path.read_bytes();inode=path.stat().st_ino;q=plan(path,var)
    def forbid(*a):raise AssertionError('Adding labels must not rebuild the scientific matrix')
    monkeypatch.setattr(Batch,'write',forbid)
    getitem=Variable.__getitem__
    def guarded(v,key):
        assert v.name!=var,'The label recipe must not read scientific data'
        return getitem(v,key)
    monkeypatch.setattr(Variable,'__getitem__',guarded)
    apply_plan(q,tmp_path/'dry',dry_run=True);assert path.read_bytes()==original
    assert apply_plan(q,tmp_path/'log')['changed']==1 and path.stat().st_ino==inode
    assert json.loads((tmp_path/'log').read_text())['rewrite_count']==0
    with netCDF4.Dataset(path) as ds:
        assert len(ds.dimensions['line'])==4 and len(ds.dimensions['strlen'])==21
        assert 'line' not in ds.variables and 'rlat' not in ds.variables
        labels=ds['sector'];labels.set_auto_chartostring(False)
        actual=[row.tobytes().decode('ascii').rstrip(' ') for row in labels[:]]
        assert actual==list(ICE_PASSAGES)
        assert labels.dimensions==('line','strlen') and labels.standard_name=='region' and labels.long_name=='ocean passage'
        assert 'units' not in labels.ncattrs()
        assert ds[var].coordinates=='sector' and ds[var].comment==comment(var)
        assert ds[var].long_name=='Preserve scientific name' and ds[var].cell_methods=='time: mean'
        ds.set_auto_maskandscale(False)
        assert ds[var][:].tobytes()==raw and ds[var].filters()==filters and ds[var].chunking()==chunks
    corrected=path.read_bytes()
    assert apply_plan(q,tmp_path/'again')['changed']==0 and path.read_bytes()==corrected


@pytest.mark.parametrize('failure',['wrong_comment_order','wrong_brand','other_coordinates','shared_strlen'])
def test_creation_requires_confirmed_order_and_safe_string_dimension(tmp_path,failure):
    var='siareaacrossline';path=tmp_path/'passages.nc';make(path,var,'NETCDF4')
    with netCDF4.Dataset(path,'a') as ds:
        if failure=='wrong_comment_order':ds[var].comment=ds[var].comment.replace('1. Fram Strait','2. Fram Strait')
        elif failure=='wrong_brand':ds.branded_variable='unknown_tavg-u-ht-u'
        elif failure=='other_coordinates':ds[var].coordinates='unknown'
        else:
            ds.createDimension('strlen',8);ds.createVariable('unrelated','S1',('strlen',))[:]=np.array(list('preserve'),dtype='S1')
    before=path.read_bytes()
    with pytest.raises(ValueError):apply_plan(plan(path,var),tmp_path/'log')
    assert path.read_bytes()==before
