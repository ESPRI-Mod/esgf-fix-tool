"""Distinguish cell-method axes from prose, and retain the approved basin codes."""
import json

import netCDF4
import numpy as np
import pytest

from esgf_fix_tool.apply import apply_plan
from esgf_fix_tool.fixes.coordinates import _replace_references
from esgf_fix_tool.fixes.reviewed_structures import BASIN_LABELS, unreferenced
from esgf_fix_tool.plan import FixPlan, Step


METHODS = ('depth: longitude: sum where sea '
           '(along a zig-zag grid path spanning a basin) where sea time: mean')


@pytest.mark.parametrize('fmt', ['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT_OFFSET'])
@pytest.mark.parametrize('numeric', [True, False])
def test_sector_preserves_basin_and_exact_cell_methods(tmp_path, fmt, numeric):
    path = tmp_path / 'hfbasin.nc'
    science = np.arange(6, dtype='f4').reshape(2, 3)
    with netCDF4.Dataset(path, 'w', format=fmt) as ds:
        ds.source_id = 'IPSL-CM6-ESMCO2'
        ds.variable_id = 'hfbasin'
        ds.createDimension('time', 2)
        ds.createDimension('basin', 3)
        if numeric:
            basin = ds.createVariable('basin', 'f8', ('basin',))
            basin[:] = [1, 2, 3]
            basin.long_name = 'Sub-basin mask (1=Global 2=Atlantic 3=Indo-Pacific)'
            basin.standard_name = 'region'
            basin.flag_values = np.array([1, 2, 3], dtype='i4')
            basin.flag_meanings = 'global_ocean atlantic_ocean indo_pacific_ocean'
            saved_attrs = {a: basin.getncattr(a) for a in basin.ncattrs()}
        else:
            # Old tool outputs may already have sector and no numeric basin.
            ds.createDimension('strlen', 21)
            labels = np.full((3, 21), b' ', dtype='S1')
            for row, label in zip(labels, BASIN_LABELS):
                row[:len(label)] = np.frombuffer(label.encode(), dtype='S1')
            ds.createVariable('sector', 'S1', ('basin', 'strlen'))[:] = labels
        main = ds.createVariable('hfbasin', 'f4', ('time', 'basin'))
        main[:] = science
        main.coordinates = 'basin' if numeric else 'sector'
        main.cell_methods = METHODS

    plan = FixPlan('t', 's', {str(path): [Step('C032', 'fix_basin_sector', {})]})
    before = path.read_bytes()
    apply_plan(plan, tmp_path / 'dry.jsonl', dry_run=True)
    assert path.read_bytes() == before
    apply_plan(plan, tmp_path / 'apply.jsonl')
    with netCDF4.Dataset(path) as ds:
        assert len(ds.dimensions['basin']) == 3
        assert ds['hfbasin'].cell_methods == METHODS
        assert ds['hfbasin'].coordinates == 'sector'
        np.testing.assert_array_equal(ds['hfbasin'][:], science)
        assert ds['sector'].dimensions == ('basin', 'strlen')
        assert [row.tobytes().decode().strip() for row in ds['sector'][:]] == list(BASIN_LABELS)
        assert ('basin' in ds.variables) == numeric
        if numeric:
            np.testing.assert_array_equal(ds['basin'][:], [1, 2, 3])
            assert ds['basin'].units == '1'
            assert ds['basin'].long_name == 'Ocean Basin Index'
            assert not any(a in ds['basin'].ncattrs() for a in ('standard_name', 'flag_values', 'flag_meanings'))
    # No deletion/reordering: adding a sector uses direct updates.
    assert json.loads((tmp_path / 'apply.jsonl').read_text())['rewrite_count'] == 0
    after = path.read_bytes()
    assert apply_plan(plan, tmp_path / 'replay.jsonl')['changed'] == 0
    assert path.read_bytes() == after


@pytest.mark.parametrize('methods', [METHODS, 'time: mean where basin',
                                   'time: mean (basin: not an axis (basin))'])
def test_comment_and_qualifier_words_are_not_variable_references(tmp_path, methods):
    with netCDF4.Dataset(tmp_path / 'refs.nc', 'w') as ds:
        ds.createVariable('science', 'f4').cell_methods = methods
        unreferenced(ds, {'basin'})
        _replace_references(ds, 'basin', 'sector')
        assert ds['science'].cell_methods == methods


@pytest.mark.parametrize('attr,text', [('cell_methods', 'basin: time: mean (basin: prose)'),
                                     ('cell_methods', 'time: mean basin : sum'),
                                     ('coordinates', 'latitude basin'),
                                     ('formula_terms', 'a: basin b: b')])
def test_real_reference_still_blocks_deletion(tmp_path, attr, text):
    with netCDF4.Dataset(tmp_path / 'refs.nc', 'w') as ds:
        ds.createVariable('science', 'f4').setncattr(attr, text)
        with pytest.raises(ValueError, match='still referenced'):
            unreferenced(ds, {'basin'})


def test_rename_changes_axis_names_but_keeps_comment_exactly(tmp_path):
    with netCDF4.Dataset(tmp_path / 'refs.nc', 'w') as ds:
        ds.createVariable('science', 'f4').cell_methods = 'basin: time: mean (basin: prose (basin))'
        _replace_references(ds, 'basin', 'sector')
        assert ds['science'].cell_methods == 'sector: time: mean (basin: prose (basin))'


@pytest.mark.parametrize('text', ['time: mean (basin', 'time: mean ) basin: sum'])
def test_malformed_comments_block_reference_checks(tmp_path, text):
    with netCDF4.Dataset(tmp_path / 'refs.nc', 'w') as ds:
        ds.createVariable('science', 'f4').cell_methods = text
        with pytest.raises(ValueError, match='Unbalanced parentheses'):
            unreferenced(ds, {'basin'})
