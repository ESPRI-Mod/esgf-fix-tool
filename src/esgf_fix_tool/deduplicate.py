"""Apply approved component-copy and unused-product cleanup policies, without QA rules."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import cftime
import netCDF4
import numpy as np

from . import __version__

# variable: (horizontal label, surface label, preferred component)
POLICY = {
    "areacella": ("hxy", "u", "ATM"),
    "evspsbl": ("hxy", "u", "ATM"),
    "snc": ("hxy", "lnd", "SRF"),
}
UNUSED_PRODUCTS = {"ch4_tclm-u-hm-air", "n2o_tclm-u-hm-air"}


def unused_product(root, path):
    """Select only the two explicitly retired brands in a flat CHM component tree."""
    root, path = Path(root), Path(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if not (len(parts) >= 2 and (parts[0] == 'CHM' or parts[:2] == ('CMIP7', 'CHM'))):
        return None
    if '..' in parts:
        return None
    fields = path.name.split('_', 2)
    brand = '_'.join(fields[:2])
    return brand if path.suffix == '.nc' and len(fields) == 3 and brand in UNUSED_PRODUCTS else None


def validate_unused(root, path):
    """Verify the exact unused product from metadata, without reading science arrays."""
    root, path = Path(root).resolve(), Path(path)
    brand = unused_product(root, path)
    if not brand or path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError('Expected an approved regular unused CHM product inside the simulation')
    signature = _signature(path)
    variable = brand.split('_', 1)[0]
    with netCDF4.Dataset(path) as ds:
        if (getattr(ds, 'branded_variable', '') != brand
                or getattr(ds, 'variable_id', '') != variable or variable not in ds.variables
                or any(not getattr(ds, attr, '') for attr in IDENTITY)
                or getattr(ds, 'frequency', '') != 'mon'):
            raise ValueError('Unused-product filename and NetCDF identity disagree; file retained')
        prefix = '_'.join((brand, *(str(getattr(ds, a)) for a in
                         ('frequency','region','grid_label','source_id','experiment_id','variant_label'))))
        if path.stem != prefix and not path.stem.startswith(prefix + '_'):
            raise ValueError('Unused-product filename and NetCDF identity disagree; file retained')
    return signature


def _unused_candidates(root):
    for component in (root / 'CHM', root / 'CMIP7' / 'CHM'):
        if component.is_dir() and not component.is_symlink():
            for directory, dirs, files in os.walk(component, followlinks=False):
                dirs.sort()
                for filename in sorted(files):
                    path = Path(directory) / filename
                    if unused_product(root, path):
                        yield path
# CF calendar names that mean the same calendar (a missing calendar is "standard")
CALENDAR_ALIASES = {"": "standard", "gregorian": "standard", "365_day": "noleap", "366_day": "all_leap"}
# standard (mixed Julian/Gregorian) and proleptic_gregorian give the same dates from the Gregorian reform on
GREGORIAN_REFORM = cftime.DatetimeProlepticGregorian(1582, 10, 15)

IDENTITY = (
    "source_id", "experiment_id", "variant_label", "branded_variable",
    "frequency", "region", "grid_label",
)


def _signature(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _candidates(root):
    """Pair exact relative filenames, replacing only the ATM/SRF directory."""
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for filename in sorted(files):
            if not filename.endswith(".nc"):
                continue
            path = Path(directory) / filename
            peer = _preferred_peer(root, path)
            if peer is not None:
                variable, brand, keep = peer
                yield variable, brand, path, keep


def _preferred_peer(root, path):
    fields = path.stem.split("_", 2)
    if path.suffix != ".nc" or len(fields) != 3 or fields[0] not in POLICY:
        return None
    variable, brand, _ = fields
    horizontal, surface, preferred = POLICY[variable]
    labels = brand.split("-")
    if len(labels) != 4 or labels[2:] != [horizontal, surface]:
        return None
    try:
        parts = list(path.relative_to(root).parts)
    except ValueError:
        return None
    positions = [i for i, part in enumerate(parts[:-1]) if part in ("ATM", "SRF")]
    if len(positions) != 1 or parts[positions[0]] == preferred:
        return None
    parts[positions[0]] = preferred
    return variable, variable + "_" + brand, root.joinpath(*parts)


def completed_discard(root, path):
    """Replay skips only an absent secondary copy with a readable preferred peer.

    Never apply the deleted copy's planned metadata changes to the kept file.
    Existing files and broken symbolic links still follow normal validation.
    """
    path = Path(path)
    if path.exists() or path.is_symlink():
        return None
    peer = _preferred_peer(root, path)
    if peer is None:
        return None
    variable, brand, keep = peer
    if not keep.is_file() or keep.is_symlink() or keep.resolve() != keep:
        return None
    try:
        with netCDF4.Dataset(keep) as ds:
            if (getattr(ds, "variable_id", "") != variable
                    or getattr(ds, "branded_variable", "") != brand
                    or any(not getattr(ds, attr, "") for attr in IDENTITY)
                    or variable not in ds.variables or ds[variable].size == 0):
                return None
            prefix = "_".join((brand, *(str(getattr(ds, a)) for a in
                ("frequency", "region", "grid_label", "source_id", "experiment_id", "variant_label"))))
            if keep.stem != prefix and not keep.stem.startswith(prefix + "_"):
                return None
    except OSError:
        return None
    return keep


def _same_calendar(a, b, values, units):
    """True if two CF calendars give the same dates for these time values."""
    a, b = (CALENDAR_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in (a, b))
    if a == b:
        return True
    if {a, b} != {"standard", "proleptic_gregorian"}:
        return False
    # the reference date and every value must fall on or after the reform (15 October 1582)
    first = cftime.num2date([0, float(values.min())], units, calendar="proleptic_gregorian")
    return all(day >= GREGORIAN_REFORM for day in first)


def _validate(variable, brand, discard, keep, root):
    """Validate file identity and temporal coverage, never scientific equality."""
    for path in (discard, keep):
        if path.is_symlink() or path.resolve() != path:
            raise ValueError(f"Symbolic links are not accepted: {path}")
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f"Expected a regular file within the simulation: {path}")
    signatures = (_signature(discard), _signature(keep))
    with netCDF4.Dataset(discard) as src, netCDF4.Dataset(keep) as dst:
        for ds in (src, dst):
            if (
                getattr(ds, "variable_id", "") != variable
                or getattr(ds, "branded_variable", "") != brand
                or variable not in ds.variables
                or ds[variable].size == 0
            ):
                raise ValueError("Unexpected or empty product; both files retained")
        for attr in IDENTITY:
            if not getattr(src, attr, "") or getattr(src, attr) != getattr(dst, attr, None):
                raise ValueError(f"Product identity differs or is missing: {attr}")
        # Metadata must also agree with the filename used to select the pair.
        prefix = "_".join((brand, *(str(getattr(src, a)) for a in
                            ("frequency", "region", "grid_label", "source_id", "experiment_id", "variant_label"))))
        if not (discard.stem == prefix or discard.stem.startswith(prefix + "_")):
            raise ValueError("Filename and NetCDF product identity disagree")
        if src.frequency != "fx":
            if "time" not in src.variables or "time" not in dst.variables:
                raise ValueError("Time coordinate missing; both files retained")
            a, b = src["time"], dst["time"]
            x, y = np.ma.asarray(a[:]), np.ma.asarray(b[:])
            if (
                a.dimensions != ("time",) or b.dimensions != ("time",)
                or not x.size or x.shape != y.shape
                or getattr(a, "units", None) != getattr(b, "units", None)
                or not getattr(a, "units", "")
                or np.ma.getmaskarray(x).any() or np.ma.getmaskarray(y).any()
                or not np.isfinite(x).all() or not np.array_equal(x, y)
            ):
                raise ValueError("Temporal coverage differs; both files retained")
            calendars = getattr(a, "calendar", ""), getattr(b, "calendar", "")
            if not _same_calendar(*calendars, x, a.units):
                raise ValueError(f"Calendars differ ({calendars[0] or 'none'} / {calendars[1] or 'none'}); "
                                 "both files retained")
    return signatures


def apply_root(plan, only_under=None):
    """Use the mapped report scope; recover the flat CMIP7 tree for old plans.

    Never treat an arbitrary --only-under ancestor as permission to scan every
    simulation below it. An empty legacy plan needs a directly named ATM/SRF tree.
    """
    root = getattr(plan, "deduplicate_root", None)
    if root is None:
        roots = set()
        for name in plan.files:
            path = Path(name)
            for parent in path.parents:
                if parent.name in ("ATM", "SRF", "CHM") and parent.parent.name == "CMIP7":
                    roots.add(parent.parent)
                    break
        if len(roots) == 1:
            root = roots.pop()
        elif not roots and only_under is not None:
            base = Path(only_under)
            for candidate in (base, base / "CMIP7"):
                if ((candidate / "ATM").is_dir() and (candidate / "SRF").is_dir()
                        or (candidate / "CHM").is_dir()):
                    root = candidate
                    break
    if root is None:
        return None
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Deduplication scope does not exist: {root}; regenerate/map the plan")
    if only_under is not None and not root.is_relative_to(Path(only_under).resolve()):
        raise ValueError(f"Deduplication scope {root} is outside --only-under {only_under}")
    if any(not Path(name).resolve().is_relative_to(root) for name in plan.files):
        raise ValueError("Plan files are outside the deduplication scope; regenerate/map the plan")
    # Automatic cleanup is limited to one flat component tree. A report scope
    # over published DRS data or several simulations must not trigger a broad scan.
    for candidate in (root, root / "CMIP7"):
        if any((candidate / component).is_dir() for component in ("ATM", "SRF", "CHM")):
            return candidate
    return None


def deduplicate(root, fixlog, *, dry_run=False, only_under=None, emit=print, blocked_paths=()):
    """Unlink approved component copies and explicitly retired chemistry products.

    Deleting needs ``only_under``: the caller names the directory deletions are allowed in, which must
    contain ``root``. A dry run needs nothing. For duplicate copies, a missing keeper is never guessed or synthesized. No NetCDF
    array is edited. Replaying after deletion finds no secondary candidate and changes nothing.
    """
    root, fixlog = Path(root).resolve(), Path(fixlog)
    if not root.is_dir():
        raise ValueError("Expected the simulation directory containing component subdirectories")
    # These are simulation files: never delete without an explicit, named consent.
    if not dry_run:
        if only_under is None:
            raise ValueError("Deleting files requires --only-under DIR, the directory deletions are allowed in; "
                             "nothing deleted (use --dry-run to preview)")
        only_under = Path(only_under).resolve()
        if not root.is_relative_to(only_under):
            raise ValueError(f"{root} is not inside --only-under {only_under}; nothing deleted")
    if fixlog.suffix.lower() == ".nc":
        raise ValueError("The deduplication log must not be a NetCDF file")
    counts = dict(deleted=0, would_delete=0, missing_keeper=0, failed=0)
    blocked = {Path(p).resolve() for p in blocked_paths}
    with fixlog.open("a", encoding="utf-8") as log:
        for variable, brand, discard, keep in _candidates(root):
            entry = dict(time=datetime.now(UTC).isoformat(), operation="component-deduplication",
                         variable=variable, branded_variable=brand, discard=str(discard), keep=str(keep),
                         dry_run=dry_run, tool_version=__version__,
                         etb_run_id=os.environ.get("ETB_RUN_ID") or None)
            try:
                if discard.resolve() in blocked or keep.resolve() in blocked:
                    raise ValueError("A planned correction failed for this pair; both files retained")
                if not keep.exists():
                    status = "missing_keeper"
                else:
                    signatures = _validate(variable, brand, discard, keep, root)
                    # Record the intention before unlinking and recheck both files
                    # after metadata inspection to catch concurrent replacements.
                    entry["status"] = "validated"
                    log.write(json.dumps(entry) + "\n")
                    log.flush()
                    if (_signature(discard), _signature(keep)) != signatures:
                        raise ValueError("A file changed during inspection; nothing deleted")
                    if dry_run:
                        status = "would_delete"
                    else:
                        if not discard.resolve().is_relative_to(only_under):
                            raise ValueError(f"{discard} is outside --only-under {only_under}; nothing deleted")
                        discard.unlink()
                        status = "deleted"
            except Exception as exc:  # noqa: BLE001 - one bad pair is logged as failed, the others go on
                status = "failed"
                entry["error"] = str(exc)
            entry["status"] = status
            log.write(json.dumps(entry) + "\n")
            log.flush()
            counts[status] += 1
            preferred = POLICY[variable][2]
            other = "SRF" if preferred == "ATM" else "ATM"
            emit(f"{status}: {discard.name} [{other}] — keep: {preferred}" +
                 (f" — {entry['error']}" if "error" in entry else ""))
        # These are not duplicates: the user explicitly retired the two global
        # climatology products. No preferred peer or equality test is required.
        for discard in _unused_candidates(root):
            for key in ('obsolete_deleted', 'obsolete_would_delete', 'obsolete_failed'):
                counts.setdefault(key, 0)
            entry = dict(time=datetime.now(UTC).isoformat(), operation='unused-product-removal',
                         branded_variable=unused_product(root, discard), discard=str(discard),
                         dry_run=dry_run, tool_version=__version__)
            try:
                if discard.resolve() in blocked:
                    raise ValueError('A planned correction failed; unused product retained')
                signature = validate_unused(root, discard)
                entry['status'] = 'validated'
                log.write(json.dumps(entry) + '\n'); log.flush()
                if _signature(discard) != signature:
                    raise ValueError('Unused product changed during inspection; retained')
                if dry_run:
                    status = 'would_delete'
                else:
                    if not discard.resolve().is_relative_to(only_under):
                        raise ValueError('Unused product is outside --only-under; retained')
                    discard.unlink()
                    status = 'deleted'
            except Exception as exc:  # noqa: BLE001 -- record one failed candidate and continue
                status = 'failed'
                entry['error'] = str(exc)
            entry['status'] = status
            log.write(json.dumps(entry) + '\n'); log.flush()
            counts['obsolete_' + status] += 1
            emit(f"{status}: {discard.name} [retired product]" +
                 (f" — {entry['error']}" if 'error' in entry else ''))
    return counts
