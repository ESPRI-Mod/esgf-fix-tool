import hashlib
import json
import threading
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.cli.main import app
from esgf_fix_tool.cli.progress import ConsoleProgress
from esgf_fix_tool.data_integrity import _slices, data_fingerprints
from esgf_fix_tool.fixes import REGISTRY, FixFunction
from esgf_fix_tool.plan import FixPlan, Step


def make(path):
    with netCDF4.Dataset(path, 'w') as ds:
        ds.createDimension('time',3)
        v=ds.createVariable('tas','f4',('time',));v[:]=[1.,2.,3.];v.history='original variable history'
        ds.history='global history'


def plan(path,fix='drop_var_attr',params=None):
    return FixPlan('test','report',{str(path):[Step('C001',fix,params if params is not None else {'var':'tas','attr':'history'})]})


@pytest.mark.parametrize('shape',[(),(0,7),(8,0),(7,),(2,3),(5,7,9),(2,4,7,11)])
def test_bounded_slices_follow_canonical_byte_order_without_revisiting_data(shape):
    a=np.arange(np.prod(shape,dtype=int),dtype='f8').reshape(shape)
    parts=[np.asarray(a[slab]) for slab in _slices(shape,8,budget=64)]
    assert b''.join(p.tobytes() for p in parts)==a.tobytes()
    assert all(p.nbytes<=64 for p in parts)


def test_fingerprints_are_independent_of_compression_and_chunk_shapes(tmp_path,monkeypatch):
    from esgf_fix_tool import data_integrity
    original=data_integrity._slices
    monkeypatch.setattr(data_integrity,'_slices',lambda shape,itemsize:original(shape,itemsize,budget=128))
    values=np.arange(5*7*9,dtype='f4').reshape(5,7,9);values[0,0,0]=np.nan
    results=[]
    for i,chunks in enumerate([None,(1,7,9),(5,1,3)]):
        p=tmp_path/f'{i}.nc'
        with netCDF4.Dataset(p,'w') as ds:
            for n,size in zip('abc',values.shape):ds.createDimension(n,size)
            options=dict(zlib=True,chunksizes=chunks) if chunks else {}
            ds.createVariable('v','f4',tuple('abc'),**options)[:]=values
        with netCDF4.Dataset(p) as ds:results.append(data_fingerprints(ds))
    assert results[0]==results[1]==results[2]
    assert next(iter(results[0]))[-1]==hashlib.sha256(values.tobytes()).hexdigest()


def test_done_is_emitted_only_after_hooks_verification_and_atomic_commit(tmp_path,monkeypatch):
    p=tmp_path/'test.nc';make(p);before=p.read_bytes();events=[]
    original=REGISTRY['drop_var_attr']
    def after(source,output,**params):
        assert p.read_bytes()==before
        assert not any(e.kind=='fix' and e.status=='done' for e in events)
    monkeypatch.setitem(REGISTRY,'drop_var_attr',FixFunction('drop_var_attr','metadata',original.func,
                        verify_data=True,after_close=after))
    def observe(event):
        events.append(event)
        if event.kind=='fix' and event.status=='done':
            with netCDF4.Dataset(p) as ds:assert 'history' not in ds['tas'].ncattrs()
    apply_plan(plan(p),tmp_path/'log',events=observe)
    assert events[0].kind=='file' and events[0].status=='start'
    assert [e.status for e in events if e.kind=='fix']==['start','prepared','done']
    assert any(e.kind=='io' and e.details['variable']=='tas' for e in events)
    assert events[-1].kind=='file' and events[-1].status=='done'


@pytest.mark.parametrize('error',[ValueError,KeyboardInterrupt])
def test_failure_or_cancellation_keeps_completed_files_and_never_reports_pending_fix_done(tmp_path,monkeypatch,error):
    first,second=tmp_path/'first.nc',tmp_path/'second.nc';make(first);make(second)
    before=second.read_bytes();events=[]
    def reject(ds):raise error('stopped during recipe')
    monkeypatch.setitem(REGISTRY,'reject_test',FixFunction('reject_test','metadata',reject))
    p=plan(first);p.files[str(second)]=[Step('C002','reject_test',{})]
    with pytest.raises(error):apply_plan(p,tmp_path/'log',events=events.append)
    assert second.read_bytes()==before
    assert len((tmp_path/'log').read_text().splitlines())==1
    assert any(e.path==str(first) and e.kind=='fix' and e.status=='done' for e in events)
    assert not any(e.path==str(second) and e.kind=='fix' and e.status=='done' for e in events)
    assert events[-1].status==('cancelled' if error==KeyboardInterrupt else 'failed')
    assert not list(tmp_path.glob('*.fixtmp'))


def test_cli_displays_titles_parameters_and_persists_live_events(tmp_path):
    p=tmp_path/'test.nc';make(p);q=tmp_path/'plan.json';plan(p).dump(q)
    log=tmp_path/'fixlog.jsonl'
    result=CliRunner().invoke(app,['apply',str(q),'--fixlog',str(log)])
    assert result.exit_code==0,result.output
    assert '[1/1] En cours' in result.output and 'Fix: C001' in result.output
    assert 'Suppression' in result.output and '"attr": "history"' in result.output
    assert result.output.index('Préparé')<result.output.index(' - Done')
    assert '\x1b[' not in result.output
    events=[json.loads(s) for s in (tmp_path/'fixlog.events.jsonl').read_text().splitlines()]
    assert events[0]['kind']=='file' and events[0]['status']=='start'
    assert any(e['phase']=='Application directe des noms et attributs' for e in events)
    assert events[-1]['kind']=='file' and events[-1]['status']=='done'
    quiet=CliRunner().invoke(app,['apply',str(q),'--fixlog',str(log),'--quiet'])
    assert quiet.exit_code==0 and 'Fix:' not in quiet.output and "'unchanged': 1" in quiet.output


def test_renderer_has_no_periodic_worker(tmp_path):
    import esgf_fix_tool.cli.progress as progress
    assert not hasattr(progress, 'threading')
    with ConsoleProgress(tmp_path/'events') as display:
        assert not hasattr(display, 'thread')


@pytest.mark.parametrize('stage',['open','write','close'])
def test_optional_event_log_failure_does_not_prevent_the_requested_fix(tmp_path,monkeypatch,stage):
    p=tmp_path/'test.nc';make(p);q=tmp_path/'plan.json';plan(p).dump(q)
    original=Path.open
    class BrokenStream:
        def __init__(self,stream):self.stream=stream
        def write(self,text):
            if stage=='write':raise OSError('synthetic event-log write failure')
            return self.stream.write(text)
        def flush(self):self.stream.flush()
        def close(self):
            self.stream.close()
            if stage=='close':raise OSError('synthetic event-log close failure')
    def open_checked(path,*args,**kwargs):
        if str(path).endswith('.events.jsonl'):
            if stage=='open':raise PermissionError('synthetic event-log permission failure')
            return BrokenStream(original(path,*args,**kwargs))
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_checked)
    result=CliRunner().invoke(app,['apply',str(q),'--fixlog',str(tmp_path/'fixlog.jsonl')])
    assert result.exit_code==0,result.output
    assert 'Journal de progression indisponible' in result.output
    with netCDF4.Dataset(p) as ds:assert 'history' not in ds['tas'].ncattrs()
