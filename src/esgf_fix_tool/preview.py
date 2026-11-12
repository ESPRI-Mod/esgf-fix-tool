"""Small, diskless metadata preview; science arrays are read lazily from the read-only source.

The preview contains headers only, never a memory copy of a large scientific field.
Numeric edits are limited to the small auxiliary arrays used by reviewed recipes.
"""
import netCDF4
import numpy as np

from .fixes.layouts import _copy_attrs, _creation_options, _raw

MAX_AUX_BYTES = 8 * 1024 * 1024


class Variable:
    def __init__(self, header, source=None):
        object.__setattr__(self, '_header', header)
        object.__setattr__(self, '_source', source)
        object.__setattr__(self, '_data', None)

    def __getattr__(self, name):
        return getattr(self._header, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._header, name, value)

    def set_auto_maskandscale(self, enabled):
        self._header.set_auto_maskandscale(enabled)
        if self._source is not None:
            self._source.set_auto_maskandscale(enabled)

    def set_auto_chartostring(self, enabled):
        self._header.set_auto_chartostring(enabled)
        if self._source is not None:
            self._source.set_auto_chartostring(enabled)

    def __getitem__(self, key):
        if self._data is not None:
            return self._data[key]
        if self._source is not None:
            return self._source[key]
        return self._array()[key]

    def _array(self):
        if self._data is None:
            dtype = object if self.dtype is str else self.dtype
            if self.size * max(8, np.dtype(dtype).itemsize) > MAX_AUX_BYTES:
                raise ValueError(f'Recipe attempts a large numeric edit of {self.name}; an explicit transform is required')
            if self._source is not None:
                _raw(self._source)
                self._data = np.asarray(self._source[...],dtype=object if self.dtype is str else None).copy()
            else:
                self._data = np.zeros(self.shape, dtype=dtype)
        return self._data

    def __setitem__(self, key, value):
        self._array()[key] = value

    def assignValue(self, value):
        self[()] = value


class Preview:
    def __init__(self, source):
        if source.groups or source.cmptypes or source.enumtypes or source.vltypes:
            raise ValueError('The single-write planner requires a flat NetCDF file without custom types')
        object.__setattr__(self, '_source', source)
        object.__setattr__(self, '_header', netCDF4.Dataset('metadata-preview', 'w', diskless=True, persist=False))
        object.__setattr__(self, 'variables', {})
        object.__setattr__(self, '_dim_origins', {})
        object.__setattr__(self, '_deleted_dimensions', set())
        _copy_attrs(source, self._header)
        for name, dim in source.dimensions.items():
            self._header.createDimension(name, None if dim.isunlimited() else len(dim))
            self._dim_origins[name] = name

        # Extending only a private byte variable sets an unlimited dimension's length
        # without allocating any part of a science array. Recipes never see this variable.
        for i, (name, dim) in enumerate(source.dimensions.items()):
            if dim.isunlimited() and len(dim):
                private_name = f'__extent_{i}'
                while private_name in source.variables:
                    private_name += '_'
                extent = self._header.createVariable(private_name, 'i1', (name,), fill_value=False, chunksizes=(1,))
                extent[len(dim)-1] = 0
        for name, v in source.variables.items():
            options = {'fill_value': v.getncattr('_FillValue') if '_FillValue' in v.ncattrs() else False}
            if v.dimensions:
                options['chunksizes'] = (1,) * v.ndim
            header = self._header.createVariable(name, v.datatype, v.dimensions, **options)
            _copy_attrs(v, header, skip=['_FillValue'])
            self.variables[name] = Variable(header, v)

    def __getattr__(self, name):
        return getattr(self._header, name)

    def __setattr__(self, name, value):
        if name.startswith('_') or name == 'variables':
            object.__setattr__(self, name, value)
        else:
            setattr(self._header, name, value)

    def __getitem__(self, name):
        return self.variables[name]

    @property
    def dimensions(self):
        return {n: d for n, d in self._header.dimensions.items() if n not in self._deleted_dimensions}

    def deleteVariable(self, name):
        """Omit a reviewed variable from the single output, never mutate the source."""
        if name not in self.variables:
            return
        retired = '__deleted_' + name
        while retired in self._header.variables:
            retired += '_'
        self._header.renameVariable(name, retired)
        del self.variables[name]

    def deleteDimension(self, name):
        if any(name in v.dimensions for v in self.variables.values()):
            raise ValueError(f'Cannot remove dimension {name} while a retained variable uses it')
        self._deleted_dimensions.add(name)
        self._dim_origins.pop(name, None)

    def renameVariable(self, old, new):
        self._header.renameVariable(old, new)
        self.variables = {new if n == old else n: v for n, v in self.variables.items()}

    def renameDimension(self, old, new):
        self._header.renameDimension(old, new)
        origin = self._dim_origins.pop(old)
        self._dim_origins[new] = origin

    def createDimension(self, name, size):
        self._header.createDimension(name, size)
        self._dim_origins[name] = None

    def createVariable(self, name, dtype, dimensions=(), **kwargs):
        header = self._header.createVariable(name, dtype, dimensions, **kwargs)
        v = Variable(header)
        self.variables[name] = v
        return v

    def replaceVariable(self, name, dtype, dimensions=(), **kwargs):
        """Prepare a type replacement, without retiring a variable in the real file.

        NetCDF cannot change an existing variable's storage type. The old header
        stays private to this diskless preview; Batch writes one clean output.
        """
        if name not in self.variables:
            raise ValueError(f'Cannot replace absent variable {name}')
        old = self.variables[name]
        source = old._source if old._source is not None else old._header
        options = _creation_options(source, dimensions if set(dimensions) <= set(source.dimensions) else source.dimensions)
        chunks = options.get('chunksizes')
        if chunks is not None:
            if len(chunks) != len(dimensions):
                options.pop('chunksizes')
            else:
                options['chunksizes'] = tuple(min(c, max(1, len(self.dimensions[d])))
                                              for c, d in zip(chunks, dimensions))
        options.update(kwargs)
        retired = '__replaced_' + name
        while retired in self._header.variables:
            retired += '_'
        self._header.renameVariable(name, retired)
        v = self.createVariable(name, dtype, dimensions, **options)
        object.__setattr__(v, '_creation_options', options)
        return v

    def close(self):
        self._header.close()
