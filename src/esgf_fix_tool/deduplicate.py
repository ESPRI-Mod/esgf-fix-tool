"""Resolve the user's approved ATM/SRF production overlaps, without QA rules."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import netCDF4
import numpy as np

# variable: (horizontal label, surface label, preferred component)
POLICY = {
    "areacella": ("hxy", "u", "ATM"),
    "evspsbl": ("hxy", "u", "ATM"),
    "snc": ("hxy", "lnd", "SRF"),
}
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
                or getattr(a, "calendar", "standard") != getattr(b, "calendar", "standard")
                or np.ma.getmaskarray(x).any() or np.ma.getmaskarray(y).any()
                or not np.isfinite(x).all() or not np.array_equal(x, y)
            ):
                raise ValueError("Temporal coverage differs; both files retained")
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
                if parent.name in ("ATM", "SRF") and parent.parent.name == "CMIP7":
                    roots.add(parent.parent)
                    break
        if len(roots) == 1:
            root = roots.pop()
        elif not roots and only_under is not None:
            base = Path(only_under)
            for candidate in (base, base / "CMIP7"):
                if (candidate / "ATM").is_dir() and (candidate / "SRF").is_dir():
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
        if any((candidate / component).is_dir() for component in ("ATM", "SRF")):
            return candidate
    return None


def deduplicate(root, fixlog, *, dry_run=False, only_under=None, emit=print, blocked_paths=()):
    """Unlink only approved secondary products with a validated preferred peer.

    Deleting needs ``only_under``: the caller names the directory deletions are allowed in, which must
    contain ``root``. A dry run needs nothing. A missing keeper is never guessed or synthesized. No NetCDF
    array is edited. Replaying after deletion finds no secondary candidate and changes nothing.
    """
    root, fixlog = Path(root).resolve(), Path(fixlog)
    if not root.is_dir():
        raise ValueError("Expected the simulation directory containing ATM/SRF")
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
                         dry_run=dry_run)
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
    return counts
