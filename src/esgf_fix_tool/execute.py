"""Prepare every fix before writing: direct patches or exactly one rebuilt output."""

import os
import shutil
import tempfile
import time
from pathlib import Path

import netCDF4

from . import fixes
from .batch_write import Batch, raw_equal
from .fixes import bounds, cleanup, hybrid, layouts, rectilinear
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
    cleanup.verify_retired_metadata(output)
    with netCDF4.Dataset(output) as result:
        if "axis_nbounds" in result.dimensions:
            raise ValueError(
                "axis_nbounds survived the shared bounds-dimension normalization"
            )
    if any(hybrid.is_hybrid_step(s) for s in steps):
        hybrid.verify_hybrid_cleanup(output)
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


def _break_hardlinks(path):
    """Give ``path`` its own copy of the data: its other hard links keep the previous content."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".linkbreak", dir=path.parent)
    os.close(fd)
    try:
        shutil.copy2(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def execute(path, steps, *, dry_run=False, break_hardlinks=False):
    path = Path(path)
    other_links = 0
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
                        prepared = False
                        if s.fix == "normalize_hybrid_coordinate":
                            # Bind approved reference coefficients in the metadata preview
                            # before capturing the normalization preservation baseline.
                            # This shared prerequisite also covers a C102-only QA finding.
                            prepared = hybrid.prepare_normalization_inputs(preview)
                            coefficient_baseline = hybrid.coefficient_snapshot(preview)
                        changed = bool(method.func(preview, **s.params)) or prepared
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
            # C001 is a file-wide invariant, including variables created by later
            # recipes. Recognize legacy per-variable plans without changing their
            # generic drop_var_attr API or applying unselected corrections.
            history_index = next(
                (
                    i
                    for i, s in enumerate(steps)
                    if s.fix == "drop_variable_histories"
                    or s.fix == "drop_var_attr"
                    and s.params.get("attr") == "history"
                ),
                None,
            )
            if history_index is not None:
                with (
                    step_scope(steps[history_index]),
                    activity("Contrôle final de history sur toutes les variables"),
                ):
                    remaining = fixes.attributes._history_variables(preview)
                    cleaned = fixes.attributes.drop_variable_histories(preview)
                    entries[history_index]["changed"] |= cleaned
                    entries[history_index]["history_final_sweep"] = remaining
            # Clean after every selected recipe so later auxiliary creation cannot
            # reintroduce empty attributes or legacy coefficient backups.
            hybrid_index = next(
                (i for i, s in enumerate(steps) if hybrid.is_hybrid_step(s)), None
            )
            if hybrid_index is not None:
                with (
                    step_scope(steps[hybrid_index]),
                    activity("Nettoyage final des métadonnées hybrides"),
                ):
                    cleaned = hybrid.clean_hybrid_metadata(preview)
                    entries[hybrid_index]["changed"] |= cleaned
                    entries[hybrid_index]["hybrid_final_cleanup"] = cleaned
            # axis_nbounds is a two-endpoint index, never an independent grid.
            # Run the shared normalization even if another rule selected the file.
            if entries and "axis_nbounds" in preview.dimensions:
                dimension = preview.dimensions["axis_nbounds"]
                if len(dimension) != 2 or dimension.isunlimited():
                    raise ValueError(
                        "axis_nbounds must be a fixed size-two endpoint dimension"
                    )
                with activity("Normalisation commune axis_nbounds vers bnds"):
                    normalized = bounds.normalize_bounds_dimension(preview)
                    entries[0]["changed"] |= normalized
                    entries[0]["axis_nbounds_normalized"] = normalized
            # Shared cleanup is explicitly approved for every selected file. It
            # runs last so auxiliary replacements cannot leave fresh backup arrays.
            with activity("Nettoyage final des variables et dimensions techniques"):
                cleaned = cleanup.clean_retired_metadata(preview)
                if entries:
                    entries[0]["changed"] |= cleaned
                    entries[0]["retired_metadata_cleanup"] = cleaned
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
                # Other hard links (e.g. a published DRS version) must keep the previous content.
                # A rewrite gives the path a new file anyway; a direct update needs a copy first.
                other_links = path.stat().st_nlink - 1 if changed else 0
                if other_links and not rewrite and not break_hardlinks:
                    raise PermissionError(
                        "Direct updates require an independent copy: this file has hardlinks; "
                        "no change applied. Use --break-hardlinks to copy it first, so its other "
                        "links keep the previous content."
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
                if other_links:
                    for e in entries:
                        e["hardlinks_broken"] = other_links
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

    if other_links:
        with activity("Copie du fichier pour rompre ses liens physiques"):
            _break_hardlinks(path)
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
