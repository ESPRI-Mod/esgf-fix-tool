"""Compile all recipes into one schema, then patch metadata or stream one rebuilt file."""
from dataclasses import dataclass
from hashlib import sha256
import tempfile

import netCDF4
import numpy as np

from .data_integrity import data_blocks
from .fixes import bounds, coordinates, horizontal, hybrid, layouts, rectilinear, climatology
from .preview import MAX_AUX_BYTES


def attributes(obj):
    return {n:(obj.getncattr(n),layouts._attribute_type(obj,n)) for n in obj.ncattrs()}


def equal(a,b):
    x,y=np.asarray(a),np.asarray(b)
    return x.dtype==y.dtype and x.shape==y.shape and (np.array_equal(x,y,equal_nan=True) if x.dtype.kind in 'fc' else np.array_equal(x,y))


def set_attributes(target, wanted, *, new=False):
    """Write only differences, retaining the storage type of unchanged string attributes."""
    for name in set(target.ncattrs())-wanted.keys():
        if name!='_FillValue':
            target.delncattr(name)
    for name,(value,kind) in wanted.items():
        if name=='_FillValue':
            continue
        if name in target.ncattrs() and layouts._attribute_type(target,name)==kind and equal(target.getncattr(name),value):
            continue
        if kind==12:
            target.setncattr_string(name,value)
        else:
            target.setncattr(name,value)


@dataclass
class VariableSpec:
    name: str
    origin: str | None
    dimensions: tuple
    order: tuple
    attrs: dict
    data: np.ndarray | None
    dtype: object
    fill: object
    # Source dimension names are immutable; renamed dimensions are kept separately.
    source_dimensions: tuple
    filters: object
    chunks: object
    creation_options: object = None


KNOWN_FINALIZERS={None,coordinates._restore_coordinate_values,coordinates._restore_auxiliary_values,
                  horizontal._restore_horizontal,hybrid._finish_metadata,bounds._finish_climatology,
                  bounds._merge_dimensions,layouts._finish_layout,rectilinear._finish_profile}


class Batch:
    def __init__(self, preview, steps, registry):
        self.source=preview._source
        self.data_model=self.source.data_model
        self.attrs=attributes(preview._header)
        self.dimensions={n:(preview._dim_origins[n],len(d),d.isunlimited()) for n,d in preview.dimensions.items()}
        self.variables={n:VariableSpec(n,v._source.name if v._source is not None else None,
            v.dimensions,tuple(range(v.ndim)),attributes(v._header),v._data.copy() if v._data is not None else None,
            v._source.dtype if v._source is not None else v.dtype,v.getncattr('_FillValue') if '_FillValue' in v.ncattrs() else False,
            v._source.dimensions if v._source is not None else v.dimensions,
            v._source.filters() if v._source is not None else None,
            v._source.chunking() if v._source is not None else None,
            getattr(v, '_creation_options', None))
            for n,v in preview.variables.items()}
        self.custom_hooks=[s for s in steps if registry[s.fix].after_close not in KNOWN_FINALIZERS]
        self.layouts=[]
        self.written_digests = {}

        # All metadata recipes have already run on the preview. Combine the remaining
        # axis operations instead of calling each old whole-file after-close rewriter.
        desired={}
        shared_singletons = set()
        for s in steps:
            if s.fix!='fix_data_layout':
                continue
            var=s.params['var']
            target=tuple(s.params['target_order'])
            if var in desired and desired[var]!=target:
                raise ValueError(f'Conflicting final layouts for {var}')
            desired[var]=target
            if s.params.get('drop_shared'):
                shared_singletons.update(s.params.get('drop', ()))
            self.layouts.append(s)
        for var,target in desired.items():
            if shared_singletons.intersection(target):
                raise ValueError(f'Final layout of {var} still requires a shared singleton scheduled for removal')
            spec=self.variables[var]
            positions=[spec.dimensions.index(d) for d in target]
            self.variables[var].order=tuple(spec.order[i] for i in positions)
            self.variables[var].dimensions=target

        # Remove the reviewed shared singleton from auxiliaries as well as the
        # main field. Use dimension identities, independent of variable renames.
        for name in shared_singletons:
            self.dimensions.pop(name, None)
        for spec in self.variables.values():
            if shared_singletons.intersection(spec.dimensions):
                kept = [i for i, name in enumerate(spec.dimensions) if name not in shared_singletons]
                spec.order = tuple(spec.order[i] for i in kept)
                spec.dimensions = tuple(spec.dimensions[i] for i in kept)

        for s in steps:
            if s.fix=='normalize_bounds_dimension':
                mapping=bounds._dimension_map(preview)
                # A completed local replacement leaves only unreferenced original arrays.
                if mapping and bounds._local_bound_replacements(preview, mapping) != []:
                    self.dimensions.setdefault('bnds',(None,2,False))
                    for old in mapping:
                        self.dimensions.pop(old,None)
                    for v in self.variables.values():
                        v.dimensions=tuple(mapping.get(d,d) for d in v.dimensions)
            elif s.fix=='fix_basin_profile_latitude':
                self._profile(preview,s.params['var'])

        # Drop buffered arrays which did not actually change; this is crucial on replay.
        for v in self.variables.values():
            if v.data is not None and v.origin is not None:
                source=self.source[v.origin]
                layouts._raw(source)
                if raw_equal(source[:],v.data):
                    v.data=None
        climatology.configure(self, preview)

    def _profile(self,preview,var):
        name,values=rectilinear._profile(preview,var)
        lat=self.variables.pop(name)
        lat.name='lat'
        if name=='nav_lat':
            origin,size,unlimited=self.dimensions.pop('y')
            self.dimensions['lat']=(origin,size,unlimited)
            self.dimensions.pop('x')
            for v in [lat,*self.variables.values()]:
                selected=[i for i,d in enumerate(v.dimensions) if d!='x']
                v.order=tuple(v.order[i] for i in selected)
                v.dimensions=tuple('lat' if v.dimensions[i]=='y' else v.dimensions[i] for i in selected)
        source=self.source[lat.origin]
        layouts._raw(source)
        lat.data=np.asarray(source[:]).copy()
        lat.data.reshape(-1)[-1]=rectilinear.NEW_END
        lat.attrs['axis']=('Y',2)
        if 'name' in lat.attrs and lat.attrs['name'][0]=='nav_lat':
            lat.attrs['name']=('lat',2)
        self.variables['lat']=lat
        import re
        token=re.compile(r'(?<![\w/])nav_lat(?![\w/])')
        for v in self.variables.values():
            for attr in coordinates.REFERENCE_ATTRS:
                if attr in v.attrs and isinstance(v.attrs[attr][0],str):
                    old,kind=v.attrs[attr]
                    new=token.sub('lat',old)
                    if new!=old:
                        v.attrs[attr]=(new,2)
        main=self.variables[var]
        if 'coordinates' in main.attrs:
            text,kind=main.attrs['coordinates']
            main.attrs['coordinates']=(' '.join(t for t in text.split() if t not in ['nav_lon','longitude']),2)

    @property
    def rewrite(self):
        if self.climatology is not None:
            return True
        if self.custom_hooks:
            return True
        # A replaced storage type has a new buffered variable and no surviving
        # source identity. A clean output must omit the old variable entirely.
        if {v.origin for v in self.variables.values() if v.origin is not None} != set(self.source.variables):
            return True
        # Removed/merged dimension identities require rebuilding. Plain renames do not.
        origins={origin for origin,_,_ in self.dimensions.values() if origin is not None}
        if origins!=set(self.source.dimensions):
            return True
        for v in self.variables.values():
            if v.origin is None:
                continue
            source=self.source[v.origin]
            if v.order!=tuple(range(source.ndim)):
                return True
            if tuple(self.dimensions[d][0] for d in v.dimensions)!=source.dimensions:
                return True
        return False

    @property
    def changed(self):
        if self.rewrite or self.attrs.keys()!=set(self.source.ncattrs()):
            return True
        if not attrs_match(self.source,self.attrs):
            return True
        if any(name!=origin or origin is None for name,(origin,_,_) in self.dimensions.items()):
            return True
        for v in self.variables.values():
            if v.name!=v.origin or v.data is not None or v.origin is None:
                return True
            if not attrs_match(self.source[v.origin],v.attrs):
                return True
        return False

    def transform(self, data, spec):
        return layouts._transform(np.asarray(data),tuple(range(len(spec.source_dimensions))),spec.order)

    def _create(self,dst,spec):
        if spec.origin is not None:
            source=self.source[spec.origin]
            kept=tuple(source.dimensions[i] for i in spec.order)
            options=layouts._creation_options(source,kept)
            if self.climatology is not None and isinstance(spec.chunks,list):
                options['chunksizes'] = tuple(spec.chunks[i] for i in spec.order)
            dtype=source.datatype
        else:
            options=spec.creation_options or {'fill_value':spec.fill}
            dtype=spec.dtype
        return dst.createVariable(spec.name,dtype,spec.dimensions,**options)

    def write(self,output):
        """The sole full-file write: create final schema and stream each field once."""
        self.written_digests.clear()
        with netCDF4.Dataset(output,'w',format=self.data_model) as dst:
            set_attributes(dst,self.attrs)
            for name,(_,size,unlimited) in self.dimensions.items():
                dst.createDimension(name,None if unlimited else size)
            for spec in self.variables.values():
                out=self._create(dst,spec)
                layouts._raw(out)
                if spec.name in self.climate_fields:
                    for slab, values in climatology.averages(self.source[spec.origin],spec):
                        write_array(out,slab,values)
                elif spec.data is not None:
                    write_array(out,Ellipsis,self.transform(spec.data,spec))
                elif spec.origin is not None:
                    source=self.source[spec.origin]
                    layouts._raw(source)
                    # Record the expected output bytes while the source block is in memory.
                    # Variable-length strings still use the element-by-element comparison.
                    digest = sha256() if source.dtype is not str else None
                    for slab in data_blocks(source,'Réécriture regroupée'):
                        target=tuple(slab[i] for i in spec.order)
                        values = self.transform(source[slab], spec)
                        if digest is not None:
                            values = np.ascontiguousarray(values) if values.ndim else values
                            digest.update(values.data)
                        out[target] = values
                    if digest is not None:
                        self.written_digests[spec.name] = digest.digest()
                else:
                    raise ValueError(f'New variable {spec.name} has no approved values')
                # Packing must be installed after writing already-packed raw values.
                set_attributes(out,spec.attrs)

    def verify(self,output):
        """Check every mapped array after the output is closed and any hooks have run."""
        with netCDF4.Dataset(output) as dst:
            self.verify_schema(dst)
            for spec in self.variables.values():
                out=dst[spec.name]
                layouts._raw(out)
                if spec.name in self.climate_fields:
                    for slab, expected in climatology.averages(self.source[spec.origin],spec):
                        if not raw_equal(out[slab],expected):
                            raise ValueError(f'Climatological mean differs for {spec.name}')
                elif spec.data is not None:
                    if not raw_equal(out[...],self.transform(spec.data,spec)):
                        raise ValueError(f'Raw values differ for {spec.name}')
                elif spec.origin is not None:
                    source=self.source[spec.origin]
                    expected_digest = self.written_digests.get(spec.name)
                    if expected_digest is not None:
                        digest = sha256()
                        # Traverse the same mapped slabs using source metadata only.
                        for slab in data_blocks(source, 'Vérification de la sortie regroupée'):
                            target = tuple(slab[i] for i in spec.order)
                            values = np.asarray(out[target])
                            if values.ndim:
                                values = np.ascontiguousarray(values)
                            digest.update(values.data)
                        if digest.digest() != expected_digest:
                            raise ValueError(f'Raw values differ for {spec.name}')
                        continue
                    # External NCO output and strings have no digest from the Python copy.
                    layouts._raw(source)
                    for slab in data_blocks(source,'Vérification de la sortie regroupée'):
                        target=tuple(slab[i] for i in spec.order)
                        if not raw_equal(out[target],self.transform(source[slab],spec)):
                            raise ValueError(f'Raw values differ for {spec.name}')

    def verify_schema(self,dst):
        if set(dst.variables)!=set(self.variables) or set(dst.dimensions)!=set(self.dimensions):
            raise ValueError('Unexpected final variable/dimension inventory')
        if dst.data_model!=self.data_model or not attrs_match(dst,self.attrs):
            raise ValueError('Unexpected output format or global attributes')
        for name,(_,size,unlimited) in self.dimensions.items():
            if len(dst.dimensions[name])!=size or dst.dimensions[name].isunlimited()!=unlimited:
                raise ValueError(f'Unexpected size of {name}')
        for v in self.variables.values():
            if dst[v.name].dimensions!=v.dimensions or dst[v.name].dtype!=v.dtype or not attrs_match(dst[v.name],v.attrs):
                raise ValueError(f'Unexpected final metadata for {v.name}')
            if v.origin is not None and v.filters!=dst[v.name].filters():
                raise ValueError(f'Compression changed for {v.name}')
            expected_chunks=[v.chunks[i] for i in v.order] if isinstance(v.chunks,list) else v.chunks
            if v.origin is not None and dst[v.name].chunking()!=expected_chunks:
                raise ValueError(f'Chunk mapping changed for {v.name}')

    def patch_headers(self,dst):
        """Set the final metadata on an existing file; this never streams a science field."""
        for name,(origin,size,unlimited) in self.dimensions.items():
            if name not in dst.dimensions:
                if origin is not None and origin in dst.dimensions:
                    dst.renameDimension(origin,name)
                else:
                    dst.createDimension(name,None if unlimited else size)
        for spec in self.variables.values():
            if spec.name not in dst.variables:
                if spec.origin is not None and spec.origin in dst.variables:
                    dst.renameVariable(spec.origin,spec.name)
                else:
                    self._create(dst,spec)
        set_attributes(dst,self.attrs)
        for spec in self.variables.values():
            set_attributes(dst[spec.name],spec.attrs)

    def patch_auxiliaries(self,dst,backups):
        # Reopen after identifier changes: NetCDF-4 can resurrect attributes or lose
        # coordinate values on close. Reapply metadata and restore only affected arrays.
        set_attributes(dst,self.attrs)
        for spec in self.variables.values():
            out=dst[spec.name]
            layouts._raw(out)
            set_attributes(out,spec.attrs)
            if spec.data is not None:
                wanted=self.transform(spec.data,spec)
                # A newly allocated no-fill variable does not have defined data
                # to compare. Always initialize it before it can be read back.
                if spec.origin is None or not raw_equal(out[...],wanted):
                    write_array(out,Ellipsis,wanted)
            elif spec.name in backups:
                backups[spec.name].restore(out)

    def verify_patches(self,output,backups):
        with netCDF4.Dataset(output) as ds:
            self.verify_schema(ds)
            for spec in self.variables.values():
                out=ds[spec.name]
                layouts._raw(out)
                if spec.data is not None:
                    if not raw_equal(out[...],self.transform(spec.data,spec)):
                        raise ValueError(f'Raw values differ for patched auxiliary {spec.name}')
                elif spec.name in backups:
                    backups[spec.name].verify(out)

    def snapshots(self):
        backups={}
        main_id = str(getattr(self.source, 'variable_id', ''))
        try:
            for spec in self.variables.values():
                renamed_dim=any(d!=self.dimensions[d][0] for d in spec.dimensions)
                coordinate_rename=(renamed_dim and (spec.name in spec.dimensions or spec.origin in spec.source_dimensions))
                # The NetCDF coordinate-rename workaround must never turn a main-field
                # rename into a full science-array backup. CMIP files name that field
                # explicitly; either its old or corrected name can match variable_id.
                renamed_auxiliary = spec.name != spec.origin and main_id not in (spec.name, spec.origin)
                # Buffered edits already contain the complete final auxiliary values.
                if spec.origin is not None and spec.data is None and (renamed_auxiliary or coordinate_rename):
                    backups[spec.name]=Snapshot(self.source[spec.origin])
            return backups
        except BaseException:
            for backup in backups.values():
                backup.close()
            raise

    def nco(self):
        """Retain NCO for a standalone permutation; compound transforms use one Python pass."""
        explicit=[s for s in self.layouts if s.params.get('backend')=='nco']
        eligible=(len(self.layouts)==1 and not self.custom_hooks and self.climatology is None)
        if {v.origin for v in self.variables.values() if v.origin is not None} != set(self.source.variables):
            eligible=False
        if eligible:
            for v in self.variables.values():
                if v.origin is not None and len(v.order)!=len(v.source_dimensions):
                    eligible=False
            if {o for o,_,_ in self.dimensions.values() if o is not None}!=set(self.source.dimensions):
                eligible=False
            if any(o is not None and o!=n for n,(o,_,_) in self.dimensions.items()):
                eligible=False
        if explicit and not eligible:
            raise ValueError('Combined transforms require backend=auto/python for a single rewrite')
        if not eligible:
            return None
        step=self.layouts[0]
        backend=step.params.get('backend','auto')
        if backend not in ['auto','python','nco']:
            raise ValueError(f'Unknown layout backend {backend}')
        executable=layouts.shutil.which('ncpdq') if backend!='python' else None
        if backend=='nco' and executable is None:
            raise RuntimeError('ncpdq is unavailable')
        if executable:
            v=self.variables[step.params['var']]
            target=','.join(v.source_dimensions[i] for i in v.order)
            return executable,target
        return None


class Snapshot:
    """Back up a renamed auxiliary only, spooling large coordinates without copying the file."""
    def __init__(self,source):
        layouts._raw(source)
        self.shape=source.shape
        self.dtype=source.dtype
        self.file=None
        self.parts=[]
        self.data=None
        if source.size*max(8,np.dtype(source.dtype).itemsize)<=MAX_AUX_BYTES:
            self.data=np.asarray(source[...]).copy()
        else:
            if source.dtype is str:
                raise ValueError('Large string-coordinate snapshots are not supported')
            self.file=tempfile.TemporaryFile()
            for slab in data_blocks(source,'Sauvegarde de la coordonnée renommée'):
                a=np.asarray(source[slab])
                offset=self.file.tell()
                self.file.write(a.tobytes())
                self.parts.append((slab,offset,a.shape,a.dtype,a.nbytes))

    def blocks(self):
        if self.data is not None:
            yield Ellipsis,self.data
        else:
            for slab,offset,shape,dtype,size in self.parts:
                self.file.seek(offset)
                yield slab,np.frombuffer(self.file.read(size),dtype=dtype).reshape(shape)

    def restore(self,target):
        for slab,wanted in self.blocks():
            if not raw_equal(target[slab],wanted):
                write_array(target,slab,wanted)

    def verify(self,target):
        for slab,wanted in self.blocks():
            if not raw_equal(target[slab],wanted):
                raise ValueError(f'Raw values changed during rename of {target.name}')

    def close(self):
        if self.file is not None:
            self.file.close()


def write_array(variable,slab,data):
    if variable.dtype is str and variable.ndim==0:
        variable[()]=np.asarray(data).item()
    else:
        variable[slab]=data


def raw_equal(a,b):
    x,y=np.asarray(a),np.asarray(b)
    if x.dtype.kind in 'OU' and y.dtype.kind in 'OU':
        return x.shape==y.shape and np.array_equal(x,y)
    return x.dtype==y.dtype and x.shape==y.shape and (np.array_equal(x,y) if x.dtype.kind=='O' else x.tobytes()==y.tobytes())


def attrs_match(obj,wanted):
    return (set(obj.ncattrs())==set(wanted) and all(layouts._attribute_type(obj,n)==kind and equal(obj.getncattr(n),value)
                                                   for n,(value,kind) in wanted.items()))
