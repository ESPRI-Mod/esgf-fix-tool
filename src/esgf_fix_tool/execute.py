"""Prepare every fix before writing: direct patches or exactly one rebuilt output."""

import os
import shutil
import tempfile
import time
import traceback
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
            rectilinear.verify_profile_output(output, s.params["var"])
        elif s.fix == "normalize_hybrid_coordinate":
            with step_scope(s), activity("Contrôle final du correctif"):
                method.verify_final(
                    path, output, coefficient_baseline=coefficient_baseline
                )
        elif method.verify_final is not None:
            with step_scope(s), activity("Contrôle final du correctif"):
                method.verify_final(path, output, **s.params)


class PreparationFailure(Exception):
    """A recipe failed while the source was still read-only."""

    def __init__(self, index, step, error, trace):
        self.index, self.step, self.error, self.trace = index, step, error, trace
        super().__init__(str(error))


def execute(path, steps, *, dry_run=False, allow_hardlinks=False, continue_on_fix_error=False):
    """Discard failed previews and rebuild only the remaining independent fixes.

    A failed recipe may already have mutated its private preview. Never reuse
    that state. Retry preparation from the untouched source; commit at most once.
    Write/verification errors are deliberately not caught or retried here.
    """
    active = list(range(len(steps)))
    failures = {}
    first_error = None
    while active:
        try:
            result = _execute(path, [steps[i] for i in active], dry_run=dry_run,
                              allow_hardlinks=allow_hardlinks)
            combined = dict(zip(active, result)) | failures
            mode = result[0]["write_mode"] if result else "none"
            for entry in failures.values():
                entry.update(write_mode=mode, rewrite_count=0, dry_run=dry_run,
                             data_verified=False)
            return [combined[i] for i in range(len(steps))]
        except PreparationFailure as failure:
            if not continue_on_fix_error:
                raise failure.error.with_traceback(failure.error.__traceback__)
            failed_index = active[failure.index]
            first_error = first_error or failure.error
            failures[failed_index] = dict(
                rule=failure.step.rule, fix=failure.step.fix, params=failure.step.params,
                changed=False, status="failed", error=str(failure.error),
                traceback=failure.trace,
            )
            with step_scope(failure.step):
                notify("error", "failed", traceback=failure.trace)
            from .plan import STEP_DEPENDENCIES

            blocked_methods = {failure.step.fix}
            # Hybrid reconstruction and its metadata form a scientifically linked
            # group. Do not declare an axis compliant after its coefficients fail.
            failed_hybrid = hybrid.is_hybrid_step(failure.step) or (
                failure.step.fix == "rename_coordinate" and failure.step.params.get("target") == "lev"
            )
            pending = [i for i in active if i != failed_index]
            changed = True
            while changed:
                changed = False
                for i in list(pending):
                    step = steps[i]
                    prerequisites = STEP_DEPENDENCIES.get(step.fix, set()) & blocked_methods
                    if (prerequisites == {"promote_coordinate_to_double"}
                            and step.params.get("coordinate") != failure.step.params.get("var")):
                        prerequisites = set()  # Another coordinate's promotion is independent.
                    if prerequisites or (
                        failed_hybrid and (hybrid.is_hybrid_step(step) or step.params.get("var") in {"lev", "ap", "b", "ap_bnds", "b_bnds", "lev_bnds"})
                    ):
                        failures[i] = dict(rule=step.rule, fix=step.fix, params=step.params,
                                           changed=False, status="blocked", error="Selected prerequisite failed")
                        pending.remove(i)
                        blocked_methods.add(step.fix)
                        changed = True
            active = pending
    # If every recipe failed/was blocked, preserve the normal per-file failure.
    if first_error is not None:
        raise first_error.with_traceback(first_error.__traceback__)
    return []


def _execute(path, steps, *, dry_run=False, allow_hardlinks=False):
    path = Path(path)
    entries = []
    backups = {}
    temporary = None
    coefficient_baseline = None
    with netCDF4.Dataset(path) as source:
        with activity("Lecture du schéma sans écriture"):
            preview = Preview(source)
        try:
            for preparation_index, s in enumerate(steps):
                try:
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
                except Exception as exc:
                    raise PreparationFailure(preparation_index, s, exc, traceback.format_exc()) from exc
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
                link_count = path.stat().st_nlink
                if changed and link_count > 1:
                    if allow_hardlinks and rewrite:
                        # Atomic replacement would detach this path from its aliases.
                        # Never silently split a published hardlink set in shared mode.
                        raise PermissionError(
                            "--allow-hardlinks supports direct updates only; this file "
                            "needs a structural rewrite, which would break its hardlinks. "
                            "Use an independent copy and rebuild its publication links."
                        )
                    if not allow_hardlinks and not rewrite:
                        raise PermissionError(
                            "Direct updates require an independent copy: this file has hardlinks; "
                            "no change applied. Use --allow-hardlinks to explicitly update all "
                            "physical aliases, or break its links first."
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
                if allow_hardlinks and link_count > 1:
                    for e in entries:
                        e.update(hardlink_count=link_count, shared_hardlinks=True)
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
