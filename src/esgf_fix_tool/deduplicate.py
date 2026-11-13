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
            fields = filename[:-3].split("_", 2)
            if len(fields) != 3 or fields[0] not in POLICY:
                continue
            variable, brand, _ = fields
            horizontal, surface, preferred = POLICY[variable]
            labels = brand.split("-")
            if len(labels) != 4 or labels[2:] != [horizontal, surface]:
                continue
            path = Path(directory) / filename
            parts = list(path.relative_to(root).parts)
            positions = [i for i, part in enumerate(parts[:-1]) if part in ("ATM", "SRF")]
            if len(positions) != 1 or parts[positions[0]] == preferred:
                continue
            parts[positions[0]] = preferred
            yield variable, variable + "_" + brand, path, root.joinpath(*parts)


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
                or getattr(ds, "source_id", "") != "IPSL-CM6-ESMCO2"
                or variable not in ds.variables
                or ds[variable].size == 0
            ):
                raise ValueError("Unexpected or empty IPSL product; both files retained")
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


def deduplicate(root, fixlog, *, dry_run=False, only_under=None, emit=print):
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
    with fixlog.open("a", encoding="utf-8") as log:
        for variable, brand, discard, keep in _candidates(root):
            entry = dict(time=datetime.now(UTC).isoformat(), operation="component-deduplication",
                         variable=variable, branded_variable=brand, discard=str(discard), keep=str(keep),
                         dry_run=dry_run)
            try:
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
            emit(f"{status}: {discard} — keep: {keep}" +
                 (f" — {entry['error']}" if "error" in entry else ""))
    return counts
