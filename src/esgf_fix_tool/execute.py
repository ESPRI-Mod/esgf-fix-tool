"""Prepare every fix before writing: direct patches or exactly one rebuilt output."""

import os
import shutil
import tempfile
import time
from pathlib import Path

import netCDF4

from . import fixes
from .batch_write import Batch, raw_equal
from .fixes import hybrid, layouts, rectilinear
from .preview import Preview
from .progress import activity, notify, step_scope


def data_state(preview):
    return {id(v): array_state(v._data) for v in preview.variables.values()}


def array_state(data):
    if data is None:
        return None
    return (
        data.shape,
        data.dtype.str,
        tuple(data.ravel().tolist()) if data.dtype.kind == "O" else data.tobytes(),
    )


def check_numeric_edits(preview, before, allowed):
    for name, v in preview.variables.items():
        if v._source is None or v._data is None:
            continue
        if before.get(id(v)) == array_state(v._data):
            continue
        layouts._raw(v._source)
        if raw_equal(v._source[...], v._data):
            continue
        if name not in allowed and v._source.name not in allowed:
            raise ValueError(
                f"Raw values changed without a declared data target: {name}"
            )


def final_checks(path, output, steps, entries, coefficient_baseline=None):
    for s, entry in zip(steps, entries):
        method = fixes.REGISTRY[s.fix]
        # Unified verification covers composed dimension mappings, which the old
        # per-recipe profile verifier could not account for after another recipe.
        if s.fix == "fix_basin_profile_latitude":
            with netCDF4.Dataset(output) as ds:
                rectilinear._profile(ds, s.params["var"])
        elif s.fix == "normalize_hybrid_coordinate":
            with step_scope(s), activity("Contrôle final du correctif"):
                method.verify_final(
                    path, output, coefficient_baseline=coefficient_baseline
                )
        elif method.verify_final is not None:
            with step_scope(s), activity("Contrôle final du correctif"):
                method.verify_final(path, output, **s.params)


def execute(path, steps, *, dry_run=False):
    path = Path(path)
    entries = []
    backups = {}
    temporary = None
    coefficient_baseline = None
    with netCDF4.Dataset(path) as source:
        with activity("Lecture du schéma sans écriture"):
            preview = Preview(source)
        try:
            for s in steps:
                method = fixes.REGISTRY[s.fix]
                if (
                    method.kind != "metadata"
                    and not method.layout_change
                    and not (method.data_targets and method.verify_final)
                ):
                    raise NotImplementedError(
                        f"Structural fix is not supported: {s.fix}"
                    )
                with step_scope(s):
                    notify("fix", "start")
                    started = time.monotonic()
                    before = data_state(preview)
                    with activity("Préparation du correctif sans écriture"):
                        if s.fix == "normalize_hybrid_coordinate":
                            # Read-only validation catches incompatible dimensions before
                            # writing; the snapshot follows all selected coefficient fixes.
                            hybrid._normalization_values(preview)
                            coefficient_baseline = hybrid.coefficient_snapshot(preview)
                        changed = bool(method.func(preview, **s.params))
                        allowed = (
                            method.data_targets(s.params)
                            if method.data_targets is not None
                            else set()
                        )
                        check_numeric_edits(preview, before, allowed)
                    entries.append(
                        dict(
                            rule=s.rule,
                            fix=s.fix,
                            params=s.params,
                            changed=changed,
                            prepare_seconds=time.monotonic() - started,
                        )
                    )
                    notify("fix", "prepared", changed=changed)
            with activity("Regroupement de toutes les transformations du fichier"):
                batch = Batch(preview, steps, fixes.REGISTRY)
                rewrite = batch.rewrite
                changed = batch.changed
                nco = batch.nco() if rewrite else None
                # Check creation/filter support during preflight, including dry-run.
                if rewrite:
                    for v in batch.variables.values():
                        if v.origin is not None:
                            layouts._creation_options(
                                source[v.origin],
                                tuple(v.source_dimensions[i] for i in v.order),
                            )
                if changed and not rewrite and path.stat().st_nlink > 1:
                    raise PermissionError(
                        "Direct updates require an independent copy: this file has hardlinks; "
                        "no change applied. Break its physical links before applying metadata fixes."
                    )
                mode = (
                    "rewrite-once"
                    if rewrite and changed
                    else "in-place"
                    if changed
                    else "none"
                )
                for e in entries:
                    e.update(
                        write_mode=mode,
                        rewrite_count=0,
                        dry_run=dry_run,
                        data_verified=not dry_run,
                        verification_scope="preflight-only"
                        if dry_run
                        else "full-mapped-arrays"
                        if rewrite
                        else "metadata-and-edited-auxiliaries",
                    )
                for s, e in zip(steps, entries):
                    if fixes.REGISTRY[s.fix].layout_change:
                        e["layout_verified"] = not dry_run
                if not changed:
                    for e in entries:
                        e.update(
                            changed=False,
                            data_verified=not dry_run,
                            verification_scope="preflight-only"
                            if dry_run
                            else "no-write-preflight",
                        )
                    return entries
                if dry_run:
                    return entries

            if rewrite:
                fd, temporary = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".fixtmp", dir=path.parent
                )
                os.close(fd)
                try:
                    with activity("Réécriture unique regroupée"):
                        if nco:
                            executable, target = nco
                            command = [
                                executable,
                                "-O",
                                "-h",
                                "-a",
                                target,
                                str(path),
                                temporary,
                            ]
                            result = layouts.subprocess.run(
                                command, capture_output=True, text=True
                            )
                            if result.returncode:
                                raise RuntimeError(
                                    f"ncpdq failed: {result.stderr[-2000:]}"
                                )
                            backups = batch.snapshots()
                            with netCDF4.Dataset(temporary, "a") as dst:
                                batch.patch_headers(dst)
                            with netCDF4.Dataset(temporary, "a") as dst:
                                batch.patch_auxiliaries(dst, backups)
                            backend = "ncpdq"
                        else:
                            batch.write(temporary)
                            backend = "python-grouped"
                    for s in batch.custom_hooks:
                        with step_scope(s), activity("Finalisation complémentaire"):
                            details = fixes.REGISTRY[s.fix].after_close(
                                path, temporary, **s.params
                            )
                            if details:
                                next(e for e in entries if e["rule"] == s.rule).update(
                                    details
                                )
                    final_checks(path, temporary, steps, entries, coefficient_baseline)
                    with activity(
                        "Vérification de tous les tableaux de la sortie regroupée"
                    ):
                        batch.verify(temporary)
                    with activity("Remplacement atomique après une seule réécriture"):
                        shutil.copymode(path, temporary)
                        os.replace(temporary, path)
                        temporary = None
                    for e in entries:
                        e.update(backend=backend, rewrite_count=1, data_verified=True)
                    return entries
                finally:
                    if temporary is not None and os.path.exists(temporary):
                        os.unlink(temporary)
                    for b in backups.values():
                        b.close()

            # Only auxiliaries affected by renames/edits are saved, never the main field.
            # Close the read-only source BEFORE opening the same file in update mode.
            with activity("Sauvegarde ciblée des coordonnées affectées"):
                backups = batch.snapshots()
        finally:
            preview.close()

    try:
        with activity("Application directe des noms et attributs"):
            reopen = any(
                origin is not None and name != origin
                for name, (origin, _, _) in batch.dimensions.items()
            ) or any(
                v.origin is not None and v.name != v.origin
                for v in batch.variables.values()
            )
            with netCDF4.Dataset(path, "a") as dst:
                batch.patch_headers(dst)
                if not reopen:
                    batch.patch_auxiliaries(dst, backups)
        if reopen:
            with activity(
                "Finalisation directe des coordonnées renommées après fermeture"
            ), netCDF4.Dataset(path, "a") as dst:
                batch.patch_auxiliaries(dst, backups)
        final_checks(path, path, steps, entries, coefficient_baseline)
        with activity("Vérification des métadonnées et coordonnées affectées"):
            batch.verify_patches(path, backups)
        for e in entries:
            e.update(backend="netCDF4-in-place", data_verified=True)
        return entries
    finally:
        for b in backups.values():
            b.close()
